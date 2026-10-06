"""local.match: cache_only matching, search fallback, export, and gap analysis."""

from __future__ import annotations

import csv

import pytest

from discogs_mcp.cache.db import CacheDB
from discogs_mcp.client import DiscogsClient
from discogs_mcp.local import match as matcher
from discogs_mcp.local.scan import ensure_schema

from . import payloads as p

pytestmark = pytest.mark.respx(base_url="https://api.discogs.com")


@pytest.fixture
def db(tmp_path):
    cache = CacheDB(tmp_path / "cache.db")
    ensure_schema(cache)
    yield cache
    cache.close()


@pytest.fixture
async def client():
    c = DiscogsClient("t", backoff_base=0)
    yield c
    await c.aclose()


def _seed_collection(db, release_id=1001, title="Selected Ambient Works 85-92", artist="Aphex Twin",
                      master_id=5001):
    db.upsert_release_basic(
        p.basic_information(release_id=release_id, title=title, master_id=master_id,
                             artists=[{"name": artist}])
    )
    db.conn.execute(
        "INSERT INTO collection_items(instance_id, release_id, folder_id, rating, date_added, fields) "
        "VALUES(?,?,?,?,?,?)",
        (release_id, release_id, 1, 0, "2024-01-01", "{}"),
    )
    db.conn.commit()


def _seed_local_file(db, folder="music/aphex", artist="Aphex Twin", album="Selected Ambient Works 85-92",
                      title="Xtal", path=None):
    db.conn.execute(
        """INSERT INTO local_files(path, folder, artist, albumartist, album, title, year, label, catno,
               barcode, discogs_release_id, ext, mtime, scanned_at)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (path or f"{folder}/{title}.mp3", folder, artist, artist, album, title, 1992, None, None, None,
         None, "mp3", 1.0, "now"),
    )
    db.conn.commit()


async def test_match_cache_only_matches_confidently(client, db):
    _seed_collection(db)
    _seed_local_file(db)
    result = await matcher.match(None, db, strategy="cache_only")
    assert result["albums"] == 1
    assert result["by_method"]["cache"] == 1
    row = db.conn.execute("SELECT release_id, confidence, method FROM local_matches").fetchone()
    assert row["release_id"] == 1001
    assert row["confidence"] >= matcher.CONFIDENT
    assert row["method"] == "cache"


async def test_match_cache_only_raises_without_scanned_files(db):
    with pytest.raises(ValueError, match="scan_local_library"):
        await matcher.match(None, db, strategy="cache_only")


async def test_match_tag_based_release_id_skips_lookup(client, db):
    db.conn.execute(
        """INSERT INTO local_files(path, folder, artist, albumartist, album, title, year, label, catno,
               barcode, discogs_release_id, ext, mtime, scanned_at)
           VALUES('x.mp3','music','Artist','Artist','Album','T',2000,NULL,NULL,NULL,4242,'mp3',1.0,'now')"""
    )
    db.conn.commit()
    result = await matcher.match(None, db, strategy="cache_only")
    assert result["by_method"]["tag"] == 1
    row = db.conn.execute("SELECT release_id, confidence, method FROM local_matches").fetchone()
    assert row["release_id"] == 4242
    assert row["confidence"] == 1.0
    assert row["method"] == "tag"


async def test_match_falls_back_to_search_when_cache_weak(client, db, respx_mock):
    # Local album has no good cache candidate (collection has something unrelated).
    _seed_collection(db, release_id=9999, title="Totally Unrelated Noise", artist="Nobody")
    _seed_local_file(db, artist="Boards of Canada", album="Music Has the Right to Children", title="Roygbiv")

    respx_mock.get("/database/search").respond(
        json={
            "results": [
                p.search_result(
                    id=2002, title="Boards of Canada - Music Has the Right to Children", year="1998",
                    catno="WARPCD55",
                )
            ]
        }
    )
    result = await matcher.match(client, db, strategy="cache_first", max_api_lookups=5)
    assert result["api_lookups_used"] == 1
    row = db.conn.execute("SELECT release_id, method FROM local_matches").fetchone()
    assert row["method"] == "search"
    assert row["release_id"] == 2002


async def test_match_search_only_skips_cache_candidates(client, db, respx_mock):
    _seed_collection(db)  # would otherwise match confidently via cache
    _seed_local_file(db)
    respx_mock.get("/database/search").respond(
        json={"results": [p.search_result(id=1001, title="Aphex Twin - Selected Ambient Works 85-92")]}
    )
    result = await matcher.match(client, db, strategy="search_only", max_api_lookups=5)
    assert result["api_lookups_used"] == 1
    row = db.conn.execute("SELECT method FROM local_matches").fetchone()
    assert row["method"] == "search"


async def test_match_rematch_false_skips_previously_matched(client, db):
    _seed_collection(db)
    _seed_local_file(db)
    await matcher.match(None, db, strategy="cache_only")
    result = await matcher.match(None, db, strategy="cache_only")
    assert result["by_method"].get("previously_matched") == 1


async def test_match_rematch_true_reprocesses(client, db):
    _seed_collection(db)
    _seed_local_file(db)
    await matcher.match(None, db, strategy="cache_only")
    result = await matcher.match(None, db, strategy="cache_only", rematch=True)
    assert result["by_method"].get("cache") == 1


def test_export_matches_writes_csv(db, tmp_path):
    db.conn.execute(
        """INSERT INTO local_matches(album_key, folder, artist, album, year, track_count, release_id,
               master_id, confidence, method, candidates, matched_at)
           VALUES('k','music','Aphex Twin','SAW 85-92',1992,2,1001,5001,0.95,'cache','[]','now')"""
    )
    db.conn.commit()
    target = tmp_path / "matches.csv"
    result = matcher.export_matches(db, target, min_confidence=0.9)
    assert result["rows"] == 1
    with target.open() as fh:
        rows = list(csv.DictReader(fh))
    assert rows[0]["artist"] == "Aphex Twin"
    assert rows[0]["discogs_release_id"] == "1001"
    assert rows[0]["discogs_url"] == "https://www.discogs.com/release/1001"


def test_export_matches_filters_by_min_confidence(db, tmp_path):
    db.conn.execute(
        """INSERT INTO local_matches(album_key, folder, artist, album, year, track_count, release_id,
               master_id, confidence, method, candidates, matched_at)
           VALUES('k','music','Artist','Album',2000,1,NULL,NULL,0.2,'none','[]','now')"""
    )
    db.conn.commit()
    result = matcher.export_matches(db, tmp_path / "out.csv", min_confidence=0.5)
    assert result["rows"] == 0


# -- find_gaps ------------------------------------------------------------------


def test_find_gaps_identifies_vinyl_without_digital_and_digital_without_vinyl(db):
    # Owned on vinyl, matched digitally -> not a gap.
    db.upsert_release_basic(p.basic_information(release_id=1, master_id=100, title="Have Both"))
    # Owned on vinyl, no digital match -> vinyl-without-digital gap.
    db.upsert_release_basic(p.basic_information(release_id=2, master_id=200, title="Vinyl Only"))
    db.conn.executemany(
        "INSERT INTO collection_items(instance_id, release_id, folder_id, rating, date_added, fields) "
        "VALUES(?,?,?,?,?,?)",
        [(1, 1, 1, 0, "2024-01-01", "{}"), (2, 2, 1, 0, "2024-01-01", "{}")],
    )
    db.conn.execute(
        """INSERT INTO local_matches(album_key, folder, artist, album, year, track_count, release_id,
               master_id, confidence, method, candidates, matched_at)
           VALUES('k1','music','A','Have Both',2000,1,1,100,0.95,'cache','[]','now')"""
    )
    # A digital-only album with no physical copy -> digital-without-vinyl gap.
    db.conn.execute(
        """INSERT INTO local_matches(album_key, folder, artist, album, year, track_count, release_id,
               master_id, confidence, method, candidates, matched_at)
           VALUES('k2','music','B','Digital Only',2005,1,3,300,0.95,'cache','[]','now')"""
    )
    db.conn.commit()

    gaps = matcher.find_gaps(db, physical_format="Vinyl", min_confidence=0.9)
    vinyl_without_digital = gaps["vinyl_without_digital"]["items"]
    digital_without_vinyl = gaps["digital_without_vinyl"]["items"]
    assert {r["id"] for r in vinyl_without_digital} == {2}
    assert {r["release_id"] for r in digital_without_vinyl} == {3}
