"""local.scan: tag normalization/extraction with real in-memory mutagen frames, and
scan() with read_file monkeypatched (no real audio files needed)."""

from __future__ import annotations

from pathlib import Path

import pytest
from mutagen.id3 import ID3, TALB, TDRC, TIT2, TPE1, TPE2, TXXX

from discogs_mcp.cache.db import CacheDB
from discogs_mcp.local import scan as scanner


def _id3(**txxx) -> ID3:
    id3 = ID3()
    id3.add(TPE1(encoding=3, text=["Aphex Twin"]))
    id3.add(TPE2(encoding=3, text=["Aphex Twin"]))
    id3.add(TALB(encoding=3, text=["Selected Ambient Works 85-92"]))
    id3.add(TIT2(encoding=3, text=["Xtal"]))
    id3.add(TDRC(encoding=3, text=["1992"]))
    for desc, value in txxx.items():
        id3.add(TXXX(encoding=3, desc=desc, text=[value]))
    return id3


def test_normalize_tags_flattens_id3_frames():
    flat = scanner.normalize_tags(_id3(CATALOGNUMBER="AMB 3922"))
    assert flat["tpe1"] == "Aphex Twin"
    assert flat["tpe2"] == "Aphex Twin"
    assert flat["talb"] == "Selected Ambient Works 85-92"
    assert flat["tit2"] == "Xtal"
    assert flat["tdrc"] == "1992"
    assert flat["catalognumber"] == "AMB 3922"


def test_normalize_tags_handles_none():
    assert scanner.normalize_tags(None) == {}


def test_normalize_tags_strips_txxx_prefix():
    id3 = ID3()
    id3.add(TXXX(encoding=3, desc="DISCOGS_RELEASE_ID", text=["1001"]))
    flat = scanner.normalize_tags(id3)
    assert flat["discogs_release_id"] == "1001"


def test_normalize_tags_handles_mp4_freeform_bytes():
    tags = {"----:com.apple.iTunes:LABEL": [b"Warp Records"]}
    flat = scanner.normalize_tags(tags)
    assert flat["label"] == "Warp Records"


def test_extract_maps_aliases_and_parses_year():
    flat = scanner.normalize_tags(_id3(CATALOGNUMBER="AMB 3922", BARCODE="5021603992123"))
    out = scanner.extract(flat)
    assert out["artist"] == "Aphex Twin"
    assert out["albumartist"] == "Aphex Twin"
    assert out["album"] == "Selected Ambient Works 85-92"
    assert out["title"] == "Xtal"
    assert out["year"] == 1992
    assert out["catno"] == "AMB 3922"
    assert out["barcode"] == "5021603992123"
    assert out["discogs_release_id"] is None


def test_extract_parses_discogs_release_id_digits_only():
    flat = {"discogs_release_id": "release/1234567"}
    out = scanner.extract({**scanner.normalize_tags(_id3()), **flat})
    assert out["discogs_release_id"] == 1234567


def test_extract_missing_fields_are_none():
    out = scanner.extract({})
    assert out == {
        "artist": None,
        "albumartist": None,
        "album": None,
        "title": None,
        "label": None,
        "catno": None,
        "barcode": None,
        "discogs_release_id": None,
        "year": None,
    }


# -- scan() --------------------------------------------------------------------


@pytest.fixture
def db(tmp_path):
    cache = CacheDB(tmp_path / "cache.db")
    yield cache
    cache.close()


def _touch(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\x00")


def test_scan_reads_files_via_read_file(db, tmp_path, monkeypatch):
    root = tmp_path / "music"
    f1 = root / "Aphex Twin" / "SAW 85-92" / "01 Xtal.mp3"
    f2 = root / "Aphex Twin" / "SAW 85-92" / "02 Tha.mp3"
    _touch(f1)
    _touch(f2)

    def fake_read_file(path: Path):
        return scanner.extract({"artist": "Aphex Twin", "album": "SAW 85-92", "title": path.stem})

    monkeypatch.setattr(scanner, "read_file", fake_read_file)
    result = scanner.scan(db, root)
    assert result["files_scanned"] == 2
    assert result["files_unchanged"] == 0
    assert result["albums"] == 1
    rows = db.conn.execute("SELECT artist, album, title FROM local_files ORDER BY title").fetchall()
    assert [dict(r) for r in rows] == [
        {"artist": "Aphex Twin", "album": "SAW 85-92", "title": "01 Xtal"},
        {"artist": "Aphex Twin", "album": "SAW 85-92", "title": "02 Tha"},
    ]


def test_scan_skips_unchanged_files_unless_rescan(db, tmp_path, monkeypatch):
    root = tmp_path / "music"
    f1 = root / "album" / "01.mp3"
    _touch(f1)
    monkeypatch.setattr(scanner, "read_file", lambda path: scanner.extract({"title": "one"}))

    first = scanner.scan(db, root)
    assert first["files_scanned"] == 1

    second = scanner.scan(db, root)
    assert second["files_scanned"] == 0
    assert second["files_unchanged"] == 1

    third = scanner.scan(db, root, rescan=True)
    assert third["files_scanned"] == 1


def test_scan_removes_deleted_files(db, tmp_path, monkeypatch):
    root = tmp_path / "music"
    f1 = root / "album" / "01.mp3"
    _touch(f1)
    monkeypatch.setattr(scanner, "read_file", lambda path: scanner.extract({"title": "one"}))
    scanner.scan(db, root)

    f1.unlink()
    result = scanner.scan(db, root)
    assert result["files_removed"] == 1
    count = db.conn.execute("SELECT COUNT(*) FROM local_files").fetchone()[0]
    assert count == 0


def test_scan_counts_unreadable_files(db, tmp_path, monkeypatch):
    root = tmp_path / "music"
    f1 = root / "album" / "01.mp3"
    _touch(f1)
    monkeypatch.setattr(scanner, "read_file", lambda path: None)
    result = scanner.scan(db, root)
    assert result["files_unreadable"] == 1
    assert result["files_scanned"] == 0


def test_scan_ignores_non_audio_and_dotunderscore_files(db, tmp_path, monkeypatch):
    root = tmp_path / "music"
    _touch(root / "cover.jpg")
    _touch(root / "._01.mp3")
    monkeypatch.setattr(scanner, "read_file", lambda path: scanner.extract({"title": "x"}))
    result = scanner.scan(db, root)
    assert result["files_scanned"] == 0


def test_scan_raises_for_missing_directory(db, tmp_path):
    with pytest.raises(FileNotFoundError):
        scanner.scan(db, tmp_path / "does-not-exist")
