"""Integration tests for the MCP FastAPI scaffold.

The Engine is backed by a real file-backed SQLite store so the health
endpoint exercises a genuine ``schema_version`` roundtrip. Tool
registration is verified by asking FastMCP to list its registered tools.
Tool bodies are stubs; their real behavior is covered by later changes.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
import structlog
from fastapi.testclient import TestClient
from starlette.routing import Mount

from rivermind.adapters.stores.sqlite import SQLiteMemoryStore
from rivermind.adapters.transports import mcp as mcp_module
from rivermind.adapters.transports.mcp import create_app
from rivermind.core.engine import Engine

if TYPE_CHECKING:
    from collections.abc import Generator
    from pathlib import Path


@pytest.fixture
def engine(tmp_db_path: Path) -> Generator[Engine, None, None]:
    store = SQLiteMemoryStore(tmp_db_path)
    try:
        yield Engine(store)
    finally:
        store.close()


@pytest.fixture
def client(engine: Engine) -> TestClient:
    return TestClient(create_app(engine))


def test_health_endpoint_returns_schema_version(client: TestClient) -> None:
    response = client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body == {"status": "ok", "schema_version": 1}


def test_health_endpoint_reports_error_when_store_unreachable() -> None:
    class _BrokenEngine:
        def schema_version(self) -> int:
            raise RuntimeError("store unreachable")

    # The factory types Engine but only uses schema_version at /health;
    # duck-typing a broken stand-in is sufficient for the failure path.
    app = create_app(_BrokenEngine())  # type: ignore[arg-type]
    client = TestClient(app)
    body = client.get("/health").json()
    assert body["status"] == "error"
    assert body["schema_version"] == 0


@pytest.mark.asyncio
async def test_four_tool_stubs_are_registered(engine: Engine) -> None:
    app = create_app(engine)
    mcp = app.state.mcp
    tools = await mcp.list_tools()
    names = {t.name for t in tools}
    assert names == {
        "record_observation",
        "get_timeline",
        "get_current_state",
        "get_narrative",
    }


@pytest.mark.asyncio
async def test_tool_stubs_return_not_implemented(engine: Engine) -> None:
    app = create_app(engine)
    mcp = app.state.mcp
    for name in (
        "record_observation",
        "get_timeline",
        "get_current_state",
        "get_narrative",
    ):
        result = await mcp.call_tool(name, {})
        # FastMCP returns a (content, structured) tuple since a recent release;
        # older versions return just content. Normalize by unwrapping.
        payload = result[1] if isinstance(result, tuple) else result
        assert payload["status"] == "not_implemented"


@pytest.mark.asyncio
async def test_tool_call_emits_structured_log(engine: Engine) -> None:
    cap = structlog.testing.LogCapture()
    structlog.configure(processors=[cap])
    try:
        app = create_app(engine)
        mcp = app.state.mcp
        await mcp.call_tool("record_observation", {})
    finally:
        structlog.reset_defaults()

    events = {entry["event"] for entry in cap.entries}
    assert "tool_call_start" in events
    assert "tool_call_end" in events
    ends = [e for e in cap.entries if e["event"] == "tool_call_end"]
    assert ends[-1]["tool"] == "record_observation"
    assert "duration_ms" in ends[-1]
    assert "request_id" in ends[-1]


def test_mcp_endpoint_is_mounted(engine: Engine) -> None:
    app = create_app(engine)
    mounts = [r for r in app.routes if isinstance(r, Mount) and r.path == "/mcp"]
    assert len(mounts) == 1


def test_app_has_no_module_level_state() -> None:
    # Spot-check: the module exposes the factory and a logger, no mutable
    # app or mcp instance at import time.
    assert not hasattr(mcp_module, "app")
    assert not hasattr(mcp_module, "mcp")
