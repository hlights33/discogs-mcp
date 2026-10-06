"""The sync_collection / sync_release_details / query_collection / collection_summary /
export_collection tools, exercised through the in-memory MCP Client."""

from __future__ import annotations

import pytest

from . import payloads as p
from .conftest import call_err, call_ok

pytestmark = pytest.mark.respx(base_url="https://api.discogs.com")


def _folders_route(respx_mock, count=1):
    respx_mock.get("/users/tester/collection/folders").respond(
        json={"folders": [p.collection_folder(id=0, name="All", count=count), p.collection_folder(count=count)]}
    )


async def test_sync_collection_tool(built, respx_mock):
    mcp, _app = built
    _folders_route(respx_mock)
    respx_mock.get("/users/tester/collection/fields").respond(json={"fields": [p.collection_field()]})
    respx_mock.get("/users/tester/collection/folders/0/releases").respond(
        json=p.paged([p.collection_item(instance_id=1, release_id=1001)], "releases")
    )
    respx_mock.get("/users/tester/wants").respond(json=p.paged([], "wants"))

    out = await call_ok(mcp, "sync_collection", {})
    assert out["mode"] == "full"
    assert out["collection_items_cached"] == 1


async def test_sync_release_details_background_false_stores_tracks_and_country(built, respx_mock):
    mcp, app = built
    app.cache.upsert_release_basic(p.basic_information(release_id=1001))
    app.cache.conn.commit()
    respx_mock.get("/releases/1001").respond(json=p.release_detail(id=1001, country="UK"))

    out = await call_ok(mcp, "sync_release_details", {"background": False})
    assert out["state"] == "finished"
    row = app.cache.conn.execute("SELECT country FROM releases WHERE id=1001").fetchone()
    assert row[0] == "UK"
    tracks = app.cache.conn.execute("SELECT COUNT(*) FROM tracks WHERE release_id=1001").fetchone()[0]
    assert tracks == 2


async def test_sync_release_details_resumes_with_nothing_to_do(built, respx_mock):
    mcp, app = built
    app.cache.upsert_release_basic(p.basic_information(release_id=1001))
    app.cache.conn.commit()
    respx_mock.get("/releases/1001").respond(json=p.release_detail(id=1001, country="UK"))
    await call_ok(mcp, "sync_release_details", {"background": False})

    out = await call_ok(mcp, "sync_release_details", {"background": False})
    assert out["status"] == "nothing to do"
    assert out["releases_missing_details"] == 0


async def test_sync_release_details_404_marks_missing(built, respx_mock):
    mcp, app = built
    app.cache.upsert_release_basic(p.basic_information(release_id=9999))
    app.cache.conn.commit()
    respx_mock.get("/releases/9999").respond(404, json={"message": "Release not found"})

    out = await call_ok(mcp, "sync_release_details", {"background": False})
    assert out["errors"] == 1
    row = app.cache.conn.execute("SELECT details_fetched_at FROM releases WHERE id=9999").fetchone()
    assert row[0] == "missing"


async def test_sync_status_reports_counts(built, respx_mock):
    mcp, app = built
    app.cache.upsert_release_basic(p.basic_information(release_id=1001))
    app.cache.conn.execute(
        "INSERT INTO collection_items(instance_id, release_id, folder_id, rating, date_added, fields) "
        "VALUES(1, 1001, 1, 0, '2024-01-01', '{}')"
    )
    app.cache.conn.commit()
    out = await call_ok(mcp, "sync_status", {})
    assert out["collection_items"] == 1
    assert out["releases_missing_details"] == 1


async def test_query_collection_tool_runs_select(built, respx_mock):
    mcp, app = built
    app.cache.upsert_release_basic(p.basic_information(release_id=1001, title="Query Me"))
    app.cache.conn.execute(
        "INSERT INTO collection_items(instance_id, release_id, folder_id, rating, date_added, fields) "
        "VALUES(1, 1001, 1, 0, '2024-01-01', '{}')"
    )
    app.cache.conn.commit()
    out = await call_ok(mcp, "query_collection", {"sql": "SELECT title FROM collection"})
    assert out["rows"] == [["Query Me"]]


async def test_query_collection_tool_rejects_write(built, respx_mock):
    mcp, app = built
    app.cache.conn.commit()
    message = await call_err(mcp, "query_collection", {"sql": "DELETE FROM collection_items"})
    assert "only" in message.lower() or "select" in message.lower()


async def test_collection_summary_tool_requires_cache(built, respx_mock):
    mcp, _app = built
    message = await call_err(mcp, "collection_summary", {})
    assert "cache is empty" in message.lower()


async def test_collection_summary_tool(built, respx_mock):
    mcp, app = built
    app.cache.upsert_release_basic(p.basic_information(release_id=1001, master_id=5001))
    app.cache.upsert_release_basic(p.basic_information(release_id=1002, master_id=5001, title="Dup"))
    app.cache.conn.executemany(
        "INSERT INTO collection_items(instance_id, release_id, folder_id, rating, date_added, fields) "
        "VALUES(?,?,?,?,?,?)",
        [(1, 1001, 1, 0, "2024-01-01", "{}"), (2, 1002, 1, 0, "2024-01-02", "{}")],
    )
    app.cache.conn.commit()
    out = await call_ok(mcp, "collection_summary", {})
    assert out["total_items"] == 2
    assert out["duplicates"]["count"] == 1


async def test_export_collection_tool(built, respx_mock, tmp_path):
    mcp, app = built
    app.cache.upsert_release_basic(p.basic_information(release_id=1001, title="Export Me"))
    app.cache.conn.execute(
        "INSERT INTO collection_items(instance_id, release_id, folder_id, rating, date_added, fields) "
        "VALUES(1, 1001, 1, 0, '2024-01-01', '{}')"
    )
    app.cache.conn.commit()
    target = tmp_path / "out.csv"
    out = await call_ok(mcp, "export_collection", {"format": "csv", "path": str(target)})
    assert out["rows"] == 1
    assert target.exists()
