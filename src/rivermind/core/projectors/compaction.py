"""Observation compaction.

Soft-deletes redundant fact observations via ``superseded_by``. Runs as a
phase of the re-eval pipeline. Two passes, in order:

1. Same-content-and-session dedup within a 5-minute window. Catches
   accidental duplicate writes from the calling LLM re-submitting a tool
   call. Keeps the latest; marks earlier ones superseded.
2. Per-slot latest-wins for ``(subject, attribute)``. Keeps the fact with
   the newest ``observed_at``; marks all earlier ones superseded.

Soft-delete only. Observations stay in the log and surface when
``include_superseded=True``. The DB CHECK constraint scopes supersession
to ``kind='fact'``, so events and reflections are never touched here.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import structlog

from rivermind.core.models import Kind

if TYPE_CHECKING:
    from rivermind.core.interfaces import MemoryStore
    from rivermind.core.models import Observation

_logger = structlog.get_logger()

_EPOCH = datetime.min.replace(tzinfo=UTC)
_FAR_FUTURE = datetime.max.replace(tzinfo=UTC)
_DEDUP_WINDOW = timedelta(minutes=5)
_MIN_GROUP_SIZE = 2


@dataclass(frozen=True)
class CompactionSummary:
    """Return value from :func:`compact`.

    ``superseded_count`` and ``deduped_count`` are disjoint; a single
    observation is marked at most once across the two passes. ``warnings``
    holds the message of every ``mark_observation_superseded`` call that
    raised; the loop continues past per-mark failures.
    """

    superseded_count: int
    deduped_count: int
    warnings: list[str] = field(default_factory=list)


def compact(store: MemoryStore) -> CompactionSummary:
    """Collapse redundant fact observations via ``superseded_by``.

    Loads every non-superseded fact once, runs dedup, then per-slot
    latest-wins on whatever survives dedup. A failing mark appends to
    ``warnings`` and the loop continues. Returns the counts and any
    warnings for the re-eval pipeline to log.
    """
    observations = store.get_observations(_EPOCH, _FAR_FUTURE, include_superseded=False)
    facts = [o for o in observations if o.kind is Kind.FACT]

    warnings: list[str] = []
    deduped_count, survivors = _dedup_same_content_session(store, facts, warnings)
    superseded_count = _supersede_older_per_slot(store, survivors, warnings)

    return CompactionSummary(
        superseded_count=superseded_count,
        deduped_count=deduped_count,
        warnings=warnings,
    )


def _dedup_same_content_session(
    store: MemoryStore,
    facts: list[Observation],
    warnings: list[str],
) -> tuple[int, list[Observation]]:
    """Mark older duplicates in each (content, session_id) group.

    Duplicate = same content, same non-null session_id, observed within
    ``_DEDUP_WINDOW`` of a later observation in the same group. Keeps the
    latest; marks the rest superseded. Returns the count marked and the
    facts that were NOT marked (survivors for the next pass).
    """
    groups: dict[tuple[str, str], list[Observation]] = {}
    orphans: list[Observation] = []
    for obs in facts:
        if obs.session_id is None:
            orphans.append(obs)
            continue
        groups.setdefault((obs.content, obs.session_id), []).append(obs)

    deduped = 0
    survivors: list[Observation] = list(orphans)

    for (_content, _session), group in groups.items():
        if len(group) < _MIN_GROUP_SIZE:
            survivors.extend(group)
            continue
        group.sort(key=lambda o: o.observed_at)
        winner = group[-1]
        to_supersede: list[Observation] = []
        for older in group[:-1]:
            if (winner.observed_at - older.observed_at) <= _DEDUP_WINDOW:
                to_supersede.append(older)
            else:
                survivors.append(older)
        for older in to_supersede:
            try:
                store.mark_observation_superseded(older.id, winner.id)
                deduped += 1
            except Exception as exc:
                warnings.append(f"dedup mark failed for {older.id}: {exc}")
                survivors.append(older)
        survivors.append(winner)

    return deduped, survivors


def _supersede_older_per_slot(
    store: MemoryStore,
    facts: list[Observation],
    warnings: list[str],
) -> int:
    """Mark older facts in each (subject, attribute) group superseded.

    Only applies to groups of size >= 2. Winner is the fact with the
    largest ``observed_at``; ties are broken by the caller's fetch order
    (the store orders by observed_at ascending, so the later-recorded
    fact wins on ties).
    """
    slots: dict[tuple[str, str], list[Observation]] = {}
    for obs in facts:
        if obs.subject is None or obs.attribute is None:
            continue
        slots.setdefault((obs.subject, obs.attribute), []).append(obs)

    marked = 0
    for group in slots.values():
        if len(group) < _MIN_GROUP_SIZE:
            continue
        group.sort(key=lambda o: o.observed_at)
        winner = group[-1]
        for older in group[:-1]:
            try:
                store.mark_observation_superseded(older.id, winner.id)
                marked += 1
            except Exception as exc:
                warnings.append(f"slot mark failed for {older.id}: {exc}")

    return marked
