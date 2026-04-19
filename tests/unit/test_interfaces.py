"""Structural sanity checks for core/interfaces.py.

Protocols carry no behavior, so these tests are mostly lint-like guards:
they prove the interfaces are importable, methods have the expected
signatures, and dummy implementations satisfy ``isinstance`` thanks to
``@runtime_checkable``.
"""

from __future__ import annotations

import inspect
from datetime import UTC, datetime

from rivermind.core.interfaces import Embedder, Extractor, MemoryStore, Transport
from rivermind.core.models import Kind, Narrative, Observation, State


def _t() -> datetime:
    return datetime(2026, 4, 18, 12, 0, 0, tzinfo=UTC)


class _StubMemoryStore:
    def save_observation(self, observation: Observation) -> None:
        self.last = observation

    def get_observations(
        self,
        start: datetime,
        end: datetime,
        topic: str | None = None,
        *,
        limit: int | None = None,
        include_superseded: bool = False,
    ) -> list[Observation]:
        return []

    def upsert_state(self, state: State) -> None:
        pass

    def get_state(self, subject: str, attribute: str) -> State | None:
        return None

    def list_states(
        self,
        subject: str | None = None,
        attribute: str | None = None,
    ) -> list[State]:
        return []

    def clear_state(self) -> None:
        pass

    def save_narrative(self, narrative: Narrative) -> None:
        pass

    def mark_narrative_superseded(self, old_id: str, new_id: str) -> None:
        pass

    def get_narratives(
        self,
        period_start: datetime,
        period_end: datetime,
        topic: str | None = None,
        *,
        include_superseded: bool = False,
    ) -> list[Narrative]:
        return []

    def record_reeval(self, period_start: datetime, period_end: datetime) -> None:
        pass

    def reeval_exists(self, period_start: datetime, period_end: datetime) -> bool:
        return False

    def schema_version(self) -> int:
        return 1


class _StubEmbedder:
    def embed(self, texts: list[str]) -> list[list[float]]:
        return [[0.0] for _ in texts]


class _StubExtractor:
    def extract(self, excerpt: str) -> Observation:
        return Observation(
            id="obs-stub",
            content=excerpt,
            kind=Kind.REFLECTION,
            observed_at=_t(),
        )


def test_memory_store_isinstance_via_runtime_checkable() -> None:
    assert isinstance(_StubMemoryStore(), MemoryStore)


def test_embedder_isinstance_via_runtime_checkable() -> None:
    assert isinstance(_StubEmbedder(), Embedder)


def test_extractor_isinstance_via_runtime_checkable() -> None:
    assert isinstance(_StubExtractor(), Extractor)


def test_incomplete_store_is_not_an_instance() -> None:
    class Partial:
        def save_observation(self, observation: Observation) -> None:
            pass

    assert not isinstance(Partial(), MemoryStore)


def test_memory_store_method_names() -> None:
    expected = {
        "save_observation",
        "get_observations",
        "upsert_state",
        "get_state",
        "list_states",
        "clear_state",
        "save_narrative",
        "mark_narrative_superseded",
        "get_narratives",
        "record_reeval",
        "reeval_exists",
        "schema_version",
    }
    actual = {
        name
        for name, _ in inspect.getmembers(MemoryStore, predicate=inspect.isfunction)
        if not name.startswith("_")
    }
    assert actual == expected


def test_get_observations_signature() -> None:
    sig = inspect.signature(MemoryStore.get_observations)
    assert list(sig.parameters) == [
        "self",
        "start",
        "end",
        "topic",
        "limit",
        "include_superseded",
    ]
    assert sig.parameters["topic"].default is None
    assert sig.parameters["limit"].default is None
    assert sig.parameters["include_superseded"].default is False


def test_get_narratives_signature() -> None:
    sig = inspect.signature(MemoryStore.get_narratives)
    assert list(sig.parameters) == [
        "self",
        "period_start",
        "period_end",
        "topic",
        "include_superseded",
    ]
    assert sig.parameters["topic"].default is None
    assert sig.parameters["include_superseded"].default is False


def test_embed_signature() -> None:
    sig = inspect.signature(Embedder.embed)
    assert list(sig.parameters) == ["self", "texts"]


def test_extract_signature() -> None:
    sig = inspect.signature(Extractor.extract)
    assert list(sig.parameters) == ["self", "excerpt"]


def test_stub_store_round_trips_observation() -> None:
    store = _StubMemoryStore()
    obs = Observation(
        id="obs-1",
        content="hi",
        kind=Kind.EVENT,
        observed_at=_t(),
    )
    store.save_observation(obs)
    assert store.last == obs


def test_transport_is_exported() -> None:
    assert Transport is not None
