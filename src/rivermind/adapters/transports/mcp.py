"""MCP transport: a thin FastAPI + FastMCP shell over an Engine.

Exposes an ``create_app(engine)`` factory that returns a configured
FastAPI app. The app mounts the MCP streamable HTTP transport under
``/mcp`` and a plain ``/health`` endpoint for liveness checks.
"""

from __future__ import annotations

import time
import uuid
from datetime import datetime
from functools import wraps
from typing import TYPE_CHECKING, Annotated, Any, Literal

import structlog
from fastapi import FastAPI
from mcp.server.fastmcp import FastMCP
from pydantic import Field, JsonValue, ValidationError

from rivermind.core.ids import new_observation_id
from rivermind.core.models import Kind, Observation

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
        description=(
            "Record an observation in the temporal memory log. "
            "Use kind='fact' for supersedable key-value state (subject, attribute, "
            "and value are required). Use kind='event' for one-time occurrences "
            "(no supersession). Use kind='reflection' for subjective thoughts. "
            "observed_at is the world time the thing happened (ISO-8601 with timezone), "
            "not the current moment. Returns the new observation's id."
        ),
    )
    @_log_tool_call("record_observation")
    async def record_observation(
        kind: Literal["fact", "event", "reflection"],
        content: Annotated[str, Field(min_length=1, max_length=2000)],
        observed_at: str,
        subject: Annotated[str | None, Field(max_length=100)] = None,
        attribute: Annotated[str | None, Field(max_length=100)] = None,
        value: JsonValue | None = None,
        session_id: str | None = None,
    ) -> dict[str, str]:
        errors: list[dict[str, str]] = []

        try:
            parsed_observed_at = datetime.fromisoformat(observed_at)
        except ValueError as exc:
            errors.append({"field": "observed_at", "msg": f"not a valid ISO-8601 datetime: {exc}"})
            parsed_observed_at = None

        if session_id is not None:
            try:
                uuid.UUID(session_id)
            except ValueError:
                errors.append({"field": "session_id", "msg": "must be a UUID string"})

        if errors:
            raise ValueError(f"record_observation validation failed: {errors}")

        try:
            observation = Observation(
                id=new_observation_id(),
                content=content,
                kind=Kind(kind),
                subject=subject,
                attribute=attribute,
                value=value,
                observed_at=parsed_observed_at,  # type: ignore[arg-type]
                session_id=session_id,
            )
        except ValidationError as exc:
            model_errors = [
                {
                    "field": ".".join(str(p) for p in err["loc"]) or "<root>",
                    "msg": err["msg"],
                }
                for err in exc.errors()
            ]
            raise ValueError(f"record_observation validation failed: {model_errors}") from exc

        return {"id": engine.record_observation(observation)}

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
