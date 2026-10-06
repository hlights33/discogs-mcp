"""Read-only SQL against the cache, guarded three ways.

1. Text check: a single SELECT/WITH statement.
2. The connection is opened with mode=ro, so SQLite itself refuses writes.
3. An authorizer callback denies everything except reads and function calls,
   which blocks ATTACH, PRAGMA, temp-table creation and the like.
"""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path
from typing import Any

MAX_ROWS = 500
MAX_STEPS = 50_000_000  # progress-handler budget, roughly a few seconds of work

_ALLOWED_ACTIONS = {
    sqlite3.SQLITE_SELECT,
    sqlite3.SQLITE_READ,
    sqlite3.SQLITE_FUNCTION,
    getattr(sqlite3, "SQLITE_RECURSIVE", 33),
}
_COMMENTS = re.compile(r"--[^\n]*|/\*.*?\*/", re.DOTALL)
_STRINGS = re.compile(r"'(?:[^']|'')*'|\"(?:[^\"]|\"\")*\"")


class QueryError(ValueError):
    pass


def check_sql(sql: str) -> str:
    stripped = _COMMENTS.sub(" ", sql).strip().rstrip(";").strip()
    if not stripped:
        raise QueryError("Empty query.")
    if ";" in _STRINGS.sub("''", stripped):
        raise QueryError("Only a single statement is allowed.")
    first = stripped.split(None, 1)[0].upper()
    if first not in {"SELECT", "WITH"}:
        raise QueryError("Only SELECT (or WITH ... SELECT) queries are allowed.")
    return stripped


def _authorizer(action: int, arg1: Any, arg2: Any, db: Any, trigger: Any) -> int:
    if action in _ALLOWED_ACTIONS:
        return sqlite3.SQLITE_OK
    return sqlite3.SQLITE_DENY


def run_readonly(path: Path, sql: str, params: list[Any] | None = None, max_rows: int = MAX_ROWS) -> dict:
    sql = check_sql(sql)
    if not Path(path).exists():
        raise QueryError("The cache is empty. Run sync_collection first.")
    conn = sqlite3.connect(f"{Path(path).resolve().as_uri()}?mode=ro", uri=True)
    try:
        conn.set_authorizer(_authorizer)
        steps = 0

        def progress() -> int:
            nonlocal steps
            steps += 1000
            return 1 if steps > MAX_STEPS else 0

        conn.set_progress_handler(progress, 1000)
        try:
            cur = conn.execute(sql, params or [])
            rows = cur.fetchmany(max_rows + 1)
        except sqlite3.DatabaseError as exc:
            msg = str(exc)
            if "not authorized" in msg:
                msg = "Query uses a disallowed operation (only reads are permitted)."
            elif "interrupted" in msg:
                msg = "Query took too long and was stopped. Add filters or a LIMIT."
            raise QueryError(msg) from exc
        columns = [d[0] for d in cur.description or []]
    finally:
        conn.close()
    truncated = len(rows) > max_rows
    rows = rows[:max_rows]
    return {
        "columns": columns,
        "rows": [list(r) for r in rows],
        "row_count": len(rows),
        "truncated": truncated,
    }
