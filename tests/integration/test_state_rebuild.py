"""Integration test for drift recovery via ``rebuild_state``.

Seeds a real SQLite store through the Engine, corrupts the ``state`` table
at the SQL level, then calls ``rebuild_state`` and asserts the projection
matches what the observations project to.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from rivermind.adapters.stores.sqlite import SQLiteMemoryStore
from rivermind.core.engine import Engine
from rivermind.core.models import Kind, Observation
from rivermind.core.projectors.state import rebuild_state

if TYPE_CHECKING:
    from collections.abc import Callable
    from datetime import datetime
    from pathlib import Path


def test_rebuild_state_recovers_from_corruption(
    tmp_db_path: Path,
    t: Callable[[int], datetime],
) -> None:
    with SQLiteMemoryStore(tmp_db_path) as store:
        engine = Engine(store)

        engine.record_observation(
            Observation(
                id="obs-1",
                content="user works at Globex",
                kind=Kind.FACT,
                subject="user",
                attribute="employer",
                value="Globex",
                observed_at=t(0),
            )
        )
        engine.record_observation(
            Observation(
                id="obs-event",
                content="HQ visit",
                kind=Kind.EVENT,
                observed_at=t(30),
            )
        )
        engine.record_observation(
            Observation(
                id="obs-2",
                content="user works at Acme",
                kind=Kind.FACT,
                subject="user",
                attribute="employer",
                value="Acme",
                observed_at=t(3600),
            )
        )

        before = engine.get_current_state(subject="user", attribute="employer")
        assert len(before) == 1
        assert before[0].current_value == "Acme"
        assert before[0].source_observation == "obs-2"

        # Corrupt state directly at the SQL layer. The CHECK constraint
        # requires current_value to be NULL or valid JSON, so write a JSON
        # string literal. source_observation stays pointed at obs-2 to
        # satisfy the FK; the rebuild proves it re-derives correctly anyway.
        store._conn.execute("UPDATE state SET current_value = '\"CORRUPTED\"'")
        store._conn.commit()
        corrupted = engine.get_current_state(subject="user", attribute="employer")
        assert corrupted[0].current_value == "CORRUPTED"

        summary = rebuild_state(store, on_progress=lambda _d, _t: None)
        assert summary.rows_rebuilt == 2  # both fact observations replayed
        assert summary.warnings == []

        after = engine.get_current_state(subject="user", attribute="employer")
        assert len(after) == 1
        assert after[0].current_value == "Acme"
        assert after[0].source_observation == "obs-2"
        assert after[0].current_since == t(3600)
