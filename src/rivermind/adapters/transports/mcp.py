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


def _parse_iso8601(value: str, *, field: str, tool: str) -> datetime:
    try:
        return datetime.fromisoformat(value)
    except ValueError as exc:
        error = [{"field": field, "msg": f"not a valid ISO-8601 datetime: {exc}"}]
        raise ValueError(f"{tool} validation failed: {error}") from exc


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
        parsed_observed_at = _parse_iso8601(
            observed_at, field="observed_at", tool="record_observation"
        )
        if session_id is not None:
            try:
                uuid.UUID(session_id)
            except ValueError as exc:
                errors = [{"field": "session_id", "msg": "must be a UUID string"}]
                raise ValueError(f"record_observation validation failed: {errors}") from exc
        try:
            observation = Observation(
                id=new_observation_id(),
                content=content,
                kind=Kind(kind),
                subject=subject,
                attribute=attribute,
                value=value,
                observed_at=parsed_observed_at,
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
        description=(
            "Return observations whose observed_at falls in [start, end], ordered "
            "chronologically (oldest first). Start and end are ISO-8601 datetimes "
            "with timezone. Optional topic filters via full-text match against content. "
            "Superseded observations are excluded by default; set include_superseded=true "
            "to see them. limit caps rows returned (default 100, max 500). When the page is "
            "full, next_cursor is the last returned observed_at and can be passed as start "
            "to continue; otherwise next_cursor is null."
        ),
    )
    @_log_tool_call("get_timeline")
    async def get_timeline(
        start: str,
        end: str,
        topic: str | None = None,
        limit: Annotated[int, Field(ge=1, le=500)] = 100,
        include_superseded: bool = False,
    ) -> dict[str, Any]:
        parsed_start = _parse_iso8601(start, field="start", tool="get_timeline")
        parsed_end = _parse_iso8601(end, field="end", tool="get_timeline")
        observations = engine.get_timeline(
            parsed_start,
            parsed_end,
            topic,
            limit=limit,
            include_superseded=include_superseded,
        )
        serialized = [o.model_dump(mode="json") for o in observations]
        next_cursor = serialized[-1]["observed_at"] if len(serialized) == limit else None
        return {"observations": serialized, "next_cursor": next_cursor}

    @mcp.tool(
        name="get_current_state",
        description=(
            "Return current (subject, attribute) state rows. Hit this first when "
            "answering questions about present truth (e.g., 'where does the user work?'); "
            "each row carries a source_observation id so callers can drill back to the "
            "originating observation. Filter by subject and/or attribute, or omit both "
            "to return every row. Empty results are normal, not errors."
        ),
    )
    @_log_tool_call("get_current_state")
    async def get_current_state(
        subject: str | None = None,
        attribute: str | None = None,
    ) -> dict[str, Any]:
        rows = engine.get_current_state(subject=subject, attribute=attribute)
        return {"states": [s.model_dump(mode="json") for s in rows]}

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
