"""Server entry point: builds the MCPServer, registers tools, picks a transport."""

from __future__ import annotations

import argparse
import hmac
import logging
import sys

from mcp.server.mcpserver import MCPServer

from . import __version__, prompts
from .config import Settings
from .state import App
from .tools import cache, collection, database, marketplace, user, wantlist

INSTRUCTIONS = """Discogs: music database lookups plus the user's own collection, wantlist and
marketplace prices.
- For questions across the whole collection (counts, labels, styles, "what do I own from X"),
  use the local cache: sync_collection once, then query_collection / collection_summary.
  Do not page through get_collection for that.
- Every write tool defaults to dry_run=true and returns a preview; show it to the user and only
  re-run with dry_run=false after they agree. Removals/deletions also need confirm=true.
- Discogs allows 60 requests/minute; prefer the cache and avoid needless calls."""

LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}


def build_server(settings: Settings | None = None, app: App | None = None) -> tuple[MCPServer, App]:
    app = app or App(settings)
    mcp = MCPServer("discogs", instructions=INSTRUCTIONS, version=__version__)
    for module in (database, user, marketplace, collection, wantlist, cache):
        module.register(mcp, app)
    if app.settings.enable_local_library:
        from .tools import local

        try:
            local.register(mcp, app)
        except ImportError as exc:  # pragma: no cover - depends on installed extras
            logging.getLogger(__name__).warning(
                "Local library tools disabled: install the `local` extra (%s)", exc
            )
    prompts.register(mcp, app)
    return mcp, app


class BearerAuth:
    """ASGI middleware requiring `Authorization: Bearer <token>` on every HTTP request."""

    def __init__(self, inner, token: str):
        self.inner = inner
        self.expected = f"Bearer {token}".encode()

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            supplied = dict(scope.get("headers") or []).get(b"authorization", b"")
            if not hmac.compare_digest(supplied, self.expected):
                await send(
                    {
                        "type": "http.response.start",
                        "status": 401,
                        "headers": [(b"content-type", b"application/json"),
                                    (b"www-authenticate", b"Bearer")],
                    }
                )
                await send({"type": "http.response.body", "body": b'{"error":"unauthorized"}'})
                return
        await self.inner(scope, receive, send)


def http_app(mcp: MCPServer, settings: Settings, host: str):
    starlette_app = mcp.streamable_http_app(host=host)
    token = settings.mcp_auth_token.get_secret_value() if settings.mcp_auth_token else None
    if host not in LOCAL_HOSTS and not token:
        raise SystemExit(
            f"Refusing to listen on {host} without MCP_AUTH_TOKEN. Set a bearer token, or bind "
            "to 127.0.0.1 and tunnel with authentication."
        )
    return BearerAuth(starlette_app, token) if token else starlette_app


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="discogs-mcp", description="Discogs MCP server")
    parser.add_argument("--http", action="store_true", help="serve streamable HTTP instead of stdio")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--version", action="version", version=__version__)
    args = parser.parse_args(argv)

    # stdout carries the stdio protocol, so logs must go to stderr.
    logging.basicConfig(level=logging.INFO, stream=sys.stderr,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)  # request lines are noise

    settings = Settings()
    mcp, _app = build_server(settings)
    if args.http:
        import uvicorn

        uvicorn.run(http_app(mcp, settings, args.host), host=args.host, port=args.port, log_level="info")
    else:
        mcp.run("stdio")


if __name__ == "__main__":
    main()
