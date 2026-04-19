"""Entity id generation.

Ids are UUIDv4 with an entity-type prefix. The prefix makes ids
self-describing in logs and in foreign-key columns; the UUID part
provides uniqueness across processes and machines.
"""

from __future__ import annotations

from uuid import uuid4


def new_observation_id() -> str:
    """Return a fresh observation id of the form ``obs-<uuid4>``."""
    return f"obs-{uuid4()}"


def new_narrative_id() -> str:
    """Return a fresh narrative id of the form ``nar-<uuid4>``."""
    return f"nar-{uuid4()}"
