"""Local-library tools (scan_local_library, match_local_to_discogs, export_local_matches,
find_gaps), registered only when DISCOGS_ENABLE_LOCAL=True."""

from __future__ import annotations

import pytest

from discogs_mcp.client import DiscogsClient
from discogs_mcp.local import scan as scanner
from discogs_mcp.local.scan import ensure_schema
from discogs_mcp.server import build_server
from discogs_mcp.state import App

from . import payloads as p
from .conftest import call_ok, make_settings

pytestmark = pytest.mark.respx(base_url="https://api.discogs.com")


@pytest.fixture
async def local_app(tmp_path):
    settings = make_settings(tmp_path, DISCOGS_ENABLE_LOCAL=True)
    a = App(settings, client=DiscogsClient("test-token-xyz", backoff_base=0))
    yield a
    await a.aclose()


@pytest.fixture
async def local_built(local_app):
    mcp, app2 = build_server(app=local_app)
    return mcp, app2


async def test_local_tools_are_registered(local_built):
    mcp, _app = local_built
    from mcp.client import Client

    async with Client(mcp) as c:
        names = {t.name for t in (await c.list_tools()).tools}
    for name in ("scan_local_library", "match_local_to_discogs", "export_local_matches", "find_gaps"):
        assert name in names


async def test_scan_local_library_tool(local_built, tmp_path, monkeypatch):
    mcp, _app = local_built
    root = tmp_path / "music"
    f = root / "Aphex Twin" / "SAW" / "01.mp3"
    f.parent.mkdir(parents=True)
    f.write_bytes(b"\x00")
    monkeypatch.setattr(scanner, "read_file", lambda path: scanner.extract({"artist": "Aphex Twin"}))

    out = await call_ok(mcp, "scan_local_library", {"path": str(root)})
    assert out["files_scanned"] == 1


async def test_match_local_to_discogs_cache_only(local_built, tmp_path, monkeypatch):
    mcp, app = local_built
    app.cache.upsert_release_basic(p.basic_information(release_id=1001))
    app.cache.conn.execute(
        "INSERT INTO collection_items(instance_id, release_id, folder_id, rating, date_added, fields) "
        "VALUES(1, 1001, 1, 0, '2024-01-01', '{}')"
    )
    app.cache.conn.commit()
    root = tmp_path / "music"
    f = root / "01.mp3"
    f.parent.mkdir(parents=True)
    f.write_bytes(b"\x00")
    monkeypatch.setattr(
        scanner, "read_file", lambda path: scanner.extract({"artist": "Aphex Twin", "album": "Selected Ambient Works 85-92"})
    )
    await call_ok(mcp, "scan_local_library", {"path": str(root)})

    out = await call_ok(mcp, "match_local_to_discogs", {"strategy": "cache_only"})
    assert out["albums"] == 1
    assert out["by_method"]["cache"] == 1


async def test_export_local_matches_tool(local_built, tmp_path):
    mcp, app = local_built
    ensure_schema(app.cache)
    app.cache.conn.execute(
        """INSERT INTO local_matches VALUES('k','music','Artist','Album',2000,1,1001,5001,0.95,'cache','[]','now')"""
    )
    app.cache.conn.commit()
    target = tmp_path / "out.csv"
    out = await call_ok(mcp, "export_local_matches", {"path": str(target)})
    assert out["rows"] == 1
    assert target.exists()


async def test_find_gaps_tool(local_built, tmp_path):
    mcp, app = local_built
    app.cache.upsert_release_basic(p.basic_information(release_id=1001))
    app.cache.conn.execute(
        "INSERT INTO collection_items(instance_id, release_id, folder_id, rating, date_added, fields) "
        "VALUES(1, 1001, 1, 0, '2024-01-01', '{}')"
    )
    app.cache.conn.commit()
    out = await call_ok(mcp, "find_gaps", {})
    assert out["physical_format"] == "Vinyl"
    assert out["vinyl_without_digital"]["count"] == 1
