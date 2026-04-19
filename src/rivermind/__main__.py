"""Minimal ``python -m rivermind`` entry point.

Starts the FastAPI + FastMCP server over a SQLite-backed Engine. Intended
as a bootstrap while a proper CLI lands; keep this tiny so swapping it out
later is a mechanical change.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import uvicorn

from rivermind.adapters.stores.sqlite import SQLiteMemoryStore
from rivermind.adapters.transports.mcp import create_app
from rivermind.core.engine import Engine

_DEFAULT_DB = Path.home() / ".rivermind" / "rivermind.db"


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
        app = create_app(engine)
        uvicorn.run(app, host=args.host, port=args.port, log_level="info")
    finally:
        store.close()


if __name__ == "__main__":
    main()
