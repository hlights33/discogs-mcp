"""Local cache tools: sync, read-only SQL, summary, export."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, Literal

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from ..cache import sync
from ..cache.query import QueryError, run_readonly
from ..state import App
from . import LOCAL_READ, LOCAL_WRITE

SCHEMA_HELP = """
Tables and views (SQLite):
- collection (view): instance_id, folder_id, folder, rating, date_added, fields, release_id,
  master_id, artist, title, year, labels, catnos, format, formats, genres, styles, country
- wants (view): release_id, rating, notes, date_added, master_id, artist, title, year, labels,
  catnos, format, formats, genres, styles, country
- releases: id, master_id, artist, title, year, labels, catnos, format, formats, genres, styles,
  country, details_fetched_at
- release_labels: release_id, label_id, name, catno   (one row per label on a release)
- collection_items: instance_id, release_id, folder_id, rating, date_added, fields
- item_fields: instance_id, field_id, name, value   (e.g. name='Media Condition')
- folders: id, name, count
- tracks: release_id, position, title, artists, duration, seq   (needs sync_release_details)
- sync_meta: key, value
labels, catnos, genres and styles are JSON arrays: use json_each, e.g.
  SELECT s.value AS style, COUNT(*) FROM collection, json_each(collection.styles) s GROUP BY 1
fields is a JSON object: json_extract(fields, '$."Media Condition"').
country and tracks are only populated after sync_release_details.
""".strip()


def register(mcp: MCPServer, app: App) -> None:
    @mcp.tool(annotations=LOCAL_WRITE)
    async def sync_collection(full: bool = False) -> dict:
        """Copy the collection, folders and wantlist into the local SQLite cache.

        Incremental (default) fetches only items added since the last sync. full=true
        re-reads everything and also catches removals, folder moves and edits. Run this
        before using query_collection or collection_summary. Tracklists and countries
        come from sync_release_details.
        """
        app.require_token()
        user = await app.username()
        return await sync.sync_collection(app.client, app.cache, user, full=full)

    @mcp.tool(annotations=LOCAL_WRITE)
    async def sync_release_details(
        max_releases: int | None = None, background: bool = True
    ) -> dict:
        """Fetch full release detail (tracklist, country) for cached releases that lack it.

        Costs one API request per release (Discogs allows 60/min), so by default it runs in
        the background and returns immediately; check progress with sync_status. It is
        resumable: re-running continues where it stopped. Leaves headroom in the rate limit
        for other tools.
        """
        app.require_token()
        task = app.background.get("release_details")
        if task is not None and not task.done():
            return {"status": "already running", **app.background.get("release_details_progress", {})}
        pending = sync.missing_details_count(app.cache)
        if pending == 0:
            return {"status": "nothing to do", "releases_missing_details": 0}
        progress: dict[str, Any] = {}
        app.background["release_details_progress"] = progress
        coro = sync.fetch_release_details(
            app.client, app.cache, max_releases=max_releases, progress=progress
        )
        if not background:
            return await coro
        app.background["release_details"] = asyncio.create_task(coro)
        todo = min(pending, max_releases or pending)
        return {
            "status": "started in background",
            "releases_to_fetch": todo,
            "estimated_minutes": round(todo / 50, 1),
            "next_step": "Call sync_status to check progress.",
        }

    @mcp.tool(annotations=LOCAL_READ)
    async def sync_status() -> dict:
        """Last sync times, cache counts and progress of any background detail fetch."""
        db = app.cache
        task = app.background.get("release_details")
        progress = dict(app.background.get("release_details_progress", {}))
        if task is not None and task.done() and task.exception():
            progress["state"] = f"failed: {task.exception()}"
        return {
            "cache_path": str(app.settings.cache_path),
            "username": db.get_meta("username"),
            "last_sync": db.get_meta("collection_synced_at"),
            "last_full_sync": db.get_meta("full_synced_at"),
            "collection_items": db.conn.execute("SELECT COUNT(*) FROM collection_items").fetchone()[0],
            "wantlist_items": db.conn.execute("SELECT COUNT(*) FROM wantlist").fetchone()[0],
            "releases_missing_details": sync.missing_details_count(db),
            "release_details_job": progress or None,
            "rate_limit_remaining": app.client.limiter.remaining,
        }

    @mcp.tool(annotations=LOCAL_READ, description=(
        "Run a read-only SQL SELECT against the local collection cache (max 500 rows). "
        "Use it to answer any question across the whole collection without API calls.\n\n"
        + SCHEMA_HELP
    ))
    async def query_collection(sql: str) -> dict:
        try:
            return run_readonly(app.settings.cache_path, sql)
        except QueryError as exc:
            raise ToolError(str(exc)) from exc

    @mcp.tool(annotations=LOCAL_READ)
    async def collection_summary(top: int = 15) -> dict:
        """Collection overview from the cache: counts by genre, style, label, artist, decade,
        format and country, duplicate masters (same album owned more than once), and the
        last sync time."""
        if not app.settings.cache_path.exists():
            raise ToolError("The cache is empty. Run sync_collection first.")
        return sync.collection_summary(app.cache, top=top)

    @mcp.tool(annotations=LOCAL_WRITE)
    async def export_collection(
        format: Literal["csv", "json"] = "csv", path: str | None = None
    ) -> dict:
        """Write the cached collection to a CSV or JSON file (default ~/.discogs-mcp/collection.<fmt>)."""
        if not app.settings.cache_path.exists():
            raise ToolError("The cache is empty. Run sync_collection first.")
        target = Path(path) if path else app.settings.cache_path.parent / f"collection.{format}"
        return sync.export_collection(app.cache, format, target)
