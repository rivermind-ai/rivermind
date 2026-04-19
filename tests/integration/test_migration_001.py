import json
import sqlite3
from collections.abc import Generator
from pathlib import Path

import pytest

MIGRATION_PATH = (
    Path(__file__).resolve().parents[2]
    / "src"
    / "rivermind"
    / "adapters"
    / "stores"
    / "migrations"
    / "001_initial.sql"
)


@pytest.fixture
def db() -> Generator[sqlite3.Connection, None, None]:
    conn = sqlite3.connect(":memory:")
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(MIGRATION_PATH.read_text())
    try:
        yield conn
    finally:
        conn.close()


def _names(conn: sqlite3.Connection, kind: str) -> set[str]:
    rows = conn.execute("SELECT name FROM sqlite_master WHERE type = ?", (kind,)).fetchall()
    return {r[0] for r in rows}


def test_expected_tables_exist(db: sqlite3.Connection) -> None:
    tables = _names(db, "table")
    assert {"observations", "state", "narratives", "schema_version"} <= tables


def test_fts_virtual_table_exists(db: sqlite3.Connection) -> None:
    rows = db.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'observations_fts'"
    ).fetchall()
    assert len(rows) == 1


def test_observations_columns(db: sqlite3.Connection) -> None:
    cols = {row[1] for row in db.execute("PRAGMA table_info(observations)")}
    assert cols == {
        "id",
        "content",
        "kind",
        "subject",
        "attribute",
        "value",
        "observed_at",
        "recorded_at",
        "source_model",
        "session_id",
        "superseded_by",
    }


def test_state_columns_and_primary_key(db: sqlite3.Connection) -> None:
    info = db.execute("PRAGMA table_info(state)").fetchall()
    cols = {row[1] for row in info}
    assert cols == {"subject", "attribute", "current_value", "current_since", "source_observation"}
    pk_cols = {row[1] for row in info if row[5]}
    assert pk_cols == {"subject", "attribute"}


def test_narratives_columns(db: sqlite3.Connection) -> None:
    cols = {row[1] for row in db.execute("PRAGMA table_info(narratives)")}
    assert cols == {
        "id",
        "content",
        "topic",
        "period_start",
        "period_end",
        "source_observations",
        "generated_at",
        "superseded_by",
    }


def test_required_indexes(db: sqlite3.Connection) -> None:
    indexes = _names(db, "index")
    assert "idx_observations_observed_at" in indexes
    assert "idx_state_subject" in indexes


def test_schema_version_seeded_with_single_row(db: sqlite3.Connection) -> None:
    rows = db.execute("SELECT id, version FROM schema_version").fetchall()
    assert rows == [("schema", 1)]
    with pytest.raises(sqlite3.IntegrityError):
        db.execute(
            "INSERT INTO schema_version (id, version) VALUES (?, ?)",
            ("other", 2),
        )


def test_schema_version_id_must_be_sentinel(db: sqlite3.Connection) -> None:
    with pytest.raises(sqlite3.IntegrityError):
        db.execute("UPDATE schema_version SET id = 'something-else'")


def test_fts_insert_trigger_indexes_new_row(db: sqlite3.Connection) -> None:
    db.execute(
        "INSERT INTO observations "
        "(id, content, kind, subject, attribute, value, observed_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            "obs-1",
            "user works at Acme",
            "fact",
            "user",
            "employer",
            json.dumps("Acme"),
            "2026-04-01T00:00:00Z",
        ),
    )
    rows = db.execute(
        "SELECT rowid FROM observations_fts WHERE observations_fts MATCH 'Acme'"
    ).fetchall()
    assert len(rows) == 1


def test_observation_id_prefix_enforced(db: sqlite3.Connection) -> None:
    with pytest.raises(sqlite3.IntegrityError):
        db.execute(
            "INSERT INTO observations (id, content, kind, observed_at) VALUES (?, ?, ?, ?)",
            ("not-prefixed", "x", "event", "2026-04-01T00:00:00Z"),
        )


def test_narrative_id_prefix_enforced(db: sqlite3.Connection) -> None:
    with pytest.raises(sqlite3.IntegrityError):
        db.execute(
            "INSERT INTO narratives "
            "(id, content, period_start, period_end, source_observations) "
            "VALUES (?, ?, ?, ?, ?)",
            ("obs-wrong", "x", "2026-04-01", "2026-04-07", "[]"),
        )


def test_kind_enum_rejects_unknown(db: sqlite3.Connection) -> None:
    with pytest.raises(sqlite3.IntegrityError):
        db.execute(
            "INSERT INTO observations (id, content, kind, observed_at) VALUES (?, ?, ?, ?)",
            ("obs-2", "x", "preference", "2026-04-01T00:00:00Z"),
        )


def test_fact_requires_subject_and_attribute(db: sqlite3.Connection) -> None:
    with pytest.raises(sqlite3.IntegrityError):
        db.execute(
            "INSERT INTO observations (id, content, kind, observed_at) VALUES (?, ?, ?, ?)",
            ("obs-3", "orphan fact", "fact", "2026-04-01T00:00:00Z"),
        )


def test_event_without_subject_is_allowed(db: sqlite3.Connection) -> None:
    db.execute(
        "INSERT INTO observations (id, content, kind, observed_at) VALUES (?, ?, ?, ?)",
        ("obs-4", "team standup", "event", "2026-04-01T00:00:00Z"),
    )


def test_only_facts_can_be_superseded(db: sqlite3.Connection) -> None:
    db.execute(
        "INSERT INTO observations (id, content, kind, observed_at) VALUES (?, ?, ?, ?)",
        ("obs-5", "stripe onsite", "event", "2026-04-01T00:00:00Z"),
    )
    with pytest.raises(sqlite3.IntegrityError):
        db.execute(
            "INSERT INTO observations "
            "(id, content, kind, observed_at, superseded_by) "
            "VALUES (?, ?, ?, ?, ?)",
            ("obs-6", "another event", "event", "2026-04-02T00:00:00Z", "obs-5"),
        )


def test_invalid_json_value_rejected(db: sqlite3.Connection) -> None:
    with pytest.raises(sqlite3.IntegrityError):
        db.execute(
            "INSERT INTO observations "
            "(id, content, kind, subject, attribute, value, observed_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("obs-7", "x", "fact", "user", "role", "not json", "2026-04-01T00:00:00Z"),
        )


def test_state_source_observation_fk_enforced(db: sqlite3.Connection) -> None:
    with pytest.raises(sqlite3.IntegrityError):
        db.execute(
            "INSERT INTO state "
            "(subject, attribute, current_value, current_since, source_observation) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                "user",
                "employer",
                json.dumps("Acme"),
                "2026-04-01T00:00:00Z",
                "obs-does-not-exist",
            ),
        )


def test_narrative_source_observations_must_be_valid_json(db: sqlite3.Connection) -> None:
    with pytest.raises(sqlite3.IntegrityError):
        db.execute(
            "INSERT INTO narratives "
            "(id, content, period_start, period_end, source_observations) "
            "VALUES (?, ?, ?, ?, ?)",
            ("nar-1", "weekly summary", "2026-04-01", "2026-04-07", "not json"),
        )
