"""Read tags from local audio files into the cache (requires the `local` extra)."""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

from ..cache.db import CacheDB, now_iso

AUDIO_EXTENSIONS = {".flac", ".mp3", ".m4a", ".mp4", ".aac", ".alac", ".ogg", ".oga", ".opus",
                    ".aif", ".aiff", ".wav", ".wv", ".ape", ".dsf", ".wma"}

LOCAL_SCHEMA = """
CREATE TABLE IF NOT EXISTS local_files (
    path TEXT PRIMARY KEY,
    folder TEXT,
    artist TEXT,
    albumartist TEXT,
    album TEXT,
    title TEXT,
    year INTEGER,
    label TEXT,
    catno TEXT,
    barcode TEXT,
    discogs_release_id INTEGER,
    ext TEXT,
    mtime REAL,
    scanned_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_local_files_folder ON local_files(folder);

CREATE TABLE IF NOT EXISTS local_matches (
    album_key TEXT PRIMARY KEY,   -- folder|albumartist|album
    folder TEXT,
    artist TEXT,
    album TEXT,
    year INTEGER,
    track_count INTEGER,
    release_id INTEGER,
    master_id INTEGER,
    confidence REAL,
    method TEXT,                  -- tag | cache | search | none
    candidates TEXT,              -- JSON list of top candidates
    matched_at TEXT
);
"""

# Our field -> tag keys (lowercased) seen across ID3 TXXX, Vorbis comments, MP4 freeform, APE.
ALIASES: dict[str, tuple[str, ...]] = {
    "artist": ("artist", "tpe1", "©art"),
    "albumartist": ("albumartist", "album artist", "album_artist", "tpe2", "aart"),
    "album": ("album", "talb", "©alb"),
    "title": ("title", "tit2", "©nam"),
    "date": ("date", "year", "originaldate", "tdrc", "tyer", "©day"),
    "label": ("label", "organization", "publisher", "tpub", "recordlabel"),
    "catno": ("catalognumber", "catalog #", "catalog", "catno", "labelno", "catalog number"),
    "barcode": ("barcode", "upc", "ean"),
    "discogs_release_id": ("discogs_release_id", "discogs release id", "discogs_releaseid",
                            "discogs-release-id"),
}
_MP4_FREEFORM = re.compile(r"^----:[^:]+:(.+)$")
_YEAR = re.compile(r"(\d{4})")


def _text(value: Any) -> str | None:
    if isinstance(value, list | tuple):
        value = value[0] if value else None
    if value is None:
        return None
    if hasattr(value, "text"):  # ID3 frame
        return _text(value.text)
    if isinstance(value, bytes):  # MP4 freeform atom
        return value.decode("utf-8", "replace")
    return str(value).strip() or None


def normalize_tags(tags: Any) -> dict[str, str]:
    """Flatten any mutagen tag container into {lowercase key: first string value}."""
    flat: dict[str, str] = {}
    if tags is None:
        return flat
    for key in list(tags.keys()):
        name = str(key)
        if name.upper().startswith("TXXX:"):
            name = name[5:]
        elif m := _MP4_FREEFORM.match(name):
            name = m.group(1)
        value = _text(tags[key])
        if value:
            flat.setdefault(name.lower(), value)
    return flat


def extract(flat: dict[str, str]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for field, keys in ALIASES.items():
        out[field] = next((flat[k] for k in keys if k in flat), None)
    year = _YEAR.search(out.pop("date") or "")
    out["year"] = int(year.group(1)) if year else None
    rid = re.search(r"\d+", out["discogs_release_id"] or "")
    out["discogs_release_id"] = int(rid.group()) if rid else None
    return out


def read_file(path: Path) -> dict[str, Any] | None:
    import mutagen

    try:
        audio = mutagen.File(path)
    except Exception:
        return None
    if audio is None:
        return None
    return extract(normalize_tags(audio.tags))


def ensure_schema(db: CacheDB) -> None:
    db.conn.executescript(LOCAL_SCHEMA)


def scan(db: CacheDB, root: Path, *, rescan: bool = False) -> dict[str, Any]:
    root = root.expanduser().resolve()
    if not root.is_dir():
        raise FileNotFoundError(f"{root} is not a directory")
    ensure_schema(db)
    prefix = str(root) + os.sep
    known = {
        r[0]: r[1]
        for r in db.conn.execute(
            "SELECT path, mtime FROM local_files WHERE substr(path, 1, ?) = ?", (len(prefix), prefix)
        )
    }
    seen: set[str] = set()
    scanned = skipped = unreadable = 0
    for dirpath, _dirs, files in os.walk(root):
        for name in files:
            p = Path(dirpath, name)
            if p.suffix.lower() not in AUDIO_EXTENSIONS or name.startswith("._"):
                continue
            key = str(p)
            seen.add(key)
            mtime = p.stat().st_mtime
            if not rescan and known.get(key) == mtime:
                skipped += 1
                continue
            tags = read_file(p)
            if tags is None:
                unreadable += 1
                continue
            db.conn.execute(
                """INSERT OR REPLACE INTO local_files(path, folder, artist, albumartist, album, title,
                       year, label, catno, barcode, discogs_release_id, ext, mtime, scanned_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (key, dirpath, tags["artist"], tags["albumartist"], tags["album"], tags["title"],
                 tags["year"], tags["label"], tags["catno"], tags["barcode"],
                 tags["discogs_release_id"], p.suffix.lower().lstrip("."), mtime, now_iso()),
            )
            scanned += 1
    gone = set(known) - seen
    db.conn.executemany("DELETE FROM local_files WHERE path=?", [(g,) for g in gone])
    db.conn.commit()
    albums = db.conn.execute(
        "SELECT COUNT(DISTINCT folder || '|' || COALESCE(album, '')) FROM local_files "
        "WHERE substr(path, 1, ?) = ?",
        (len(prefix), prefix),
    ).fetchone()[0]
    return {
        "root": str(root),
        "files_scanned": scanned,
        "files_unchanged": skipped,
        "files_unreadable": unreadable,
        "files_removed": len(gone),
        "albums": albums,
    }
