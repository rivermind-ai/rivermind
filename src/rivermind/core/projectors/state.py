"""Write-path projector for the ``state`` table.

Called on every fact write. Builds a :class:`State` row from the observation
and hands it to the store's ``upsert_state``. The store is responsible for
the stale-drop guard (only accepting writes whose ``current_since`` is
strictly newer than the existing row), so late-arriving observations cannot
clobber newer state.

Also exposes :func:`rebuild_state` for drift recovery: drop all state rows,
then replay every non-superseded fact observation in ``observed_at`` order.

Known fragility: ``save_observation`` and ``upsert_state`` run in separate
transactions at the store layer. Same applies to the rebuild's
``clear_state`` + replay loop. If the process crashes partway, state is
empty or partial; re-running the rebuild recovers. Eliminating the window
requires a transaction-boundary API on ``MemoryStore`` that does not exist
yet.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from rivermind.core.models import Kind, State

if TYPE_CHECKING:
    from collections.abc import Callable

    from rivermind.core.interfaces import MemoryStore
    from rivermind.core.models import Observation


_EPOCH = datetime.min.replace(tzinfo=UTC)
_FAR_FUTURE = datetime.max.replace(tzinfo=UTC)
_PROGRESS_EVERY = 1000


def project_fact(observation: Observation, store: MemoryStore) -> None:
    """Upsert the state row for ``(subject, attribute)`` from a fact.

    A no-op for non-fact observations and for facts missing ``subject`` or
    ``attribute``. Idempotent: repeated calls with the same observation
    produce no state change (the store's stale-drop guard rejects writes
    whose ``current_since`` is not strictly newer).
    """
    if observation.kind is not Kind.FACT:
        return
    if observation.subject is None or observation.attribute is None:
        return
    store.upsert_state(
        State(
            subject=observation.subject,
            attribute=observation.attribute,
            current_value=observation.value,
            current_since=observation.observed_at,
            source_observation=observation.id,
        )
    )


@dataclass(frozen=True)
class RebuildSummary:
    """Return value from :func:`rebuild_state`."""

    rows_rebuilt: int
    duration_seconds: float
    warnings: list[str] = field(default_factory=list)


def _default_progress(done: int, total: int) -> None:
    print(f"rebuilt {done}/{total} observations")


def rebuild_state(
    store: MemoryStore,
    *,
    on_progress: Callable[[int, int], None] | None = None,
) -> RebuildSummary:
    """Drop every state row, then replay every non-superseded fact.

    Observations are processed in ``observed_at`` ascending order; the
    store's stale-drop guard ensures the newest fact per slot wins.
    ``on_progress`` is called every 1000 observations and once more at
    completion with ``(total, total)``; defaults to a one-line print.
    """
    started = time.perf_counter()
    warnings: list[str] = []
    store.clear_state()

    observations = store.get_observations(_EPOCH, _FAR_FUTURE, include_superseded=False)
    total = len(observations)
    progress = on_progress or _default_progress

    rows_rebuilt = 0
    for idx, obs in enumerate(observations, start=1):
        if obs.kind is Kind.FACT:
            if obs.subject is None or obs.attribute is None:
                warnings.append(f"fact {obs.id} missing subject or attribute; skipped")
            else:
                project_fact(obs, store)
                rows_rebuilt += 1
        if idx % _PROGRESS_EVERY == 0:
            progress(idx, total)
    progress(total, total)

    duration = time.perf_counter() - started
    return RebuildSummary(
        rows_rebuilt=rows_rebuilt,
        duration_seconds=duration,
        warnings=warnings,
    )
