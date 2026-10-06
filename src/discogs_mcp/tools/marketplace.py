"""Marketplace pricing data (read only)."""

from __future__ import annotations

from mcp.server.mcpserver import MCPServer

from .. import models
from ..state import App
from . import READ

CONDITION_ORDER = [
    "Mint (M)",
    "Near Mint (NM or M-)",
    "Very Good Plus (VG+)",
    "Very Good (VG)",
    "Good Plus (G+)",
    "Good (G)",
    "Fair (F)",
    "Poor (P)",
]


def register(mcp: MCPServer, app: App) -> None:
    @mcp.tool(annotations=READ)
    async def get_release_stats(release_id: int, currency: str | None = None) -> dict:
        """Marketplace snapshot for a release: lowest listed price and number for sale.

        `currency` is an ISO code such as USD, EUR, GBP (defaults to your account currency).
        """
        data = await app.client.get(f"/marketplace/stats/{release_id}", curr_abbr=currency)
        return {
            "release_id": release_id,
            "lowest_price": models.price(data.get("lowest_price")),
            "num_for_sale": data.get("num_for_sale") or 0,
            "blocked_from_sale": data.get("blocked_from_sale", False),
        }

    @mcp.tool(annotations=READ)
    async def get_price_suggestions(release_id: int) -> dict:
        """Suggested sale price for each condition grade, based on Discogs sales history.

        Discogs only returns this for accounts with seller settings filled in; otherwise
        it returns a 403 and this tool explains that.
        """
        app.require_token()
        data = await app.client.get(f"/marketplace/price_suggestions/{release_id}")
        ordered = {c: models.price(data[c]) for c in CONDITION_ORDER if c in data}
        ordered.update({c: models.price(v) for c, v in data.items() if c not in ordered})
        return {"release_id": release_id, "suggestions": ordered}

    @mcp.tool(annotations=READ)
    async def get_collection_value(username: str | None = None) -> dict:
        """Estimated collection value: minimum, median and maximum (based on sales history)."""
        app.require_token()
        user = username or await app.username()
        data = await app.client.get(f"/users/{user}/collection/value")
        return {
            "username": user,
            "minimum": data.get("minimum"),
            "median": data.get("median"),
            "maximum": data.get("maximum"),
        }
