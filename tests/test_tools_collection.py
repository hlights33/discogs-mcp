"""Collection read tools, plus one representative write (dry-run + applied) per tool
for payload shaping. Exhaustive write-safety gating lives in test_write_safety.py."""

from __future__ import annotations

import pytest

from . import payloads as p
from .conftest import call_err, call_ok

pytestmark = pytest.mark.respx(base_url="https://api.discogs.com")


async def test_list_collection_folders(built, respx_mock):
    mcp, _app = built
    respx_mock.get("/users/tester/collection/folders").respond(
        json={"folders": [p.collection_folder(id=0, name="All", count=2), p.collection_folder()]}
    )
    out = await call_ok(mcp, "list_collection_folders", {})
    assert out["folders"][0]["name"] == "All"
    assert out["folders"][1]["count"] == 2


async def test_get_collection(built, respx_mock):
    mcp, _app = built
    respx_mock.get("/users/tester/collection/fields").respond(json={"fields": [p.collection_field()]})
    respx_mock.get("/users/tester/collection/folders/0/releases").respond(
        json=p.paged([p.collection_item()], "releases")
    )
    out = await call_ok(mcp, "get_collection", {})
    item = out["items"][0]
    assert item["instance_id"] == 9001
    assert item["fields"]["Media Condition"] == "Near Mint (NM or M-)"
    assert item["artist"] == "Aphex Twin"


async def test_find_in_collection_owned(built, respx_mock):
    mcp, _app = built
    respx_mock.get("/users/tester/collection/fields").respond(json={"fields": [p.collection_field()]})
    respx_mock.get("/users/tester/collection/releases/1001").respond(
        json={"releases": [p.collection_item()]}
    )
    out = await call_ok(mcp, "find_in_collection", {"release_id": 1001})
    assert out["owned"] is True
    assert len(out["instances"]) == 1


async def test_find_in_collection_not_owned(built, respx_mock):
    mcp, _app = built
    respx_mock.get("/users/tester/collection/fields").respond(json={"fields": []})
    respx_mock.get("/users/tester/collection/releases/404404").respond(json={"releases": []})
    out = await call_ok(mcp, "find_in_collection", {"release_id": 404404})
    assert out["owned"] is False
    assert out["instances"] == []


async def test_get_collection_fields(built, respx_mock):
    mcp, _app = built
    respx_mock.get("/users/tester/collection/fields").respond(json={"fields": [p.collection_field()]})
    out = await call_ok(mcp, "get_collection_fields", {})
    assert out["fields"][0]["name"] == "Media Condition"


# -- add_to_collection: dry-run preview, then applied -------------------------


@pytest.mark.respx(base_url="https://api.discogs.com", assert_all_called=False)
async def test_add_to_collection_dry_run_previews_without_posting(built, respx_mock):
    mcp, _app = built
    respx_mock.get("/releases/1001").respond(json=p.basic_information())
    respx_mock.get("/users/tester/collection/releases/1001").respond(json={"releases": []})
    respx_mock.get("/users/tester/collection/folders/1").respond(json=p.collection_folder(id=1))
    post_route = respx_mock.post("/users/tester/collection/folders/1/releases/1001").respond(
        json={"instance_id": 555}
    )
    out = await call_ok(mcp, "add_to_collection", {"release_id": 1001})
    assert out["dry_run"] is True
    assert out["would"]["action"] == "add_to_collection"
    assert not post_route.called


async def test_add_to_collection_applies_when_dry_run_false(built, respx_mock):
    mcp, _app = built
    respx_mock.get("/releases/1001").respond(json=p.basic_information())
    respx_mock.get("/users/tester/collection/releases/1001").respond(json={"releases": []})
    respx_mock.get("/users/tester/collection/folders/1").respond(json=p.collection_folder())
    post_route = respx_mock.post("/users/tester/collection/folders/1/releases/1001").respond(
        json={"instance_id": 555}
    )
    out = await call_ok(mcp, "add_to_collection", {"release_id": 1001, "dry_run": False})
    assert out["done"] is True
    assert out["instance_id"] == 555
    assert post_route.called


async def test_add_to_collection_rejects_folder_zero(built, respx_mock):
    mcp, _app = built
    message = await call_err(mcp, "add_to_collection", {"release_id": 1001, "folder_id": 0})
    assert "Folder 0" in message


# -- move / rate / set_collection_field ---------------------------------------


@pytest.mark.respx(base_url="https://api.discogs.com", assert_all_called=False)
async def test_move_collection_item_dry_run(built, respx_mock):
    mcp, _app = built
    respx_mock.get("/users/tester/collection/releases/1001").respond(
        json={"releases": [p.collection_item()]}
    )
    respx_mock.get("/users/tester/collection/folders/1").respond(json=p.collection_folder(id=1))
    respx_mock.get("/users/tester/collection/folders/3").respond(
        json=p.collection_folder(id=3, name="Vinyl")
    )
    post_route = respx_mock.post(
        "/users/tester/collection/folders/1/releases/1001/instances/9001"
    ).respond(json={})
    out = await call_ok(
        mcp, "move_collection_item", {"release_id": 1001, "instance_id": 9001, "to_folder_id": 3}
    )
    assert out["dry_run"] is True
    assert out["would"]["to_folder"] == "Vinyl"
    assert not post_route.called


async def test_move_collection_item_applies(built, respx_mock):
    mcp, _app = built
    respx_mock.get("/users/tester/collection/releases/1001").respond(
        json={"releases": [p.collection_item()]}
    )
    respx_mock.get("/users/tester/collection/folders/1").respond(json=p.collection_folder(id=1))
    respx_mock.get("/users/tester/collection/folders/3").respond(
        json=p.collection_folder(id=3, name="Vinyl")
    )
    post_route = respx_mock.post(
        "/users/tester/collection/folders/1/releases/1001/instances/9001"
    ).respond(json={})
    out = await call_ok(
        mcp,
        "move_collection_item",
        {"release_id": 1001, "instance_id": 9001, "to_folder_id": 3, "dry_run": False},
    )
    assert out["done"] is True
    assert post_route.called
    assert post_route.calls[0].request.content  # carried the folder_id json body


async def test_move_collection_item_rejects_folder_zero(built, respx_mock):
    mcp, _app = built
    message = await call_err(
        mcp, "move_collection_item", {"release_id": 1001, "instance_id": 9001, "to_folder_id": 0}
    )
    assert "folder 0" in message.lower()


async def test_rate_collection_item_validates_range(built, respx_mock):
    mcp, _app = built
    message = await call_err(
        mcp, "rate_collection_item", {"release_id": 1001, "instance_id": 9001, "rating": 7}
    )
    assert "0 and 5" in message


async def test_rate_collection_item_applies(built, respx_mock):
    mcp, _app = built
    respx_mock.get("/users/tester/collection/releases/1001").respond(
        json={"releases": [p.collection_item(rating=2)]}
    )
    post_route = respx_mock.post(
        "/users/tester/collection/folders/1/releases/1001/instances/9001"
    ).respond(json={})
    out = await call_ok(
        mcp,
        "rate_collection_item",
        {"release_id": 1001, "instance_id": 9001, "rating": 5, "dry_run": False},
    )
    assert out["rating_before"] == 2
    assert out["rating_after"] == 5
    assert post_route.called


async def test_set_collection_field_unknown_field(built, respx_mock):
    mcp, _app = built
    respx_mock.get("/users/tester/collection/releases/1001").respond(
        json={"releases": [p.collection_item()]}
    )
    respx_mock.get("/users/tester/collection/fields").respond(json={"fields": [p.collection_field(id=1)]})
    message = await call_err(
        mcp,
        "set_collection_field",
        {"release_id": 1001, "instance_id": 9001, "field_id": 999, "value": "x"},
    )
    assert "Unknown field_id" in message


async def test_set_collection_field_applies(built, respx_mock):
    mcp, _app = built
    respx_mock.get("/users/tester/collection/releases/1001").respond(
        json={"releases": [p.collection_item()]}
    )
    respx_mock.get("/users/tester/collection/fields").respond(json={"fields": [p.collection_field(id=1)]})
    post_route = respx_mock.post(
        "/users/tester/collection/folders/1/releases/1001/instances/9001/fields/1"
    ).respond(json={})
    out = await call_ok(
        mcp,
        "set_collection_field",
        {"release_id": 1001, "instance_id": 9001, "field_id": 1, "value": "Mint (M)", "dry_run": False},
    )
    assert out["done"] is True
    assert post_route.called


# -- folders --------------------------------------------------------------


async def test_create_folder_dry_run_then_apply(built, respx_mock):
    mcp, _app = built
    preview = await call_ok(mcp, "create_folder", {"name": "Techno"})
    assert preview["dry_run"] is True
    post_route = respx_mock.post("/users/tester/collection/folders").respond(json={"id": 7})
    out = await call_ok(mcp, "create_folder", {"name": "Techno", "dry_run": False})
    assert out["folder_id"] == 7
    assert post_route.called


async def test_rename_folder_rejects_builtin_folders(built, respx_mock):
    mcp, _app = built
    message = await call_err(mcp, "rename_folder", {"folder_id": 0, "name": "x"})
    assert "cannot be renamed" in message
    message = await call_err(mcp, "rename_folder", {"folder_id": 1, "name": "x"})
    assert "cannot be renamed" in message


async def test_rename_folder_applies(built, respx_mock):
    mcp, _app = built
    respx_mock.get("/users/tester/collection/folders/5").respond(
        json=p.collection_folder(id=5, name="Old")
    )
    post_route = respx_mock.post("/users/tester/collection/folders/5").respond(json={})
    out = await call_ok(mcp, "rename_folder", {"folder_id": 5, "name": "New", "dry_run": False})
    assert out["done"] is True
    assert post_route.called


async def test_delete_folder_rejects_builtin_folders(built, respx_mock):
    mcp, _app = built
    message = await call_err(mcp, "delete_folder", {"folder_id": 1})
    assert "cannot be deleted" in message


async def test_delete_folder_warns_when_not_empty(built, respx_mock):
    mcp, _app = built
    respx_mock.get("/users/tester/collection/folders/5").respond(
        json=p.collection_folder(id=5, name="Techno", count=3)
    )
    out = await call_ok(mcp, "delete_folder", {"folder_id": 5})
    assert "not empty" in out["would"]["warning"].lower()


@pytest.mark.respx(base_url="https://api.discogs.com", assert_all_called=False)
async def test_delete_folder_destructive_requires_confirm(built, respx_mock):
    mcp, _app = built
    respx_mock.get("/users/tester/collection/folders/5").respond(
        json=p.collection_folder(id=5, name="Techno", count=0)
    )
    delete_route = respx_mock.delete("/users/tester/collection/folders/5").respond(json={})
    message = await call_err(mcp, "delete_folder", {"folder_id": 5, "dry_run": False})
    assert "confirm" in message.lower()
    assert not delete_route.called


async def test_delete_folder_applies_with_confirm(built, respx_mock):
    mcp, _app = built
    respx_mock.get("/users/tester/collection/folders/5").respond(
        json=p.collection_folder(id=5, name="Techno", count=0)
    )
    delete_route = respx_mock.delete("/users/tester/collection/folders/5").respond(json={})
    out = await call_ok(mcp, "delete_folder", {"folder_id": 5, "dry_run": False, "confirm": True})
    assert out["done"] is True
    assert delete_route.called


# -- remove_from_collection (destructive) --------------------------------------


@pytest.mark.respx(base_url="https://api.discogs.com", assert_all_called=False)
async def test_remove_from_collection_dry_run(built, respx_mock):
    mcp, _app = built
    respx_mock.get("/users/tester/collection/releases/1001").respond(
        json={"releases": [p.collection_item()]}
    )
    delete_route = respx_mock.delete(
        "/users/tester/collection/folders/1/releases/1001/instances/9001"
    ).respond(json={})
    out = await call_ok(mcp, "remove_from_collection", {"release_id": 1001, "instance_id": 9001})
    assert out["dry_run"] is True
    assert not delete_route.called


@pytest.mark.respx(base_url="https://api.discogs.com", assert_all_called=False)
async def test_remove_from_collection_requires_confirm(built, respx_mock):
    mcp, _app = built
    respx_mock.get("/users/tester/collection/releases/1001").respond(
        json={"releases": [p.collection_item()]}
    )
    delete_route = respx_mock.delete(
        "/users/tester/collection/folders/1/releases/1001/instances/9001"
    ).respond(json={})
    message = await call_err(
        mcp, "remove_from_collection", {"release_id": 1001, "instance_id": 9001, "dry_run": False}
    )
    assert "confirm" in message.lower()
    assert not delete_route.called


async def test_remove_from_collection_applies_with_confirm(built, respx_mock):
    mcp, _app = built
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


async def test_remove_from_collection_unknown_instance(built, respx_mock):
    mcp, _app = built
    respx_mock.get("/users/tester/collection/releases/1001").respond(json={"releases": []})
    message = await call_err(mcp, "remove_from_collection", {"release_id": 1001, "instance_id": 9999})
    assert "not in" in message
