"""Adapter-specific tests for SQLiteMemoryStore.

Generic MemoryStore contract tests (that any implementation must pass)
are RIV-17's scope.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from collections.abc import Generator
    from pathlib import Path

from rivermind.adapters.stores.sqlite import SQLiteMemoryStore
from rivermind.core.interfaces import MemoryStore
from rivermind.core.models import Kind, Narrative, Observation, State


def _t(offset_seconds: int = 0) -> datetime:
    return datetime(2026, 4, 18, 12, 0, 0, tzinfo=UTC) + timedelta(seconds=offset_seconds)


@pytest.fixture
def store() -> Generator[SQLiteMemoryStore, None, None]:
    s = SQLiteMemoryStore(":memory:")
    try:
        yield s
    finally:
        s.close()


def test_construction_applies_migrations(store: SQLiteMemoryStore) -> None:
    row = store._conn.execute("SELECT version FROM schema_version").fetchone()
    assert row["version"] == 1


def test_pragmas_are_set(store: SQLiteMemoryStore) -> None:
    mode = store._conn.execute("PRAGMA journal_mode").fetchone()[0]
    assert mode in {"wal", "memory"}  # :memory: downgrades WAL silently
    fks = store._conn.execute("PRAGMA foreign_keys").fetchone()[0]
    assert fks == 1


def test_store_satisfies_memory_store_protocol(store: SQLiteMemoryStore) -> None:
    assert isinstance(store, MemoryStore)


def test_save_and_get_observation_roundtrip(store: SQLiteMemoryStore) -> None:
    obs = Observation(
        id="obs-1",
        content="user works at Acme",
        kind=Kind.FACT,
        subject="user",
        attribute="employer",
        value={"company": "Acme", "start": 2026},
        observed_at=_t(),
        source_model="claude-opus",
        session_id="sess-1",
    )
    store.save_observation(obs)
    got = store.get_observations(_t(-60), _t(60))
    assert len(got) == 1
    assert got[0] == obs


def test_get_observations_ordered_by_observed_at_asc(store: SQLiteMemoryStore) -> None:
    for i in [2, 0, 1]:
        store.save_observation(
            Observation(
                id=f"obs-{i}",
                content=f"event {i}",
                kind=Kind.EVENT,
                observed_at=_t(i),
            )
        )
    got = store.get_observations(_t(-60), _t(60))
    assert [o.id for o in got] == ["obs-0", "obs-1", "obs-2"]


def test_get_observations_topic_hits_fts(store: SQLiteMemoryStore) -> None:
    store.save_observation(
        Observation(id="obs-a", content="visited Acme HQ", kind=Kind.EVENT, observed_at=_t())
    )
    store.save_observation(
        Observation(id="obs-b", content="lunch with a friend", kind=Kind.EVENT, observed_at=_t(1))
    )
    hits = store.get_observations(_t(-60), _t(60), topic="Acme")
    assert [o.id for o in hits] == ["obs-a"]


def test_get_observations_topic_no_match_returns_empty(store: SQLiteMemoryStore) -> None:
    store.save_observation(
        Observation(id="obs-c", content="lunch", kind=Kind.EVENT, observed_at=_t())
    )
    assert store.get_observations(_t(-60), _t(60), topic="bicycle") == []


def test_upsert_state_inserts_new_row(store: SQLiteMemoryStore) -> None:
    store.save_observation(
        Observation(
            id="obs-1",
            content="joined Acme",
            kind=Kind.FACT,
            subject="user",
            attribute="employer",
            value="Acme",
            observed_at=_t(),
        )
    )
    store.upsert_state(
        State(
            subject="user",
            attribute="employer",
            current_value="Acme",
            current_since=_t(),
            source_observation="obs-1",
        )
    )
    got = store.get_state("user", "employer")
    assert got is not None
    assert got.current_value == "Acme"


def test_upsert_state_updates_when_newer(store: SQLiteMemoryStore) -> None:
    store.save_observation(
        Observation(
            id="obs-1",
            content="joined Globex",
            kind=Kind.FACT,
            subject="user",
            attribute="employer",
            value="Globex",
            observed_at=_t(),
        )
    )
    store.save_observation(
        Observation(
            id="obs-2",
            content="joined Acme",
            kind=Kind.FACT,
            subject="user",
            attribute="employer",
            value="Acme",
            observed_at=_t(3600),
        )
    )
    store.upsert_state(
        State(
            subject="user",
            attribute="employer",
            current_value="Globex",
            current_since=_t(),
            source_observation="obs-1",
        )
    )
    store.upsert_state(
        State(
            subject="user",
            attribute="employer",
            current_value="Acme",
            current_since=_t(3600),
            source_observation="obs-2",
        )
    )
    got = store.get_state("user", "employer")
    assert got is not None
    assert got.current_value == "Acme"
    assert got.source_observation == "obs-2"


def test_upsert_state_drops_stale_write(store: SQLiteMemoryStore) -> None:
    store.save_observation(
        Observation(
            id="obs-1",
            content="joined Globex",
            kind=Kind.FACT,
            subject="user",
            attribute="employer",
            value="Globex",
            observed_at=_t(),
        )
    )
    store.save_observation(
        Observation(
            id="obs-2",
            content="joined Acme",
            kind=Kind.FACT,
            subject="user",
            attribute="employer",
            value="Acme",
            observed_at=_t(3600),
        )
    )
    store.upsert_state(
        State(
            subject="user",
            attribute="employer",
            current_value="Acme",
            current_since=_t(3600),
            source_observation="obs-2",
        )
    )
    store.upsert_state(
        State(
            subject="user",
            attribute="employer",
            current_value="Globex",
            current_since=_t(),
            source_observation="obs-1",
        )
    )
    got = store.get_state("user", "employer")
    assert got is not None
    assert got.current_value == "Acme"
    assert got.source_observation == "obs-2"


def test_get_state_returns_none_on_miss(store: SQLiteMemoryStore) -> None:
    assert store.get_state("nobody", "nothing") is None


def test_save_narrative_roundtrips_source_observations_list(store: SQLiteMemoryStore) -> None:
    for i in range(2):
        store.save_observation(
            Observation(
                id=f"obs-{i}",
                content=f"event {i}",
                kind=Kind.EVENT,
                observed_at=_t(i),
            )
        )
    n = Narrative(
        id="nar-1",
        content="weekly summary",
        topic="career",
        period_start=_t(),
        period_end=_t(3600),
        source_observations=["obs-0", "obs-1"],
    )
    store.save_narrative(n)
    got = store.get_narratives(_t(-60), _t(4000))
    assert len(got) == 1
    assert got[0].source_observations == ["obs-0", "obs-1"]


def test_get_narratives_window_overlap(store: SQLiteMemoryStore) -> None:
    narratives = [
        Narrative(
            id="nar-before",
            content="x",
            period_start=_t(-7200),
            period_end=_t(-3600),
            source_observations=[],
        ),
        Narrative(
            id="nar-overlap-left",
            content="x",
            period_start=_t(-3600),
            period_end=_t(30),
            source_observations=[],
        ),
        Narrative(
            id="nar-inside",
            content="x",
            period_start=_t(10),
            period_end=_t(50),
            source_observations=[],
        ),
        Narrative(
            id="nar-after",
            content="x",
            period_start=_t(3600),
            period_end=_t(7200),
            source_observations=[],
        ),
    ]
    for n in narratives:
        store.save_narrative(n)
    got = store.get_narratives(_t(), _t(60))
    got_ids = {n.id for n in got}
    assert got_ids == {"nar-overlap-left", "nar-inside"}


def test_get_narratives_topic_is_exact_match_not_fts(store: SQLiteMemoryStore) -> None:
    store.save_narrative(
        Narrative(
            id="nar-1",
            content="Acme notes and project review",
            topic="career",
            period_start=_t(),
            period_end=_t(60),
            source_observations=[],
        )
    )
    assert store.get_narratives(_t(-60), _t(120), topic="career") != []
    assert store.get_narratives(_t(-60), _t(120), topic="Acme") == []


def test_get_narratives_ordered_by_generated_at_desc(store: SQLiteMemoryStore) -> None:
    for i in range(3):
        store.save_narrative(
            Narrative(
                id=f"nar-{i}",
                content="x",
                period_start=_t(),
                period_end=_t(60),
                source_observations=[],
                generated_at=_t(i),
            )
        )
    got = store.get_narratives(_t(-60), _t(120))
    assert [n.id for n in got] == ["nar-2", "nar-1", "nar-0"]


def test_context_manager_closes_connection() -> None:
    with SQLiteMemoryStore(":memory:") as s:
        pass
    with pytest.raises(sqlite3.ProgrammingError):
        s._conn.execute("SELECT 1")


def test_file_backed_database_roundtrips(tmp_path: Path) -> None:
    db_path = tmp_path / "riv.db"
    with SQLiteMemoryStore(db_path) as s:
        s.save_observation(Observation(id="obs-1", content="hi", kind=Kind.EVENT, observed_at=_t()))
    with SQLiteMemoryStore(db_path) as s:
        got = s.get_observations(_t(-60), _t(60))
    assert [o.id for o in got] == ["obs-1"]
