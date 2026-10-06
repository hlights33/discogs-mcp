"""MCP resources and prompts."""

from __future__ import annotations

import json

from mcp.server.mcpserver import MCPServer

from .cache import sync
from .state import App


def register(mcp: MCPServer, app: App) -> None:
    @mcp.resource(
        "discogs://collection/summary",
        name="collection_summary",
        description="Latest collection summary from the local cache (genres, styles, labels, decades, duplicates).",
        mime_type="application/json",
    )
    def collection_summary_resource() -> str:
        if not app.settings.cache_path.exists():
            return json.dumps({"error": "Cache is empty. Run the sync_collection tool first."})
        return json.dumps(sync.collection_summary(app.cache), ensure_ascii=False, indent=2)

    @mcp.prompt(description="Identify exactly which pressing of a record you have.")
    def identify_pressing(
        artist: str = "",
        title: str = "",
        barcode: str = "",
        matrix_runout: str = "",
        label_details: str = "",
    ) -> str:
        clues = "\n".join(
            f"- {name}: {value}"
            for name, value in [
                ("Artist", artist),
                ("Title", title),
                ("Barcode", barcode),
                ("Matrix / runout etching", matrix_runout),
                ("Label, catalog number, country, other print details", label_details),
            ]
            if value
        ) or "- (none yet: ask me for the barcode, runout etchings and label details)"
        return f"""Help me identify exactly which pressing (Discogs release) of a record I have.

Clues:
{clues}

Work through it like this:
1. If there is a barcode, call `search` with `barcode` first; it often pins the release directly.
   Otherwise `search` with artist + release_title (type="master") to find the master.
2. Call `get_master_versions` for that master, filtering by format/country/label where the
   clues allow, to list candidate pressings.
3. For the most likely candidates, call `get_release` and compare the `identifiers` (barcode,
   matrix/runout, rights society, pressing plant), label/catalog number, and notes against my clues.
   Matrix/runout text is the strongest evidence; normalize spaces and dashes when comparing.
4. Tell me the best match with its release id and Discogs URL, how confident you are, and
   which details would distinguish it from the runner-up if it's ambiguous.
5. Then check `find_in_collection` to see if I already own it, and offer `get_release_stats`
   / `get_price_suggestions` for its value. Don't add anything to my collection unless I ask."""

    @mcp.prompt(description="Dig for records related to one you love, via label, artists and credits.")
    def crate_dig(release_id: str = "", artist: str = "", title: str = "") -> str:
        target = f"release {release_id}" if release_id else f"{artist} – {title}".strip(" –")
        return f"""Let's crate dig from {target or "a record I'll name"}.

1. Get the release (`get_release`; use `search` first if you only have artist/title).
2. Follow the threads, a few calls each:
   - Label: `get_label_releases` for neighbouring catalog numbers from the same era.
   - Artist: `get_artist` for aliases and groups/members, then `get_artist_releases` for side projects.
   - Credits: producers, engineers, remixers and session players from the release credits;
     look up their other work with `get_artist_releases`.
3. If my collection cache exists, check with `query_collection` which of the suggestions I
   already own or want (match by release id or master id) and leave those out.
4. Give me 8–12 suggestions grouped by thread (label / artist / credit), each with one line on
   why it connects, its year, and its release or master id. Optionally include the lowest price
   via `get_release_stats` for the top picks."""
