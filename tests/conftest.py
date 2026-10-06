"""Shared fixtures.

Every test is isolated from a developer's real environment: DISCOGS_*/MCP_AUTH_TOKEN
env vars are stripped and the cwd is moved to a throwaway tmp_path (so a stray
``.env`` in the repo can never leak a real token into a test), and Settings is
always built with ``_env_file=None`` as a second line of defense.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from mcp.client import Client

from discogs_mcp.client import DiscogsClient
from discogs_mcp.config import Settings
from discogs_mcp.server import build_server
from discogs_mcp.state import App

ENV_KEYS = [
    "DISCOGS_TOKEN",
    "DISCOGS_USERNAME",
    "DISCOGS_CACHE_PATH",
    "DISCOGS_BASE_URL",
    "DISCOGS_ENABLE_LOCAL",
    "MCP_AUTH_TOKEN",
]

TOKEN = "test-token-xyz"
USERNAME = "tester"


@pytest.fixture(autouse=True)
def _isolated_env(tmp_path, monkeypatch):
    for key in ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.chdir(tmp_path)
    yield


def make_settings(tmp_path: Path, **overrides: Any) -> Settings:
    kwargs: dict[str, Any] = dict(
        DISCOGS_TOKEN=TOKEN,
        DISCOGS_USERNAME=USERNAME,
        DISCOGS_CACHE_PATH=tmp_path / "cache.db",
        DISCOGS_ENABLE_LOCAL=False,
    )
    kwargs.update(overrides)
    return Settings(_env_file=None, **kwargs)


@pytest.fixture
def settings(tmp_path) -> Settings:
    return make_settings(tmp_path)


@pytest.fixture
async def client():
    """A DiscogsClient with no backoff delay, for fast retry tests."""
    c = DiscogsClient(TOKEN, backoff_base=0)
    yield c
    await c.aclose()


@pytest.fixture
async def app(settings):
    """An App with its own zero-backoff client, closed at teardown."""
    a = App(settings, client=DiscogsClient(TOKEN, backoff_base=0))
    yield a
    await a.aclose()


@pytest.fixture
async def built(app):
    """(mcp, app) with every tool module registered."""
    mcp, app2 = build_server(app=app)
    return mcp, app2


@pytest.fixture
async def mcp(built):
    mcp, _app = built
    return mcp


async def call(mcp, name: str, args: dict[str, Any] | None = None):
    """Call a tool via an in-memory Client; return (is_error, parsed_json_or_text)."""
    async with Client(mcp) as c:
        result = await c.call_tool(name, args or {})
        text = result.content[0].text if result.content else ""
        try:
            payload = json.loads(text)
        except (json.JSONDecodeError, TypeError):
            payload = text
        return result.is_error, payload


async def call_ok(mcp, name: str, args: dict[str, Any] | None = None):
    is_error, payload = await call(mcp, name, args)
    assert not is_error, f"{name} failed: {payload}"
    return payload


async def call_err(mcp, name: str, args: dict[str, Any] | None = None) -> str:
    is_error, payload = await call(mcp, name, args)
    assert is_error, f"{name} unexpectedly succeeded: {payload}"
    return payload
