"""Unit tests for ``rivermind.core.projectors.narrative.synthesize_narrative``.

Uses a minimal in-memory fake store and a recorded ``NarrativeSynthesizer``
so the tests are deterministic, fast, and don't require any LLM SDK.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from rivermind.core.models import Kind, Narrative, Observation
from rivermind.core.projectors.narrative import synthesize_narrative

if TYPE_CHECKING:
    import pytest


def _t(offset_seconds: int = 0) -> datetime:
    return datetime(2026, 4, 19, 12, 0, 0, tzinfo=UTC) + timedelta(seconds=offset_seconds)


@dataclass
class _RecordedCall:
    prompt: str


class _FakeSynthesizer:
    def __init__(self, *, response: str = "synthesized narrative body") -> None:
        self.response = response
        self.calls: list[_RecordedCall] = []

    def synthesize(self, prompt: str) -> str:
        self.calls.append(_RecordedCall(prompt=prompt))
        return self.response


@dataclass
class _FakeStore:
    """Minimal MemoryStore impl covering what the narrative projector touches."""

    observations: list[Observation] = field(default_factory=list)
    narratives: list[Narrative] = field(default_factory=list)

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
        return sorted(hits, key=lambda o: o.observed_at)

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


def _seed_observation(
    store: _FakeStore,
    *,
    id_: str,
    content: str,
    kind: Kind = Kind.EVENT,
    observed_at: datetime | None = None,
) -> None:
    store.observations.append(
        Observation(
            id=id_,
            content=content,
            kind=kind,
            observed_at=observed_at or _t(),
        )
    )


def test_synthesize_skipped_when_env_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("RIVERMIND_API_KEY", raising=False)
    store = _FakeStore()
    _seed_observation(store, id_="obs-1", content="something")
    result = synthesize_narrative(
        _t(-60),
        _t(60),
        None,
        store,
        _FakeSynthesizer(),  # type: ignore[arg-type]
    )
    assert result is None
    assert store.narratives == []


def test_synthesize_skipped_when_no_synthesizer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("RIVERMIND_API_KEY", "test-key")
    store = _FakeStore()
    _seed_observation(store, id_="obs-1", content="something")
    result = synthesize_narrative(_t(-60), _t(60), None, store, None)  # type: ignore[arg-type]
    assert result is None
    assert store.narratives == []


def test_synthesize_skipped_when_no_observations_in_period(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("RIVERMIND_API_KEY", "test-key")
    store = _FakeStore()
    result = synthesize_narrative(
        _t(-60),
        _t(60),
        None,
        store,
        _FakeSynthesizer(),  # type: ignore[arg-type]
    )
    assert result is None
    assert store.narratives == []


def test_synthesize_persists_narrative_and_source_observations(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("RIVERMIND_API_KEY", "test-key")
    store = _FakeStore()
    _seed_observation(store, id_="obs-1", content="first", observed_at=_t(0))
    _seed_observation(store, id_="obs-2", content="second", observed_at=_t(60))

    synth = _FakeSynthesizer(response="the story")
    result = synthesize_narrative(
        _t(-60),
        _t(120),
        None,
        store,
        synth,  # type: ignore[arg-type]
    )

    assert result is not None
    assert result.content == "the story"
    assert result.period_start == _t(-60)
    assert result.period_end == _t(120)
    assert result.topic is None
    assert result.source_observations == ["obs-1", "obs-2"]
    assert result.id.startswith("nar-")
    assert result.superseded_by is None
    assert store.narratives == [result]


def test_synthesize_passes_rendered_prompt_to_synthesizer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("RIVERMIND_API_KEY", "test-key")
    store = _FakeStore()
    _seed_observation(store, id_="obs-1", content="visited Acme HQ", observed_at=_t())

    synth = _FakeSynthesizer()
    synthesize_narrative(
        _t(-60),
        _t(60),
        "career",
        store,
        synth,  # type: ignore[arg-type]
    )

    assert len(synth.calls) == 1
    prompt = synth.calls[0].prompt
    assert "visited Acme HQ" in prompt
    assert "career" in prompt
    assert "rivermind" in prompt.lower()


def test_synthesize_marks_prior_narrative_superseded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("RIVERMIND_API_KEY", "test-key")
    store = _FakeStore()
    _seed_observation(store, id_="obs-1", content="anything", observed_at=_t())

    prior = Narrative(
        id="nar-old",
        content="older draft",
        topic="career",
        period_start=_t(-60),
        period_end=_t(60),
        source_observations=["obs-1"],
        generated_at=_t(-3600),
    )
    store.save_narrative(prior)

    result = synthesize_narrative(
        _t(-60),
        _t(60),
        "career",
        store,  # type: ignore[arg-type]
        _FakeSynthesizer(response="new draft"),
    )

    assert result is not None
    assert result.content == "new draft"

    prior_now = next(n for n in store.narratives if n.id == "nar-old")
    assert prior_now.superseded_by == result.id


def test_synthesize_does_not_touch_narrative_for_different_topic(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("RIVERMIND_API_KEY", "test-key")
    store = _FakeStore()
    _seed_observation(store, id_="obs-1", content="x", observed_at=_t())

    other_topic = Narrative(
        id="nar-fitness",
        content="fitness notes",
        topic="fitness",
        period_start=_t(-60),
        period_end=_t(60),
        source_observations=["obs-1"],
    )
    store.save_narrative(other_topic)

    synthesize_narrative(
        _t(-60),
        _t(60),
        "career",
        store,  # type: ignore[arg-type]
        _FakeSynthesizer(),
    )

    unchanged = next(n for n in store.narratives if n.id == "nar-fitness")
    assert unchanged.superseded_by is None


def test_synthesize_does_not_touch_narrative_with_different_period(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("RIVERMIND_API_KEY", "test-key")
    store = _FakeStore()
    _seed_observation(store, id_="obs-1", content="x", observed_at=_t())

    # Prior narrative overlaps the new window but has different exact
    # boundaries; should be left alone.
    overlapping = Narrative(
        id="nar-overlap",
        content="overlap notes",
        topic="career",
        period_start=_t(-120),
        period_end=_t(30),
        source_observations=["obs-1"],
    )
    store.save_narrative(overlapping)

    synthesize_narrative(
        _t(-60),
        _t(60),
        "career",
        store,  # type: ignore[arg-type]
        _FakeSynthesizer(),
    )

    unchanged = next(n for n in store.narratives if n.id == "nar-overlap")
    assert unchanged.superseded_by is None
