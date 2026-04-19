"""Write-path projector for the ``state`` table.

Called on every fact write. Builds a :class:`State` row from the observation
and hands it to the store's ``upsert_state``. The store is responsible for
the stale-drop guard (only accepting writes whose ``current_since`` is
strictly newer than the existing row), so late-arriving observations cannot
clobber newer state.

Known fragility: ``save_observation`` and ``upsert_state`` run in separate
transactions at the store layer. If the process crashes between them, the
observation log contains a fact that is not yet reflected in the state
projection. State is rebuildable from the log, so this is recoverable but
not invisible. Eliminating the window requires a transaction-boundary API
on ``MemoryStore`` that does not exist yet.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from rivermind.core.models import Kind, State

if TYPE_CHECKING:
    from rivermind.core.interfaces import MemoryStore
    from rivermind.core.models import Observation


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
