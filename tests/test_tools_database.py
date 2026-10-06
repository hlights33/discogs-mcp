"""Every database-lookup tool exercised once via the in-memory MCP Client."""

from __future__ import annotations

import pytest

from . import payloads as p
from .conftest import call_ok

pytestmark = pytest.mark.respx(base_url="https://api.discogs.com")


async def test_search(built, respx_mock):
    mcp, _app = built
    respx_mock.get("/database/search").respond(json=p.search_response())
    out = await call_ok(mcp, "search", {"query": "Aphex Twin"})
    assert out["results"][0]["id"] == 1001
    assert out["results"][0]["title"].startswith("Aphex Twin")
    assert "resource_url" not in out["results"][0]


async def test_get_release(built, respx_mock):
    mcp, _app = built
    respx_mock.get("/releases/1001").respond(json=p.release_detail())
    out = await call_ok(mcp, "get_release", {"release_id": 1001})
    assert out["id"] == 1001
    assert out["artist"] == "Aphex Twin"
    assert out["format"] == "2×Vinyl (LP, Album)"
    assert len(out["tracklist"]) == 2
    assert "images" not in out


async def test_get_release_with_images(built, respx_mock):
    mcp, _app = built
    respx_mock.get("/releases/1001").respond(json=p.release_detail())
    out = await call_ok(mcp, "get_release", {"release_id": 1001, "include_images": True})
    assert out["images"] == ["https://example/img1.jpg"]


async def test_get_master(built, respx_mock):
    mcp, _app = built
    respx_mock.get("/masters/5001").respond(json=p.master_detail())
    out = await call_ok(mcp, "get_master", {"master_id": 5001})
    assert out["id"] == 5001
    assert out["main_release"] == 1001


async def test_get_master_versions(built, respx_mock):
    mcp, _app = built
    respx_mock.get("/masters/5001/versions").respond(json=p.paged([p.version()], "versions"))
    out = await call_ok(mcp, "get_master_versions", {"master_id": 5001})
    assert out["versions"][0]["id"] == 1001
    assert out["versions"][0]["have"] == 1000
    assert out["versions"][0]["want"] == 500


async def test_get_artist(built, respx_mock):
    mcp, _app = built
    respx_mock.get("/artists/45").respond(json=p.artist_detail())
    out = await call_ok(mcp, "get_artist", {"artist_id": 45})
    assert out["name"] == "Aphex Twin"
    assert out["real_name"] == "Richard D. James"


async def test_get_artist_releases(built, respx_mock):
    mcp, _app = built
    respx_mock.get("/artists/45/releases").respond(json=p.paged([p.discography_item()], "releases"))
    out = await call_ok(mcp, "get_artist_releases", {"artist_id": 45})
    assert out["releases"][0]["title"] == "Selected Ambient Works 85-92"


async def test_get_label(built, respx_mock):
    mcp, _app = built
    respx_mock.get("/labels/50").respond(json=p.label_detail())
    out = await call_ok(mcp, "get_label", {"label_id": 50})
    assert out["name"] == "Warp Records"


async def test_get_label_releases(built, respx_mock):
    mcp, _app = built
    respx_mock.get("/labels/50/releases").respond(json=p.paged([p.discography_item()], "releases"))
    out = await call_ok(mcp, "get_label_releases", {"label_id": 50})
    assert out["releases"][0]["catno"] == "AMB 3922"
