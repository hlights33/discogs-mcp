"""cache.query.run_readonly: the SQL guard (single SELECT/WITH, no writes/pragmas/attach) and row cap."""

from __future__ import annotations

import pytest

from discogs_mcp.cache.db import CacheDB
from discogs_mcp.cache.query import QueryError, run_readonly

from . import payloads as p


@pytest.fixture
def db(tmp_path):
    cache = CacheDB(tmp_path / "cache.db")
    for i in range(1, 11):
        cache.upsert_release_basic(p.basic_information(release_id=i, title=f"Release {i}", styles=["Ambient"]))
        cache.conn.execute(
            "INSERT INTO collection_items(instance_id, release_id, folder_id, rating, date_added, fields) "
            "VALUES(?,?,?,?,?,?)",
            (i, i, 1, 0, "2024-01-01", "{}"),
        )
    cache.conn.commit()
    yield cache
    cache.close()


def test_select_works(db):
    result = run_readonly(db.path, "SELECT title FROM collection ORDER BY title")
    assert result["row_count"] == 10
    assert result["rows"][0] == ["Release 1"]
    assert result["truncated"] is False


def test_with_cte_select_works(db):
    result = run_readonly(
        db.path, "WITH recent AS (SELECT title FROM collection) SELECT title FROM recent LIMIT 1"
    )
    assert result["row_count"] == 1


def test_json_each_over_styles(db):
    result = run_readonly(
        db.path,
        "SELECT s.value, COUNT(*) FROM collection, json_each(collection.styles) s GROUP BY 1",
    )
    assert result["columns"] == ["value", "COUNT(*)"]
    assert result["rows"] == [["Ambient", 10]]


@pytest.mark.parametrize(
    "sql",
    [
        "INSERT INTO releases(id) VALUES(99999)",
        "UPDATE releases SET title='x'",
        "DELETE FROM releases",
        "DROP TABLE releases",
        "ATTACH DATABASE 'x.db' AS x",
        "PRAGMA user_version=5",
    ],
)
def test_rejects_write_statements_by_keyword(db, sql):
    with pytest.raises(QueryError):
        run_readonly(db.path, sql)


def test_rejects_multiple_statements(db):
    with pytest.raises(QueryError, match="single statement"):
        run_readonly(db.path, "SELECT 1; SELECT 2")


def test_rejects_cte_write(db):
    with pytest.raises(QueryError):
        run_readonly(db.path, "WITH x AS (SELECT id FROM releases) DELETE FROM releases WHERE id IN (SELECT id FROM x)")


def test_rejects_select_that_touches_pragma_via_table_valued_function(db):
    # Both are syntactically a plain SELECT (so the text check lets them through), but
    # SQLite resolves a `pragma_*` table-valued function via the PRAGMA authorizer action,
    # which the authorizer denies outright -- defense-in-depth beyond the text check.
    with pytest.raises(QueryError, match="disallowed operation"):
        run_readonly(db.path, "SELECT * FROM pragma_table_info('releases')")
    with pytest.raises(QueryError, match="disallowed operation"):
        run_readonly(db.path, "SELECT * FROM pragma_user_version")


def test_authorizer_denies_everything_but_reads_and_functions():
    import sqlite3

    from discogs_mcp.cache.query import _authorizer

    assert _authorizer(sqlite3.SQLITE_SELECT, None, None, None, None) == sqlite3.SQLITE_OK
    assert _authorizer(sqlite3.SQLITE_READ, "t", "c", "main", None) == sqlite3.SQLITE_OK
    assert _authorizer(sqlite3.SQLITE_FUNCTION, None, "abs", "main", None) == sqlite3.SQLITE_OK
    for denied in (
        sqlite3.SQLITE_INSERT,
        sqlite3.SQLITE_UPDATE,
        sqlite3.SQLITE_DELETE,
        sqlite3.SQLITE_DROP_TABLE,
        sqlite3.SQLITE_ATTACH,
        sqlite3.SQLITE_PRAGMA,
        sqlite3.SQLITE_CREATE_TEMP_TABLE,
    ):
        assert _authorizer(denied, None, None, None, None) == sqlite3.SQLITE_DENY


def test_empty_query_rejected(db):
    with pytest.raises(QueryError, match="Empty"):
        run_readonly(db.path, "   ")


def test_non_select_keyword_rejected(db):
    with pytest.raises(QueryError, match="SELECT"):
        run_readonly(db.path, "EXPLAIN SELECT 1")


def test_missing_cache_file_raises(tmp_path):
    with pytest.raises(QueryError, match="empty"):
        run_readonly(tmp_path / "nope.db", "SELECT 1")


def test_row_cap_truncates_at_500(tmp_path):
    cache = CacheDB(tmp_path / "big.db")
    for i in range(1, 601):
        cache.upsert_release_basic(p.basic_information(release_id=i, title=f"R{i}"))
        cache.conn.execute(
            "INSERT INTO collection_items(instance_id, release_id, folder_id, rating, date_added, fields) "
            "VALUES(?,?,?,?,?,?)",
            (i, i, 1, 0, "2024-01-01", "{}"),
        )
    cache.conn.commit()
    result = run_readonly(cache.path, "SELECT title FROM collection")
    assert result["row_count"] == 500
    assert result["truncated"] is True
    cache.close()


def test_row_cap_not_truncated_when_under_limit(db):
    result = run_readonly(db.path, "SELECT title FROM collection")
    assert result["truncated"] is False
    assert result["row_count"] == 10
