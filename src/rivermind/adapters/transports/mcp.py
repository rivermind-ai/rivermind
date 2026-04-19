"""MCP transport: a thin FastAPI + FastMCP shell over an Engine.

Exposes an ``create_app(engine)`` factory that returns a configured
FastAPI app. The app mounts the MCP streamable HTTP transport under
``/mcp`` and a plain ``/health`` endpoint for liveness checks.

Four MCP tools are registered as stubs. Bodies are placeholders; real
behavior lands in later changes that wire each tool through to the
Engine.
"""

from __future__ import annotations

import time
import uuid
from functools import wraps
from typing import TYPE_CHECKING, Any

import structlog
from fastapi import FastAPI
from mcp.server.fastmcp import FastMCP

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from rivermind.core.engine import Engine

_logger = structlog.get_logger()

_NOT_IMPLEMENTED_PAYLOAD: dict[str, Any] = {
    "status": "not_implemented",
    "detail": "tool stub — real handler lands in a follow-up change",
}


def _log_tool_call(
    tool_name: str,
) -> Callable[[Callable[..., Awaitable[Any]]], Callable[..., Awaitable[Any]]]:
    """Decorator that emits a structured log entry per tool call.

    Logs request_id, tool name, duration in ms, and outcome. Exceptions are
    re-raised after being logged so MCP's error handling sees them.
    """

    def decorate(func: Callable[..., Awaitable[Any]]) -> Callable[..., Awaitable[Any]]:
        @wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            request_id = uuid.uuid4().hex[:12]
            start = time.perf_counter()
            log = _logger.bind(tool=tool_name, request_id=request_id)
            log.info("tool_call_start")
            try:
                result = await func(*args, **kwargs)
            except Exception:
                log.exception(
                    "tool_call_error",
                    duration_ms=round((time.perf_counter() - start) * 1000, 3),
                )
                raise
            log.info(
                "tool_call_end",
                duration_ms=round((time.perf_counter() - start) * 1000, 3),
            )
            return result

        return wrapper

    return decorate


def create_app(engine: Engine) -> FastAPI:
    """Build a FastAPI app that hosts the MCP server and a health endpoint.

    The ``engine`` is captured in the closure of tool handlers; there is
    no module-level state and no singleton. Tests instantiate this factory
    with a fake engine to exercise the transport in isolation.
    """
    mcp = FastMCP("rivermind")

    @mcp.tool(
        name="record_observation",
        description="Record an observation (fact, event, or reflection).",
    )
    @_log_tool_call("record_observation")
    async def record_observation() -> dict[str, Any]:
        return _NOT_IMPLEMENTED_PAYLOAD

    @mcp.tool(
        name="get_timeline",
        description="Return observations in a time range, chronologically.",
    )
    @_log_tool_call("get_timeline")
    async def get_timeline() -> dict[str, Any]:
        return _NOT_IMPLEMENTED_PAYLOAD

    @mcp.tool(
        name="get_current_state",
        description="Return current (subject, attribute) state rows.",
    )
    @_log_tool_call("get_current_state")
    async def get_current_state() -> dict[str, Any]:
        return _NOT_IMPLEMENTED_PAYLOAD

    @mcp.tool(
        name="get_narrative",
        description="Return the most recent narrative for a time window.",
    )
    @_log_tool_call("get_narrative")
    async def get_narrative() -> dict[str, Any]:
        return _NOT_IMPLEMENTED_PAYLOAD

    app = FastAPI(title="rivermind", version="0.0.1")

    @app.get("/health")
    async def health() -> dict[str, Any]:
        try:
            version = engine.schema_version()
            status = "ok"
        except Exception as exc:
            version = 0
            status = "error"
            _logger.exception("health_check_failed", error=str(exc))
        return {"status": status, "schema_version": version}

    app.mount("/mcp", mcp.streamable_http_app())
    app.state.mcp = mcp
    app.state.engine = engine
    return app
