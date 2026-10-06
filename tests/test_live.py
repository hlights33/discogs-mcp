"""Live smoke tests against the real Discogs API. Skipped unless DISCOGS_TOKEN is set
in the environment and the `live` marker is explicitly selected (pytest.ini deselects
it by default: `addopts = -m 'not live'`).

Run with: uv run pytest -m live tests/test_live.py
"""

from __future__ import annotations

import os

import pytest

from discogs_mcp.config import Settings
from discogs_mcp.server import build_server

pytestmark = pytest.mark.live

LIVE_TOKEN = os.environ.get("DISCOGS_TOKEN")

requires_live_token = pytest.mark.skipif(
    not LIVE_TOKEN, reason="DISCOGS_TOKEN is not set in the environment"
)


@pytest.fixture
async def live_app(tmp_path):
    # Deliberately bypasses the _isolated_env autouse fixture's env stripping by
    # reading DISCOGS_TOKEN directly: a live test needs the real credential.
    settings = Settings(
        DISCOGS_TOKEN=LIVE_TOKEN,
        DISCOGS_CACHE_PATH=tmp_path / "live-cache.db",
        _env_file=None,
    )
    mcp, app = build_server(settings)
    yield mcp, app
    await app.aclose()


@requires_live_token
async def test_whoami_live(live_app):
    from mcp.client import Client

    mcp, _app = live_app
    async with Client(mcp) as c:
        result = await c.call_tool("whoami", {})
        assert not result.is_error, result.content[0].text
        assert "username" in result.content[0].text


@requires_live_token
async def test_search_live(live_app):
    from mcp.client import Client

    mcp, _app = live_app
    async with Client(mcp) as c:
        result = await c.call_tool("search", {"query": "Aphex Twin Selected Ambient Works", "type": "release"})
        assert not result.is_error, result.content[0].text
        assert '"results"' in result.content[0].text


@requires_live_token
async def test_get_release_for_first_search_result_live(live_app):
    import json

    from mcp.client import Client

    mcp, _app = live_app
    async with Client(mcp) as c:
        search_result = await c.call_tool(
            "search", {"query": "Aphex Twin Selected Ambient Works", "type": "release"}
        )
        assert not search_result.is_error
        results = json.loads(search_result.content[0].text)["results"]
        assert results, "expected at least one search result"
        release_id = results[0]["id"]

        release_result = await c.call_tool("get_release", {"release_id": release_id})
        assert not release_result.is_error, release_result.content[0].text
        release = json.loads(release_result.content[0].text)
        assert release["id"] == release_id
        assert "tracklist" in release


@requires_live_token
async def test_sync_collection_live_into_tmp_cache(live_app):
    from mcp.client import Client

    mcp, _app = live_app
    async with Client(mcp) as c:
        result = await c.call_tool("sync_collection", {})
        assert not result.is_error, result.content[0].text
        assert '"collection_items_cached"' in result.content[0].text
