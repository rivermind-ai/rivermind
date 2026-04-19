"""SQLite implementation of ``MemoryStore``.

Single-file database with WAL mode and FTS5. The schema lives in
``migrations/001_initial.sql``; this adapter only writes SQL against it
and converts rows back into core data models.
"""

from __future__ import annotations

import json
import sqlite3
from typing import TYPE_CHECKING, Any, Self

from rivermind.adapters.stores.migrations import apply_migrations, current_version
from rivermind.core.interfaces import MemoryStore
from rivermind.core.models import Narrative, Observation, State

if TYPE_CHECKING:
    from datetime import datetime
    from pathlib import Path
    from types import TracebackType


class SQLiteMemoryStore(MemoryStore):
    """MemoryStore backed by a single-file SQLite database.

    Opens one long-lived connection with ``check_same_thread=False``;
    Python's ``sqlite3`` module serializes writes internally and WAL gives
    concurrent readers for free. Acceptable for the v0.1 shape: one
    rivermind process, one user, one in-process scheduler.
    """

    def __init__(self, path: str | Path, *, migrate: bool = True) -> None:
        self._path = str(path)
        self._conn = sqlite3.connect(self._path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode = WAL")
        self._conn.execute("PRAGMA foreign_keys = ON")
        if migrate:
            self.migrate()

    def migrate(self) -> None:
        """Apply any un-applied schema migrations."""
        apply_migrations(self._conn)

    def close(self) -> None:
        """Close the underlying connection."""
        self._conn.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    def save_observation(self, observation: Observation) -> None:
        with self._conn:
            self._conn.execute(
                "INSERT INTO observations "
                "(id, content, kind, subject, attribute, value, "
                " observed_at, recorded_at, source_model, session_id, superseded_by) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    observation.id,
                    observation.content,
                    observation.kind.value,
                    observation.subject,
                    observation.attribute,
                    None if observation.value is None else json.dumps(observation.value),
                    observation.observed_at.isoformat(),
                    observation.recorded_at.isoformat(),
                    observation.source_model,
                    observation.session_id,
                    observation.superseded_by,
                ),
            )

    def get_observations(
        self,
        start: datetime,
        end: datetime,
        topic: str | None = None,
        *,
        limit: int | None = None,
        include_superseded: bool = False,
    ) -> list[Observation]:
        superseded_clause = "" if include_superseded else " AND o.superseded_by IS NULL"
        limit_clause = " LIMIT ?" if limit is not None else ""
        params: list[object] = []
        if topic is None:
            base = "SELECT o.* FROM observations AS o WHERE o.observed_at BETWEEN ? AND ?"
            params.extend([start.isoformat(), end.isoformat()])
        else:
            base = (
                "SELECT o.* FROM observations AS o "
                "JOIN observations_fts AS f ON f.rowid = o.rowid "
                "WHERE observations_fts MATCH ? AND o.observed_at BETWEEN ? AND ?"
            )
            params.extend([topic, start.isoformat(), end.isoformat()])
        sql = base + superseded_clause + " ORDER BY o.observed_at ASC" + limit_clause
        if limit is not None:
            params.append(limit)
        cur = self._conn.execute(sql, params)
        return [self._row_to_observation(row) for row in cur.fetchall()]

    def upsert_state(self, state: State) -> None:
        with self._conn:
            self._conn.execute(
                "INSERT INTO state "
                "(subject, attribute, current_value, current_since, source_observation) "
                "VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(subject, attribute) DO UPDATE SET "
                "  current_value = excluded.current_value, "
                "  current_since = excluded.current_since, "
                "  source_observation = excluded.source_observation "
                "WHERE excluded.current_since > state.current_since",
                (
                    state.subject,
                    state.attribute,
                    None if state.current_value is None else json.dumps(state.current_value),
                    state.current_since.isoformat(),
                    state.source_observation,
                ),
            )

    def get_state(self, subject: str, attribute: str) -> State | None:
        row = self._conn.execute(
            "SELECT * FROM state WHERE subject = ? AND attribute = ?",
            (subject, attribute),
        ).fetchone()
        if row is None:
            return None
        return self._row_to_state(row)

    def list_states(
        self,
        subject: str | None = None,
        attribute: str | None = None,
    ) -> list[State]:
        if subject is not None and attribute is not None:
            cur = self._conn.execute(
                "SELECT * FROM state WHERE subject = ? AND attribute = ? "
                "ORDER BY subject ASC, attribute ASC",
                (subject, attribute),
            )
        elif subject is not None:
            cur = self._conn.execute(
                "SELECT * FROM state WHERE subject = ? ORDER BY subject ASC, attribute ASC",
                (subject,),
            )
        elif attribute is not None:
            cur = self._conn.execute(
                "SELECT * FROM state WHERE attribute = ? ORDER BY subject ASC, attribute ASC",
                (attribute,),
            )
        else:
            cur = self._conn.execute("SELECT * FROM state ORDER BY subject ASC, attribute ASC")
        return [self._row_to_state(row) for row in cur.fetchall()]

    def save_narrative(self, narrative: Narrative) -> None:
        with self._conn:
            self._conn.execute(
                "INSERT INTO narratives "
                "(id, content, topic, period_start, period_end, "
                " source_observations, generated_at, superseded_by) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    narrative.id,
                    narrative.content,
                    narrative.topic,
                    narrative.period_start.isoformat(),
                    narrative.period_end.isoformat(),
                    json.dumps(narrative.source_observations),
                    narrative.generated_at.isoformat(),
                    narrative.superseded_by,
                ),
            )

    def schema_version(self) -> int:
        return current_version(self._conn)

    def get_narratives(
        self,
        period_start: datetime,
        period_end: datetime,
        topic: str | None = None,
        *,
        include_superseded: bool = False,
    ) -> list[Narrative]:
        superseded_clause = "" if include_superseded else " AND n.superseded_by IS NULL"
        params: list[object] = [period_end.isoformat(), period_start.isoformat()]
        if topic is None:
            base = "SELECT n.* FROM narratives AS n WHERE n.period_start <= ? AND n.period_end >= ?"
        else:
            base = (
                "SELECT n.* FROM narratives AS n "
                "WHERE n.period_start <= ? AND n.period_end >= ? AND n.topic = ?"
            )
            params.append(topic)
        sql = base + superseded_clause + " ORDER BY n.generated_at DESC"
        cur = self._conn.execute(sql, params)
        return [self._row_to_narrative(row) for row in cur.fetchall()]

    @staticmethod
    def _row_to_state(row: sqlite3.Row) -> State:
        return State.model_validate(
            {
                "subject": row["subject"],
                "attribute": row["attribute"],
                "current_value": _load_json(row["current_value"]),
                "current_since": row["current_since"],
                "source_observation": row["source_observation"],
            }
        )

    @staticmethod
    def _row_to_observation(row: sqlite3.Row) -> Observation:
        return Observation.model_validate(
            {
                "id": row["id"],
                "content": row["content"],
                "kind": row["kind"],
                "subject": row["subject"],
                "attribute": row["attribute"],
                "value": _load_json(row["value"]),
                "observed_at": row["observed_at"],
                "recorded_at": row["recorded_at"],
                "source_model": row["source_model"],
                "session_id": row["session_id"],
                "superseded_by": row["superseded_by"],
            }
        )

    @staticmethod
    def _row_to_narrative(row: sqlite3.Row) -> Narrative:
        return Narrative.model_validate(
            {
                "id": row["id"],
                "content": row["content"],
                "topic": row["topic"],
                "period_start": row["period_start"],
                "period_end": row["period_end"],
                "source_observations": json.loads(row["source_observations"]),
                "generated_at": row["generated_at"],
                "superseded_by": row["superseded_by"],
            }
        )


def _load_json(value: str | None) -> Any:
    return None if value is None else json.loads(value)
