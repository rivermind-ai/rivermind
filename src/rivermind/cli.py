"""Rivermind command-line interface.

Click-based entry point for humans. Every subcommand is a thin adapter:
open a store, call one library function, print a result, exit with a
meaningful code (0 success, 1 user error, 2 system error).

The CLI holds zero business logic. Anything interesting lives under
``rivermind.core`` or ``rivermind.adapters``.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

import click
import structlog

from rivermind.adapters.stores.sqlite import SQLiteMemoryStore
from rivermind.core.engine import Engine
from rivermind.core.models import Narrative, Observation
from rivermind.core.projectors.state import rebuild_state
from rivermind.core.reeval import run_reeval

if TYPE_CHECKING:
    from collections.abc import Iterator

    from rivermind.core.interfaces import NarrativeSynthesizer

_logger = structlog.get_logger()

_DEFAULT_DB = Path.home() / ".rivermind" / "rivermind.db"
_API_KEY_ENV = "RIVERMIND_API_KEY"
_PROVIDER_ENV = "RIVERMIND_LLM_PROVIDER"
_DEFAULT_PROVIDER = "anthropic"
_SUPPORTED_PROVIDERS = ("anthropic", "openai")

_EPOCH = datetime.min.replace(tzinfo=UTC)
_FAR_FUTURE = datetime.max.replace(tzinfo=UTC)

_EXIT_USER_ERROR = 1
_EXIT_SYSTEM_ERROR = 2


def _build_synthesizer() -> NarrativeSynthesizer | None:
    """Construct a ``NarrativeSynthesizer`` from env vars, or ``None``.

    Returns ``None`` when ``RIVERMIND_API_KEY`` is unset; narrative
    synthesis is disabled in that case. Raises ``ValueError`` for an
    unknown provider (a configuration error the user should fix).
    """
    if os.environ.get(_API_KEY_ENV) is None:
        _logger.warning(
            "narrative_synthesis_disabled",
            reason=f"set {_API_KEY_ENV} to enable",
        )
        return None
    provider = os.environ.get(_PROVIDER_ENV, _DEFAULT_PROVIDER).lower()
    if provider == "anthropic":
        from rivermind.adapters.extractors.anthropic import AnthropicSynthesizer  # noqa: PLC0415

        return AnthropicSynthesizer()
    if provider == "openai":
        from rivermind.adapters.extractors.openai import OpenAISynthesizer  # noqa: PLC0415

        return OpenAISynthesizer()
    raise ValueError(
        f"unknown {_PROVIDER_ENV}={provider!r}; expected one of {_SUPPORTED_PROVIDERS}"
    )


def _open_store(ctx: click.Context) -> SQLiteMemoryStore:
    """Open a store at the path resolved from the global ``--db`` option."""
    db_path: Path = ctx.obj["db"]
    db_path.parent.mkdir(parents=True, exist_ok=True)
    return SQLiteMemoryStore(db_path)


def _parse_iso(value: str | None, field: str) -> datetime | None:
    if value is None:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError as exc:
        raise click.BadParameter(f"{field}: not a valid ISO-8601 datetime ({exc})") from exc


@click.group(context_settings={"help_option_names": ["-h", "--help"]})
@click.option(
    "--db",
    type=click.Path(path_type=Path),
    default=_DEFAULT_DB,
    show_default=True,
    help="SQLite database path.",
)
@click.pass_context
def cli(ctx: click.Context, db: Path) -> None:
    """Rivermind: temporal memory layer for LLMs."""
    ctx.ensure_object(dict)
    ctx.obj["db"] = db


# ---- init -----------------------------------------------------------------


@cli.command()
@click.pass_context
def init(ctx: click.Context) -> None:
    """Create the database and apply any pending migrations."""
    store = _open_store(ctx)
    try:
        version = store.schema_version()
    finally:
        store.close()
    click.echo(f"Initialized schema v{version} at {ctx.obj['db']}")


# ---- serve ----------------------------------------------------------------


@cli.command()
@click.option("--host", default="127.0.0.1", show_default=True)
@click.option("--port", default=8080, type=int, show_default=True)
@click.option(
    "--no-reeval",
    is_flag=True,
    default=False,
    help="Skip the startup re-eval background pass.",
)
@click.pass_context
def serve(ctx: click.Context, host: str, port: int, no_reeval: bool) -> None:
    """Start the FastAPI + MCP server."""
    import uvicorn  # noqa: PLC0415

    from rivermind.adapters.transports.mcp import create_app  # noqa: PLC0415

    store = _open_store(ctx)
    try:
        engine = Engine(store)
        synthesizer = _build_synthesizer()
        app = create_app(
            engine,
            synthesizer=synthesizer,
            run_reeval_on_startup=not no_reeval,
        )
        uvicorn.run(app, host=host, port=port, log_level="info")
    finally:
        store.close()


# ---- timeline -------------------------------------------------------------


@cli.command()
@click.option("--start", help="ISO-8601 start of window. Defaults to the epoch.")
@click.option("--end", help="ISO-8601 end of window. Defaults to the far future.")
@click.option("--topic", help="FTS5 match against observation content.")
@click.option("--limit", type=int, default=100, show_default=True)
@click.option(
    "--include-superseded",
    is_flag=True,
    default=False,
    help="Include observations whose superseded_by is non-null.",
)
@click.option("--json", "json_out", is_flag=True, default=False, help="Emit JSON.")
@click.pass_context
def timeline(
    ctx: click.Context,
    start: str | None,
    end: str | None,
    topic: str | None,
    limit: int,
    include_superseded: bool,
    json_out: bool,
) -> None:
    """Print observations within a time window."""
    parsed_start = _parse_iso(start, "--start") or _EPOCH
    parsed_end = _parse_iso(end, "--end") or _FAR_FUTURE

    store = _open_store(ctx)
    try:
        engine = Engine(store)
        observations = engine.get_timeline(
            parsed_start,
            parsed_end,
            topic,
            limit=limit,
            include_superseded=include_superseded,
        )
    finally:
        store.close()

    if json_out:
        click.echo(json.dumps([o.model_dump(mode="json") for o in observations], indent=2))
        return
    if not observations:
        click.echo("no observations in the requested window")
        return
    for obs in observations:
        click.echo(
            f"{obs.observed_at.isoformat()}  {obs.kind.value:10s}  {obs.id}  {obs.content[:80]}"
        )


# ---- state ----------------------------------------------------------------


@cli.command()
@click.option("--subject")
@click.option("--attribute")
@click.option(
    "--rebuild",
    is_flag=True,
    default=False,
    help="Drop the state projection and replay every non-superseded fact.",
)
@click.option("--json", "json_out", is_flag=True, default=False, help="Emit JSON.")
@click.pass_context
def state(
    ctx: click.Context,
    subject: str | None,
    attribute: str | None,
    rebuild: bool,
    json_out: bool,
) -> None:
    """Print state rows, or rebuild the state projection from observations."""
    store = _open_store(ctx)
    try:
        if rebuild:
            summary = rebuild_state(store, on_progress=lambda *_: None)
            click.echo(
                f"Rebuilt {summary.rows_rebuilt} state rows in {summary.duration_seconds:.3f}s"
            )
            if summary.warnings:
                for w in summary.warnings:
                    click.echo(f"  warning: {w}")
            return
        rows = store.list_states(subject=subject, attribute=attribute)
    finally:
        store.close()

    if json_out:
        click.echo(json.dumps([s.model_dump(mode="json") for s in rows], indent=2))
        return
    if not rows:
        click.echo("no state rows")
        return
    for s in rows:
        value_repr = "(null)" if s.current_value is None else repr(s.current_value)
        click.echo(
            f"{s.subject}.{s.attribute} = {value_repr}  "
            f"since {s.current_since.isoformat()}  from {s.source_observation}"
        )


# ---- reeval ---------------------------------------------------------------


@cli.command()
@click.pass_context
def reeval(ctx: click.Context) -> None:
    """Run the re-eval pipeline: synthesis + compaction + state rebuild."""
    store = _open_store(ctx)
    try:
        synthesizer = _build_synthesizer()
        summary = run_reeval(store, synthesizer=synthesizer)
    finally:
        store.close()
    click.echo(
        f"processed {summary.weeks_processed} weeks, "
        f"wrote {summary.narratives_written} narratives, "
        f"{len(summary.warnings)} warnings"
    )
    for w in summary.warnings:
        click.echo(f"  warning: {w}")


# ---- export ---------------------------------------------------------------


def _iter_all_observations(store: SQLiteMemoryStore) -> Iterator[Observation]:
    yield from store.get_observations(_EPOCH, _FAR_FUTURE, include_superseded=True)


def _iter_all_narratives(store: SQLiteMemoryStore) -> Iterator[Narrative]:
    yield from store.get_narratives(_EPOCH, _FAR_FUTURE, include_superseded=True)


@cli.command()
@click.option(
    "--format",
    "fmt",
    type=click.Choice(["json", "sqlite"]),
    default="json",
    show_default=True,
)
@click.option(
    "--out",
    required=True,
    type=click.Path(path_type=Path),
    help="Output file path.",
)
@click.pass_context
def export(ctx: click.Context, fmt: str, out: Path) -> None:
    """Dump observations, state, and narratives to a file."""
    out.parent.mkdir(parents=True, exist_ok=True)
    db_path: Path = ctx.obj["db"]
    if fmt == "sqlite":
        if not db_path.exists():
            raise click.ClickException(f"database not found: {db_path}")
        shutil.copyfile(db_path, out)
        click.echo(f"Copied {db_path} -> {out}")
        return

    store = _open_store(ctx)
    try:
        observations = list(_iter_all_observations(store))
        states = store.list_states()
        narratives = list(_iter_all_narratives(store))
        schema_version = store.schema_version()
    finally:
        store.close()

    payload = {
        "schema_version": schema_version,
        "exported_at": datetime.now(UTC).isoformat(),
        "observations": [o.model_dump(mode="json") for o in observations],
        "states": [s.model_dump(mode="json") for s in states],
        "narratives": [n.model_dump(mode="json") for n in narratives],
    }
    out.write_text(json.dumps(payload, indent=2))
    click.echo(
        f"Exported {len(observations)} observations, "
        f"{len(states)} state rows, {len(narratives)} narratives to {out}"
    )


# ---- import ---------------------------------------------------------------


@cli.command(name="import")
@click.option(
    "--from",
    "source",
    required=True,
    type=click.Path(exists=True, path_type=Path),
    help="Rivermind JSON export file.",
)
@click.pass_context
def import_cmd(ctx: click.Context, source: Path) -> None:
    """Import observations and narratives from a prior JSON export."""
    try:
        payload = json.loads(source.read_text())
    except json.JSONDecodeError as exc:
        raise click.ClickException(f"{source}: not valid JSON ({exc})") from exc
    for key in ("observations", "narratives"):
        if key not in payload:
            raise click.ClickException(f"{source}: missing required key {key!r}")

    store = _open_store(ctx)
    try:
        observation_count = 0
        for raw in payload["observations"]:
            store.save_observation(Observation.model_validate(raw))
            observation_count += 1
        narrative_count = 0
        for raw in payload["narratives"]:
            store.save_narrative(Narrative.model_validate(raw))
            narrative_count += 1
        rebuild_state(store, on_progress=lambda *_: None)
    finally:
        store.close()
    click.echo(f"Imported {observation_count} observations, {narrative_count} narratives")


# ---- entry points ---------------------------------------------------------


def main() -> None:
    """Console-script entry point."""
    try:
        cli(obj={})
    except ValueError as exc:
        # Configuration errors raised before Click's own handling.
        click.echo(f"error: {exc}", err=True)
        sys.exit(_EXIT_USER_ERROR)
    except Exception as exc:  # pragma: no cover (defensive)
        click.echo(f"system error: {exc}", err=True)
        sys.exit(_EXIT_SYSTEM_ERROR)
