"""Write-safety contract checked two ways:

1. Schema introspection via list_tools: every open-world Discogs write tool has a
   dry_run parameter defaulting to True, and the destructive ones additionally have
   destructive_hint=True and a confirm parameter defaulting to False.
2. Behavioural spot-checks (dry run makes no write call; destructive without confirm
   errors and makes no write call; destructive with confirm applies the write).
   Per-tool payload-shaping is covered more fully in test_tools_collection.py /
   test_tools_wantlist.py; this file focuses on the gating mechanism itself.
"""

from __future__ import annotations

import pytest
from mcp.client import Client

from . import payloads as p
from .conftest import call_err, call_ok

pytestmark = pytest.mark.respx(base_url="https://api.discogs.com")

OPEN_WORLD_WRITE_TOOLS = [
    "add_to_collection",
    "remove_from_collection",
    "move_collection_item",
    "rate_collection_item",
    "set_collection_field",
    "create_folder",
    "rename_folder",
    "delete_folder",
    "add_to_wantlist",
    "remove_from_wantlist",
]

DESTRUCTIVE_TOOLS = {"remove_from_collection", "delete_folder", "remove_from_wantlist"}


async def test_every_open_world_write_tool_defaults_dry_run_true(mcp):
    async with Client(mcp) as c:
        tools = {t.name: t for t in (await c.list_tools()).tools}

    for name in OPEN_WORLD_WRITE_TOOLS:
        assert name in tools, f"{name} is not registered"
        tool = tools[name]
        assert tool.annotations is not None, f"{name} has no annotations"
        assert tool.annotations.read_only_hint is False, f"{name} should not be read-only"
        props = tool.input_schema.get("properties", {})
        assert "dry_run" in props, f"{name} has no dry_run parameter"
        assert props["dry_run"].get("default") is True, f"{name}.dry_run should default to True"


async def test_destructive_tools_flagged_and_require_confirm(mcp):
    async with Client(mcp) as c:
        tools = {t.name: t for t in (await c.list_tools()).tools}

    for name in DESTRUCTIVE_TOOLS:
        tool = tools[name]
        assert tool.annotations.destructive_hint is True, f"{name} should be destructive_hint=True"
        props = tool.input_schema.get("properties", {})
        assert "confirm" in props, f"{name} has no confirm parameter"
        assert props["confirm"].get("default") is False, f"{name}.confirm should default to False"


async def test_non_destructive_write_tools_are_not_flagged_destructive(mcp):
    async with Client(mcp) as c:
        tools = {t.name: t for t in (await c.list_tools()).tools}

    for name in set(OPEN_WORLD_WRITE_TOOLS) - DESTRUCTIVE_TOOLS:
        tool = tools[name]
        assert tool.annotations.destructive_hint is not True, f"{name} should not be destructive_hint=True"


# -- behavioural: dry run never calls the write endpoint -----------------------

NON_GET_METHODS = {"POST", "PUT", "DELETE", "PATCH"}


def _assert_no_writes(respx_mock):
    for call in respx_mock.calls:
        assert call.request.method not in NON_GET_METHODS, f"unexpected {call.request.method} {call.request.url}"


@pytest.mark.respx(base_url="https://api.discogs.com", assert_all_called=False)
async def test_add_to_collection_dry_run_makes_no_write(mcp, respx_mock):
    respx_mock.get("/releases/1001").respond(json=p.basic_information())
    respx_mock.get("/users/tester/collection/releases/1001").respond(json={"releases": []})
    respx_mock.get("/users/tester/collection/folders/1").respond(json=p.collection_folder(id=1))
    respx_mock.post("/users/tester/collection/folders/1/releases/1001").respond(json={"instance_id": 1})
    out = await call_ok(mcp, "add_to_collection", {"release_id": 1001})
    assert out["dry_run"] is True
    _assert_no_writes(respx_mock)


@pytest.mark.respx(base_url="https://api.discogs.com", assert_all_called=False)
async def test_move_collection_item_dry_run_makes_no_write(mcp, respx_mock):
    respx_mock.get("/users/tester/collection/releases/1001").respond(
        json={"releases": [p.collection_item()]}
    )
    respx_mock.get("/users/tester/collection/folders/1").respond(json=p.collection_folder(id=1))
    respx_mock.get("/users/tester/collection/folders/2").respond(json=p.collection_folder(id=2))
    respx_mock.post("/users/tester/collection/folders/1/releases/1001/instances/9001").respond(json={})
    out = await call_ok(
        mcp, "move_collection_item", {"release_id": 1001, "instance_id": 9001, "to_folder_id": 2}
    )
    assert out["dry_run"] is True
    _assert_no_writes(respx_mock)


@pytest.mark.respx(base_url="https://api.discogs.com", assert_all_called=False)
async def test_rate_collection_item_dry_run_makes_no_write(mcp, respx_mock):
    respx_mock.get("/users/tester/collection/releases/1001").respond(
        json={"releases": [p.collection_item()]}
    )
    respx_mock.post("/users/tester/collection/folders/1/releases/1001/instances/9001").respond(json={})
    out = await call_ok(mcp, "rate_collection_item", {"release_id": 1001, "instance_id": 9001, "rating": 3})
    assert out["dry_run"] is True
    _assert_no_writes(respx_mock)


@pytest.mark.respx(base_url="https://api.discogs.com", assert_all_called=False)
async def test_set_collection_field_dry_run_makes_no_write(mcp, respx_mock):
    respx_mock.get("/users/tester/collection/releases/1001").respond(
        json={"releases": [p.collection_item()]}
    )
    respx_mock.get("/users/tester/collection/fields").respond(json={"fields": [p.collection_field(id=1)]})
    respx_mock.post("/users/tester/collection/folders/1/releases/1001/instances/9001/fields/1").respond(json={})
    out = await call_ok(
        mcp,
        "set_collection_field",
        {"release_id": 1001, "instance_id": 9001, "field_id": 1, "value": "Mint (M)"},
    )
    assert out["dry_run"] is True
    _assert_no_writes(respx_mock)


@pytest.mark.respx(base_url="https://api.discogs.com", assert_all_called=False)
async def test_create_folder_dry_run_makes_no_write(mcp, respx_mock):
    respx_mock.post("/users/tester/collection/folders").respond(json={"id": 1})
    out = await call_ok(mcp, "create_folder", {"name": "Jazz"})
    assert out["dry_run"] is True
    _assert_no_writes(respx_mock)


@pytest.mark.respx(base_url="https://api.discogs.com", assert_all_called=False)
async def test_rename_folder_dry_run_makes_no_write(mcp, respx_mock):
    respx_mock.get("/users/tester/collection/folders/5").respond(json=p.collection_folder(id=5))
    respx_mock.post("/users/tester/collection/folders/5").respond(json={})
    out = await call_ok(mcp, "rename_folder", {"folder_id": 5, "name": "x"})
    assert out["dry_run"] is True
    _assert_no_writes(respx_mock)


@pytest.mark.respx(base_url="https://api.discogs.com", assert_all_called=False)
async def test_delete_folder_dry_run_makes_no_write(mcp, respx_mock):
    respx_mock.get("/users/tester/collection/folders/5").respond(json=p.collection_folder(id=5, count=0))
    respx_mock.delete("/users/tester/collection/folders/5").respond(json={})
    out = await call_ok(mcp, "delete_folder", {"folder_id": 5})
    assert out["dry_run"] is True
    _assert_no_writes(respx_mock)


@pytest.mark.respx(base_url="https://api.discogs.com", assert_all_called=False)
async def test_add_to_wantlist_dry_run_makes_no_write(mcp, respx_mock):
    respx_mock.get("/releases/2002").respond(json=p.basic_information(release_id=2002))
    respx_mock.get("/users/tester/wants").respond(json=p.paged([], "wants"))
    respx_mock.put("/users/tester/wants/2002").respond(json={})
    out = await call_ok(mcp, "add_to_wantlist", {"release_id": 2002})
    assert out["dry_run"] is True
    _assert_no_writes(respx_mock)


@pytest.mark.respx(base_url="https://api.discogs.com", assert_all_called=False)
async def test_remove_from_collection_dry_run_makes_no_write(mcp, respx_mock):
    respx_mock.get("/users/tester/collection/releases/1001").respond(
        json={"releases": [p.collection_item()]}
    )
    respx_mock.delete("/users/tester/collection/folders/1/releases/1001/instances/9001").respond(json={})
    out = await call_ok(mcp, "remove_from_collection", {"release_id": 1001, "instance_id": 9001})
    assert out["dry_run"] is True
    _assert_no_writes(respx_mock)


@pytest.mark.respx(base_url="https://api.discogs.com", assert_all_called=False)
async def test_remove_from_wantlist_dry_run_makes_no_write(mcp, respx_mock):
    respx_mock.get("/users/tester/wants").respond(json=p.paged([p.want_item()], "wants"))
    respx_mock.delete("/users/tester/wants/2002").respond(json={})
    out = await call_ok(mcp, "remove_from_wantlist", {"release_id": 2002})
    assert out["dry_run"] is True
    _assert_no_writes(respx_mock)


# -- behavioural: destructive without confirm errors and makes no write --------


@pytest.mark.respx(base_url="https://api.discogs.com", assert_all_called=False)
async def test_destructive_without_confirm_errors_and_makes_no_write(mcp, respx_mock):
    respx_mock.get("/users/tester/collection/releases/1001").respond(
        json={"releases": [p.collection_item()]}
    )
    respx_mock.delete("/users/tester/collection/folders/1/releases/1001/instances/9001").respond(json={})
    message = await call_err(
        mcp, "remove_from_collection", {"release_id": 1001, "instance_id": 9001, "dry_run": False}
    )
    assert "confirm" in message.lower()
    _assert_no_writes(respx_mock)


async def test_destructive_with_confirm_applies(mcp, respx_mock):
    respx_mock.get("/users/tester/collection/releases/1001").respond(
        json={"releases": [p.collection_item()]}
    )
    delete_route = respx_mock.delete(
        "/users/tester/collection/folders/1/releases/1001/instances/9001"
    ).respond(json={})
    out = await call_ok(
        mcp,
        "remove_from_collection",
        {"release_id": 1001, "instance_id": 9001, "dry_run": False, "confirm": True},
    )
    assert out["done"] is True
    assert delete_route.called
