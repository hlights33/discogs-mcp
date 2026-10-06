"""Async Discogs REST client: auth, proactive rate limiting, retries, pagination, error mapping."""

from __future__ import annotations

import asyncio
import logging
import random
import time
from collections.abc import AsyncIterator
from typing import Any

import httpx
from mcp.server.mcpserver.exceptions import ToolError

from . import __version__

log = logging.getLogger(__name__)

USER_AGENT = f"discogs-mcp/{__version__} +https://github.com/hlights33/discogs-mcp"
MAX_TRIES = 3


class DiscogsError(ToolError):
    """An API failure with a message that is safe and useful to show to Claude.

    Subclasses ToolError so that a DiscogsError left uncaught by a tool still
    reaches the model with its real message: MCPServer only passes the message
    of a ToolError/ResourceError through as-is, and turns any other exception
    into a generic "Error executing tool <name>" with the detail withheld.
    """

    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


class RateLimiter:
    """Throttles proactively using X-Discogs-Ratelimit-Remaining.

    Discogs uses a moving 60-second window. When the remaining budget gets low we
    space requests out so the window refills instead of slamming into 429s.
    """

    def __init__(self, limit: int = 60, low_water: int = 5, window: float = 60.0):
        self.limit = limit
        self.low_water = low_water
        self.window = window
        self.remaining: int | None = None
        self._lock = asyncio.Lock()
        self._last = 0.0

    def update(self, headers: httpx.Headers) -> None:
        try:
            if "X-Discogs-Ratelimit" in headers:
                self.limit = int(headers["X-Discogs-Ratelimit"])
            if "X-Discogs-Ratelimit-Remaining" in headers:
                self.remaining = int(headers["X-Discogs-Ratelimit-Remaining"])
        except ValueError:
            pass

    def delay(self) -> float:
        if self.remaining is None or self.remaining > self.low_water:
            return 0.0
        per_request = self.window / max(self.limit, 1)
        if self.remaining <= 0:
            return per_request * 3
        # Pace slightly below the sustained limit so the moving window refills.
        return per_request * 1.2

    async def acquire(self) -> None:
        async with self._lock:
            wait = self.delay() - (time.monotonic() - self._last)
            if wait > 0:
                await asyncio.sleep(wait)
            self._last = time.monotonic()


def _error_for(resp: httpx.Response) -> DiscogsError:
    try:
        detail = resp.json().get("message", "")
    except Exception:
        detail = ""
    path = resp.request.url.path
    status = resp.status_code
    if status == 401:
        msg = "Discogs rejected the token (401). Check DISCOGS_TOKEN is a valid personal access token."
    elif status == 403 and path.startswith("/marketplace/price_suggestions"):
        msg = (
            "Price suggestions are unavailable (403). Discogs only returns them for accounts "
            "that have filled in seller settings (Marketplace → Settings). Use get_release_stats "
            "for the lowest listed price instead."
        )
    elif status == 403 and path.startswith("/marketplace"):
        msg = f"Discogs refused this marketplace request (403): {detail or 'the account may lack marketplace access'}."
    elif status == 403:
        msg = f"Forbidden (403): {detail or 'this resource is private or belongs to another user'}."
    elif status == 404:
        msg = f"Not found (404): {path}" + (f" — {detail}" if detail else "")
    elif status == 422:
        msg = f"Discogs rejected the request (422): {detail or resp.text[:200]}"
    elif status == 429:
        msg = "Discogs rate limit hit (429) and retries were exhausted. Wait a minute and try again."
    else:
        msg = f"Discogs API error {status}: {detail or resp.text[:200]}"
    return DiscogsError(msg, status)


class DiscogsClient:
    def __init__(
        self,
        token: str | None,
        base_url: str = "https://api.discogs.com",
        *,
        rate_limiter: RateLimiter | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        backoff_base: float = 1.0,
    ):
        headers = {"User-Agent": USER_AGENT, "Accept": "application/vnd.discogs.v2.discogs+json"}
        if token:
            headers["Authorization"] = f"Discogs token={token}"
        self._http = httpx.AsyncClient(
            base_url=base_url, headers=headers, timeout=30.0, transport=transport
        )
        self.has_token = bool(token)
        self.limiter = rate_limiter or RateLimiter()
        self.backoff_base = backoff_base

    async def aclose(self) -> None:
        await self._http.aclose()

    async def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
    ) -> Any:
        if params:
            params = {k: v for k, v in params.items() if v is not None}
        for attempt in range(1, MAX_TRIES + 1):
            await self.limiter.acquire()
            try:
                resp = await self._http.request(method, path, params=params, json=json)
            except httpx.TransportError as exc:
                if attempt == MAX_TRIES:
                    raise DiscogsError(f"Network error talking to Discogs: {exc}") from exc
                await asyncio.sleep(self._backoff(attempt))
                continue
            self.limiter.update(resp.headers)
            if resp.status_code == 429 or resp.status_code >= 500:
                if attempt == MAX_TRIES:
                    raise _error_for(resp)
                if resp.status_code == 429:
                    self.limiter.remaining = 0
                wait = self._backoff(attempt)
                log.warning("Discogs %s on %s, retrying in %.1fs", resp.status_code, path, wait)
                await asyncio.sleep(wait)
                continue
            if resp.status_code >= 400:
                raise _error_for(resp)
            if resp.status_code == 204 or not resp.content:
                return {}
            return resp.json()
        raise AssertionError("unreachable")

    def _backoff(self, attempt: int) -> float:
        return self.backoff_base * (2 ** (attempt - 1)) + random.uniform(0, self.backoff_base)

    async def get(self, path: str, **params: Any) -> Any:
        return await self.request("GET", path, params=params)

    async def paginate(
        self, path: str, key: str, *, limit: int | None = None, params: dict[str, Any] | None = None
    ) -> AsyncIterator[dict[str, Any]]:
        """Yield items under `key` across pages, following pagination.urls.next."""
        count = 0
        query: dict[str, Any] | None = {"per_page": 100, **(params or {})}
        url: str | None = path
        while url:
            data = await self.request("GET", url, params=query)
            for item in data.get(key, []):
                yield item
                count += 1
                if limit is not None and count >= limit:
                    return
            url = (data.get("pagination") or {}).get("urls", {}).get("next")
            query = None  # the next URL already carries the query string

    async def collect(
        self, path: str, key: str, *, limit: int | None = None, params: dict[str, Any] | None = None
    ) -> list[dict[str, Any]]:
        return [item async for item in self.paginate(path, key, limit=limit, params=params)]

    async def page(
        self, path: str, key: str, *, page: int = 1, per_page: int = 50, params: dict[str, Any] | None = None
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        """Fetch a single page; returns (items, pagination summary)."""
        data = await self.request(
            "GET", path, params={"page": page, "per_page": min(per_page, 100), **(params or {})}
        )
        pg = data.get("pagination") or {}
        return data.get(key, []), {
            "page": pg.get("page", page),
            "pages": pg.get("pages"),
            "items": pg.get("items"),
            "per_page": pg.get("per_page", per_page),
        }
