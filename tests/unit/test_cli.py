"""Unit tests for ``rivermind.cli``.

Uses Click's ``CliRunner`` so tests stay in-process. Each test that
touches the DB opens its own tmp-path file.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING
from unittest.mock import MagicMock

import pytest
from click.testing import CliRunner

from rivermind.adapters.stores.sqlite import SQLiteMemoryStore
from rivermind.adapters.transports import mcp as mcp_mod
from rivermind.cli import cli
from rivermind.core.models import Kind, Observation

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "cli.db"


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


def _seed_one_fact(db_path: Path) -> None:
    s = SQLiteMemoryStore(db_path)
    try:
        s.save_observation(
            Observation(
                id="obs-1",
                content="joined Acme",
                kind=Kind.FACT,
                subject="user",
                attribute="employer",
                value="Acme",
                observed_at=datetime(2026, 3, 15, tzinfo=UTC),
            )
        )
    finally:
        s.close()


# ---- help + structure -----------------------------------------------------


def test_root_help_lists_all_subcommands(runner: CliRunner) -> None:
    result = runner.invoke(cli, ["--help"])
    assert result.exit_code == 0
    for sub in ("init", "serve", "timeline", "state", "reeval", "export", "import"):
        assert sub in result.output


def test_unknown_subcommand_exits_nonzero(runner: CliRunner) -> None:
    result = runner.invoke(cli, ["nope"])
    assert result.exit_code != 0


# ---- init -----------------------------------------------------------------


def test_init_creates_db_file(runner: CliRunner, db_path: Path) -> None:
    assert not db_path.exists()
    result = runner.invoke(cli, ["--db", str(db_path), "init"])
    assert result.exit_code == 0
    assert db_path.exists()
    assert "Initialized schema" in result.output


def test_init_is_idempotent_and_preserves_observations(runner: CliRunner, db_path: Path) -> None:
    runner.invoke(cli, ["--db", str(db_path), "init"])
    _seed_one_fact(db_path)
    result = runner.invoke(cli, ["--db", str(db_path), "init"])
    assert result.exit_code == 0
    s = SQLiteMemoryStore(db_path)
    try:
        obs = s.get_observations(datetime.min.replace(tzinfo=UTC), datetime.max.replace(tzinfo=UTC))
    finally:
        s.close()
    assert [o.id for o in obs] == ["obs-1"]


# ---- timeline -------------------------------------------------------------


def test_timeline_empty_prints_friendly_message(runner: CliRunner, db_path: Path) -> None:
    runner.invoke(cli, ["--db", str(db_path), "init"])
    result = runner.invoke(cli, ["--db", str(db_path), "timeline"])
    assert result.exit_code == 0
    assert "no observations" in result.output


def test_timeline_with_data_prints_ids(runner: CliRunner, db_path: Path) -> None:
    _seed_one_fact(db_path)
    result = runner.invoke(cli, ["--db", str(db_path), "timeline"])
    assert result.exit_code == 0
    assert "obs-1" in result.output
    assert "joined Acme" in result.output


def test_timeline_json_returns_parseable_array(runner: CliRunner, db_path: Path) -> None:
    _seed_one_fact(db_path)
    result = runner.invoke(cli, ["--db", str(db_path), "timeline", "--json"])
    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert isinstance(payload, list)
    assert payload[0]["id"] == "obs-1"


def test_timeline_bad_start_raises_usage_error(runner: CliRunner, db_path: Path) -> None:
    runner.invoke(cli, ["--db", str(db_path), "init"])
    result = runner.invoke(cli, ["--db", str(db_path), "timeline", "--start", "nope"])
    assert result.exit_code != 0


# ---- state ----------------------------------------------------------------


def test_state_empty_prints_friendly_message(runner: CliRunner, db_path: Path) -> None:
    runner.invoke(cli, ["--db", str(db_path), "init"])
    result = runner.invoke(cli, ["--db", str(db_path), "state"])
    assert result.exit_code == 0
    assert "no state rows" in result.output


def test_state_after_seeding_fact_shows_row(runner: CliRunner, db_path: Path) -> None:
    _seed_one_fact(db_path)
    # The raw save_observation in _seed_one_fact bypasses the state projector;
    # rebuild so list_states has a row to return.
    runner.invoke(cli, ["--db", str(db_path), "state", "--rebuild"])
    result = runner.invoke(cli, ["--db", str(db_path), "state"])
    assert result.exit_code == 0
    assert "user.employer" in result.output
    assert "Acme" in result.output


def test_state_rebuild_prints_summary(runner: CliRunner, db_path: Path) -> None:
    _seed_one_fact(db_path)
    result = runner.invoke(cli, ["--db", str(db_path), "state", "--rebuild"])
    assert result.exit_code == 0
    assert "Rebuilt" in result.output


# ---- reeval ---------------------------------------------------------------


def test_reeval_runs_pipeline_with_no_api_key(
    runner: CliRunner, db_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("RIVERMIND_API_KEY", raising=False)
    runner.invoke(cli, ["--db", str(db_path), "init"])
    result = runner.invoke(cli, ["--db", str(db_path), "reeval"])
    assert result.exit_code == 0
    assert "processed" in result.output
    assert "weeks" in result.output


# ---- export / import ------------------------------------------------------


def test_export_json_writes_file_with_expected_keys(
    runner: CliRunner, db_path: Path, tmp_path: Path
) -> None:
    _seed_one_fact(db_path)
    out = tmp_path / "dump.json"
    result = runner.invoke(cli, ["--db", str(db_path), "export", "--out", str(out)])
    assert result.exit_code == 0
    payload = json.loads(out.read_text())
    assert set(payload.keys()) >= {
        "schema_version",
        "exported_at",
        "observations",
        "states",
        "narratives",
    }
    assert payload["observations"][0]["id"] == "obs-1"


def test_export_sqlite_copies_db_file(runner: CliRunner, db_path: Path, tmp_path: Path) -> None:
    _seed_one_fact(db_path)
    out = tmp_path / "copy.db"
    result = runner.invoke(
        cli,
        ["--db", str(db_path), "export", "--format", "sqlite", "--out", str(out)],
    )
    assert result.exit_code == 0
    assert out.exists()
    assert out.read_bytes() == db_path.read_bytes()


def test_import_roundtrip(runner: CliRunner, db_path: Path, tmp_path: Path) -> None:
    _seed_one_fact(db_path)
    out = tmp_path / "dump.json"
    runner.invoke(cli, ["--db", str(db_path), "export", "--out", str(out)])

    dest = tmp_path / "dest.db"
    result = runner.invoke(cli, ["--db", str(dest), "import", "--from", str(out)])
    assert result.exit_code == 0
    assert "Imported 1 observations" in result.output

    s = SQLiteMemoryStore(dest)
    try:
        obs = s.get_observations(datetime.min.replace(tzinfo=UTC), datetime.max.replace(tzinfo=UTC))
        state = s.get_state("user", "employer")
    finally:
        s.close()
    assert [o.id for o in obs] == ["obs-1"]
    assert state is not None
    assert state.current_value == "Acme"


def test_import_missing_file_exits_nonzero(
    runner: CliRunner, db_path: Path, tmp_path: Path
) -> None:
    result = runner.invoke(
        cli,
        ["--db", str(db_path), "import", "--from", str(tmp_path / "missing.json")],
    )
    assert result.exit_code != 0


def test_import_rejects_non_json(runner: CliRunner, db_path: Path, tmp_path: Path) -> None:
    garbage = tmp_path / "garbage.json"
    garbage.write_text("not json at all")
    result = runner.invoke(cli, ["--db", str(db_path), "import", "--from", str(garbage)])
    assert result.exit_code != 0


# ---- serve wiring ---------------------------------------------------------


def test_serve_passes_no_reeval_flag_through(
    runner: CliRunner, db_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Capture the kwargs create_app is called with; don't actually run uvicorn.
    captured: dict[str, object] = {}

    def _fake_create_app(engine: object, **kwargs: object) -> object:
        captured.update(kwargs)
        return MagicMock()

    def _fake_uvicorn_run(*_args: object, **_kwargs: object) -> None:
        return None

    monkeypatch.setattr(mcp_mod, "create_app", _fake_create_app)
    monkeypatch.setattr("uvicorn.run", _fake_uvicorn_run)

    result = runner.invoke(cli, ["--db", str(db_path), "serve", "--no-reeval", "--port", "9999"])
    assert result.exit_code == 0, result.output
    assert captured.get("run_reeval_on_startup") is False


def test_serve_default_enables_reeval(
    runner: CliRunner, db_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, object] = {}

    def _fake_create_app(engine: object, **kwargs: object) -> object:
        captured.update(kwargs)
        return MagicMock()

    def _fake_uvicorn_run(*_args: object, **_kwargs: object) -> None:
        return None

    monkeypatch.setattr(mcp_mod, "create_app", _fake_create_app)
    monkeypatch.setattr("uvicorn.run", _fake_uvicorn_run)
    monkeypatch.delenv("RIVERMIND_API_KEY", raising=False)

    result = runner.invoke(cli, ["--db", str(db_path), "serve", "--port", "9998"])
    assert result.exit_code == 0, result.output
    assert captured.get("run_reeval_on_startup") is True
