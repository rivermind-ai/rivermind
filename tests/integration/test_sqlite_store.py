"""SQLite adapter: runs the reusable MemoryStore contract suite against a
file-backed SQLite database, plus a handful of backend-specific checks.
"""

from __future__ import annotations

import sqlite3
from typing import TYPE_CHECKING

import pytest

from rivermind.adapters.stores.sqlite import SQLiteMemoryStore
from rivermind.core.models import Kind, Observation
from tests.contract.memory_store import MemoryStoreContractTests

if TYPE_CHECKING:
    from collections.abc import Callable, Generator
    from datetime import datetime
    from pathlib import Path


class TestSQLiteContractCompliance(MemoryStoreContractTests):
    """Runs the full MemoryStore contract against a real file-backed DB."""

    @pytest.fixture
    def store(self, tmp_db_path: Path) -> Generator[SQLiteMemoryStore, None, None]:
        s = SQLiteMemoryStore(tmp_db_path)
        try:
            yield s
        finally:
            s.close()


# ---- SQLite-specific tests (not part of the portable contract) ------------


def test_construction_applies_migrations(tmp_db_path: Path) -> None:
    with SQLiteMemoryStore(tmp_db_path) as s:
        row = s._conn.execute("SELECT version FROM schema_version").fetchone()
        assert row["version"] == 1


def test_pragmas_are_set(tmp_db_path: Path) -> None:
    with SQLiteMemoryStore(tmp_db_path) as s:
        mode = s._conn.execute("PRAGMA journal_mode").fetchone()[0]
        assert mode == "wal"
        fks = s._conn.execute("PRAGMA foreign_keys").fetchone()[0]
        assert fks == 1


def test_context_manager_closes_connection(tmp_db_path: Path) -> None:
    with SQLiteMemoryStore(tmp_db_path) as s:
        pass
    with pytest.raises(sqlite3.ProgrammingError):
        s._conn.execute("SELECT 1")


def test_file_backed_persists_across_connections(
    tmp_db_path: Path,
    t: Callable[[int], datetime],
) -> None:
    with SQLiteMemoryStore(tmp_db_path) as s:
        s.save_observation(Observation(id="obs-1", content="hi", kind=Kind.EVENT, observed_at=t(0)))
    with SQLiteMemoryStore(tmp_db_path) as s:
        got = s.get_observations(t(-60), t(60))
    assert [o.id for o in got] == ["obs-1"]
