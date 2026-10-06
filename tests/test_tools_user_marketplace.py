"""User identity/list tools and marketplace (read-only) tools."""

from __future__ import annotations

import pytest

from . import payloads as p
from .conftest import call_err, call_ok

pytestmark = pytest.mark.respx(base_url="https://api.discogs.com")


async def test_whoami(built, respx_mock):
    mcp, _app = built
    respx_mock.get("/oauth/identity").respond(json=p.identity())
    respx_mock.get("/users/tester").respond(json=p.profile())
    out = await call_ok(mcp, "whoami", {})
    assert out["username"] == "tester"
    assert out["num_collection"] == 2
    assert "resource_url" not in out


async def test_get_user_lists(built, respx_mock):
    mcp, _app = built
    respx_mock.get("/users/tester/lists").respond(
        json=p.paged(
            [{"id": 1, "name": "Favs", "description": "", "public": True, "date_changed": "2024-01-01"}],
            "lists",
        )
    )
    out = await call_ok(mcp, "get_user_lists", {})
    assert out["lists"][0]["name"] == "Favs"


async def test_get_list(built, respx_mock):
    mcp, _app = built
    respx_mock.get("/lists/99").respond(
        json={
            "id": 99,
            "name": "Crate",
            "description": "digging",
            "items": [{"type": "release", "id": 1001, "display_title": "SAW 85-92", "comment": "classic"}],
        }
    )
    out = await call_ok(mcp, "get_list", {"list_id": 99})
    assert out["items"][0]["title"] == "SAW 85-92"
    assert out["items"][0]["comment"] == "classic"


async def test_get_release_stats(built, respx_mock):
    mcp, _app = built
    respx_mock.get("/marketplace/stats/1001").respond(json=p.stats_response())
    out = await call_ok(mcp, "get_release_stats", {"release_id": 1001})
    assert out["lowest_price"] == "25.50 USD"
    assert out["num_for_sale"] == 12


async def test_get_price_suggestions(built, respx_mock):
    mcp, _app = built
    respx_mock.get("/marketplace/price_suggestions/1001").respond(json=p.price_suggestions())
    out = await call_ok(mcp, "get_price_suggestions", {"release_id": 1001})
    assert out["suggestions"]["Mint (M)"] == "40.00 USD"
    # CONDITION_ORDER puts Mint first even though it's not first in the source payload
    assert list(out["suggestions"].keys())[0] == "Mint (M)"


async def test_get_price_suggestions_403_explains_seller_settings(built, respx_mock):
    mcp, _app = built
    respx_mock.get("/marketplace/price_suggestions/1001").respond(403, json={"message": "Forbidden"})
    message = await call_err(mcp, "get_price_suggestions", {"release_id": 1001})
    assert "seller settings" in message


async def test_get_collection_value(built, respx_mock):
    mcp, _app = built
    respx_mock.get("/users/tester/collection/value").respond(json=p.collection_value())
    out = await call_ok(mcp, "get_collection_value", {})
    assert out["minimum"] == "$100.00"
    assert out["username"] == "tester"
