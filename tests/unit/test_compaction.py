"""Unit tests for ``rivermind.core.projectors.compaction.compact``.

Uses a fake store that implements the subset of ``MemoryStore`` compaction
touches; assertions operate on the fake's internal observation list.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from rivermind.core.models import Kind, Observation
from rivermind.core.projectors.compaction import CompactionSummary, compact

_ANCHOR = datetime(2026, 4, 1, 12, 0, 0, tzinfo=UTC)


def _t(minutes: int = 0) -> datetime:
    return _ANCHOR + timedelta(minutes=minutes)


_SESSION_A = "12345678-1234-1234-1234-123456789012"
_SESSION_B = "87654321-4321-4321-4321-210987654321"


@dataclass
class _FakeStore:
    """Minimal MemoryStore covering only what `compact` touches."""

    observations: list[Observation] = field(default_factory=list)
    raise_on_ids: set[str] = field(default_factory=set)

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

    def mark_observation_superseded(self, old_id: str, new_id: str) -> None:
        if old_id in self.raise_on_ids:
            raise RuntimeError(f"forced failure on {old_id}")
        for i, o in enumerate(self.observations):
            if o.id == old_id:
                self.observations[i] = o.model_copy(update={"superseded_by": new_id})
                return
        raise ValueError(f"observation {old_id!r} not found")


def _fact(
    id_: str,
    *,
    at: datetime,
    subject: str = "user",
    attribute: str = "employer",
    value: str = "Acme",
    content: str | None = None,
    session_id: str | None = None,
    superseded_by: str | None = None,
) -> Observation:
    return Observation(
        id=id_,
        content=content or f"{subject}.{attribute} = {value}",
        kind=Kind.FACT,
        subject=subject,
        attribute=attribute,
        value=value,
        observed_at=at,
        session_id=session_id,
        superseded_by=superseded_by,
    )


def _superseded_by(store: _FakeStore, obs_id: str) -> str | None:
    return next(o.superseded_by for o in store.observations if o.id == obs_id)


# ---- empty / no-op --------------------------------------------------------


def test_compact_empty_store() -> None:
    store = _FakeStore()
    summary = compact(store)
    assert summary == CompactionSummary(superseded_count=0, deduped_count=0, warnings=[])


def test_compact_no_duplicates_no_slot_conflicts() -> None:
    store = _FakeStore(
        observations=[
            _fact("obs-1", at=_t(0), subject="user", attribute="employer", value="Acme"),
            _fact("obs-2", at=_t(60), subject="user", attribute="role", value="staff"),
        ]
    )
    summary = compact(store)
    assert summary.superseded_count == 0
    assert summary.deduped_count == 0
    assert _superseded_by(store, "obs-1") is None
    assert _superseded_by(store, "obs-2") is None


# ---- per-slot latest-wins -------------------------------------------------


def test_compact_single_slot_keeps_latest_marks_older() -> None:
    store = _FakeStore(
        observations=[
            _fact("obs-older", at=_t(0), value="Globex"),
            _fact("obs-newer", at=_t(60), value="Acme"),
        ]
    )
    summary = compact(store)
    assert summary.superseded_count == 1
    assert summary.deduped_count == 0
    assert _superseded_by(store, "obs-older") == "obs-newer"
    assert _superseded_by(store, "obs-newer") is None


def test_compact_multiple_slots_resolved_independently() -> None:
    store = _FakeStore(
        observations=[
            _fact("obs-emp-1", at=_t(0), attribute="employer", value="Globex"),
            _fact("obs-emp-2", at=_t(60), attribute="employer", value="Acme"),
            _fact("obs-role-1", at=_t(0), attribute="role", value="junior"),
            _fact("obs-role-2", at=_t(60), attribute="role", value="staff"),
        ]
    )
    summary = compact(store)
    assert summary.superseded_count == 2
    assert _superseded_by(store, "obs-emp-1") == "obs-emp-2"
    assert _superseded_by(store, "obs-role-1") == "obs-role-2"


def test_compact_already_superseded_not_revisited() -> None:
    # obs-1 is already superseded by obs-2. compact should ignore it
    # (it won't show up in get_observations with the default filter).
    store = _FakeStore(
        observations=[
            _fact("obs-1", at=_t(0), value="Globex", superseded_by="obs-2"),
            _fact("obs-2", at=_t(60), value="Acme"),
        ]
    )
    summary = compact(store)
    assert summary.superseded_count == 0
    assert _superseded_by(store, "obs-1") == "obs-2"
    assert _superseded_by(store, "obs-2") is None


def test_compact_events_and_reflections_untouched() -> None:
    store = _FakeStore(
        observations=[
            _fact("obs-fact-a", at=_t(0), value="Globex"),
            _fact("obs-fact-b", at=_t(60), value="Acme"),
            Observation(
                id="obs-event",
                content="visited HQ",
                kind=Kind.EVENT,
                observed_at=_t(30),
            ),
            Observation(
                id="obs-reflection",
                content="went well",
                kind=Kind.REFLECTION,
                observed_at=_t(45),
            ),
        ]
    )
    summary = compact(store)
    assert summary.superseded_count == 1
    assert _superseded_by(store, "obs-event") is None
    assert _superseded_by(store, "obs-reflection") is None


# ---- same-content-session dedup -------------------------------------------


def test_compact_dedup_same_content_and_session_in_window() -> None:
    store = _FakeStore(
        observations=[
            _fact(
                "obs-1",
                at=_t(0),
                subject="user",
                attribute="beverage",
                value="coffee",
                content="likes coffee",
                session_id=_SESSION_A,
            ),
            _fact(
                "obs-2",
                at=_t(2),  # 2 min later, within the 5-min window
                subject="user",
                attribute="beverage",
                value="coffee",
                content="likes coffee",
                session_id=_SESSION_A,
            ),
        ]
    )
    summary = compact(store)
    assert summary.deduped_count == 1
    assert summary.superseded_count == 0
    assert _superseded_by(store, "obs-1") == "obs-2"


def test_compact_dedup_different_session_not_deduped() -> None:
    store = _FakeStore(
        observations=[
            _fact(
                "obs-1",
                at=_t(0),
                subject="user",
                attribute="beverage-1",
                content="likes coffee",
                session_id=_SESSION_A,
            ),
            _fact(
                "obs-2",
                at=_t(2),
                subject="user",
                attribute="beverage-2",
                content="likes coffee",
                session_id=_SESSION_B,
            ),
        ]
    )
    summary = compact(store)
    assert summary.deduped_count == 0


def test_compact_dedup_outside_window_not_deduped() -> None:
    store = _FakeStore(
        observations=[
            _fact(
                "obs-1",
                at=_t(0),
                subject="user",
                attribute="beverage-1",
                content="likes coffee",
                session_id=_SESSION_A,
            ),
            _fact(
                "obs-2",
                at=_t(6),  # 6 minutes later — outside window
                subject="user",
                attribute="beverage-2",
                content="likes coffee",
                session_id=_SESSION_A,
            ),
        ]
    )
    summary = compact(store)
    assert summary.deduped_count == 0


def test_compact_dedup_null_session_skipped() -> None:
    store = _FakeStore(
        observations=[
            _fact(
                "obs-1",
                at=_t(0),
                subject="user",
                attribute="beverage-1",
                content="likes coffee",
                session_id=None,
            ),
            _fact(
                "obs-2",
                at=_t(2),
                subject="user",
                attribute="beverage-2",
                content="likes coffee",
                session_id=None,
            ),
        ]
    )
    summary = compact(store)
    assert summary.deduped_count == 0


# ---- error handling -------------------------------------------------------


def test_compact_handles_mark_failure_gracefully() -> None:
    store = _FakeStore(
        observations=[
            _fact("obs-older", at=_t(0), value="Globex"),
            _fact("obs-middle", at=_t(30), value="Initech"),
            _fact("obs-newer", at=_t(60), value="Acme"),
        ],
        raise_on_ids={"obs-older"},
    )
    summary = compact(store)
    # obs-middle still gets marked; obs-older's failure is captured
    assert summary.superseded_count == 1
    assert len(summary.warnings) == 1
    assert "obs-older" in summary.warnings[0]
    assert _superseded_by(store, "obs-middle") == "obs-newer"
    assert _superseded_by(store, "obs-older") is None


# ---- combined behavior ----------------------------------------------------


def test_compact_dedup_then_slot_both_fire() -> None:
    # Two different slots: employer (slot conflict) and beverage (dedup).
    store = _FakeStore(
        observations=[
            _fact(
                "obs-emp-old",
                at=_t(0),
                attribute="employer",
                value="Globex",
            ),
            _fact(
                "obs-emp-new",
                at=_t(60),
                attribute="employer",
                value="Acme",
            ),
            _fact(
                "obs-bev-1",
                at=_t(0),
                subject="user",
                attribute="beverage",
                value="coffee",
                content="likes coffee",
                session_id=_SESSION_A,
            ),
            _fact(
                "obs-bev-2",
                at=_t(3),
                subject="user",
                attribute="beverage",
                value="coffee",
                content="likes coffee",
                session_id=_SESSION_A,
            ),
        ]
    )
    summary = compact(store)
    assert summary.deduped_count == 1
    assert summary.superseded_count == 1
    assert _superseded_by(store, "obs-emp-old") == "obs-emp-new"
    assert _superseded_by(store, "obs-bev-1") == "obs-bev-2"
