"""Unit tests for ``rivermind.core.ids``."""

from __future__ import annotations

from uuid import UUID

from rivermind.core.ids import new_narrative_id, new_observation_id


def _uuid_part(id_: str, prefix: str) -> str:
    assert id_.startswith(prefix)
    return id_[len(prefix) :]


def test_new_observation_id_has_obs_prefix_and_valid_uuid() -> None:
    id_ = new_observation_id()
    assert id_.startswith("obs-")
    UUID(_uuid_part(id_, "obs-"))  # raises if malformed


def test_new_narrative_id_has_nar_prefix_and_valid_uuid() -> None:
    id_ = new_narrative_id()
    assert id_.startswith("nar-")
    UUID(_uuid_part(id_, "nar-"))


def test_observation_ids_are_unique_across_calls() -> None:
    ids = {new_observation_id() for _ in range(1000)}
    assert len(ids) == 1000


def test_narrative_ids_are_unique_across_calls() -> None:
    ids = {new_narrative_id() for _ in range(1000)}
    assert len(ids) == 1000
