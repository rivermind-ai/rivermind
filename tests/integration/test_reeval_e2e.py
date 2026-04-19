"""End-to-end integration test for the re-eval pipeline.

Seeds a real SQLite store with three weeks of mixed observations
(employment-fact changes, events, reflections), calls ``run_reeval``
directly with a deterministic fake synthesizer, and asserts the full
post-conditions: narrative rows, fact supersession, state, and
observation preservation. Then re-runs the pipeline and asserts it's a
no-op.

Does not exercise the FastAPI lifespan; that trigger path is covered by
``test_reeval_startup.py``. This file proves the pipeline itself is
correct.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import pytest

from rivermind.adapters.stores.sqlite import SQLiteMemoryStore
from rivermind.core import reeval as reeval_mod
from rivermind.core.engine import Engine
from rivermind.core.models import Kind, Observation
from rivermind.core.reeval import _iso_week_bounds, run_reeval

if TYPE_CHECKING:
    from collections.abc import Generator
    from pathlib import Path


# Thursday in the middle of an ISO week so the current week is partially
# complete (past observations land in fully-completed weeks).
_NOW = datetime(2026, 3, 26, 12, 0, 0, tzinfo=UTC)


class _RecordingSynth:
    """Deterministic fake synthesizer; records every prompt it receives."""

    def __init__(self) -> None:
        self.prompts: list[str] = []

    def synthesize(self, prompt: str) -> str:
        self.prompts.append(prompt)
        return f"narrative #{len(self.prompts)}"


@pytest.fixture
def store(tmp_path: Path) -> Generator[SQLiteMemoryStore, None, None]:
    s = SQLiteMemoryStore(tmp_path / "reeval_e2e.db")
    try:
        yield s
    finally:
        s.close()


@pytest.fixture(autouse=True)
def _pin_now(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(reeval_mod, "_now", lambda: _NOW)


@pytest.fixture(autouse=True)
def _enable_synthesis(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RIVERMIND_API_KEY", "sk-test")


def _seed_three_weeks(store: SQLiteMemoryStore) -> None:
    """Three completed weeks of observations.

    Week 3 (oldest): initial employer fact (Globex) + an event.
    Week 2: employer changes (Initech) + a reflection.
    Week 1 (newest completed): employer changes again (Acme) + an event.

    Each fact lands in a distinct week; compaction should mark the two
    older ones superseded by the week-1 fact. State should resolve to
    Acme.
    """
    engine = Engine(store)

    def _at(weeks_back: int, *, hour: int = 10) -> datetime:
        return (_NOW - timedelta(days=7 * weeks_back)).replace(hour=hour, minute=0, second=0)

    # Week 3
    engine.record_observation(
        Observation(
            id="obs-fact-globex",
            content="joined Globex",
            kind=Kind.FACT,
            subject="user",
            attribute="employer",
            value="Globex",
            observed_at=_at(3, hour=9),
        )
    )
    engine.record_observation(
        Observation(
            id="obs-event-w3",
            content="visited Globex HQ",
            kind=Kind.EVENT,
            observed_at=_at(3, hour=15),
        )
    )

    # Week 2
    engine.record_observation(
        Observation(
            id="obs-fact-initech",
            content="joined Initech",
            kind=Kind.FACT,
            subject="user",
            attribute="employer",
            value="Initech",
            observed_at=_at(2, hour=9),
        )
    )
    engine.record_observation(
        Observation(
            id="obs-reflection-w2",
            content="switching jobs felt good",
            kind=Kind.REFLECTION,
            observed_at=_at(2, hour=20),
        )
    )

    # Week 1
    engine.record_observation(
        Observation(
            id="obs-fact-acme",
            content="joined Acme",
            kind=Kind.FACT,
            subject="user",
            attribute="employer",
            value="Acme",
            observed_at=_at(1, hour=9),
        )
    )
    engine.record_observation(
        Observation(
            id="obs-event-w1",
            content="onboarding at Acme",
            kind=Kind.EVENT,
            observed_at=_at(1, hour=14),
        )
    )


def _expected_week_bounds() -> list[tuple[datetime, datetime]]:
    return [_iso_week_bounds(_NOW - timedelta(days=7 * i)) for i in (3, 2, 1)]


# ---- first-run correctness ------------------------------------------------


def test_reeval_creates_a_narrative_for_each_completed_week(
    store: SQLiteMemoryStore,
) -> None:
    _seed_three_weeks(store)
    synth = _RecordingSynth()

    summary = run_reeval(store, synthesizer=synth)

    assert summary.weeks_processed == 3
    assert summary.narratives_written == 3
    assert len(synth.prompts) == 3
    narratives = store.get_narratives(_NOW - timedelta(days=30), _NOW)
    assert len(narratives) == 3
    # Each narrative's period matches one of the completed weeks.
    periods = {(n.period_start, n.period_end) for n in narratives}
    assert periods == set(_expected_week_bounds())


def test_reeval_supersedes_older_employer_facts(
    store: SQLiteMemoryStore,
) -> None:
    _seed_three_weeks(store)
    run_reeval(store, synthesizer=_RecordingSynth())

    all_observations = store.get_observations(
        _NOW - timedelta(days=30), _NOW, include_superseded=True
    )
    by_id = {o.id: o for o in all_observations}
    assert by_id["obs-fact-globex"].superseded_by == "obs-fact-acme"
    assert by_id["obs-fact-initech"].superseded_by == "obs-fact-acme"
    assert by_id["obs-fact-acme"].superseded_by is None
    # Events and reflections must never be superseded.
    assert by_id["obs-event-w3"].superseded_by is None
    assert by_id["obs-event-w1"].superseded_by is None
    assert by_id["obs-reflection-w2"].superseded_by is None


def test_reeval_state_reflects_latest_fact(
    store: SQLiteMemoryStore,
) -> None:
    _seed_three_weeks(store)
    run_reeval(store, synthesizer=_RecordingSynth())

    state = store.get_state("user", "employer")
    assert state is not None
    assert state.current_value == "Acme"
    assert state.source_observation == "obs-fact-acme"


def test_reeval_does_not_hard_delete_observations(
    store: SQLiteMemoryStore,
) -> None:
    _seed_three_weeks(store)
    observations_before = store.get_observations(
        _NOW - timedelta(days=30), _NOW, include_superseded=True
    )
    assert len(observations_before) == 6

    run_reeval(store, synthesizer=_RecordingSynth())

    observations_after = store.get_observations(
        _NOW - timedelta(days=30), _NOW, include_superseded=True
    )
    assert {o.id for o in observations_after} == {o.id for o in observations_before}


def test_reeval_records_reeval_runs_for_each_week(
    store: SQLiteMemoryStore,
) -> None:
    _seed_three_weeks(store)
    run_reeval(store, synthesizer=_RecordingSynth())
    for period_start, period_end in _expected_week_bounds():
        assert store.reeval_exists(period_start, period_end)


# ---- idempotency ----------------------------------------------------------


def test_second_run_is_a_noop(store: SQLiteMemoryStore) -> None:
    _seed_three_weeks(store)
    first_synth = _RecordingSynth()
    run_reeval(store, synthesizer=first_synth)

    narratives_before = store.get_narratives(_NOW - timedelta(days=30), _NOW)
    supersession_before = {
        o.id: o.superseded_by
        for o in store.get_observations(_NOW - timedelta(days=30), _NOW, include_superseded=True)
    }
    state_before = store.get_state("user", "employer")

    second_synth = _RecordingSynth()
    second_summary = run_reeval(store, synthesizer=second_synth)

    assert second_summary.weeks_processed == 0
    assert second_summary.narratives_written == 0
    assert second_synth.prompts == []

    narratives_after = store.get_narratives(_NOW - timedelta(days=30), _NOW)
    assert {n.id for n in narratives_after} == {n.id for n in narratives_before}

    supersession_after = {
        o.id: o.superseded_by
        for o in store.get_observations(_NOW - timedelta(days=30), _NOW, include_superseded=True)
    }
    assert supersession_after == supersession_before

    state_after = store.get_state("user", "employer")
    assert state_after == state_before
