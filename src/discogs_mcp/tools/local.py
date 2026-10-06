"""Digital library matching (optional; enable with DISCOGS_ENABLE_LOCAL=1 and the `local` extra)."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from ..state import App
from . import LOCAL_READ, LOCAL_WRITE, WRITE


def register(mcp: MCPServer, app: App) -> None:
    import mutagen  # noqa: F401  (fail fast if the `local` extra is missing)

    from ..local import match as matcher
    from ..local import scan as scanner

    @mcp.tool(annotations=LOCAL_WRITE)
    async def scan_local_library(path: str, rescan: bool = False) -> dict:
        """Read tags (artist, album, title, year, label, catalog number, barcode,
        DISCOGS_RELEASE_ID) from audio files under `path` into the local cache.
        Read-only on your files. Unchanged files are skipped unless rescan=true."""
        try:
            return scanner.scan(app.cache, Path(path), rescan=rescan)
        except FileNotFoundError as exc:
            raise ToolError(str(exc)) from exc

    @mcp.tool(annotations=WRITE)
    async def match_local_to_discogs(
        strategy: Literal["cache_first", "cache_only", "search_only"] = "cache_first",
        max_api_lookups: int = 25,
        rematch: bool = False,
    ) -> dict:
        """Propose a Discogs release for each scanned local album. Never writes tags.

        cache_first fuzzy-matches against your synced collection, then falls back to Discogs
        search (capped by max_api_lookups) for weak matches. Albums with a DISCOGS_RELEASE_ID
        tag match directly. Returns confidence scores and top-3 candidates for albums that
        need review; the full mapping is saved to the local_matches table.
        """
        client = None if strategy == "cache_only" else app.client
        if client is not None:
            app.require_token()
        try:
            return await matcher.match(
                client, app.cache, strategy=strategy, max_api_lookups=max_api_lookups, rematch=rematch
            )
        except ValueError as exc:
            raise ToolError(str(exc)) from exc

    @mcp.tool(annotations=LOCAL_WRITE)
    async def export_local_matches(path: str | None = None, min_confidence: float = 0.0) -> dict:
        """Export the proposed local→Discogs mapping as CSV (folder, artist, album, release id,
        confidence, URL) for review or for retagging in a tool such as Yate."""
        target = Path(path) if path else app.settings.cache_path.parent / "local_matches.csv"
        return matcher.export_matches(app.cache, target, min_confidence=min_confidence)

    @mcp.tool(annotations=LOCAL_READ)
    async def find_gaps(physical_format: str = "Vinyl", min_confidence: float = 0.9, limit: int = 200) -> dict:
        """Releases you own physically (e.g. on Vinyl) with no digital copy, and local albums
        with no physical copy in your collection. Needs sync_collection, scan_local_library
        and match_local_to_discogs first."""
        return matcher.find_gaps(app.cache, physical_format, min_confidence, limit)
