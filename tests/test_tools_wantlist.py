"""Wantlist read and write tools."""

from __future__ import annotations

import pytest

from . import payloads as p
from .conftest import call_err, call_ok

pytestmark = pytest.mark.respx(base_url="https://api.discogs.com")


async def test_get_wantlist(built, respx_mock):
    mcp, _app = built
    respx_mock.get("/users/tester/wants").respond(json=p.paged([p.want_item()], "wants"))
    out = await call_ok(mcp, "get_wantlist", {})
    assert out["wants"][0]["release_id"] == 2002
    assert out["wants"][0]["title"] == "Druqks"


@pytest.mark.respx(base_url="https://api.discogs.com", assert_all_called=False)
async def test_add_to_wantlist_dry_run_no_put(built, respx_mock):
    mcp, _app = built
    respx_mock.get("/releases/2002").respond(json=p.basic_information(release_id=2002, title="Druqks"))
    respx_mock.get("/users/tester/wants").respond(json=p.paged([], "wants"))
    put_route = respx_mock.put("/users/tester/wants/2002").respond(json={})
    out = await call_ok(mcp, "add_to_wantlist", {"release_id": 2002, "notes": "want it"})
    assert out["dry_run"] is True
    assert out["would"]["action"] == "add_to_wantlist"
    assert not put_route.called


async def test_add_to_wantlist_applies(built, respx_mock):
    mcp, _app = built
    respx_mock.get("/releases/2002").respond(json=p.basic_information(release_id=2002, title="Druqks"))
    respx_mock.get("/users/tester/wants").respond(json=p.paged([], "wants"))
    put_route = respx_mock.put("/users/tester/wants/2002").respond(json={})
    out = await call_ok(mcp, "add_to_wantlist", {"release_id": 2002, "dry_run": False})
    assert out["done"] is True
    assert put_route.called


async def test_add_to_wantlist_detects_existing(built, respx_mock):
    mcp, _app = built
    respx_mock.get("/releases/2002").respond(json=p.basic_information(release_id=2002, title="Druqks"))
    respx_mock.get("/users/tester/wants").respond(json=p.paged([p.want_item()], "wants"))
    out = await call_ok(mcp, "add_to_wantlist", {"release_id": 2002})
    assert out["would"]["action"] == "update_wantlist_item"
    assert out["would"]["already_in_wantlist"] is True


async def test_add_to_wantlist_validates_rating(built, respx_mock):
    mcp, _app = built
    message = await call_err(mcp, "add_to_wantlist", {"release_id": 2002, "rating": 9})
    assert "0 and 5" in message


@pytest.mark.respx(base_url="https://api.discogs.com", assert_all_called=False)
async def test_remove_from_wantlist_dry_run(built, respx_mock):
    mcp, _app = built
    respx_mock.get("/users/tester/wants").respond(json=p.paged([p.want_item()], "wants"))
    delete_route = respx_mock.delete("/users/tester/wants/2002").respond(json={})
    out = await call_ok(mcp, "remove_from_wantlist", {"release_id": 2002})
    assert out["dry_run"] is True
    assert not delete_route.called


@pytest.mark.respx(base_url="https://api.discogs.com", assert_all_called=False)
async def test_remove_from_wantlist_requires_confirm(built, respx_mock):
    mcp, _app = built
    respx_mock.get("/users/tester/wants").respond(json=p.paged([p.want_item()], "wants"))
    delete_route = respx_mock.delete("/users/tester/wants/2002").respond(json={})
    message = await call_err(mcp, "remove_from_wantlist", {"release_id": 2002, "dry_run": False})
    assert "confirm" in message.lower()
    assert not delete_route.called


async def test_remove_from_wantlist_applies_with_confirm(built, respx_mock):
    mcp, _app = built
    respx_mock.get("/users/tester/wants").respond(json=p.paged([p.want_item()], "wants"))
    delete_route = respx_mock.delete("/users/tester/wants/2002").respond(json={})
    out = await call_ok(
        mcp, "remove_from_wantlist", {"release_id": 2002, "dry_run": False, "confirm": True}
    )
    assert out["done"] is True
    assert delete_route.called


async def test_remove_from_wantlist_not_present(built, respx_mock):
    mcp, _app = built
    respx_mock.get("/users/tester/wants").respond(json=p.paged([], "wants"))
    message = await call_err(mcp, "remove_from_wantlist", {"release_id": 2002})
    assert "not in" in message
