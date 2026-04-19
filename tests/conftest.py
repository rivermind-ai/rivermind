"""Shared fixtures across unit, integration, and contract suites.

- ``now`` / ``t``: deterministic clock so timestamp assertions don't flake.
- ``tmp_db_path``: file path for a scratch SQLite database.
- ``seeded_observations``: a small, canonical set of observations suitable
  for backfilling a store in tests that need existing data to query over.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import pytest

from rivermind.core.models import Kind, Observation

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path


REFERENCE_TIME = datetime(2026, 4, 18, 12, 0, 0, tzinfo=UTC)


@pytest.fixture
def now() -> datetime:
    """Fixed reference time used as the anchor for all other time fixtures."""
    return REFERENCE_TIME


@pytest.fixture
def t(now: datetime) -> Callable[[int], datetime]:
    """Return a helper that offsets ``now`` by a number of seconds."""

    def _offset(seconds: int = 0) -> datetime:
        return now + timedelta(seconds=seconds)

    return _offset


@pytest.fixture
def tmp_db_path(tmp_path: Path) -> Path:
    """Scratch SQLite file path inside the per-test temp directory."""
    return tmp_path / "rivermind-test.db"


@pytest.fixture
def seeded_observations(t: Callable[[int], datetime]) -> list[Observation]:
    """Three canonical observations (one fact, two events) spaced a minute apart."""
    return [
        Observation(
            id="obs-seed-0",
            content="visited Acme HQ",
            kind=Kind.EVENT,
            observed_at=t(0),
        ),
        Observation(
            id="obs-seed-1",
            content="lunch meeting",
            kind=Kind.EVENT,
            observed_at=t(60),
        ),
        Observation(
            id="obs-seed-2",
            content="user joined Acme",
            kind=Kind.FACT,
            subject="user",
            attribute="employer",
            value="Acme",
            observed_at=t(120),
        ),
    ]
