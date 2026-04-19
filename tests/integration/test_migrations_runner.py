import sqlite3
from collections.abc import Generator
from pathlib import Path

import pytest

from rivermind.adapters.stores.migrations import (
    DEFAULT_MIGRATIONS_DIR,
    SchemaFromFutureError,
    apply_migrations,
    current_version,
    list_migration_files,
)

INITIAL_SQL = (DEFAULT_MIGRATIONS_DIR / "001_initial.sql").read_text()


@pytest.fixture
def conn() -> Generator[sqlite3.Connection, None, None]:
    c = sqlite3.connect(":memory:")
    try:
        yield c
    finally:
        c.close()


def test_current_version_zero_on_empty_db(conn: sqlite3.Connection) -> None:
    assert current_version(conn) == 0


def test_fresh_db_applies_initial(conn: sqlite3.Connection) -> None:
    applied = apply_migrations(conn)
    assert applied == [1, 2]
    assert current_version(conn) == 2


def test_rerun_is_idempotent(conn: sqlite3.Connection) -> None:
    apply_migrations(conn)
    assert apply_migrations(conn) == []
    assert current_version(conn) == 2


def test_applied_row_has_sentinel_id_and_timestamp(conn: sqlite3.Connection) -> None:
    apply_migrations(conn)
    rows = conn.execute("SELECT id, version, applied_at FROM schema_version").fetchall()
    assert len(rows) == 1
    id_, version, applied_at = rows[0]
    assert id_ == "schema"
    assert version == 2
    assert isinstance(applied_at, str) and applied_at


def _write(dir_: Path, name: str, content: str) -> None:
    (dir_ / name).write_text(content)


def test_applies_only_newer_migrations(conn: sqlite3.Connection, tmp_path: Path) -> None:
    _write(tmp_path, "001_initial.sql", INITIAL_SQL)
    _write(
        tmp_path,
        "002_add_scratch.sql",
        "CREATE TABLE scratch (id INTEGER PRIMARY KEY, note TEXT);",
    )

    first = apply_migrations(conn, tmp_path)
    assert first == [1, 2]
    assert current_version(conn) == 2

    second = apply_migrations(conn, tmp_path)
    assert second == []

    _write(
        tmp_path,
        "003_add_more.sql",
        "CREATE TABLE another (id INTEGER PRIMARY KEY);",
    )
    third = apply_migrations(conn, tmp_path)
    assert third == [3]
    assert current_version(conn) == 3


def test_raises_on_future_schema_and_makes_no_changes(
    conn: sqlite3.Connection, tmp_path: Path
) -> None:
    _write(tmp_path, "001_initial.sql", INITIAL_SQL)
    apply_migrations(conn, tmp_path)
    conn.execute("UPDATE schema_version SET version = 99 WHERE id = 'schema'")
    conn.commit()

    before = conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name"
    ).fetchall()

    with pytest.raises(SchemaFromFutureError):
        apply_migrations(conn, tmp_path)

    after = conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name"
    ).fetchall()
    assert before == after
    assert current_version(conn) == 99


def test_no_migration_files_is_noop(conn: sqlite3.Connection, tmp_path: Path) -> None:
    assert apply_migrations(conn, tmp_path) == []
    assert current_version(conn) == 0


def test_list_migration_files_sorts_numerically(tmp_path: Path) -> None:
    for name in ("010_z.sql", "002_b.sql", "001_a.sql"):
        (tmp_path / name).write_text("-- noop\n")
    versions = [v for v, _ in list_migration_files(tmp_path)]
    assert versions == [1, 2, 10]


def test_list_migration_files_ignores_non_matching(tmp_path: Path) -> None:
    (tmp_path / "readme.txt").write_text("hello")
    (tmp_path / "001.sql").write_text("-- no underscore\n")
    (tmp_path / "1_short.sql").write_text("-- two-digit prefix\n")
    (tmp_path / "001_ok.sql").write_text("-- valid\n")
    files = list_migration_files(tmp_path)
    assert [p.name for _, p in files] == ["001_ok.sql"]


def test_list_migration_files_missing_dir_is_empty(tmp_path: Path) -> None:
    assert list_migration_files(tmp_path / "does-not-exist") == []
