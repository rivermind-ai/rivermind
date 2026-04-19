"""Core Engine: orchestrates the MemoryStore for transports to call into.

This is the single place business logic lives. Transports (MCP, CLI, REST)
are thin shims that translate their protocol to Engine method calls and
translate return values back.

- ``core/`` defines contracts and business logic.
- ``adapters/`` implements the contracts.
- Transports wire a concrete adapter into an ``Engine`` instance.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from datetime import datetime

    from rivermind.core.interfaces import Extractor, MemoryStore
    from rivermind.core.models import Narrative, Observation, State


class Engine:
    """Central orchestrator. Transports call into this; nothing else.

    Takes a :class:`MemoryStore` via constructor injection, plus an
    optional :class:`Extractor` used by later narrative-synthesis work.
    All methods are synchronous; async wrappers are a transport concern.
    """

    def __init__(
        self,
        store: MemoryStore,
        extractor: Extractor | None = None,
    ) -> None:
        self._store = store
        self._extractor = extractor

    def record_observation(self, observation: Observation) -> str:
        """Persist an observation and return its id.

        Model-level invariants (kind enum, fact requires subject/attribute/
        value, id prefix, only-facts-supersede) are enforced by Pydantic at
        ``Observation`` construction time. This method does not re-validate;
        callers that build an ``Observation`` have already passed that gate.

        Does **not** write to the ``state`` projection yet; that is a later
        addition.
        """
        self._store.save_observation(observation)
        return observation.id

    def get_timeline(
        self,
        start: datetime,
        end: datetime,
        topic: str | None = None,
    ) -> list[Observation]:
        """Return observations with ``observed_at`` in ``[start, end]``.

        If ``topic`` is given, it is forwarded to the store as an FTS5
        MATCH query against content. Results ordered by ``observed_at``
        ascending.
        """
        return self._store.get_observations(start, end, topic)

    def get_current_state(
        self,
        subject: str | None = None,
        attribute: str | None = None,
    ) -> list[State]:
        """Return current state rows filtered by optional ``subject`` and/or
        ``attribute``.

        Either or both arguments may be omitted to broaden the query.
        Returns an empty list when nothing matches.
        """
        return self._store.list_states(subject, attribute)

    def get_narrative(
        self,
        period_start: datetime,
        period_end: datetime,
        topic: str | None = None,
    ) -> Narrative | None:
        """Return the most recent narrative whose window overlaps
        ``[period_start, period_end]``, optionally filtered by ``topic``.

        The store returns matches in ``generated_at`` descending order; this
        method picks the first (most recent) or returns ``None`` if no
        narrative overlaps.
        """
        matches = self._store.get_narratives(period_start, period_end, topic)
        return matches[0] if matches else None
