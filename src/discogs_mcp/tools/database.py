"""Discogs database lookups: search, releases, masters, artists, labels."""

from __future__ import annotations

from typing import Literal

from mcp.server.mcpserver import MCPServer

from .. import models
from ..state import App
from . import READ

SearchType = Literal["release", "master", "artist", "label"]


def register(mcp: MCPServer, app: App) -> None:
    @mcp.tool(annotations=READ)
    async def search(
        query: str | None = None,
        type: SearchType | None = None,
        artist: str | None = None,
        release_title: str | None = None,
        label: str | None = None,
        catno: str | None = None,
        barcode: str | None = None,
        year: str | None = None,
        format: str | None = None,
        genre: str | None = None,
        style: str | None = None,
        country: str | None = None,
        track: str | None = None,
        page: int = 1,
        per_page: int = 25,
    ) -> dict:
        """Search the Discogs database. Combine free-text `query` with field filters.

        Use barcode or catno to pin down a specific pressing. `year` accepts a year or
        range like "1990-1995". Returns a compact list; fetch details with get_release /
        get_master / get_artist / get_label.
        """
        app.require_token()
        params = {
            "q": query,
            "type": type,
            "artist": artist,
            "release_title": release_title,
            "label": label,
            "catno": catno,
            "barcode": barcode,
            "year": year,
            "format": format,
            "genre": genre,
            "style": style,
            "country": country,
            "track": track,
        }
        items, pagination = await app.client.page(
            "/database/search", "results", page=page, per_page=per_page, params=params
        )
        return {"pagination": pagination, "results": [models.search_result(r) for r in items]}

    @mcp.tool(annotations=READ)
    async def get_release(release_id: int, include_images: bool = False) -> dict:
        """Full detail for one release (a specific pressing): tracklist, credits, labels
        and catalog numbers, formats, identifiers (barcodes, matrix/runout etchings),
        notes, community have/want and rating."""
        data = await app.client.get(f"/releases/{release_id}")
        return models.release_detail(data, keep_images=include_images)

    @mcp.tool(annotations=READ)
    async def get_master(master_id: int, include_images: bool = False) -> dict:
        """A master release: the umbrella over every version/pressing of an album."""
        data = await app.client.get(f"/masters/{master_id}")
        return models.master_detail(data, keep_images=include_images)

    @mcp.tool(annotations=READ)
    async def get_master_versions(
        master_id: int,
        format: str | None = None,
        country: str | None = None,
        year: str | None = None,
        label: str | None = None,
        sort: Literal["released", "title", "format", "label", "catno", "country"] | None = None,
        sort_order: Literal["asc", "desc"] = "asc",
        page: int = 1,
        per_page: int = 50,
    ) -> dict:
        """List every version (pressing) of a master, with label, catno, country and year.

        Filter by format (e.g. "Vinyl"), country, year (`released`) or label. Use it for
        "which pressing is this?" then confirm with get_release identifiers.
        """
        params = {
            "format": format,
            "country": country,
            "released": year,
            "label": label,
            "sort": sort,
            "sort_order": sort_order if sort else None,
        }
        items, pagination = await app.client.page(
            f"/masters/{master_id}/versions", "versions", page=page, per_page=per_page, params=params
        )
        return {"pagination": pagination, "versions": [models.version_summary(v) for v in items]}

    @mcp.tool(annotations=READ)
    async def get_artist(artist_id: int, include_images: bool = False) -> dict:
        """Artist profile, real name, aliases, name variations, groups and members."""
        data = await app.client.get(f"/artists/{artist_id}")
        return models.artist_detail(data, keep_images=include_images)

    @mcp.tool(annotations=READ)
    async def get_artist_releases(
        artist_id: int,
        sort: Literal["year", "title", "format"] = "year",
        sort_order: Literal["asc", "desc"] = "asc",
        page: int = 1,
        per_page: int = 50,
    ) -> dict:
        """An artist's discography (masters and releases, including appearances with roles)."""
        items, pagination = await app.client.page(
            f"/artists/{artist_id}/releases",
            "releases",
            page=page,
            per_page=per_page,
            params={"sort": sort, "sort_order": sort_order},
        )
        return {"pagination": pagination, "releases": [models.discography_item(r) for r in items]}

    @mcp.tool(annotations=READ)
    async def get_label(label_id: int, include_images: bool = False) -> dict:
        """Label profile including parent label and sublabels."""
        data = await app.client.get(f"/labels/{label_id}")
        return models.label_detail(data, keep_images=include_images)

    @mcp.tool(annotations=READ)
    async def get_label_releases(label_id: int, page: int = 1, per_page: int = 50) -> dict:
        """Releases on a label, with catalog numbers."""
        items, pagination = await app.client.page(
            f"/labels/{label_id}/releases", "releases", page=page, per_page=per_page
        )
        return {"pagination": pagination, "releases": [models.discography_item(r) for r in items]}
