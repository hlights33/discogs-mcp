"""SQLite cache schema and write helpers."""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .. import models

SCHEMA_VERSION = 1

SCHEMA = """
CREATE TABLE IF NOT EXISTS releases (
    id INTEGER PRIMARY KEY,
    master_id INTEGER,
    artist TEXT,
    title TEXT,
    year INTEGER,
    labels TEXT,            -- JSON array of label names
    catnos TEXT,            -- JSON array of catalog numbers
    format TEXT,            -- primary format name, e.g. Vinyl, CD
    formats TEXT,           -- human summary, e.g. "2×Vinyl (LP, Album)"
    genres TEXT,            -- JSON array
    styles TEXT,            -- JSON array
    country TEXT,           -- filled by sync_release_details
    details_fetched_at TEXT -- NULL until full detail (tracklist) has been fetched
);
CREATE INDEX IF NOT EXISTS idx_releases_master ON releases(master_id);

CREATE TABLE IF NOT EXISTS release_labels (
    release_id INTEGER NOT NULL,
    label_id INTEGER,
    name TEXT,
    catno TEXT
);
CREATE INDEX IF NOT EXISTS idx_release_labels_release ON release_labels(release_id);
CREATE INDEX IF NOT EXISTS idx_release_labels_name ON release_labels(name);

CREATE TABLE IF NOT EXISTS folders (
    id INTEGER PRIMARY KEY,
    name TEXT,
    count INTEGER
);

CREATE TABLE IF NOT EXISTS collection_items (
    instance_id INTEGER PRIMARY KEY,
    release_id INTEGER NOT NULL,
    folder_id INTEGER,
    rating INTEGER,
    date_added TEXT,
    fields TEXT             -- JSON object of custom field name -> value
);
CREATE INDEX IF NOT EXISTS idx_items_release ON collection_items(release_id);

CREATE TABLE IF NOT EXISTS item_fields (
    instance_id INTEGER NOT NULL,
    field_id INTEGER NOT NULL,
    name TEXT,
    value TEXT,
    PRIMARY KEY (instance_id, field_id)
);

CREATE TABLE IF NOT EXISTS wantlist (
    release_id INTEGER PRIMARY KEY,
    rating INTEGER,
    notes TEXT,
    date_added TEXT
);

CREATE TABLE IF NOT EXISTS tracks (
    release_id INTEGER NOT NULL,
    position TEXT,
    title TEXT,
    artists TEXT,
    duration TEXT,
    seq INTEGER
);
CREATE INDEX IF NOT EXISTS idx_tracks_release ON tracks(release_id);

CREATE TABLE IF NOT EXISTS sync_meta (
    key TEXT PRIMARY KEY,
    value TEXT
);
"""

# Views make the common joins easy for ad-hoc SQL.
VIEWS = """
CREATE VIEW IF NOT EXISTS collection AS
SELECT ci.instance_id, ci.folder_id, f.name AS folder, ci.rating, ci.date_added, ci.fields,
       r.id AS release_id, r.master_id, r.artist, r.title, r.year, r.labels, r.catnos,
       r.format, r.formats, r.genres, r.styles, r.country
FROM collection_items ci
JOIN releases r ON r.id = ci.release_id
LEFT JOIN folders f ON f.id = ci.folder_id;

CREATE VIEW IF NOT EXISTS wants AS
SELECT w.release_id, w.rating, w.notes, w.date_added,
       r.master_id, r.artist, r.title, r.year, r.labels, r.catnos, r.format, r.formats,
       r.genres, r.styles, r.country
FROM wantlist w
JOIN releases r ON r.id = w.release_id;
"""


def now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


class CacheDB:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.migrate()

    def close(self) -> None:
        self.conn.close()

    def migrate(self) -> None:
        version = self.conn.execute("PRAGMA user_version").fetchone()[0]
        if version < SCHEMA_VERSION:
            self.conn.executescript(SCHEMA + VIEWS)
            self.conn.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
            self.conn.commit()

    # -- meta -------------------------------------------------------------
    def get_meta(self, key: str) -> str | None:
        row = self.conn.execute("SELECT value FROM sync_meta WHERE key=?", (key,)).fetchone()
        return row[0] if row else None

    def set_meta(self, key: str, value: Any) -> None:
        self.conn.execute(
            "INSERT INTO sync_meta(key, value) VALUES(?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, str(value)),
        )

    # -- upserts ----------------------------------------------------------
    def upsert_release_basic(self, info: dict[str, Any]) -> None:
        """Store a release from a collection/wantlist `basic_information` block."""
        rid = info["id"]
        labels = info.get("labels") or []
        formats = info.get("formats") or []
        self.conn.execute(
            """INSERT INTO releases(id, master_id, artist, title, year, labels, catnos, format,
                                    formats, genres, styles)
               VALUES(?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(id) DO UPDATE SET
                 master_id=excluded.master_id, artist=excluded.artist, title=excluded.title,
                 year=excluded.year, labels=excluded.labels, catnos=excluded.catnos,
                 format=excluded.format, formats=excluded.formats, genres=excluded.genres,
                 styles=excluded.styles""",
            (
                rid,
                info.get("master_id") or None,
                models.artist_credit(info.get("artists")),
                info.get("title"),
                info.get("year") or None,
                json.dumps([models.clean_name(lb.get("name")) for lb in labels]),
                json.dumps([lb.get("catno") for lb in labels if lb.get("catno")]),
                formats[0].get("name") if formats else None,
                models.format_summary(formats),
                json.dumps(info.get("genres") or []),
                json.dumps(info.get("styles") or []),
            ),
        )
        self.conn.execute("DELETE FROM release_labels WHERE release_id=?", (rid,))
        self.conn.executemany(
            "INSERT INTO release_labels(release_id, label_id, name, catno) VALUES(?,?,?,?)",
            [(rid, lb.get("id"), models.clean_name(lb.get("name")), lb.get("catno")) for lb in labels],
        )

    def store_release_detail(self, release: dict[str, Any]) -> None:
        """Store fields only available from GET /releases/{id}: country and tracklist."""
        rid = release["id"]
        self.conn.execute(
            "UPDATE releases SET country=?, details_fetched_at=? WHERE id=?",
            (release.get("country"), now_iso(), rid),
        )
        self.conn.execute("DELETE FROM tracks WHERE release_id=?", (rid,))
        tracks = []
        for t in release.get("tracklist") or []:
            if t.get("sub_tracks"):
                tracks.extend(t["sub_tracks"])  # index tracks: keep the parts
            elif t.get("type_", "track") == "track":
                tracks.append(t)  # skip headings
        rows = [
            (
                rid,
                t.get("position"),
                t.get("title"),
                models.artist_credit(t.get("artists")) or None,
                t.get("duration") or None,
                seq,
            )
            for seq, t in enumerate(tracks, 1)
        ]
        self.conn.executemany(
            "INSERT INTO tracks(release_id, position, title, artists, duration, seq) VALUES(?,?,?,?,?,?)",
            rows,
        )
        self.conn.commit()

    def upsert_collection_item(self, item: dict[str, Any], field_names: dict[int, str]) -> None:
        self.upsert_release_basic(item["basic_information"])
        notes = item.get("notes") or []
        fields = {field_names.get(n["field_id"], str(n["field_id"])): n.get("value") for n in notes}
        self.conn.execute(
            """INSERT INTO collection_items(instance_id, release_id, folder_id, rating, date_added, fields)
               VALUES(?,?,?,?,?,?)
               ON CONFLICT(instance_id) DO UPDATE SET
                 release_id=excluded.release_id, folder_id=excluded.folder_id,
                 rating=excluded.rating, date_added=excluded.date_added, fields=excluded.fields""",
            (
                item["instance_id"],
                item["id"],
                item.get("folder_id"),
                item.get("rating") or 0,
                item.get("date_added"),
                json.dumps(fields),
            ),
        )
        self.conn.execute("DELETE FROM item_fields WHERE instance_id=?", (item["instance_id"],))
        self.conn.executemany(
            "INSERT INTO item_fields(instance_id, field_id, name, value) VALUES(?,?,?,?)",
            [
                (item["instance_id"], n["field_id"], field_names.get(n["field_id"]), n.get("value"))
                for n in notes
            ],
        )

    def upsert_want(self, want: dict[str, Any]) -> None:
        self.upsert_release_basic(want["basic_information"])
        self.conn.execute(
            """INSERT INTO wantlist(release_id, rating, notes, date_added) VALUES(?,?,?,?)
               ON CONFLICT(release_id) DO UPDATE SET rating=excluded.rating,
                 notes=excluded.notes, date_added=excluded.date_added""",
            (want["id"], want.get("rating") or 0, want.get("notes") or None, want.get("date_added")),
        )

    def replace_folders(self, folders: list[dict[str, Any]]) -> None:
        self.conn.execute("DELETE FROM folders")
        self.conn.executemany(
            "INSERT INTO folders(id, name, count) VALUES(?,?,?)",
            [(f["id"], f.get("name"), f.get("count")) for f in folders],
        )

    # -- small edits mirrored from write tools ------------------------------
    def delete_instance(self, instance_id: int) -> None:
        self.conn.execute("DELETE FROM collection_items WHERE instance_id=?", (instance_id,))
        self.conn.execute("DELETE FROM item_fields WHERE instance_id=?", (instance_id,))
        self.conn.commit()

    def update_instance(self, instance_id: int, **values: Any) -> None:
        cols = {k: v for k, v in values.items() if k in {"folder_id", "rating"} and v is not None}
        if cols:
            assignments = ", ".join(f"{k}=?" for k in cols)
            self.conn.execute(
                f"UPDATE collection_items SET {assignments} WHERE instance_id=?",
                (*cols.values(), instance_id),
            )
            self.conn.commit()

    def delete_want(self, release_id: int) -> None:
        self.conn.execute("DELETE FROM wantlist WHERE release_id=?", (release_id,))
        self.conn.commit()

    def prune_orphan_releases(self) -> int:
        """Drop releases no longer referenced by the collection or wantlist."""
        cur = self.conn.execute(
            """DELETE FROM releases WHERE id NOT IN (SELECT release_id FROM collection_items)
               AND id NOT IN (SELECT release_id FROM wantlist)"""
        )
        self.conn.execute("DELETE FROM release_labels WHERE release_id NOT IN (SELECT id FROM releases)")
        self.conn.execute("DELETE FROM tracks WHERE release_id NOT IN (SELECT id FROM releases)")
        return cur.rowcount
