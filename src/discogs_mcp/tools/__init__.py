"""Tool modules. Each exposes register(mcp, app)."""

from mcp.types import ToolAnnotations

READ = ToolAnnotations(readOnlyHint=True, openWorldHint=True)
LOCAL_READ = ToolAnnotations(readOnlyHint=True, openWorldHint=False)
WRITE = ToolAnnotations(readOnlyHint=False, destructiveHint=False, openWorldHint=True)
IDEMPOTENT_WRITE = ToolAnnotations(
    readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=True
)
DESTRUCTIVE = ToolAnnotations(readOnlyHint=False, destructiveHint=True, openWorldHint=True)
LOCAL_WRITE = ToolAnnotations(readOnlyHint=False, destructiveHint=False, openWorldHint=False)
