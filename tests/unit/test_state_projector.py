"""Unit tests for ``rivermind.core.projectors.state``.

Uses a minimal in-memory ``MemoryStore`` fake that honors the
stale-drop contract. Out-of-order and idempotency tests rely on that
contract being implemented — otherwise they'd pass for the wrong reason.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from rivermind.core.models import Kind, Observation, State
from rivermind.core.projectors.state import project_fact

if TYPE_CHECKING:
    from collections.abc import Sequence


def _t(offset_seconds: int = 0) -> datetime:
    return datetime(2026, 4, 19, 12, 0, 0, tzinfo=UTC) + timedelta(seconds=offset_seconds)


class _FakeStore:
    """Minimal MemoryStore impl covering only what the projector touches."""

    def __init__(self) -> None:
        self._states: list[State] = []

    def upsert_state(self, state: State) -> None:
        key = (state.subject, state.attribute)
        existing = next((s for s in self._states if (s.subject, s.attribute) == key), None)
        if existing is not None and state.current_since <= existing.current_since:
            return
        self._states = [s for s in self._states if (s.subject, s.attribute) != key]
        self._states.append(state)

    def snapshot(self) -> Sequence[State]:
        return list(self._states)


def _fact(
    id_: str,
    *,
    subject: str = "user",
    attribute: str = "employer",
    value: object | None = "Acme",
    observed_at: datetime | None = None,
) -> Observation:
    return Observation(
        id=id_,
        content=f"{subject} {attribute} is {value}",
        kind=Kind.FACT,
        subject=subject,
        attribute=attribute,
        value=value,
        observed_at=observed_at or _t(),
    )


def test_project_fact_creates_state_row() -> None:
    store = _FakeStore()
    project_fact(_fact("obs-1", value="Acme"), store)  # type: ignore[arg-type]
    rows = store.snapshot()
    assert len(rows) == 1
    row = rows[0]
    assert row.subject == "user"
    assert row.attribute == "employer"
    assert row.current_value == "Acme"
    assert row.current_since == _t()
    assert row.source_observation == "obs-1"


def test_project_fact_advances_state_on_newer_observation() -> None:
    store = _FakeStore()
    project_fact(_fact("obs-1", value="Globex", observed_at=_t(0)), store)  # type: ignore[arg-type]
    project_fact(_fact("obs-2", value="Acme", observed_at=_t(3600)), store)  # type: ignore[arg-type]
    rows = store.snapshot()
    assert len(rows) == 1
    assert rows[0].current_value == "Acme"
    assert rows[0].source_observation == "obs-2"
    assert rows[0].current_since == _t(3600)


def test_project_fact_drops_stale_observation() -> None:
    store = _FakeStore()
    # Newer fact arrives first.
    project_fact(_fact("obs-2", value="Acme", observed_at=_t(3600)), store)  # type: ignore[arg-type]
    # Older fact arrives after — out-of-order.
    project_fact(_fact("obs-1", value="Globex", observed_at=_t(0)), store)  # type: ignore[arg-type]
    rows = store.snapshot()
    assert len(rows) == 1
    assert rows[0].current_value == "Acme"
    assert rows[0].source_observation == "obs-2"


def test_project_fact_is_idempotent() -> None:
    store = _FakeStore()
    obs = _fact("obs-1", value="Acme")
    project_fact(obs, store)  # type: ignore[arg-type]
    project_fact(obs, store)  # type: ignore[arg-type]
    rows = store.snapshot()
    assert len(rows) == 1
    assert rows[0].source_observation == "obs-1"


def test_project_fact_preserves_null_value() -> None:
    store = _FakeStore()
    project_fact(
        Observation(
            id="obs-np",
            content="career framework is three questions",
            kind=Kind.FACT,
            subject="user",
            attribute="career_framework",
            observed_at=_t(),
        ),
        store,  # type: ignore[arg-type]
    )
    rows = store.snapshot()
    assert len(rows) == 1
    assert rows[0].current_value is None
    assert rows[0].attribute == "career_framework"


def test_project_fact_skips_events() -> None:
    store = _FakeStore()
    project_fact(
        Observation(
            id="obs-event",
            content="visited HQ",
            kind=Kind.EVENT,
            observed_at=_t(),
        ),
        store,  # type: ignore[arg-type]
    )
    assert store.snapshot() == []


def test_project_fact_skips_reflections() -> None:
    store = _FakeStore()
    project_fact(
        Observation(
            id="obs-refl",
            content="thinking about it",
            kind=Kind.REFLECTION,
            observed_at=_t(),
        ),
        store,  # type: ignore[arg-type]
    )
    assert store.snapshot() == []
