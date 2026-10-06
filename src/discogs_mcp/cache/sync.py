"""Pull the collection and wantlist into SQLite, and build summaries from it."""

from __future__ import annotations

import asyncio
import csv
import json
import logging
from collections import Counter
from pathlib import Path
from typing import Any

from ..client import DiscogsClient, DiscogsError
from .db import CacheDB, now_iso

log = logging.getLogger(__name__)


async def field_names(client: DiscogsClient, user: str) -> dict[int, str]:
    data = await client.get(f"/users/{user}/collection/fields")
    return {f["id"]: f["name"] for f in data.get("fields", [])}


async def sync_collection(client: DiscogsClient, db: CacheDB, user: str, *, full: bool = False) -> dict:
    """Sync folders, collection items and the wantlist.

    Incremental mode walks the collection newest-first and stops at the first
    instance already in the cache. It picks up additions only; a full sync also
    catches removals, folder moves and rating/field edits made on the website.
    """
    started = now_iso()
    if db.get_meta("username") not in (None, user):
        full = True  # a different account: never mix collections
    if db.get_meta("collection_synced_at") is None:
        full = True

    names = await field_names(client, user)
    folders = (await client.get(f"/users/{user}/collection/folders")).get("folders", [])
    db.replace_folders(folders)

    known = {r[0] for r in db.conn.execute("SELECT instance_id FROM collection_items")}
    seen: set[int] = set()
    added = 0
    stopped_early = False
    async for item in client.paginate(
        f"/users/{user}/collection/folders/0/releases",
        "releases",
        params={"sort": "added", "sort_order": "desc"},
    ):
        if not full and item["instance_id"] in known:
            stopped_early = True
            break
        seen.add(item["instance_id"])
        if item["instance_id"] not in known:
            added += 1
        db.upsert_collection_item(item, names)

    removed = 0
    if full:
        stale = known - seen
        removed = len(stale)
        db.conn.executemany(
            "DELETE FROM collection_items WHERE instance_id=?", [(i,) for i in stale]
        )
        db.conn.executemany("DELETE FROM item_fields WHERE instance_id=?", [(i,) for i in stale])

    # The wantlist has no reliable "added" ordering on the API, so always sync it fully.
    wants_seen: set[int] = set()
    async for want in client.paginate(f"/users/{user}/wants", "wants"):
        wants_seen.add(want["id"])
        db.upsert_want(want)
    known_wants = {r[0] for r in db.conn.execute("SELECT release_id FROM wantlist")}
    db.conn.executemany(
        "DELETE FROM wantlist WHERE release_id=?", [(i,) for i in known_wants - wants_seen]
    )
    pruned = db.prune_orphan_releases()

    total_remote = next((f.get("count") for f in folders if f["id"] == 0), None)
    total_cached = db.conn.execute("SELECT COUNT(*) FROM collection_items").fetchone()[0]
    db.set_meta("username", user)
    db.set_meta("collection_synced_at", started)
    if full:
        db.set_meta("full_synced_at", started)
    db.conn.commit()

    result: dict[str, Any] = {
        "mode": "full" if full else "incremental",
        "username": user,
        "added": added,
        "removed": removed,
        "collection_items_cached": total_cached,
        "collection_items_on_discogs": total_remote,
        "wantlist_items_cached": len(wants_seen),
        "orphan_releases_pruned": pruned,
        "releases_missing_details": missing_details_count(db),
        "synced_at": started,
    }
    if not full and total_remote is not None and total_remote != total_cached:
        result["warning"] = (
            f"Cache has {total_cached} items but Discogs reports {total_remote}. Items were likely "
            "removed or moved on the website; run sync_collection(full=true) to reconcile."
        )
    if not full and not stopped_early and known:
        result["note"] = "No previously-seen item was reached; everything was re-read."
    return result


def missing_details_count(db: CacheDB) -> int:
    return db.conn.execute("SELECT COUNT(*) FROM releases WHERE details_fetched_at IS NULL").fetchone()[0]


async def fetch_release_details(
    client: DiscogsClient,
    db: CacheDB,
    *,
    max_releases: int | None = None,
    reserve: int = 10,
    progress: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Fetch /releases/{id} for cached releases lacking tracklists. Resumable: work is
    tracked per release via details_fetched_at, so a restart picks up where it stopped.

    `reserve` leaves that many requests of the rate-limit window free for interactive tools.
    """
    progress = progress if progress is not None else {}
    ids = [
        r[0]
        for r in db.conn.execute(
            "SELECT id FROM releases WHERE details_fetched_at IS NULL ORDER BY id"
        )
    ]
    if max_releases is not None:
        ids = ids[:max_releases]
    progress.update({"total": len(ids), "done": 0, "errors": 0, "state": "running", "started_at": now_iso()})
    for rid in ids:
        limiter = client.limiter
        if limiter.remaining is not None and limiter.remaining <= reserve:
            # Let the moving window refill; the next response refreshes `remaining`.
            per_request = limiter.window / max(limiter.limit, 1)
            await asyncio.sleep((reserve - limiter.remaining + 1) * per_request)
        try:
            release = await client.get(f"/releases/{rid}")
            db.store_release_detail(release)
        except DiscogsError as exc:
            progress["errors"] += 1
            progress["last_error"] = f"{rid}: {exc}"
            if exc.status == 404:  # deleted/merged release: mark so we don't retry forever
                db.conn.execute("UPDATE releases SET details_fetched_at=? WHERE id=?", ("missing", rid))
                db.conn.commit()
            elif exc.status in (401, 429):
                progress["state"] = "stopped"
                break
        progress["done"] += 1
    else:
        progress["state"] = "finished"
    progress["finished_at"] = now_iso()
    progress["remaining_in_cache"] = missing_details_count(db)
    return progress


def _counter(rows: list[Any], top: int) -> list[dict[str, Any]]:
    return [{"name": k, "count": v} for k, v in Counter(rows).most_common(top)]


def collection_summary(db: CacheDB, top: int = 15) -> dict[str, Any]:
    rows = db.conn.execute(
        """SELECT r.id, r.master_id, r.artist, r.title, r.year, r.labels, r.format, r.genres,
                  r.styles, r.country
           FROM collection_items ci JOIN releases r ON r.id = ci.release_id"""
    ).fetchall()
    genres, styles, labels, decades, formats, countries, artists = ([] for _ in range(7))
    for r in rows:
        genres += json.loads(r["genres"] or "[]")
        styles += json.loads(r["styles"] or "[]")
        labels += set(json.loads(r["labels"] or "[]"))
        if r["year"]:
            decades.append(f"{r['year'] // 10 * 10}s")
        formats.append(r["format"] or "Unknown")
        if r["country"]:
            countries.append(r["country"])
        artists.append(r["artist"])
    dupes = db.conn.execute(
        """SELECT r.master_id, MIN(r.artist) AS artist, MIN(r.title) AS title,
                  COUNT(*) AS copies, GROUP_CONCAT(DISTINCT r.id) AS release_ids
           FROM collection_items ci JOIN releases r ON r.id = ci.release_id
           WHERE r.master_id IS NOT NULL
           GROUP BY r.master_id HAVING COUNT(*) > 1 ORDER BY copies DESC, artist"""
    ).fetchall()
    with_details = db.conn.execute(
        "SELECT COUNT(*) FROM releases WHERE details_fetched_at IS NOT NULL"
    ).fetchone()[0]
    return {
        "username": db.get_meta("username"),
        "last_sync": db.get_meta("collection_synced_at"),
        "last_full_sync": db.get_meta("full_synced_at"),
        "total_items": len(rows),
        "unique_releases": len({r["id"] for r in rows}),
        "wantlist_items": db.conn.execute("SELECT COUNT(*) FROM wantlist").fetchone()[0],
        "by_genre": _counter(genres, top),
        "by_style": _counter(styles, top),
        "by_label": _counter(labels, top),
        "by_artist": _counter(artists, top),
        "by_decade": sorted(_counter(decades, 20), key=lambda d: d["name"]),
        "by_format": _counter(formats, top),
        "by_country": _counter(countries, top)
        or "Country needs release details: run sync_release_details.",
        "duplicates": {
            "count": len(dupes),
            "items": [dict(d) for d in dupes[:50]],
        },
        "releases_with_details": with_details,
    }


EXPORT_SQL = """
SELECT ci.instance_id, ci.release_id, r.master_id, r.artist, r.title, r.year, r.labels, r.catnos,
       r.format, r.formats, r.genres, r.styles, r.country, f.name AS folder, ci.rating,
       ci.date_added, ci.fields
FROM collection_items ci
JOIN releases r ON r.id = ci.release_id
LEFT JOIN folders f ON f.id = ci.folder_id
ORDER BY r.artist, r.year, r.title
"""
JSON_COLUMNS = ("labels", "catnos", "genres", "styles", "fields")


def export_collection(db: CacheDB, fmt: str, path: Path) -> dict[str, Any]:
    path = path.expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = [dict(r) for r in db.conn.execute(EXPORT_SQL)]
    if fmt == "json":
        for r in rows:
            for col in JSON_COLUMNS:
                r[col] = json.loads(r[col] or "null")
        path.write_text(json.dumps(rows, indent=2, ensure_ascii=False))
    else:
        for r in rows:
            for col in ("labels", "catnos", "genres", "styles"):
                r[col] = "; ".join(json.loads(r[col] or "[]"))
        with path.open("w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=list(rows[0].keys()) if rows else ["instance_id"])
            writer.writeheader()
            writer.writerows(rows)
    return {"path": str(path), "format": fmt, "rows": len(rows)}
