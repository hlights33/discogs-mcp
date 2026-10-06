"""Process-wide state shared by every tool: settings, the API client, the cache."""

from __future__ import annotations

from typing import Any

from mcp.server.mcpserver.exceptions import ToolError

from .cache.db import CacheDB
from .client import DiscogsClient
from .config import Settings


class App:
    def __init__(self, settings: Settings | None = None, client: DiscogsClient | None = None):
        self.settings = settings or Settings()
        self.client = client or DiscogsClient(self.settings.token(), self.settings.discogs_base_url)
        self._username = self.settings.discogs_username
        self._cache: CacheDB | None = None
        self.background: dict[str, Any] = {}

    def require_token(self) -> None:
        if not self.client.has_token:
            raise ToolError(
                "DISCOGS_TOKEN is not set. Generate a personal access token at "
                "https://www.discogs.com/settings/developers and add it to the server's env."
            )

    async def username(self) -> str:
        if self._username:
            return self._username
        self.require_token()
        identity = await self.client.get("/oauth/identity")
        self._username = identity["username"]
        return self._username

    @property
    def cache(self) -> CacheDB:
        if self._cache is None:
            self._cache = CacheDB(self.settings.cache_path)
        return self._cache

    async def aclose(self) -> None:
        for task in self.background.values():
            if hasattr(task, "cancel"):
                task.cancel()
        await self.client.aclose()
        if self._cache is not None:
            self._cache.close()


def write_gate(
    *, dry_run: bool, plan: dict[str, Any], destructive: bool = False, confirm: bool = False
) -> dict[str, Any] | None:
    """Return a preview when dry_run is set; enforce confirm for destructive writes.

    Returns None when the caller should go ahead and perform the write.
    """
    if dry_run:
        step = "Call again with dry_run=false" + (" and confirm=true" if destructive else "")
        return {"dry_run": True, "would": plan, "next_step": f"{step} to apply this change."}
    if destructive and not confirm:
        raise ToolError(
            "This is a destructive change and needs confirm=true (with dry_run=false). "
            f"Planned change: {plan}"
        )
    return None
