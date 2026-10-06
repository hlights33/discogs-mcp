# Discogs MCP Connector — Build Plan for Claude Code

## Goal
Build a local MCP server, written in Python, that gives Claude full access to Discogs: database lookups, my collection and wantlist (read and write), marketplace pricing data, and a local cache for fast analysis across the whole collection. It runs in Claude Desktop over stdio, and can optionally be served over HTTP so it can be added as a remote custom connector later.

## Stack
- Python 3.12+, managed with `uv`
- `mcp` official Python SDK (FastMCP)
- `httpx` (async) for the Discogs REST API. Use the raw API rather than `python3-discogs-client` for full control over rate limits and pagination.
- `pydantic` for models and settings
- SQLite (via `sqlite3` or `sqlmodel`) for the local cache
- `rapidfuzz` for fuzzy matching
- `mutagen` (optional module) for reading tags from local audio files
- `pytest` + `respx` for tests

## Auth & config
- Use a Discogs **personal access token** (Settings → Developers → Generate token).
- Config is read from environment variables: `DISCOGS_TOKEN`, `DISCOGS_USERNAME` (auto-detect via `/oauth/identity` if unset), `DISCOGS_CACHE_PATH` (default `~/.discogs-mcp/cache.db`).
- Every request sends `Authorization: Discogs token=...` and a descriptive `User-Agent` (Discogs requires one).
- Never log the token.

## API client requirements
- Base URL: `https://api.discogs.com`
- Rate limiting: authenticated requests are capped at 60/min. Read the `X-Discogs-Ratelimit-Remaining` header and throttle proactively. On a 429, back off and retry with jitter (maximum 3 tries).
- Pagination helper: `per_page=100`, and follow `pagination.urls.next`. Support an optional `limit` cap.
- Errors map to clear messages: 401 means a bad token, 404 means not found, and 403 marketplace errors are explained.
- Responses are trimmed before returning to Claude. Strip `resource_url`, image URIs (unless requested), and other noise to keep the context small.

## Tools

### Database (read)
| Tool | Endpoint | Notes |
|---|---|---|
| `search` | `GET /database/search` | Params: query, type (release/master/artist/label), artist, release_title, label, catno, barcode, year, format, genre, style, country, track. Returns a compact list. |
| `get_release` | `GET /releases/{id}` | Tracklist, credits, labels and catalog numbers, formats, identifiers (barcodes and matrix numbers), notes, community have/want and rating. |
| `get_master` | `GET /masters/{id}` | |
| `get_master_versions` | `GET /masters/{id}/versions` | Filters: format, country, year, label. Useful for "which pressing is this". |
| `get_artist` | `GET /artists/{id}` | Profile, aliases, groups, members. |
| `get_artist_releases` | `GET /artists/{id}/releases` | Sort and paginate. |
| `get_label` | `GET /labels/{id}` | Including sublabels and parent label. |
| `get_label_releases` | `GET /labels/{id}/releases` | |

### Marketplace (read)
| Tool | Endpoint |
|---|---|
| `get_release_stats` | `GET /marketplace/stats/{release_id}`: lowest price, number for sale |
| `get_price_suggestions` | `GET /marketplace/price_suggestions/{release_id}`: price by condition. This requires seller settings on the account, so handle a 403 gracefully. |
| `get_collection_value` | `GET /users/{u}/collection/value`: min, median, and max |

### Collection
| Tool | Endpoint | Write? |
|---|---|---|
| `list_collection_folders` | `GET /users/{u}/collection/folders` | |
| `get_collection` | `GET /users/{u}/collection/folders/{folder_id}/releases` | Folder 0 = all. Sort by added/artist/title/year/rating. |
| `find_in_collection` | `GET /users/{u}/collection/releases/{release_id}` | |
| `get_collection_fields` | `GET /users/{u}/collection/fields` | Custom fields such as media/sleeve condition and notes. |
| `add_to_collection` | `POST .../folders/{f}/releases/{release_id}` | ✍️ |
| `remove_from_collection` | `DELETE .../instances/{instance_id}` | ✍️ destructive |
| `move_collection_item` / `rate_collection_item` | `POST .../instances/{instance_id}` | ✍️ |
| `set_collection_field` | `POST .../instances/{i}/fields/{field_id}` | ✍️ |
| `create_folder` / `rename_folder` / `delete_folder` | folders endpoints | ✍️ |

### Wantlist
| Tool | Endpoint | Write? |
|---|---|---|
| `get_wantlist` | `GET /users/{u}/wants` | |
| `add_to_wantlist` | `PUT /users/{u}/wants/{release_id}` (notes, rating) | ✍️ |
| `remove_from_wantlist` | `DELETE /users/{u}/wants/{release_id}` | ✍️ destructive |

### User
- `whoami`: `GET /oauth/identity` plus the profile.
- `get_user_lists`, `get_list`: `/users/{u}/lists`, `/lists/{id}`.

### Write safety
- Every write tool takes a `dry_run: bool = True` argument. When `dry_run` is true, the tool returns what it would change, and only executes when called again with `dry_run=false`.
- Destructive tools (remove/delete) also require `confirm: true`.
- Mark tools with MCP annotations (`readOnlyHint`, `destructiveHint`) so Claude Desktop prompts appropriately.

## Local cache & analytics (the high-value part)
Pulling a large collection through tool calls every time is slow and burns context. So:

- `sync_collection(full: bool=False)`: pages through the full collection and wantlist and stores them in SQLite. Tables: `releases` (id, master_id, artist, title, year, labels, catnos, formats, genres, styles, country), `collection_items` (instance_id, release_id, folder_id, rating, date_added, custom fields), `wantlist`, `tracks` (release_id, position, title, artists, duration), and `sync_meta`. An incremental sync uses `date_added` ordering and stops at already-seen items. Fetching full release detail (tracklists) is optional and rate-limited, running in the background with resume support.
- `query_collection(sql: str)`: **read-only** SQL against the cache. Open the connection in `mode=ro`, allow only SELECT, and cap rows at 500. This lets Claude answer arbitrary questions ("labels I own the most of", "styles by decade").
- `collection_summary()`: counts by genre, style, label, decade, format, and country, plus duplicates (the same master owned more than once) and the date of the last sync.
- `export_collection(format="csv"|"json", path)`: writes the cache to a file.

## Digital library matching (optional module, behind a flag)
Purpose: cross-reference my local music files with Discogs, for retagging and gap-finding.
- `scan_local_library(path)`: reads tags with mutagen (artist, album, title, year, label, catalog number, `DISCOGS_RELEASE_ID` if present) into a `local_files` table.
- `match_local_to_discogs(strategy="cache_first")`: matches first against my collection cache with rapidfuzz on normalized artist + title, then falls back to `search`. It returns a confidence score and the top 3 candidates. It **never writes tags**. The output is a proposed mapping table that I review, and that I can export as CSV for Yate.
- `find_gaps()`: lists releases I own on vinyl but have no digital copy of, and vice versa.

## MCP resources & prompts
- Resource `discogs://collection/summary`: the latest `collection_summary` output.
- Prompt `identify_pressing`: given a barcode, matrix/runout etch, or label photo details, walks through `search` → `get_master_versions` → `get_release` identifiers.
- Prompt `crate_dig`: suggests releases related to a given record, using label, artist, and credits.

## Project layout
```
discogs-mcp/
  pyproject.toml
  README.md
  src/discogs_mcp/
    server.py          # FastMCP app, tool registration, transport selection
    config.py          # pydantic-settings
    client.py          # httpx client, rate limiter, pagination, errors
    models.py          # trimmed response models
    tools/database.py
    tools/marketplace.py
    tools/collection.py
    tools/wantlist.py
    tools/user.py
    cache/db.py        # schema, migrations
    cache/sync.py
    cache/query.py     # read-only SQL guard
    local/scan.py      # mutagen (optional extra)
    local/match.py
  tests/
```
Optional dependencies: `pip install discogs-mcp[local]` pulls in mutagen and rapidfuzz.

## Transports
- Default: stdio (`uv run discogs-mcp`).
- `--http --port 8765`: streamable HTTP for use as a remote custom connector. If exposed beyond localhost, require a bearer token (`MCP_AUTH_TOKEN`). Document that it should stay local unless it's tunneled with auth.

## Claude Desktop config (put this in the README)
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

## Testing
- Unit tests with `respx`-mocked HTTP for every tool, including pagination, 429 retry, and 403 marketplace handling.
- A test that the SQL guard rejects INSERT/UPDATE/DELETE/ATTACH/PRAGMA writes.
- A test that write tools default to `dry_run=True`.
- A live smoke test (`pytest -m live`, skipped without a token) that runs `whoami`, `search("Aphex Twin Selected Ambient Works")`, `get_release`, and a small `sync_collection`.
- Manual check with the MCP Inspector: `npx @modelcontextprotocol/inspector uv run discogs-mcp`.

## Build order
1. Scaffold the project, config, and client (rate limiting, pagination, errors), with tests.
2. Database tools, then `whoami`. Verify in the Inspector.
3. Collection and wantlist read tools.
4. Cache plus `sync_collection`, `query_collection`, and `collection_summary`.
5. Write tools with dry-run and confirm.
6. Marketplace tools.
7. Resources and prompts.
8. The local-library module.
9. HTTP transport and auth.
10. README covering setup, the token, the Desktop config, and tool reference.

## Acceptance criteria
- Claude Desktop lists the server and its tools with no errors.
- "What's in my collection from Warp Records?" is answered via the cache, without paging through the API.
- "Which pressing of X do I have, and what's it worth?" works end to end.
- Adding or removing wantlist items shows a dry-run preview first.
- A full sync of a collection of around 1k records completes without hitting 429 errors.
