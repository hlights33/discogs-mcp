# discogs-mcp

An [MCP](https://modelcontextprotocol.io) server that gives Claude full access to
[Discogs](https://www.discogs.com): database lookups, your collection and wantlist (read
and write), marketplace prices, and a local SQLite cache so Claude can analyse the whole
collection without paging through the API.

- **Database**: search; releases, masters and their versions; artists; labels.
- **Collection and wantlist**: read, add, move, rate, set fields, remove. Every write is a
  dry run until you confirm it.
- **Marketplace**: lowest price and number for sale, suggested prices by condition, and
  collection value.
- **Local cache**: `sync_collection` once, then use read-only SQL (`query_collection`) and
  `collection_summary`.
- **Digital library matching** (optional): scan your audio files' tags, match the albums to
  Discogs releases, and find gaps between your vinyl and your digital copies. It never
  writes tags.
- Runs over stdio for Claude Desktop, or over streamable HTTP with bearer auth.

## Setup

1. **Install [uv](https://docs.astral.sh/uv/)** and clone the repo:

   ```sh
   git clone https://github.com/hlights33/discogs-mcp.git
   cd discogs-mcp
   uv sync                 # add --extra local for the digital-library tools
   ```

2. **Get a Discogs token.** On discogs.com, go to Settings → Developers → *Generate new
   token*. This is a personal access token. Treat it like a password. The server never
   logs it.

3. **Configure it.** The server reads these environment variables (a `.env` file in the
   working directory also works):

   | Variable | Required | Default | Purpose |
   |---|---|---|---|
   | `DISCOGS_TOKEN` | yes | | Personal access token |
   | `DISCOGS_USERNAME` | no | auto-detected via `/oauth/identity` | Your username |
   | `DISCOGS_CACHE_PATH` | no | `~/.discogs-mcp/cache.db` | SQLite cache location |
   | `DISCOGS_ENABLE_LOCAL` | no | `false` | Register the digital-library tools |
   | `MCP_AUTH_TOKEN` | for remote HTTP | | Bearer token for `--http` |

4. **Check it** with the MCP Inspector:

   ```sh
   DISCOGS_TOKEN=... npx @modelcontextprotocol/inspector uv run discogs-mcp
   ```

## Claude Desktop

Add the server to `claude_desktop_config.json` (Settings → Developer → Edit Config):

```json
{
  "mcpServers": {
    "discogs": {
      "command": "uv",
      "args": ["--directory", "/ABSOLUTE/PATH/discogs-mcp", "run", "discogs-mcp"],
      "env": { "DISCOGS_TOKEN": "…", "DISCOGS_USERNAME": "…" }
    }
  }
}
```

Restart Claude Desktop. If `uv` isn't found, use its absolute path (`which uv`). To turn on
the local-library tools, add `"DISCOGS_ENABLE_LOCAL": "1"` to `env` and run
`uv sync --extra local` once.

**Claude Code:** `claude mcp add discogs -e DISCOGS_TOKEN=... -- uv --directory /ABSOLUTE/PATH/discogs-mcp run discogs-mcp`

## Things to ask

- *"Sync my Discogs collection, then tell me which labels I own the most of."*
- *"What's in my collection from Warp Records?"* This is answered from the cache with no
  API paging.
- *"Which pressing of Selected Ambient Works 85–92 do I have, and what's it worth?"*
- *"Styles by decade across my collection."*
- *"Add release 1234 to my wantlist."* Claude shows a preview first, and only applies the
  change after you agree.
- Use the **identify_pressing** prompt with a barcode or runout etching, or **crate_dig**
  from a record you love.

## Write safety

- Every write tool takes `dry_run` (default `true`) and returns a preview of the change,
  with the release name, the folder, and the before and after values. Nothing changes until
  the tool is called again with `dry_run=false`.
- Destructive tools (`remove_from_collection`, `remove_from_wantlist`, `delete_folder`)
  also need `confirm=true`.
- Tools carry MCP annotations (`readOnlyHint`, `destructiveHint`, `idempotentHint`), so
  clients such as Claude Desktop can ask for approval as appropriate.

## Local cache

`sync_collection` copies your folders, collection items (with custom fields such as media
and sleeve condition) and wantlist into SQLite. It takes about 1 request per 100 items, so
a collection of 1,000 records syncs in about 15 requests.

- **Incremental** (the default) fetches only items added since the last sync.
  `full=true` re-reads everything and also picks up removals, folder moves and edits made
  on the website. If the cached count no longer matches Discogs, the result warns you.
- **`sync_release_details`** fetches each release's tracklist and country. It costs one
  request per release, so it runs in the background, keeps headroom in the rate limit for
  your other requests, and resumes where it stopped. Check progress with `sync_status`.
- **`query_collection(sql)`** runs read-only SQL with a 500-row cap. The connection is
  opened `mode=ro`, only a single `SELECT`/`WITH` statement is allowed, and a SQLite
  authorizer denies anything that isn't a read, including `ATTACH`, `PRAGMA` and temp
  tables. The cache contains:

  | Table or view | Columns |
  |---|---|
  | `collection` (view) | instance_id, folder, rating, date_added, fields, release_id, master_id, artist, title, year, labels, catnos, format, formats, genres, styles, country |
  | `wants` (view) | release_id, rating, notes, date_added, plus the release columns |
  | `releases` | the release columns above, plus `details_fetched_at` |
  | `release_labels` | release_id, label_id, name, catno |
  | `item_fields` | instance_id, field_id, name, value |
  | `tracks` | release_id, position, title, artists, duration, seq |
  | `folders`, `collection_items`, `wantlist`, `sync_meta` | |
  | `local_files`, `local_matches` | created by the local-library tools |

  `labels`, `catnos`, `genres` and `styles` are JSON arrays. Use `json_each` to query them:

  ```sql
  SELECT s.value AS style, (year/10)*10 AS decade, COUNT(*) AS n
  FROM collection, json_each(collection.styles) s
  WHERE year IS NOT NULL GROUP BY 1, 2 ORDER BY n DESC;
  ```
- **`collection_summary`** returns counts by genre, style, label, artist, decade, format
  and country, plus duplicates (the same master owned more than once). The same data is
  available as the resource `discogs://collection/summary`.
- **`export_collection(format="csv"|"json", path)`** writes the cached collection to a file.

## Digital library matching (optional)

Turn it on with `DISCOGS_ENABLE_LOCAL=1` and `uv sync --extra local` (this installs
mutagen and rapidfuzz).

1. `scan_local_library(path)` reads tags from FLAC, MP3, M4A/ALAC, Ogg, Opus, AIFF, WAV and
   other formats: artist, album artist, album, year, label, catalog number, barcode and
   `DISCOGS_RELEASE_ID`. Files that haven't changed are skipped on later scans.
2. `match_local_to_discogs(strategy="cache_first")` groups files into albums. An album
   with a `DISCOGS_RELEASE_ID` tag matches directly. Other albums are fuzzy-matched on
   normalized artist and title against your synced collection, and weak matches fall back
   to Discogs search, capped by `max_api_lookups`. The result gives a confidence score and
   the top 3 candidates for each album that needs review. The full mapping is saved to
   `local_matches`.
3. `export_local_matches(path)` writes the mapping as CSV (folder, artist, album, release
   id, confidence, URL), ready to review or to use for retagging in a tool such as Yate.
4. `find_gaps(physical_format="Vinyl")` lists records you own on vinyl with no digital
   copy, and digital albums with no vinyl copy. It compares by master, so a different
   pressing still counts as a copy.

These tools only read your audio files and never write tags.

## Remote HTTP (custom connector)

```sh
uv run discogs-mcp --http --port 8765                                     # http://127.0.0.1:8765/mcp
MCP_AUTH_TOKEN=$(openssl rand -hex 32) uv run discogs-mcp --http --host 0.0.0.0
```

When `MCP_AUTH_TOKEN` is set, every request must send `Authorization: Bearer <token>`. The
server refuses to bind to anything other than localhost without a token. On localhost,
DNS-rebinding protection is on.

**Keep it local** unless you put it behind a tunnel that adds authentication (for example
Cloudflare Access or Tailscale). Anyone who can reach the server can act on your Discogs
account.

## Rate limits and errors

Discogs allows 60 authenticated requests per minute. The client reads
`X-Discogs-Ratelimit-Remaining` and slows down before it reaches the limit. On a 429 or 5xx
response it backs off with jitter and tries up to 3 times. Errors come back as clear
messages:

| Status | Meaning |
|---|---|
| 401 | Bad token |
| 404 | Not found |
| 403 on price suggestions | Discogs only returns price suggestions to accounts with seller settings filled in |

Responses are trimmed before they reach Claude: resource URLs are removed, and so are image
URIs unless you pass `include_images=true`.

## Tool reference

| Area | Tools |
|---|---|
| Database | `search`, `get_release`, `get_master`, `get_master_versions`, `get_artist`, `get_artist_releases`, `get_label`, `get_label_releases` |
| User | `whoami`, `get_user_lists`, `get_list` |
| Marketplace | `get_release_stats`, `get_price_suggestions`, `get_collection_value` |
| Collection (read) | `list_collection_folders`, `get_collection`, `find_in_collection`, `get_collection_fields` |
| Collection (write ✍️) | `add_to_collection`, `move_collection_item`, `rate_collection_item`, `set_collection_field`, `create_folder`, `rename_folder`, `remove_from_collection` ⚠️, `delete_folder` ⚠️ |
| Wantlist | `get_wantlist`, `add_to_wantlist` ✍️, `remove_from_wantlist` ⚠️ |
| Cache | `sync_collection`, `sync_release_details`, `sync_status`, `query_collection`, `collection_summary`, `export_collection` |
| Local (optional) | `scan_local_library`, `match_local_to_discogs`, `export_local_matches`, `find_gaps` |
| Prompts | `identify_pressing`, `crate_dig` |
| Resources | `discogs://collection/summary` |

✍️ dry run by default · ⚠️ destructive, also needs `confirm=true`

## Development

```sh
uv sync --all-extras
uv run pytest                          # unit tests, with HTTP mocked by respx
DISCOGS_TOKEN=... uv run pytest -m live  # smoke tests against the real API
```

The code is laid out as follows:

| Path | Contents |
|---|---|
| `src/discogs_mcp/server.py` | App setup and transports |
| `client.py` | Rate limiting, retries, pagination and errors |
| `models.py` | Response trimming |
| `tools/` | One module per area |
| `cache/` | SQLite schema, sync logic and the read-only query guard |
| `local/` | Tag scanning and matching |

Built on the official MCP Python SDK (v2 `MCPServer`), with httpx and pydantic.

## License

MIT
