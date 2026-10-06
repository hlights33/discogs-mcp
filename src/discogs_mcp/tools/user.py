"""Identity, profile and user lists."""

from __future__ import annotations

from mcp.server.mcpserver import MCPServer

from .. import models
from ..state import App
from . import READ


def register(mcp: MCPServer, app: App) -> None:
    @mcp.tool(annotations=READ)
    async def whoami() -> dict:
        """The authenticated Discogs user: username, profile, collection/wantlist sizes."""
        app.require_token()
        identity = await app.client.get("/oauth/identity")
        profile = await app.client.get(f"/users/{identity['username']}")
        return models.strip_noise(
            {
                "id": identity.get("id"),
                "username": identity.get("username"),
                "name": profile.get("name"),
                "location": profile.get("location"),
                "registered": profile.get("registered"),
                "num_collection": profile.get("num_collection"),
                "num_wantlist": profile.get("num_wantlist"),
                "num_lists": profile.get("num_lists"),
                "currency": profile.get("curr_abbr"),
                "buyer_rating": profile.get("buyer_rating"),
                "seller_rating": profile.get("seller_rating"),
                "releases_contributed": profile.get("releases_contributed"),
                "url": profile.get("uri"),
            },
            keep_images=True,
        )

    @mcp.tool(annotations=READ)
    async def get_user_lists(username: str | None = None, page: int = 1, per_page: int = 50) -> dict:
        """A user's public lists (defaults to you)."""
        user = username or await app.username()
        items, pagination = await app.client.page(
            f"/users/{user}/lists", "lists", page=page, per_page=per_page
        )
        return {
            "pagination": pagination,
            "lists": [
                models.strip_noise(
                    {
                        "id": lst.get("id"),
                        "name": lst.get("name"),
                        "description": lst.get("description"),
                        "public": lst.get("public"),
                        "date_changed": lst.get("date_changed"),
                    }
                )
                for lst in items
            ],
        }

    @mcp.tool(annotations=READ)
    async def get_list(list_id: int) -> dict:
        """Items in a Discogs list, with each item's comment."""
        data = await app.client.get(f"/lists/{list_id}")
        return models.strip_noise(
            {
                "id": data.get("id"),
                "name": data.get("name"),
                "description": data.get("description"),
                "items": [
                    {
                        "type": i.get("type"),
                        "id": i.get("id"),
                        "title": i.get("display_title"),
                        "comment": i.get("comment"),
                    }
                    for i in data.get("items") or []
                ],
            }
        )
