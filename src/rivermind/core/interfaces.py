"""Core interfaces: the five seams.

Defined in ``core/``; implemented in ``adapters/``. ``core/`` never imports
from ``adapters/``. These Protocols are the only contracts adapters must
satisfy; everything else stays concrete.

The five seams:

1. ``MemoryStore`` - persistence for observations, state, and narratives.
2. ``Embedder`` - text-to-vector (stubbed; not used in v0.1, declared here
   so storage and engine code that may later want embeddings can depend on
   this type without a refactor).
3. ``Extractor`` - excerpt-to-Observation (used only by narrative synthesis
   in v0.1).
4. ``Transport`` - server factory. Kept as a loose callable alias rather
   than a Protocol so ``core/`` does not couple to any framework's types.
5. The ``Kind`` vocabulary - defined in ``core.models`` as a ``StrEnum``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from datetime import datetime

    from rivermind.core.models import Narrative, Observation, State


@runtime_checkable
class MemoryStore(Protocol):
    """Persistence for observations (append-only log) and derived projections.

    All methods are synchronous. Async callers should wrap in
    ``asyncio.to_thread`` if the event loop cannot tolerate SQLite latency.
    """

    def save_observation(self, observation: Observation) -> None:
        """Append an observation to the log.

        Observations are immutable once saved. The one permitted update is
        setting ``superseded_by`` on an existing fact observation, which is
        handled via a separate write path (not this method).
        """
        ...

    def get_observations(
        self,
        start: datetime,
        end: datetime,
        topic: str | None = None,
        *,
        limit: int | None = None,
        include_superseded: bool = False,
    ) -> list[Observation]:
        """Return observations with ``observed_at`` in ``[start, end]``.

        If ``topic`` is given, it is interpreted as an FTS5 MATCH query
        against the observation content. Results are ordered by
        ``observed_at`` ascending.

        By default, observations whose ``superseded_by`` is non-null are
        filtered out; pass ``include_superseded=True`` to include them.
        Pass a positive ``limit`` to cap the number of rows returned.
        """
        ...

    def upsert_state(self, state: State) -> None:
        """Insert or update the state row for ``(subject, attribute)``.

        The ``source_observation`` id on the state must reference an
        observation the store has already seen; implementations may enforce
        this via foreign key, or defensively at the application layer.
        """
        ...

    def get_state(self, subject: str, attribute: str) -> State | None:
        """Return the current state for ``(subject, attribute)`` or ``None``."""
        ...

    def list_states(
        self,
        subject: str | None = None,
        attribute: str | None = None,
    ) -> list[State]:
        """Return state rows filtered by optional ``subject`` and/or ``attribute``.

        With neither filter, returns every row. Results are ordered by
        ``(subject, attribute)`` ascending for stability.
        """
        ...

    def clear_state(self) -> None:
        """Delete every row from the ``state`` projection.

        Only used for drift recovery (rebuild). Never called on the write
        path. Observations and narratives are untouched.
        """
        ...

    def save_narrative(self, narrative: Narrative) -> None:
        """Persist a synthesized narrative.

        Narratives are versioned, never mutated in place. To replace one,
        save a new narrative and separately set the old narrative's
        ``superseded_by`` pointer.
        """
        ...

    def get_narratives(
        self,
        period_start: datetime,
        period_end: datetime,
        topic: str | None = None,
        *,
        include_superseded: bool = False,
    ) -> list[Narrative]:
        """Return narratives whose ``[period_start, period_end]`` window
        overlaps the requested range.

        If ``topic`` is given, it is an exact match filter on the
        ``Narrative.topic`` field (not a content search). Results are
        ordered by ``generated_at`` descending so the most recent synthesis
        comes first.

        By default, narratives whose ``superseded_by`` is non-null are
        filtered out; pass ``include_superseded=True`` to include them.
        """
        ...

    def schema_version(self) -> int:
        """Return the currently-applied schema version (0 if no schema yet).

        Used by health checks and migration logic to confirm the store is
        usable and at a known version.
        """
        ...


@runtime_checkable
class Embedder(Protocol):
    """Text-to-vector embedder.

    Not used in v0.1 (FTS5 covers topic search). Declared now so storage
    and engine code that may later want embeddings can depend on this type
    without a refactor.
    """

    def embed(self, texts: list[str]) -> list[list[float]]:
        """Return one fixed-dimension vector per input text.

        Implementations must produce vectors of consistent dimension across
        calls. Result order matches input order.
        """
        ...


@runtime_checkable
class Extractor(Protocol):
    """Produces a single ``Observation`` from a rendered excerpt of prior
    observations.

    In v0.1 this is used only by narrative synthesis; primary extraction at
    the MCP tool-call site is done by the calling LLM via a strict JSON
    schema and does not go through this interface.
    """

    def extract(self, excerpt: str) -> Observation:
        """Produce one Observation summarizing ``excerpt``.

        Implementations own the prompt and the schema enforcement.
        """
        ...


Transport = "Callable[..., Any]"
"""Server factory alias.

Implementations return a runnable server application (FastAPI instance,
CLI entry point, background worker). Kept as a forward-reference string
alias rather than a Protocol so this module does not import any transport
framework. The application bootstrap in ``config.py`` is what wires a
concrete transport to the rest of the system.
"""
