"""Core data models.

Pure types shared across the engine, adapters, and MCP transport. No I/O,
no framework coupling. Field shapes mirror the v1 SQL schema in
``adapters/stores/migrations/001_initial.sql``.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator


class Kind(StrEnum):
    """Observation kind.

    Only ``FACT`` participates in ``(subject, attribute)`` supersession;
    ``EVENT`` and ``REFLECTION`` are append-only and never supersede.
    """

    FACT = "fact"
    EVENT = "event"
    REFLECTION = "reflection"


_FROZEN = ConfigDict(frozen=True, extra="forbid")


class Observation(BaseModel):
    """A single append-only entry in the observation log."""

    model_config = _FROZEN

    id: str = Field(pattern=r"^obs-")
    content: str
    kind: Kind
    subject: str | None = None
    attribute: str | None = None
    value: JsonValue | None = None
    observed_at: datetime
    recorded_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    source_model: str | None = None
    session_id: str | None = None
    superseded_by: str | None = Field(default=None, pattern=r"^obs-")

    @model_validator(mode="after")
    def _validate_invariants(self) -> Observation:
        if self.kind is Kind.FACT:
            missing = [
                name
                for name, val in (
                    ("subject", self.subject),
                    ("attribute", self.attribute),
                    ("value", self.value),
                )
                if val is None
            ]
            if missing:
                raise ValueError(f"fact observations require {', '.join(missing)}; got None")
        if self.superseded_by is not None and self.kind is not Kind.FACT:
            raise ValueError("only fact observations can be superseded")
        return self


class State(BaseModel):
    """Current value of a ``(subject, attribute)`` slot.

    Projection over the observation log; rebuildable, never authoritative.
    """

    model_config = _FROZEN

    subject: str
    attribute: str
    current_value: JsonValue | None = None
    current_since: datetime
    source_observation: str = Field(pattern=r"^obs-")


class Narrative(BaseModel):
    """LLM-synthesized summary over a time window of observations.

    Versioned via ``superseded_by``; never mutated in place.
    """

    model_config = _FROZEN

    id: str = Field(pattern=r"^nar-")
    content: str
    topic: str | None = None
    period_start: datetime
    period_end: datetime
    source_observations: list[str]
    generated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    superseded_by: str | None = Field(default=None, pattern=r"^nar-")
