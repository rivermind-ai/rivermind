from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from rivermind.core.models import Kind, Narrative, Observation, State


def _t(seconds: int = 0) -> datetime:
    return datetime(2026, 4, 18, 12, 0, 0, tzinfo=UTC) + timedelta(seconds=seconds)


def test_kind_values_match_db_strings() -> None:
    assert Kind.FACT == "fact"
    assert Kind.EVENT == "event"
    assert Kind.REFLECTION == "reflection"
    assert {k.value for k in Kind} == {"fact", "event", "reflection"}


def test_fact_observation_requires_subject_attribute_value() -> None:
    with pytest.raises(ValidationError, match="subject, attribute, value"):
        Observation(id="obs-1", content="x", kind=Kind.FACT, observed_at=_t())


def test_fact_requires_value_even_when_subject_and_attribute_set() -> None:
    with pytest.raises(ValidationError, match="value"):
        Observation(
            id="obs-2",
            content="x",
            kind=Kind.FACT,
            subject="user",
            attribute="role",
            observed_at=_t(),
        )


def test_valid_fact_observation() -> None:
    obs = Observation(
        id="obs-3",
        content="user is admin",
        kind=Kind.FACT,
        subject="user",
        attribute="role",
        value="admin",
        observed_at=_t(),
    )
    assert obs.kind is Kind.FACT
    assert obs.value == "admin"


def test_event_observation_without_subject_is_valid() -> None:
    obs = Observation(
        id="obs-4",
        content="onsite at Stripe",
        kind=Kind.EVENT,
        observed_at=_t(),
    )
    assert obs.subject is None


def test_reflection_observation_is_valid() -> None:
    obs = Observation(
        id="obs-5",
        content="I think the design is sound",
        kind=Kind.REFLECTION,
        observed_at=_t(),
    )
    assert obs.kind is Kind.REFLECTION


def test_only_facts_can_be_superseded() -> None:
    with pytest.raises(ValidationError, match="only fact observations can be superseded"):
        Observation(
            id="obs-6",
            content="x",
            kind=Kind.EVENT,
            observed_at=_t(),
            superseded_by="obs-5",
        )


def test_fact_can_be_superseded() -> None:
    obs = Observation(
        id="obs-7",
        content="user works at Stripe",
        kind=Kind.FACT,
        subject="user",
        attribute="employer",
        value="Stripe",
        observed_at=_t(),
        superseded_by="obs-8",
    )
    assert obs.superseded_by == "obs-8"


def test_observation_id_must_be_prefixed() -> None:
    with pytest.raises(ValidationError, match="pattern"):
        Observation(id="1", content="x", kind=Kind.EVENT, observed_at=_t())


def test_superseded_by_must_be_prefixed_if_set() -> None:
    with pytest.raises(ValidationError, match="pattern"):
        Observation(
            id="obs-9",
            content="x",
            kind=Kind.FACT,
            subject="s",
            attribute="a",
            value="v",
            observed_at=_t(),
            superseded_by="not-obs",
        )


def test_observation_is_frozen() -> None:
    obs = Observation(
        id="obs-10",
        content="x",
        kind=Kind.EVENT,
        observed_at=_t(),
    )
    with pytest.raises(ValidationError):
        obs.content = "mutated"  # type: ignore[misc]


def test_observation_rejects_extra_fields() -> None:
    with pytest.raises(ValidationError):
        Observation(
            id="obs-11",
            content="x",
            kind=Kind.EVENT,
            observed_at=_t(),
            extra_field="nope",  # type: ignore[call-arg]
        )


def test_recorded_at_defaults_to_tz_aware_utc() -> None:
    obs = Observation(
        id="obs-12",
        content="x",
        kind=Kind.EVENT,
        observed_at=_t(),
    )
    assert obs.recorded_at.tzinfo is not None


def test_observation_roundtrip_via_model_dump() -> None:
    obs = Observation(
        id="obs-13",
        content="user works at Stripe",
        kind=Kind.FACT,
        subject="user",
        attribute="employer",
        value={"company": "Stripe", "start": 2026},
        observed_at=_t(),
        source_model="claude-opus",
        session_id="sess-1",
    )
    restored = Observation.model_validate(obs.model_dump())
    assert restored == obs


def test_state_basic_construction() -> None:
    s = State(
        subject="user",
        attribute="employer",
        current_value="Stripe",
        current_since=_t(),
        source_observation="obs-1",
    )
    assert s.subject == "user"
    assert s.current_value == "Stripe"


def test_state_is_frozen() -> None:
    s = State(
        subject="user",
        attribute="employer",
        current_value="Stripe",
        current_since=_t(),
        source_observation="obs-1",
    )
    with pytest.raises(ValidationError):
        s.current_value = "Other"  # type: ignore[misc]


def test_state_source_observation_must_be_prefixed() -> None:
    with pytest.raises(ValidationError, match="pattern"):
        State(
            subject="user",
            attribute="employer",
            current_value="Stripe",
            current_since=_t(),
            source_observation="123",
        )


def test_state_current_value_may_be_none() -> None:
    s = State(
        subject="user",
        attribute="nickname",
        current_value=None,
        current_since=_t(),
        source_observation="obs-1",
    )
    assert s.current_value is None


def test_narrative_basic_construction() -> None:
    n = Narrative(
        id="nar-1",
        content="Weekly summary of stripe onsite...",
        topic="career",
        period_start=_t(),
        period_end=_t(seconds=3600 * 24 * 7),
        source_observations=["obs-1", "obs-2"],
    )
    assert n.topic == "career"
    assert n.source_observations == ["obs-1", "obs-2"]


def test_narrative_id_must_be_prefixed() -> None:
    with pytest.raises(ValidationError, match="pattern"):
        Narrative(
            id="obs-1",
            content="x",
            period_start=_t(),
            period_end=_t(),
            source_observations=[],
        )


def test_narrative_superseded_by_must_be_prefixed_if_set() -> None:
    with pytest.raises(ValidationError, match="pattern"):
        Narrative(
            id="nar-2",
            content="x",
            period_start=_t(),
            period_end=_t(),
            source_observations=[],
            superseded_by="obs-3",
        )


def test_narrative_is_frozen() -> None:
    n = Narrative(
        id="nar-3",
        content="x",
        period_start=_t(),
        period_end=_t(),
        source_observations=[],
    )
    with pytest.raises(ValidationError):
        n.content = "other"  # type: ignore[misc]


def test_narrative_generated_at_defaults_to_tz_aware() -> None:
    n = Narrative(
        id="nar-4",
        content="x",
        period_start=_t(),
        period_end=_t(),
        source_observations=[],
    )
    assert n.generated_at.tzinfo is not None


def test_narrative_roundtrip_via_model_dump() -> None:
    n = Narrative(
        id="nar-5",
        content="weekly",
        topic="career",
        period_start=_t(),
        period_end=_t(seconds=3600),
        source_observations=["obs-1"],
    )
    restored = Narrative.model_validate(n.model_dump())
    assert restored == n
