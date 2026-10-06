"""Match local albums to Discogs releases. Never writes tags: it proposes a mapping."""

from __future__ import annotations

import csv
import json
import re
import unicodedata
from collections import Counter
from pathlib import Path
from typing import Any, Literal

from rapidfuzz import fuzz, process

from .. import models
from ..cache.db import CacheDB, now_iso
from ..client import DiscogsClient, DiscogsError
from .scan import ensure_schema

Strategy = Literal["cache_first", "cache_only", "search_only"]
CONFIDENT = 0.90
REVIEW = 0.75

_BRACKETS = re.compile(r"[\(\[][^\)\]]*(remaster|deluxe|edition|expanded|anniversary|bonus|mono|stereo"
                       r"|reissue|explicit|\d{2,3} ?bit|khz|vinyl|flac|web|cd)[^\)\]]*[\)\]]", re.IGNORECASE)
_PUNCT = re.compile(r"[^\w\s]")
_SPACES = re.compile(r"\s+")


def normalize(text: str | None) -> str:
    if not text:
        return ""
    text = models.clean_name(text)
    text = _BRACKETS.sub(" ", text)
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    text = text.lower().replace("&", " and ")
    text = _PUNCT.sub(" ", text)
    text = re.sub(r"^the ", "", _SPACES.sub(" ", text).strip())
    return text


def local_albums(db: CacheDB) -> list[dict[str, Any]]:
    """Group local files by folder + album tag into albums."""
    rows = db.conn.execute(
        """SELECT folder, album, artist, albumartist, year, label, catno, barcode, discogs_release_id
           FROM local_files ORDER BY folder"""
    ).fetchall()
    groups: dict[tuple[str, str], list[Any]] = {}
    for r in rows:
        groups.setdefault((r["folder"], r["album"] or ""), []).append(r)
    albums = []
    def common(files: list[Any], col: str) -> Any:
        values = [f[col] for f in files if f[col]]
        return Counter(values).most_common(1)[0][0] if values else None

    for (folder, album), files in groups.items():

        artist = common(files, "albumartist") or common(files, "artist")
        albums.append(
            {
                "album_key": f"{folder}|{artist or ''}|{album}",
                "folder": folder,
                "artist": artist,
                "album": album or Path(folder).name,
                "year": common(files, "year"),
                "label": common(files, "label"),
                "catno": common(files, "catno"),
                "discogs_release_id": common(files, "discogs_release_id"),
                "track_count": len(files),
            }
        )
    return albums


def score(album: dict[str, Any], cand_artist: str, cand_title: str, cand_year: Any = None,
          cand_catnos: list[str] | None = None) -> float:
    a = fuzz.token_sort_ratio(normalize(album["artist"]), normalize(cand_artist))
    t = fuzz.token_sort_ratio(normalize(album["album"]), normalize(cand_title))
    s = 0.45 * a + 0.55 * t
    if album.get("year") and cand_year and int(album["year"]) == int(cand_year):
        s += 3
    if album.get("catno") and cand_catnos:
        want = normalize(album["catno"]).replace(" ", "")
        if any(normalize(c).replace(" ", "") == want for c in cand_catnos if c):
            s += 10
    return round(min(s, 100.0) / 100, 3)


def cache_candidates(db: CacheDB) -> list[dict[str, Any]]:
    rows = db.conn.execute(
        """SELECT DISTINCT r.id, r.master_id, r.artist, r.title, r.year, r.catnos, r.format
           FROM collection_items ci JOIN releases r ON r.id = ci.release_id"""
    ).fetchall()
    return [
        {**dict(r), "catnos": json.loads(r["catnos"] or "[]"), "key": f"{normalize(r['artist'])} {normalize(r['title'])}"}
        for r in rows
    ]


def rank_cache(album: dict[str, Any], cands: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not cands:
        return []
    key = f"{normalize(album['artist'])} {normalize(album['album'])}"
    rough = process.extract(key, [c["key"] for c in cands], scorer=fuzz.token_set_ratio, limit=10)
    ranked = []
    for _, _, idx in rough:
        c = cands[idx]
        ranked.append(
            {
                "release_id": c["id"],
                "master_id": c["master_id"],
                "artist": c["artist"],
                "title": c["title"],
                "year": c["year"],
                "format": c["format"],
                "score": score(album, c["artist"], c["title"], c["year"], c["catnos"]),
                "source": "collection",
            }
        )
    return sorted(ranked, key=lambda c: c["score"], reverse=True)[:3]


async def rank_search(client: DiscogsClient, album: dict[str, Any]) -> list[dict[str, Any]]:
    params: dict[str, Any] = {"type": "release", "per_page": 10}
    if album.get("catno"):
        params["catno"] = album["catno"]
        params["artist"] = album["artist"]
    else:
        params["artist"] = album["artist"]
        params["release_title"] = album["album"]
    data = await client.get("/database/search", **params)
    results = data.get("results", [])
    if not results and album.get("catno"):  # catno may be wrong; retry on title
        data = await client.get(
            "/database/search", type="release", per_page=10,
            artist=album["artist"], release_title=album["album"],
        )
        results = data.get("results", [])
    ranked = []
    for r in results:
        artist, _, title = (r.get("title") or "").partition(" - ")
        ranked.append(
            {
                "release_id": r.get("id"),
                "master_id": r.get("master_id") or None,
                "artist": models.clean_name(artist),
                "title": title or artist,
                "year": r.get("year"),
                "format": ", ".join(r.get("format") or []),
                "score": score(album, artist, title or artist, r.get("year"), [r.get("catno")]),
                "source": "search",
            }
        )
    return sorted(ranked, key=lambda c: c["score"], reverse=True)[:3]


async def match(
    client: DiscogsClient | None,
    db: CacheDB,
    *,
    strategy: Strategy = "cache_first",
    max_api_lookups: int = 25,
    rematch: bool = False,
) -> dict[str, Any]:
    ensure_schema(db)
    albums = local_albums(db)
    if not albums:
        raise ValueError("No local files scanned yet. Run scan_local_library first.")
    done = {r[0] for r in db.conn.execute("SELECT album_key FROM local_matches WHERE method != 'none'")}
    cands = cache_candidates(db) if strategy != "search_only" else []
    lookups = 0
    counts: Counter[str] = Counter()
    for album in albums:
        if not rematch and album["album_key"] in done:
            counts["previously_matched"] += 1
            continue
        top: list[dict[str, Any]] = []
        method = "none"
        if album["discogs_release_id"]:
            top = [{"release_id": album["discogs_release_id"], "score": 1.0, "source": "tag"}]
            method = "tag"
        else:
            if strategy != "search_only":
                top = rank_cache(album, cands)
                method = "cache"
            best = top[0]["score"] if top else 0
            if (
                strategy != "cache_only"
                and best < CONFIDENT
                and client is not None
                and lookups < max_api_lookups
                and album["artist"]
            ):
                lookups += 1
                try:
                    found = await rank_search(client, album)
                except DiscogsError as exc:
                    if exc.status in (401, 429):
                        raise
                    found = []
                if found and found[0]["score"] > best:
                    top = sorted(top + found, key=lambda c: c["score"], reverse=True)[:3]
                    method = "search"
        best = top[0] if top else None
        confidence = best["score"] if best else 0.0
        if confidence < REVIEW and method != "tag":
            method = "none"
        counts[method] += 1
        db.conn.execute(
            """INSERT OR REPLACE INTO local_matches(album_key, folder, artist, album, year, track_count,
                   release_id, master_id, confidence, method, candidates, matched_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
            (album["album_key"], album["folder"], album["artist"], album["album"], album["year"],
             album["track_count"], best["release_id"] if best and method != "none" else None,
             best.get("master_id") if best and method != "none" else None,
             confidence, method, json.dumps(top), now_iso()),
        )
    db.conn.commit()
    review = db.conn.execute(
        """SELECT folder, artist, album, release_id, confidence, method, candidates FROM local_matches
           WHERE confidence < ? ORDER BY confidence DESC LIMIT 50""",
        (CONFIDENT,),
    ).fetchall()
    return {
        "albums": len(albums),
        "by_method": dict(counts),
        "api_lookups_used": lookups,
        "confident_threshold": CONFIDENT,
        "needs_review": [
            {**{k: r[k] for k in ("folder", "artist", "album", "release_id", "confidence", "method")},
             "candidates": json.loads(r["candidates"] or "[]")}
            for r in review
        ],
        "note": "Nothing was written to your files. Full mapping is in the local_matches table "
                "(query_collection) and can be exported with export_local_matches.",
    }


def export_matches(db: CacheDB, path: Path, min_confidence: float = 0.0) -> dict[str, Any]:
    ensure_schema(db)
    path = path.expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = db.conn.execute(
        """SELECT folder, artist, album, year, track_count, release_id, master_id, confidence, method,
                  candidates FROM local_matches WHERE confidence >= ? ORDER BY folder""",
        (min_confidence,),
    ).fetchall()
    fields = ["folder", "artist", "album", "year", "track_count", "discogs_release_id",
              "discogs_master_id", "confidence", "method", "discogs_url", "alt_release_ids"]
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        for r in rows:
            cands = json.loads(r["candidates"] or "[]")
            writer.writerow(
                {
                    "folder": r["folder"], "artist": r["artist"], "album": r["album"],
                    "year": r["year"], "track_count": r["track_count"],
                    "discogs_release_id": r["release_id"], "discogs_master_id": r["master_id"],
                    "confidence": r["confidence"], "method": r["method"],
                    "discogs_url": f"https://www.discogs.com/release/{r['release_id']}" if r["release_id"] else "",
                    "alt_release_ids": " ".join(str(c["release_id"]) for c in cands[1:]),
                }
            )
    return {"path": str(path), "rows": len(rows)}


def find_gaps(db: CacheDB, physical_format: str = "Vinyl", min_confidence: float = CONFIDENT,
              limit: int = 200) -> dict[str, Any]:
    ensure_schema(db)
    matched = db.conn.execute(
        """SELECT m.release_id, COALESCE(m.master_id, r.master_id) AS master_id
           FROM local_matches m LEFT JOIN releases r ON r.id = m.release_id
           WHERE m.release_id IS NOT NULL AND (m.confidence >= ? OR m.method = 'tag')""",
        (min_confidence,),
    ).fetchall()
    have_releases = {r[0] for r in matched}
    have_masters = {r[1] for r in matched if r[1]}
    physical = db.conn.execute(
        """SELECT DISTINCT r.id, r.master_id, r.artist, r.title, r.year, r.formats
           FROM collection_items ci JOIN releases r ON r.id = ci.release_id
           WHERE r.format = ? ORDER BY r.artist, r.year""",
        (physical_format,),
    ).fetchall()
    no_digital = [
        dict(r) for r in physical
        if r["id"] not in have_releases and (r["master_id"] is None or r["master_id"] not in have_masters)
    ]
    owned = db.conn.execute(
        """SELECT DISTINCT r.id, r.master_id FROM collection_items ci
           JOIN releases r ON r.id = ci.release_id WHERE r.format = ?""",
        (physical_format,),
    ).fetchall()
    owned_releases = {r[0] for r in owned}
    owned_masters = {r[1] for r in owned if r[1]}
    digital = db.conn.execute(
        """SELECT m.folder, m.artist, m.album, m.year, m.release_id,
                  COALESCE(m.master_id, r.master_id) AS master_id, m.confidence, m.method
           FROM local_matches m LEFT JOIN releases r ON r.id = m.release_id ORDER BY m.artist, m.album"""
    ).fetchall()
    no_physical = [
        dict(d) for d in digital
        if d["release_id"] not in owned_releases
        and (d["master_id"] is None or d["master_id"] not in owned_masters)
    ]
    return {
        "physical_format": physical_format,
        f"{physical_format.lower()}_without_digital": {"count": len(no_digital), "items": no_digital[:limit]},
        f"digital_without_{physical_format.lower()}": {
            "count": len(no_physical),
            "unmatched_count": sum(1 for d in no_physical if d["release_id"] is None),
            "items": no_physical[:limit],
        },
        "note": "Comparison is by master release when known, so a different pressing still counts. "
                "Unmatched local albums appear on the digital side; improve them with match_local_to_discogs.",
    }
