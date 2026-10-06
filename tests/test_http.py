"""server.http_app: refuses a non-local bind without MCP_AUTH_TOKEN; BearerAuth gates every request."""

from __future__ import annotations

import httpx
import pytest

from discogs_mcp.server import BearerAuth, build_server, http_app
from discogs_mcp.state import App

from .conftest import make_settings


async def _built_mcp(tmp_path, **overrides):
    settings = make_settings(tmp_path, **overrides)
    app = App(settings)
    mcp, _ = build_server(app=app)
    return mcp, settings, app


async def _trivial_app(scope, receive, send):
    assert scope["type"] == "http"
    await send({"type": "http.response.start", "status": 200, "headers": [(b"content-type", b"text/plain")]})
    await send({"type": "http.response.body", "body": b"ok"})


async def _asgi_get(app, path="/", headers=None):
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.get(path, headers=headers or {})


# -- BearerAuth -----------------------------------------------------------------


async def test_bearer_auth_rejects_missing_token():
    app = BearerAuth(_trivial_app, "secret")
    resp = await _asgi_get(app)
    assert resp.status_code == 401
    assert resp.json() == {"error": "unauthorized"}


async def test_bearer_auth_rejects_wrong_token():
    app = BearerAuth(_trivial_app, "secret")
    resp = await _asgi_get(app, headers={"Authorization": "Bearer wrong"})
    assert resp.status_code == 401


async def test_bearer_auth_accepts_correct_token():
    app = BearerAuth(_trivial_app, "secret")
    resp = await _asgi_get(app, headers={"Authorization": "Bearer secret"})
    assert resp.status_code == 200
    assert resp.text == "ok"


# -- http_app host/token gate ---------------------------------------------------


async def test_http_app_refuses_non_local_host_without_token(tmp_path):
    mcp, settings, app = await _built_mcp(tmp_path, MCP_AUTH_TOKEN=None)
    with pytest.raises(SystemExit, match="MCP_AUTH_TOKEN"):
        http_app(mcp, settings, "0.0.0.0")
    await app.aclose()


async def test_http_app_allows_local_host_without_token(tmp_path):
    mcp, settings, app = await _built_mcp(tmp_path, MCP_AUTH_TOKEN=None)
    result = http_app(mcp, settings, "127.0.0.1")
    assert result is not None  # the bare starlette app, no BearerAuth wrapper needed
    assert not isinstance(result, BearerAuth)
    await app.aclose()


async def test_http_app_wraps_in_bearer_auth_when_token_set(tmp_path):
    mcp, settings, app = await _built_mcp(tmp_path, MCP_AUTH_TOKEN="shh")
    result = http_app(mcp, settings, "0.0.0.0")
    assert isinstance(result, BearerAuth)
    await app.aclose()


async def test_http_app_allows_non_local_host_with_token(tmp_path):
    mcp, settings, app = await _built_mcp(tmp_path, MCP_AUTH_TOKEN="shh")
    result = http_app(mcp, settings, "0.0.0.0")
    assert isinstance(result, BearerAuth)
    assert result.expected == b"Bearer shh"
    await app.aclose()
