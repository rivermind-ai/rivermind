"""Minimal ``python -m rivermind`` entry point.

Starts the FastAPI + FastMCP server over a SQLite-backed Engine. Intended
as a bootstrap while a proper CLI lands; keep this tiny so swapping it out
later is a mechanical change.

Reads ``RIVERMIND_API_KEY`` and optional ``RIVERMIND_LLM_PROVIDER`` to
build the narrative synthesizer used by the startup re-eval pipeline. If
the key is absent, re-eval still runs (compaction + state rebuild) but
narrative synthesis is skipped with a clear warning.
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path
from typing import TYPE_CHECKING

import structlog
import uvicorn

from rivermind.adapters.stores.sqlite import SQLiteMemoryStore
from rivermind.adapters.transports.mcp import create_app
from rivermind.core.engine import Engine

if TYPE_CHECKING:
    from rivermind.core.interfaces import NarrativeSynthesizer

_logger = structlog.get_logger()

_DEFAULT_DB = Path.home() / ".rivermind" / "rivermind.db"
_API_KEY_ENV = "RIVERMIND_API_KEY"
_PROVIDER_ENV = "RIVERMIND_LLM_PROVIDER"
_DEFAULT_PROVIDER = "anthropic"
_SUPPORTED_PROVIDERS = ("anthropic", "openai")


def _build_synthesizer() -> NarrativeSynthesizer | None:
    """Construct a ``NarrativeSynthesizer`` from env vars, or ``None``.

    Returns ``None`` when the API key is unset. Raises ``ValueError`` for
    an unknown provider; that's a configuration error the user should fix
    before the server starts.
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


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="python -m rivermind")
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="Host interface to bind (default: 127.0.0.1, loopback only).",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8080,
        help="TCP port to listen on (default: 8080).",
    )
    parser.add_argument(
        "--db",
        type=Path,
        default=_DEFAULT_DB,
        help=f"SQLite database path (default: {_DEFAULT_DB}).",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = _parse_args(argv)
    args.db.parent.mkdir(parents=True, exist_ok=True)
    store = SQLiteMemoryStore(args.db)
    try:
        engine = Engine(store)
        synthesizer = _build_synthesizer()
        app = create_app(engine, synthesizer=synthesizer)
        uvicorn.run(app, host=args.host, port=args.port, log_level="info")
    finally:
        store.close()


if __name__ == "__main__":
    main()
