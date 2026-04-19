"""End-to-end tests for re-eval kicked off at server startup.

Uses a real SQLite store and a fake synthesizer so the test doesn't need
an LLM SDK. Verifies the background task runs after the FastAPI lifespan
enters, touches the right state, and is idempotent across restarts.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import pytest
from fastapi.testclient import TestClient

from rivermind.adapters.stores.sqlite import SQLiteMemoryStore
from rivermind.adapters.transports.mcp import create_app
from rivermind.core import reeval as reeval_mod
from rivermind.core.engine import Engine
from rivermind.core.models import Kind, Observation
from rivermind.core.reeval import _iso_week_bounds

if TYPE_CHECKING:
    from collections.abc import Callable, Generator
    from pathlib import Path


_THURSDAY_NOW = datetime(2026, 3, 26, 12, 0, 0, tzinfo=UTC)


class _RecordingSynth:
    def __init__(self) -> None:
        self.calls = 0

    def synthesize(self, prompt: str) -> str:
        self.calls += 1
        return f"narrative body #{self.calls}"


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "reeval.db"


@pytest.fixture
def store(db_path: Path) -> Generator[SQLiteMemoryStore, None, None]:
    s = SQLiteMemoryStore(db_path)
    try:
        yield s
    finally:
        s.close()


@pytest.fixture(autouse=True)
def _freeze_now(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(reeval_mod, "_now", lambda: _THURSDAY_NOW)


def _seed_event(store: SQLiteMemoryStore, *, id_: str, at: datetime) -> None:
    store.save_observation(
        Observation(id=id_, content=f"event {id_}", kind=Kind.EVENT, observed_at=at)
    )


def _wait_for(predicate: Callable[[], bool], timeout: float = 2.0, interval: float = 0.02) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return False


def _seed_three_past_weeks(store: SQLiteMemoryStore) -> list[tuple[datetime, datetime]]:
    expected: list[tuple[datetime, datetime]] = []
    for i in range(1, 4):
        at = _THURSDAY_NOW - timedelta(days=7 * i)
        _seed_event(store, id_=f"obs-w{i}", at=at)
        expected.append(_iso_week_bounds(at))
    return expected


def test_startup_triggers_reeval_for_overdue_weeks(
    store: SQLiteMemoryStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("RIVERMIND_API_KEY", "sk-test")
    expected_weeks = _seed_three_past_weeks(store)
    engine = Engine(store)
    synth = _RecordingSynth()

    app = create_app(engine, synthesizer=synth)
    with TestClient(app):
        ok = _wait_for(lambda: all(store.reeval_exists(s, e) for s, e in expected_weeks))
    assert ok, "expected 3 reeval_runs rows to be recorded within the timeout"
    assert synth.calls == 3
    all_narratives = store.get_narratives(_THURSDAY_NOW - timedelta(days=30), _THURSDAY_NOW)
    assert len(all_narratives) == 3


def test_restart_does_not_reeval_again(
    store: SQLiteMemoryStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("RIVERMIND_API_KEY", raising=False)
    expected_weeks = _seed_three_past_weeks(store)
    for s, e in expected_weeks:
        store.record_reeval(s, e)
    engine = Engine(store)

    app = create_app(engine, synthesizer=None)
    with TestClient(app):
        time.sleep(0.2)
    assert store.get_narratives(_THURSDAY_NOW - timedelta(days=30), _THURSDAY_NOW) == []


def test_startup_without_api_key_still_records_reeval_runs(
    store: SQLiteMemoryStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("RIVERMIND_API_KEY", raising=False)
    expected_weeks = _seed_three_past_weeks(store)
    engine = Engine(store)

    app = create_app(engine, synthesizer=None)
    with TestClient(app):
        ok = _wait_for(lambda: all(store.reeval_exists(s, e) for s, e in expected_weeks))
    assert ok
    assert store.get_narratives(_THURSDAY_NOW - timedelta(days=30), _THURSDAY_NOW) == []


def test_opt_out_flag_skips_startup_reeval(
    store: SQLiteMemoryStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("RIVERMIND_API_KEY", raising=False)
    _seed_three_past_weeks(store)
    engine = Engine(store)

    app = create_app(engine, run_reeval_on_startup=False)
    with TestClient(app):
        time.sleep(0.2)
    ws, we = _iso_week_bounds(_THURSDAY_NOW - timedelta(days=7))
    assert store.reeval_exists(ws, we) is False
