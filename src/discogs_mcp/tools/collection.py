"""Collection reads and writes. Every write defaults to dry_run=True."""

from __future__ import annotations

from typing import Any, Literal

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from .. import models
from ..client import DiscogsError
from ..state import App, write_gate
from . import DESTRUCTIVE, IDEMPOTENT_WRITE, READ, WRITE

CollectionSort = Literal["added", "artist", "title", "year", "rating", "label", "format", "catno"]


def collection_item(item: dict[str, Any], field_names: dict[int, str]) -> dict[str, Any]:
    fields = {
        field_names.get(n["field_id"], f"field_{n['field_id']}"): n.get("value")
        for n in item.get("notes") or []
    }
    return models.strip_noise(
        {
            "instance_id": item.get("instance_id"),
            "folder_id": item.get("folder_id"),
            "rating": item.get("rating") or None,
            "date_added": item.get("date_added"),
            **models.basic_release(item.get("basic_information") or {}),
            "fields": fields,
        }
    )


def register(mcp: MCPServer, app: App) -> None:
    field_cache: dict[str, dict[int, str]] = {}

    async def field_names(user: str) -> dict[int, str]:
        if user not in field_cache:
            data = await app.client.get(f"/users/{user}/collection/fields")
            field_cache[user] = {f["id"]: f["name"] for f in data.get("fields", [])}
        return field_cache[user]

    async def locate(user: str, release_id: int, instance_id: int) -> dict[str, Any]:
        data = await app.client.get(f"/users/{user}/collection/releases/{release_id}")
        for item in data.get("releases", []):
            if item.get("instance_id") == instance_id:
                return item
        raise ToolError(
            f"Instance {instance_id} of release {release_id} is not in {user}'s collection. "
            "Use find_in_collection to list instance ids."
        )

    def describe(item: dict[str, Any]) -> str:
        info = item.get("basic_information") or {}
        return f"{models.artist_credit(info.get('artists'))} – {info.get('title')}"

    async def folder_name(user: str, folder_id: int) -> str:
        try:
            data = await app.client.get(f"/users/{user}/collection/folders/{folder_id}")
            return data.get("name", str(folder_id))
        except DiscogsError:
            return str(folder_id)

    def mirror(fn: str, *args: Any, **kwargs: Any) -> None:
        """Keep the local cache in step with a write, if a cache exists."""
        if app.settings.cache_path.exists():
            getattr(app.cache, fn)(*args, **kwargs)

    # -- reads ---------------------------------------------------------------
    @mcp.tool(annotations=READ)
    async def list_collection_folders(username: str | None = None) -> dict:
        """Collection folders with item counts. Folder 0 is "All", 1 is "Uncategorized"."""
        user = username or await app.username()
        data = await app.client.get(f"/users/{user}/collection/folders")
        return {"folders": [models.strip_noise(f) for f in data.get("folders", [])]}

    @mcp.tool(annotations=READ)
    async def get_collection(
        folder_id: int = 0,
        sort: CollectionSort = "added",
        sort_order: Literal["asc", "desc"] = "desc",
        page: int = 1,
        per_page: int = 50,
        username: str | None = None,
    ) -> dict:
        """One page of the collection straight from Discogs (folder 0 = all).

        For questions across the whole collection, prefer the cache tools
        (query_collection, collection_summary): they avoid paging through the API.
        """
        user = username or await app.username()
        names = await field_names(user) if user == await app.username() else {}
        items, pagination = await app.client.page(
            f"/users/{user}/collection/folders/{folder_id}/releases",
            "releases",
            page=page,
            per_page=per_page,
            params={"sort": sort, "sort_order": sort_order},
        )
        return {"pagination": pagination, "items": [collection_item(i, names) for i in items]}

    @mcp.tool(annotations=READ)
    async def find_in_collection(release_id: int, username: str | None = None) -> dict:
        """Every copy (instance) of a release in the collection, with folder, rating and fields.
        Returns an empty list if it is not owned."""
        user = username or await app.username()
        names = await field_names(user) if user == await app.username() else {}
        data = await app.client.get(f"/users/{user}/collection/releases/{release_id}")
        items = [collection_item(i, names) for i in data.get("releases", [])]
        return {"release_id": release_id, "owned": bool(items), "instances": items}

    @mcp.tool(annotations=READ)
    async def get_collection_fields(username: str | None = None) -> dict:
        """Custom collection fields (e.g. Media Condition, Sleeve Condition, Notes) and their
        allowed options. Use the ids with set_collection_field."""
        user = username or await app.username()
        data = await app.client.get(f"/users/{user}/collection/fields")
        return {"fields": [models.strip_noise(f) for f in data.get("fields", [])]}

    # -- writes --------------------------------------------------------------
    @mcp.tool(annotations=WRITE)
    async def add_to_collection(release_id: int, folder_id: int = 1, dry_run: bool = True) -> dict:
        """Add a release to a collection folder (default 1 = Uncategorized; 0 is not allowed).

        Dry-run by default: returns a preview. Call again with dry_run=false to apply.
        """
        app.require_token()
        if folder_id == 0:
            raise ToolError("Folder 0 is the virtual 'All' folder; use 1 (Uncategorized) or another folder.")
        user = await app.username()
        release = await app.client.get(f"/releases/{release_id}")
        owned = await app.client.get(f"/users/{user}/collection/releases/{release_id}")
        plan = {
            "action": "add_to_collection",
            "release": f"{models.artist_credit(release.get('artists'))} – {release.get('title')}",
            "release_id": release_id,
            "format": models.format_summary(release.get("formats")),
            "folder": await folder_name(user, folder_id),
            "folder_id": folder_id,
            "copies_already_owned": len(owned.get("releases", [])),
        }
        if preview := write_gate(dry_run=dry_run, plan=plan):
            return preview
        result = await app.client.request(
            "POST", f"/users/{user}/collection/folders/{folder_id}/releases/{release_id}"
        )
        return {
            "done": True,
            **plan,
            "instance_id": result.get("instance_id"),
            "cache_note": "Run sync_collection to pull the new item into the local cache.",
        }

    @mcp.tool(annotations=DESTRUCTIVE)
    async def remove_from_collection(
        release_id: int, instance_id: int, dry_run: bool = True, confirm: bool = False
    ) -> dict:
        """Remove one copy (instance) of a release from the collection. Destructive:
        needs dry_run=false AND confirm=true. Get instance ids from find_in_collection."""
        app.require_token()
        user = await app.username()
        item = await locate(user, release_id, instance_id)
        plan = {
            "action": "remove_from_collection",
            "release": describe(item),
            "release_id": release_id,
            "instance_id": instance_id,
            "folder_id": item.get("folder_id"),
            "date_added": item.get("date_added"),
        }
        if preview := write_gate(dry_run=dry_run, plan=plan, destructive=True, confirm=confirm):
            return preview
        await app.client.request(
            "DELETE",
            f"/users/{user}/collection/folders/{item['folder_id']}/releases/{release_id}/instances/{instance_id}",
        )
        mirror("delete_instance", instance_id)
        return {"done": True, **plan}

    @mcp.tool(annotations=IDEMPOTENT_WRITE)
    async def move_collection_item(
        release_id: int, instance_id: int, to_folder_id: int, dry_run: bool = True
    ) -> dict:
        """Move a collection instance to another folder. Dry-run by default."""
        app.require_token()
        if to_folder_id == 0:
            raise ToolError("Cannot move into folder 0 ('All').")
        user = await app.username()
        item = await locate(user, release_id, instance_id)
        plan = {
            "action": "move_collection_item",
            "release": describe(item),
            "instance_id": instance_id,
            "from_folder": await folder_name(user, item["folder_id"]),
            "to_folder": await folder_name(user, to_folder_id),
            "to_folder_id": to_folder_id,
        }
        if preview := write_gate(dry_run=dry_run, plan=plan):
            return preview
        await app.client.request(
            "POST",
            f"/users/{user}/collection/folders/{item['folder_id']}/releases/{release_id}/instances/{instance_id}",
            json={"folder_id": to_folder_id},
        )
        mirror("update_instance", instance_id, folder_id=to_folder_id)
        return {"done": True, **plan}

    @mcp.tool(annotations=IDEMPOTENT_WRITE)
    async def rate_collection_item(
        release_id: int, instance_id: int, rating: int, dry_run: bool = True
    ) -> dict:
        """Set a 0–5 star rating on a collection instance (0 clears it). Dry-run by default."""
        app.require_token()
        if not 0 <= rating <= 5:
            raise ToolError("rating must be between 0 and 5.")
        user = await app.username()
        item = await locate(user, release_id, instance_id)
        plan = {
            "action": "rate_collection_item",
            "release": describe(item),
            "instance_id": instance_id,
            "rating_before": item.get("rating", 0),
            "rating_after": rating,
        }
        if preview := write_gate(dry_run=dry_run, plan=plan):
            return preview
        await app.client.request(
            "POST",
            f"/users/{user}/collection/folders/{item['folder_id']}/releases/{release_id}/instances/{instance_id}",
            json={"rating": rating},
        )
        mirror("update_instance", instance_id, rating=rating)
        return {"done": True, **plan}

    @mcp.tool(annotations=IDEMPOTENT_WRITE)
    async def set_collection_field(
        release_id: int, instance_id: int, field_id: int, value: str, dry_run: bool = True
    ) -> dict:
        """Set a custom field (e.g. Media Condition) on a collection instance. For dropdown
        fields the value must be one of the options from get_collection_fields. Dry-run by default."""
        app.require_token()
        user = await app.username()
        item = await locate(user, release_id, instance_id)
        names = await field_names(user)
        if field_id not in names:
            raise ToolError(f"Unknown field_id {field_id}. Known fields: {names}")
        before = next((n.get("value") for n in item.get("notes") or [] if n["field_id"] == field_id), None)
        plan = {
            "action": "set_collection_field",
            "release": describe(item),
            "instance_id": instance_id,
            "field": names[field_id],
            "value_before": before,
            "value_after": value,
        }
        if preview := write_gate(dry_run=dry_run, plan=plan):
            return preview
        await app.client.request(
            "POST",
            f"/users/{user}/collection/folders/{item['folder_id']}/releases/{release_id}"
            f"/instances/{instance_id}/fields/{field_id}",
            params={"value": value},
            json={"value": value},
        )
        return {"done": True, **plan, "cache_note": "Run sync_collection(full=true) to refresh fields in the cache."}

    @mcp.tool(annotations=WRITE)
    async def create_folder(name: str, dry_run: bool = True) -> dict:
        """Create a collection folder. Dry-run by default."""
        app.require_token()
        user = await app.username()
        plan = {"action": "create_folder", "name": name}
        if preview := write_gate(dry_run=dry_run, plan=plan):
            return preview
        result = await app.client.request("POST", f"/users/{user}/collection/folders", json={"name": name})
        return {"done": True, **plan, "folder_id": result.get("id")}

    @mcp.tool(annotations=IDEMPOTENT_WRITE)
    async def rename_folder(folder_id: int, name: str, dry_run: bool = True) -> dict:
        """Rename a collection folder (not 0 or 1). Dry-run by default."""
        app.require_token()
        if folder_id in (0, 1):
            raise ToolError("Folders 0 (All) and 1 (Uncategorized) cannot be renamed.")
        user = await app.username()
        plan = {
            "action": "rename_folder",
            "folder_id": folder_id,
            "name_before": await folder_name(user, folder_id),
            "name_after": name,
        }
        if preview := write_gate(dry_run=dry_run, plan=plan):
            return preview
        await app.client.request(
            "POST", f"/users/{user}/collection/folders/{folder_id}", json={"name": name}
        )
        return {"done": True, **plan}

    @mcp.tool(annotations=DESTRUCTIVE)
    async def delete_folder(folder_id: int, dry_run: bool = True, confirm: bool = False) -> dict:
        """Delete an empty collection folder (not 0 or 1). Destructive: needs dry_run=false
        AND confirm=true. Discogs refuses to delete folders that still contain items."""
        app.require_token()
        if folder_id in (0, 1):
            raise ToolError("Folders 0 (All) and 1 (Uncategorized) cannot be deleted.")
        user = await app.username()
        folder = await app.client.get(f"/users/{user}/collection/folders/{folder_id}")
        plan = {
            "action": "delete_folder",
            "folder_id": folder_id,
            "name": folder.get("name"),
            "item_count": folder.get("count"),
        }
        if folder.get("count"):
            plan["warning"] = "Folder is not empty; Discogs will refuse. Move its items first."
        if preview := write_gate(dry_run=dry_run, plan=plan, destructive=True, confirm=confirm):
            return preview
        await app.client.request("DELETE", f"/users/{user}/collection/folders/{folder_id}")
        return {"done": True, **plan}
