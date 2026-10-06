"""cache.sync: full/incremental sync_collection, sync_release_details, collection_summary."""

from __future__ import annotations

import pytest

from discogs_mcp.cache import sync
from discogs_mcp.cache.db import CacheDB
from discogs_mcp.client import DiscogsClient

from . import payloads as p

pytestmark = pytest.mark.respx(base_url="https://api.discogs.com")


@pytest.fixture
def db(tmp_path):
    cache = CacheDB(tmp_path / "cache.db")
    yield cache
    cache.close()


@pytest.fixture
async def client():
    c = DiscogsClient("t", backoff_base=0)
    yield c
    await c.aclose()


def _folders_route(respx_mock, count=2):
    respx_mock.get("/users/tester/collection/folders").respond(
        json={"folders": [p.collection_folder(id=0, name="All", count=count), p.collection_folder(count=count)]}
    )


def _fields_route(respx_mock):
    respx_mock.get("/users/tester/collection/fields").respond(json={"fields": [p.collection_field()]})


def _wants_route(respx_mock, items=None):
    respx_mock.get("/users/tester/wants").respond(json=p.paged(items or [], "wants"))


async def test_sync_collection_full(client, db, respx_mock):
    _folders_route(respx_mock, count=2)
    _fields_route(respx_mock)
    items = [
        p.collection_item(instance_id=1, release_id=101),
        p.collection_item(instance_id=2, release_id=102),
    ]
    respx_mock.get("/users/tester/collection/folders/0/releases").respond(json=p.paged(items, "releases"))
    _wants_route(respx_mock)

    result = await sync.sync_collection(client, db, "tester", full=True)
    assert result["mode"] == "full"
    assert result["added"] == 2
    assert result["removed"] == 0
    assert result["collection_items_cached"] == 2
    assert db.get_meta("username") == "tester"
    assert db.get_meta("full_synced_at") is not None


@pytest.mark.respx(base_url="https://api.discogs.com", assert_all_called=False)
async def test_sync_collection_incremental_stops_at_known_instance(client, db, respx_mock):
    # Seed the cache as if a prior full sync already happened.
    _folders_route(respx_mock, count=3)
    _fields_route(respx_mock)
    seeded = [
        p.collection_item(instance_id=1, release_id=101),
        p.collection_item(instance_id=2, release_id=102),
    ]
    respx_mock.get("/users/tester/collection/folders/0/releases").respond(json=p.paged(seeded, "releases"))
    _wants_route(respx_mock)
    await sync.sync_collection(client, db, "tester", full=True)
    respx_mock.clear()

    # Now simulate the API newest-first: one brand-new item (instance 3) followed by
    # the already-known instance 2. Incremental must stop at instance 2 without
    # reading further (it never requests page 2).
    _folders_route(respx_mock, count=4)
    _fields_route(respx_mock)
    page1 = {
        "releases": [
            p.collection_item(instance_id=3, release_id=103),
            p.collection_item(instance_id=2, release_id=102),
        ],
        "pagination": {"urls": {"next": "https://api.discogs.com/users/tester/collection/folders/0/releases?page=2"}},
    }
    page2_route = respx_mock.get(
        "/users/tester/collection/folders/0/releases", params={"page": "2"}
    ).respond(json=p.paged([p.collection_item(instance_id=1, release_id=101)], "releases"))
    respx_mock.get("/users/tester/collection/folders/0/releases").respond(json=page1)
    _wants_route(respx_mock)

    result = await sync.sync_collection(client, db, "tester", full=False)
    assert result["mode"] == "incremental"
    assert result["added"] == 1
    assert not page2_route.called
    ids = {r[0] for r in db.conn.execute("SELECT instance_id FROM collection_items")}
    assert ids == {1, 2, 3}


async def test_sync_collection_full_removes_stale_items(client, db, respx_mock):
    _folders_route(respx_mock, count=2)
    _fields_route(respx_mock)
    seeded = [
        p.collection_item(instance_id=1, release_id=101),
        p.collection_item(instance_id=2, release_id=102),
    ]
    respx_mock.get("/users/tester/collection/folders/0/releases").respond(json=p.paged(seeded, "releases"))
    _wants_route(respx_mock)
    await sync.sync_collection(client, db, "tester", full=True)
    respx_mock.clear()

    # Instance 2 was removed on Discogs; only instance 1 remains.
    _folders_route(respx_mock, count=1)
    _fields_route(respx_mock)
    respx_mock.get("/users/tester/collection/folders/0/releases").respond(
        json=p.paged([p.collection_item(instance_id=1, release_id=101)], "releases")
    )
    _wants_route(respx_mock)

    result = await sync.sync_collection(client, db, "tester", full=True)
    assert result["removed"] == 1
    ids = {r[0] for r in db.conn.execute("SELECT instance_id FROM collection_items")}
    assert ids == {1}
    # release 102 is now orphaned and pruned
    assert result["orphan_releases_pruned"] == 1
    remaining = {r[0] for r in db.conn.execute("SELECT id FROM releases")}
    assert 102 not in remaining


async def test_sync_collection_syncs_wantlist(client, db, respx_mock):
    _folders_route(respx_mock, count=0)
    _fields_route(respx_mock)
    respx_mock.get("/users/tester/collection/folders/0/releases").respond(json=p.paged([], "releases"))
    _wants_route(respx_mock, [p.want_item(release_id=2002)])

    result = await sync.sync_collection(client, db, "tester", full=True)
    assert result["wantlist_items_cached"] == 1
    row = db.conn.execute("SELECT release_id FROM wantlist").fetchone()
    assert row[0] == 2002


async def test_sync_collection_different_account_forces_full(client, db, respx_mock):
    db.set_meta("username", "someone_else")
    db.set_meta("collection_synced_at", "2024-01-01T00:00:00")
    db.conn.commit()
    _folders_route(respx_mock, count=0)
    _fields_route(respx_mock)
    respx_mock.get("/users/tester/collection/folders/0/releases").respond(json=p.paged([], "releases"))
    _wants_route(respx_mock)

    result = await sync.sync_collection(client, db, "tester", full=False)
    assert result["mode"] == "full"


# -- sync_release_details -----------------------------------------------------


async def test_fetch_release_details_resumable(client, db, respx_mock):
    db.upsert_release_basic(p.basic_information(release_id=1001))
    db.upsert_release_basic(p.basic_information(release_id=1002, title="Druqks"))
    db.conn.commit()

    respx_mock.get("/releases/1001").respond(json=p.release_detail(id=1001, country="UK"))
    respx_mock.get("/releases/1002").respond(json=p.release_detail(id=1002, country="US", title="Druqks"))

    progress = await sync.fetch_release_details(client, db)
    assert progress["state"] == "finished"
    assert progress["done"] == 2
    assert sync.missing_details_count(db) == 0
    row = db.conn.execute("SELECT country FROM releases WHERE id=1001").fetchone()
    assert row[0] == "UK"
    tracks = db.conn.execute("SELECT COUNT(*) FROM tracks WHERE release_id=1001").fetchone()[0]
    assert tracks == 2

    # Resuming with nothing left to do should be a no-op.
    progress2 = await sync.fetch_release_details(client, db)
    assert progress2["total"] == 0


async def test_fetch_release_details_marks_404_as_missing(client, db, respx_mock):
    db.upsert_release_basic(p.basic_information(release_id=9999))
    db.conn.commit()
    respx_mock.get("/releases/9999").respond(404, json={"message": "Release not found"})

    progress = await sync.fetch_release_details(client, db)
    assert progress["errors"] == 1
    row = db.conn.execute("SELECT details_fetched_at FROM releases WHERE id=9999").fetchone()
    assert row[0] == "missing"
    # missing releases don't count as "still needing details" forever-retried
    assert sync.missing_details_count(db) == 0


async def test_fetch_release_details_stops_on_401(client, db, respx_mock):
    db.upsert_release_basic(p.basic_information(release_id=1))
    db.upsert_release_basic(p.basic_information(release_id=2, title="Two"))
    db.conn.commit()
    respx_mock.get("/releases/1").respond(401, json={"message": "bad token"})

    progress = await sync.fetch_release_details(client, db)
    assert progress["state"] == "stopped"
    assert sync.missing_details_count(db) == 2  # neither release got details


# -- collection_summary --------------------------------------------------------


def test_collection_summary_counts_and_duplicates(db):
    # Two different releases sharing a master_id (duplicate copies of the same album).
    db.upsert_release_basic(
        p.basic_information(release_id=1, master_id=500, title="Same Album", year=1992,
                             genres=["Electronic"], styles=["Ambient"])
    )
    db.upsert_release_basic(
        p.basic_information(release_id=2, master_id=500, title="Same Album", year=1993,
                             genres=["Electronic"], styles=["IDM"])
    )
    db.upsert_release_basic(
        p.basic_information(release_id=3, master_id=None, title="Solo Release", year=2001,
                             genres=["Rock"], styles=["Indie"])
    )
    db.conn.executemany(
        "INSERT INTO collection_items(instance_id, release_id, folder_id, rating, date_added, fields) "
        "VALUES(?,?,?,?,?,?)",
        [(1, 1, 1, 5, "2024-01-01", "{}"), (2, 2, 1, 4, "2024-01-02", "{}"), (3, 3, 1, 0, "2024-01-03", "{}")],
    )
    db.conn.commit()

    summary = sync.collection_summary(db)
    assert summary["total_items"] == 3
    assert summary["unique_releases"] == 3
    assert summary["duplicates"]["count"] == 1
    assert summary["duplicates"]["items"][0]["copies"] == 2
    genre_names = {g["name"] for g in summary["by_genre"]}
    assert "Electronic" in genre_names and "Rock" in genre_names
    decade_names = {d["name"] for d in summary["by_decade"]}
    assert decade_names == {"1990s", "2000s"}


# -- export_collection ----------------------------------------------------------


def test_export_collection_csv_and_json(db, tmp_path):
    db.upsert_release_basic(p.basic_information(release_id=1, title="Export Me"))
    db.conn.execute(
        "INSERT INTO collection_items(instance_id, release_id, folder_id, rating, date_added, fields) "
        "VALUES(1, 1, 1, 5, '2024-01-01', '{}')"
    )
    db.replace_folders([p.collection_folder(id=1, name="Uncategorized")])
    db.conn.commit()

    csv_result = sync.export_collection(db, "csv", tmp_path / "out.csv")
    assert csv_result["rows"] == 1
    csv_text = (tmp_path / "out.csv").read_text()
    assert "Export Me" in csv_text
    assert "Uncategorized" in csv_text

    json_result = sync.export_collection(db, "json", tmp_path / "out.json")
    assert json_result["rows"] == 1
    import json as jsonlib

    data = jsonlib.loads((tmp_path / "out.json").read_text())
    assert data[0]["title"] == "Export Me"
    assert isinstance(data[0]["genres"], list)
