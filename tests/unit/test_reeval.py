"""Unit tests for ``rivermind.core.reeval``.

Exercises the re-eval pipeline and its helpers against an in-memory fake
store and fake synthesizer. The full pipeline call path is covered here;
integration with the FastAPI lifespan is tested separately.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from rivermind.core.models import Kind, Narrative, Observation, State
from rivermind.core.reeval import (
    ReevalSummary,
    _iso_week_bounds,
    _weeks_needing_reeval,
    run_reeval,
)

if TYPE_CHECKING:
    import pytest


# Monday, March 2 2026, 12:00 UTC. ISO week 10.
_MONDAY_NOON = datetime(2026, 3, 2, 12, 0, 0, tzinfo=UTC)


def _d(year: int, month: int, day: int, hour: int = 12) -> datetime:
    return datetime(year, month, day, hour, tzinfo=UTC)


@dataclass
class _FakeStore:
    """Fake MemoryStore covering every method ``run_reeval`` exercises."""

    observations: list[Observation] = field(default_factory=list)
    states: list[State] = field(default_factory=list)
    narratives: list[Narrative] = field(default_factory=list)
    reevals: set[tuple[datetime, datetime]] = field(default_factory=set)
    record_reeval_raises: bool = False
    rebuild_state_raises: bool = False

    def save_observation(self, observation: Observation) -> None:
        self.observations.append(observation)

    def mark_observation_superseded(self, old_id: str, new_id: str) -> None:
        for i, o in enumerate(self.observations):
            if o.id == old_id:
                self.observations[i] = o.model_copy(update={"superseded_by": new_id})
                return
        raise ValueError(f"observation {old_id!r} not found")

    def get_observations(
        self,
        start: datetime,
        end: datetime,
        topic: str | None = None,
        *,
        limit: int | None = None,
        include_superseded: bool = False,
    ) -> list[Observation]:
        hits = [o for o in self.observations if start <= o.observed_at <= end]
        if not include_superseded:
            hits = [o for o in hits if o.superseded_by is None]
        hits.sort(key=lambda o: o.observed_at)
        return hits[:limit] if limit is not None else hits

    def upsert_state(self, state: State) -> None:
        key = (state.subject, state.attribute)
        existing = next((s for s in self.states if (s.subject, s.attribute) == key), None)
        if existing is not None and state.current_since <= existing.current_since:
            return
        self.states = [s for s in self.states if (s.subject, s.attribute) != key]
        self.states.append(state)

    def get_state(self, subject: str, attribute: str) -> State | None:
        for s in self.states:
            if s.subject == subject and s.attribute == attribute:
                return s
        return None

    def list_states(
        self,
        subject: str | None = None,
        attribute: str | None = None,
    ) -> list[State]:
        return list(self.states)

    def clear_state(self) -> None:
        self.states = []

    def save_narrative(self, narrative: Narrative) -> None:
        self.narratives.append(narrative)

    def mark_narrative_superseded(self, old_id: str, new_id: str) -> None:
        for i, n in enumerate(self.narratives):
            if n.id == old_id:
                self.narratives[i] = n.model_copy(update={"superseded_by": new_id})
                return
        raise ValueError(f"narrative {old_id!r} not found")

    def get_narratives(
        self,
        period_start: datetime,
        period_end: datetime,
        topic: str | None = None,
        *,
        include_superseded: bool = False,
    ) -> list[Narrative]:
        hits = [
            n
            for n in self.narratives
            if n.period_start <= period_end and n.period_end >= period_start
        ]
        if topic is not None:
            hits = [n for n in hits if n.topic == topic]
        if not include_superseded:
            hits = [n for n in hits if n.superseded_by is None]
        return sorted(hits, key=lambda n: n.generated_at, reverse=True)

    def record_reeval(self, period_start: datetime, period_end: datetime) -> None:
        if self.record_reeval_raises:
            raise RuntimeError("record_reeval forced failure")
        self.reevals.add((period_start, period_end))

    def reeval_exists(self, period_start: datetime, period_end: datetime) -> bool:
        return (period_start, period_end) in self.reevals

    def schema_version(self) -> int:
        return 2


@dataclass
class _FakeSynthesizer:
    response: str = "the narrative"
    raise_once: bool = False
    call_count: int = 0

    def synthesize(self, prompt: str) -> str:
        self.call_count += 1
        if self.raise_once and self.call_count == 1:
            raise RuntimeError("first call fails")
        return self.response


def _seed_event(store: _FakeStore, *, id_: str, at: datetime) -> None:
    store.observations.append(
        Observation(id=id_, content=f"event {id_}", kind=Kind.EVENT, observed_at=at)
    )


# ---- _iso_week_bounds ------------------------------------------------------


def test_iso_week_bounds_from_monday() -> None:
    start, end = _iso_week_bounds(_MONDAY_NOON)
    assert start == datetime(2026, 3, 2, 0, 0, 0, tzinfo=UTC)
    assert end == datetime(2026, 3, 8, 23, 59, 59, 999999, tzinfo=UTC)


def test_iso_week_bounds_from_sunday() -> None:
    # Sunday March 8 2026
    start, end = _iso_week_bounds(_d(2026, 3, 8, 23))
    assert start == datetime(2026, 3, 2, tzinfo=UTC)
    assert end.day == 8


def test_iso_week_bounds_naive_treated_as_utc() -> None:
    naive = datetime(2026, 3, 4, 10, 0, 0)
    start, _ = _iso_week_bounds(naive)
    assert start == datetime(2026, 3, 2, tzinfo=UTC)


# ---- _weeks_needing_reeval -------------------------------------------------


def test_weeks_needing_reeval_discovers_past_weeks() -> None:
    store = _FakeStore()
    # Seed one observation per week for three past weeks
    now = _d(2026, 3, 30)
    for i in range(1, 4):
        _seed_event(store, id_=f"obs-w{i}", at=now - timedelta(days=7 * i))
    result = _weeks_needing_reeval(store, now)
    assert len(result) == 3
    # Ordered ascending
    assert result[0][0] < result[1][0] < result[2][0]


def test_weeks_needing_reeval_excludes_current_week() -> None:
    store = _FakeStore()
    now = _d(2026, 3, 5)  # Thursday
    _seed_event(store, id_="obs-now", at=now - timedelta(days=1))  # same week
    _seed_event(store, id_="obs-past", at=now - timedelta(days=10))  # prior week
    result = _weeks_needing_reeval(store, now)
    assert len(result) == 1
    past_start, _ = result[0]
    assert past_start < _d(2026, 3, 2)


def test_weeks_needing_reeval_excludes_already_recorded() -> None:
    store = _FakeStore()
    now = _d(2026, 3, 30)
    for i in range(1, 4):
        _seed_event(store, id_=f"obs-w{i}", at=now - timedelta(days=7 * i))
    # Pre-record one of the three
    target = _iso_week_bounds(now - timedelta(days=14))
    store.reevals.add(target)
    result = _weeks_needing_reeval(store, now)
    assert len(result) == 2
    assert target not in result


def test_weeks_needing_reeval_excludes_weeks_without_observations() -> None:
    store = _FakeStore()
    now = _d(2026, 3, 30)
    # Only weeks 1 and 3 back have observations; week 2 is a gap
    _seed_event(store, id_="obs-w1", at=now - timedelta(days=7))
    _seed_event(store, id_="obs-w3", at=now - timedelta(days=21))
    result = _weeks_needing_reeval(store, now)
    assert len(result) == 2


def test_weeks_needing_reeval_empty_store() -> None:
    store = _FakeStore()
    assert _weeks_needing_reeval(store, _d(2026, 3, 30)) == []


# ---- run_reeval -----------------------------------------------------------


def test_run_reeval_invokes_synthesizer_per_week(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("RIVERMIND_API_KEY", "sk-test")
    store = _FakeStore()
    now = _d(2026, 3, 30)
    _seed_event(store, id_="obs-1", at=now - timedelta(days=7))
    _seed_event(store, id_="obs-2", at=now - timedelta(days=14))
    synth = _FakeSynthesizer()

    summary = run_reeval(store, synthesizer=synth, now=now)

    assert summary.weeks_processed == 2
    assert synth.call_count == 2
    assert summary.narratives_written == 2
    assert len(store.reevals) == 2
    assert summary.warnings == []


def test_run_reeval_without_synthesizer_still_records_runs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("RIVERMIND_API_KEY", raising=False)
    store = _FakeStore()
    now = _d(2026, 3, 30)
    _seed_event(store, id_="obs-1", at=now - timedelta(days=7))
    _seed_event(store, id_="obs-2", at=now - timedelta(days=14))

    summary = run_reeval(store, synthesizer=None, now=now)

    assert summary.weeks_processed == 2
    assert summary.narratives_written == 0
    assert len(store.reevals) == 2
    assert store.narratives == []


def test_run_reeval_synth_failure_continues_and_records_all(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("RIVERMIND_API_KEY", "sk-test")
    store = _FakeStore()
    now = _d(2026, 3, 30)
    _seed_event(store, id_="obs-1", at=now - timedelta(days=7))
    _seed_event(store, id_="obs-2", at=now - timedelta(days=14))
    synth = _FakeSynthesizer(raise_once=True)

    summary = run_reeval(store, synthesizer=synth, now=now)

    assert summary.weeks_processed == 2
    assert summary.narratives_written == 1
    assert len(store.reevals) == 2
    assert len(summary.warnings) == 1
    assert "synthesis failed" in summary.warnings[0]


def test_run_reeval_returns_summary(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("RIVERMIND_API_KEY", raising=False)
    store = _FakeStore()
    summary = run_reeval(store, synthesizer=None, now=_d(2026, 3, 30))
    assert isinstance(summary, ReevalSummary)
    assert summary.weeks_processed == 0
    assert summary.warnings == []


def test_run_reeval_skips_current_week_observations(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("RIVERMIND_API_KEY", raising=False)
    store = _FakeStore()
    now = _d(2026, 3, 5)  # Thursday
    _seed_event(store, id_="obs-now", at=now - timedelta(hours=2))
    summary = run_reeval(store, synthesizer=None, now=now)
    assert summary.weeks_processed == 0
    assert store.reevals == set()


def test_run_reeval_idempotent_across_runs(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("RIVERMIND_API_KEY", raising=False)
    store = _FakeStore()
    now = _d(2026, 3, 30)
    _seed_event(store, id_="obs-1", at=now - timedelta(days=7))

    first = run_reeval(store, synthesizer=None, now=now)
    assert first.weeks_processed == 1

    second = run_reeval(store, synthesizer=None, now=now)
    assert second.weeks_processed == 0
