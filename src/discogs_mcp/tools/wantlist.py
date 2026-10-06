"""Wantlist reads and writes. Writes default to dry_run=True."""

from __future__ import annotations

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from .. import models
from ..state import App, write_gate
from . import DESTRUCTIVE, IDEMPOTENT_WRITE, READ


def register(mcp: MCPServer, app: App) -> None:
    async def current_want(user: str, release_id: int, *, page_api: bool = True) -> dict | None:
        """Wantlist entry for a release, or None. Discogs has no single-item GET, so check
        the cache first and only page through the wantlist when needed."""
        if app.settings.cache_path.exists() and app.cache.get_meta("username") == user:
            row = app.cache.conn.execute(
                "SELECT artist, title, notes, date_added FROM wants WHERE release_id=?", (release_id,)
            ).fetchone()
            if row:
                return {"release": f"{row['artist']} – {row['title']}", "notes": row["notes"],
                        "date_added": row["date_added"]}
        if not page_api:
            return None
        async for want in app.client.paginate(f"/users/{user}/wants", "wants"):
            if want["id"] == release_id:
                info = want.get("basic_information") or {}
                return {
                    "release": f"{models.artist_credit(info.get('artists'))} – {info.get('title')}",
                    "notes": want.get("notes"),
                    "date_added": want.get("date_added"),
                }
        return None

    @mcp.tool(annotations=READ)
    async def get_wantlist(page: int = 1, per_page: int = 50, username: str | None = None) -> dict:
        """One page of the wantlist. For whole-wantlist questions, sync_collection and then
        query the `wants` view with query_collection."""
        user = username or await app.username()
        items, pagination = await app.client.page(
            f"/users/{user}/wants", "wants", page=page, per_page=per_page
        )
        return {
            "pagination": pagination,
            "wants": [
                models.strip_noise(
                    {
                        **models.basic_release(w.get("basic_information") or {}),
                        "rating": w.get("rating") or None,
                        "notes": w.get("notes"),
                        "date_added": w.get("date_added"),
                    }
                )
                for w in items
            ],
        }

    @mcp.tool(annotations=IDEMPOTENT_WRITE)
    async def add_to_wantlist(
        release_id: int, notes: str | None = None, rating: int | None = None, dry_run: bool = True
    ) -> dict:
        """Add a release to the wantlist, or update notes/rating if it's already there.

        Dry-run by default: returns a preview. Call again with dry_run=false to apply.
        """
        app.require_token()
        if rating is not None and not 0 <= rating <= 5:
            raise ToolError("rating must be between 0 and 5.")
        user = await app.username()
        release = await app.client.get(f"/releases/{release_id}")
        existing = await current_want(user, release_id, page_api=not app.settings.cache_path.exists())
        plan = models.strip_noise(
            {
                "action": "update_wantlist_item" if existing else "add_to_wantlist",
                "release": f"{models.artist_credit(release.get('artists'))} – {release.get('title')}",
                "release_id": release_id,
                "format": models.format_summary(release.get("formats")),
                "year": release.get("year"),
                "notes": notes,
                "rating": rating,
                "already_in_wantlist": bool(existing),
            }
        )
        if preview := write_gate(dry_run=dry_run, plan=plan):
            return preview
        body = {k: v for k, v in {"notes": notes, "rating": rating}.items() if v is not None}
        await app.client.request("PUT", f"/users/{user}/wants/{release_id}", json=body or None)
        return {"done": True, **plan, "cache_note": "Run sync_collection to refresh the cached wantlist."}

    @mcp.tool(annotations=DESTRUCTIVE)
    async def remove_from_wantlist(release_id: int, dry_run: bool = True, confirm: bool = False) -> dict:
        """Remove a release from the wantlist. Destructive: needs dry_run=false AND confirm=true."""
        app.require_token()
        user = await app.username()
        existing = await current_want(user, release_id)
        if existing is None:
            raise ToolError(f"Release {release_id} is not in {user}'s wantlist.")
        plan = models.strip_noise(
            {
                "action": "remove_from_wantlist",
                "release": existing["release"],
                "release_id": release_id,
                "notes": existing.get("notes"),
                "date_added": existing.get("date_added"),
            }
        )
        if preview := write_gate(dry_run=dry_run, plan=plan, destructive=True, confirm=confirm):
            return preview
        await app.client.request("DELETE", f"/users/{user}/wants/{release_id}")
        if app.settings.cache_path.exists():
            app.cache.delete_want(release_id)
        return {"done": True, **plan}
