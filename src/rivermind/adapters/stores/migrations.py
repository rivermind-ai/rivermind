"""Schema migration runner.

Applies numbered SQL files from the ``migrations/`` directory to a SQLite
database, tracking the applied version in the ``schema_version`` table.
Forward-only; refuses to run against a DB whose recorded version is newer
than what this code ships.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import sqlite3

DEFAULT_MIGRATIONS_DIR = Path(__file__).parent / "migrations"
_FILENAME_RE = re.compile(r"^(\d{3})_.+\.sql$")


class MigrationError(Exception):
    """Base class for migration-runner failures."""


class SchemaFromFutureError(MigrationError):
    """DB is on a schema version newer than any migration file shipped."""


def list_migration_files(
    migrations_dir: Path = DEFAULT_MIGRATIONS_DIR,
) -> list[tuple[int, Path]]:
    """Return matching migration files sorted by numeric prefix."""
    found: list[tuple[int, Path]] = []
    if not migrations_dir.is_dir():
        return found
    for path in migrations_dir.iterdir():
        m = _FILENAME_RE.match(path.name)
        if m:
            found.append((int(m.group(1)), path))
    found.sort(key=lambda item: item[0])
    return found


def current_version(conn: sqlite3.Connection) -> int:
    """Return the recorded schema version, or 0 if the table is absent."""
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'schema_version'"
    ).fetchone()
    if row is None:
        return 0
    v = conn.execute("SELECT version FROM schema_version WHERE id = 'schema'").fetchone()
    return int(v[0]) if v else 0


def apply_migrations(
    conn: sqlite3.Connection,
    migrations_dir: Path = DEFAULT_MIGRATIONS_DIR,
) -> list[int]:
    """Apply every un-applied migration in ``migrations_dir``.

    Returns the list of versions actually applied (empty if already up to date).
    Raises :class:`SchemaFromFutureError` if the DB is ahead of this code's
    highest migration file; no SQL is executed in that case.
    """
    files = list_migration_files(migrations_dir)
    if not files:
        return []

    latest_available = files[-1][0]
    db_version = current_version(conn)

    if db_version > latest_available:
        raise SchemaFromFutureError(
            f"Database is at schema version {db_version}, "
            f"but this code only ships migrations up to {latest_available}. "
            f"Refusing to run to avoid corruption."
        )

    applied: list[int] = []
    for version, path in files:
        if version <= db_version:
            continue
        conn.executescript(path.read_text())
        conn.execute(
            "INSERT INTO schema_version (id, version) VALUES ('schema', ?) "
            "ON CONFLICT(id) DO UPDATE SET "
            "version = excluded.version, "
            "applied_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')",
            (version,),
        )
        conn.commit()
        applied.append(version)
    return applied
