"""Tests for ``core.engine.Engine``.

Uses a stub MemoryStore so tests don't touch SQLite. Broader integration
coverage is handled by the SQLite adapter tests.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from rivermind.core.engine import Engine
from rivermind.core.models import Kind, Narrative, Observation, State

if TYPE_CHECKING:
    from collections.abc import Sequence


def _t(offset: int = 0) -> datetime:
    return datetime(2026, 4, 18, 12, 0, 0, tzinfo=UTC) + timedelta(seconds=offset)


class _RecordingStore:
    """In-memory MemoryStore stand-in that records calls for assertions."""

    def __init__(
        self,
        observations: Sequence[Observation] = (),
        states: Sequence[State] = (),
        narratives: Sequence[Narrative] = (),
    ) -> None:
        self._observations: list[Observation] = list(observations)
        self._states: list[State] = list(states)
        self._narratives: list[Narrative] = list(narratives)
        self.calls: list[tuple[str, tuple[object, ...]]] = []

    def save_observation(self, observation: Observation) -> None:
        self.calls.append(("save_observation", (observation,)))
        self._observations.append(observation)

    def get_observations(
        self,
        start: datetime,
        end: datetime,
        topic: str | None = None,
        *,
        limit: int | None = None,
        include_superseded: bool = False,
    ) -> list[Observation]:
        self.calls.append(("get_observations", (start, end, topic, limit, include_superseded)))
        hits = [o for o in self._observations if start <= o.observed_at <= end]
        if topic is not None:
            hits = [o for o in hits if topic.lower() in o.content.lower()]
        if not include_superseded:
            hits = [o for o in hits if o.superseded_by is None]
        ordered = sorted(hits, key=lambda o: o.observed_at)
        return ordered[:limit] if limit is not None else ordered

    def upsert_state(self, state: State) -> None:
        self.calls.append(("upsert_state", (state,)))
        key = (state.subject, state.attribute)
        existing = next((s for s in self._states if (s.subject, s.attribute) == key), None)
        if existing is not None and state.current_since <= existing.current_since:
            return
        self._states = [s for s in self._states if (s.subject, s.attribute) != key]
        self._states.append(state)

    def get_state(self, subject: str, attribute: str) -> State | None:
        for s in self._states:
            if s.subject == subject and s.attribute == attribute:
                return s
        return None

    def list_states(
        self,
        subject: str | None = None,
        attribute: str | None = None,
    ) -> list[State]:
        self.calls.append(("list_states", (subject, attribute)))
        hits = self._states
        if subject is not None:
            hits = [s for s in hits if s.subject == subject]
        if attribute is not None:
            hits = [s for s in hits if s.attribute == attribute]
        return sorted(hits, key=lambda s: (s.subject, s.attribute))

    def clear_state(self) -> None:
        self.calls.append(("clear_state", ()))
        self._states = []

    def save_narrative(self, narrative: Narrative) -> None:
        self._narratives.append(narrative)

    def get_narratives(
        self,
        period_start: datetime,
        period_end: datetime,
        topic: str | None = None,
        *,
        include_superseded: bool = False,
    ) -> list[Narrative]:
        self.calls.append(("get_narratives", (period_start, period_end, topic, include_superseded)))
        hits = [
            n
            for n in self._narratives
            if n.period_start <= period_end and n.period_end >= period_start
        ]
        if topic is not None:
            hits = [n for n in hits if n.topic == topic]
        if not include_superseded:
            hits = [n for n in hits if n.superseded_by is None]
        return sorted(hits, key=lambda n: n.generated_at, reverse=True)

    def schema_version(self) -> int:
        self.calls.append(("schema_version", ()))
        return 1


def test_record_observation_persists_and_returns_id() -> None:
    store = _RecordingStore()
    engine = Engine(store)
    obs = Observation(
        id="obs-1",
        content="hi",
        kind=Kind.EVENT,
        observed_at=_t(),
    )
    returned_id = engine.record_observation(obs)
    assert returned_id == "obs-1"
    assert store.calls[0] == ("save_observation", (obs,))


def test_record_observation_projects_state_for_fact() -> None:
    store = _RecordingStore()
    engine = Engine(store)
    fact = Observation(
        id="obs-fact",
        content="user is admin",
        kind=Kind.FACT,
        subject="user",
        attribute="role",
        value="admin",
        observed_at=_t(),
    )
    engine.record_observation(fact)
    rows = engine.get_current_state(subject="user", attribute="role")
    assert len(rows) == 1
    assert rows[0].current_value == "admin"
    assert rows[0].source_observation == "obs-fact"
    assert rows[0].current_since == _t()


def test_record_observation_does_not_project_state_for_event() -> None:
    store = _RecordingStore()
    engine = Engine(store)
    engine.record_observation(
        Observation(
            id="obs-event",
            content="visited HQ",
            kind=Kind.EVENT,
            observed_at=_t(),
        )
    )
    call_names = {name for name, _ in store.calls}
    assert "upsert_state" not in call_names
    assert engine.get_current_state() == []


def test_get_timeline_passes_through_to_store() -> None:
    store = _RecordingStore()
    engine = Engine(store)
    engine.get_timeline(_t(), _t(60))
    assert store.calls[-1] == ("get_observations", (_t(), _t(60), None, None, False))


def test_get_timeline_forwards_topic() -> None:
    store = _RecordingStore()
    engine = Engine(store)
    engine.get_timeline(_t(), _t(60), topic="acme")
    assert store.calls[-1] == ("get_observations", (_t(), _t(60), "acme", None, False))


def test_get_current_state_forwards_filters() -> None:
    store = _RecordingStore()
    engine = Engine(store)
    engine.get_current_state(subject="user", attribute="role")
    assert store.calls[-1] == ("list_states", ("user", "role"))


def test_get_current_state_no_filter_forwards_none_none() -> None:
    store = _RecordingStore()
    engine = Engine(store)
    engine.get_current_state()
    assert store.calls[-1] == ("list_states", (None, None))


def test_get_current_state_returns_store_results() -> None:
    states = [
        State(
            subject="user",
            attribute="role",
            current_value="staff",
            current_since=_t(),
            source_observation="obs-1",
        ),
        State(
            subject="user",
            attribute="employer",
            current_value="Acme",
            current_since=_t(),
            source_observation="obs-2",
        ),
    ]
    store = _RecordingStore(states=states)
    engine = Engine(store)
    rows = engine.get_current_state(subject="user")
    assert {(s.subject, s.attribute) for s in rows} == {("user", "role"), ("user", "employer")}


def test_get_narrative_returns_most_recent_matching() -> None:
    narratives = [
        Narrative(
            id="nar-old",
            content="older summary",
            period_start=_t(),
            period_end=_t(60),
            source_observations=[],
            generated_at=_t(10),
        ),
        Narrative(
            id="nar-new",
            content="newer summary",
            period_start=_t(),
            period_end=_t(60),
            source_observations=[],
            generated_at=_t(20),
        ),
    ]
    store = _RecordingStore(narratives=narratives)
    engine = Engine(store)
    got = engine.get_narrative(_t(-60), _t(120))
    assert got is not None
    assert got.id == "nar-new"


def test_get_narrative_returns_none_on_miss() -> None:
    store = _RecordingStore()
    engine = Engine(store)
    assert engine.get_narrative(_t(), _t(60)) is None


def test_get_narrative_forwards_topic() -> None:
    store = _RecordingStore()
    engine = Engine(store)
    engine.get_narrative(_t(), _t(60), topic="career")
    assert store.calls[-1] == ("get_narratives", (_t(), _t(60), "career", False))


def test_get_narrative_forwards_include_superseded() -> None:
    store = _RecordingStore()
    engine = Engine(store)
    engine.get_narrative(_t(), _t(60), include_superseded=True)
    assert store.calls[-1] == ("get_narratives", (_t(), _t(60), None, True))


def test_get_timeline_forwards_limit_and_include_superseded() -> None:
    store = _RecordingStore()
    engine = Engine(store)
    engine.get_timeline(_t(), _t(60), limit=50, include_superseded=True)
    assert store.calls[-1] == (
        "get_observations",
        (_t(), _t(60), None, 50, True),
    )


def test_schema_version_delegates_to_store() -> None:
    store = _RecordingStore()
    engine = Engine(store)
    assert engine.schema_version() == 1
    assert store.calls[-1] == ("schema_version", ())


def test_engine_accepts_optional_extractor() -> None:
    store = _RecordingStore()
    engine_no_ext = Engine(store)
    assert engine_no_ext._extractor is None

    class _StubExtractor:
        def extract(self, excerpt: str) -> Observation:
            return Observation(id="obs-x", content=excerpt, kind=Kind.REFLECTION, observed_at=_t())

    ext = _StubExtractor()
    engine_with_ext = Engine(store, extractor=ext)
    assert engine_with_ext._extractor is ext
