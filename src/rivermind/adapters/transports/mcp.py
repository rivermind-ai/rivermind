"""MCP transport: a thin FastAPI + FastMCP shell over an Engine.

Exposes an ``create_app(engine)`` factory that returns a configured
FastAPI app. The app mounts the MCP streamable HTTP transport under
``/mcp`` and a plain ``/health`` endpoint for liveness checks.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
import uuid
from datetime import UTC, datetime, timedelta
from functools import wraps
from typing import TYPE_CHECKING, Annotated, Any, Literal

import structlog
from fastapi import FastAPI
from mcp.server.fastmcp import FastMCP
from pydantic import Field, JsonValue, ValidationError

from rivermind.core.ids import new_observation_id
from rivermind.core.models import Kind, Observation
from rivermind.core.reeval import run_reeval

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Awaitable, Callable
    from contextlib import AbstractAsyncContextManager

    from rivermind.core.engine import Engine
    from rivermind.core.interfaces import NarrativeSynthesizer

_logger = structlog.get_logger()


def _parse_iso8601(value: str, *, field: str, tool: str) -> datetime:
    try:
        return datetime.fromisoformat(value)
    except ValueError as exc:
        error = [{"field": field, "msg": f"not a valid ISO-8601 datetime: {exc}"}]
        raise ValueError(f"{tool} validation failed: {error}") from exc


def _now() -> datetime:
    """Return the current moment. Separated so tests can monkeypatch it."""
    return datetime.now(UTC)


_PERIOD_KEYWORDS: dict[str, timedelta] = {
    "last_week": timedelta(days=7),
    "last_month": timedelta(days=30),
    "last_quarter": timedelta(days=90),
}


def _parse_period(value: str) -> tuple[datetime, datetime]:
    """Resolve a ``period`` string into a ``(start, end)`` window.

    Accepts one of the keywords ``last_week`` / ``last_month`` /
    ``last_quarter`` (rolling windows ending at :func:`_now`), or an ISO
    8601 interval of the form ``<start>/<end>``. Anything else raises a
    ``ValueError`` whose message lists the accepted forms.
    """
    if value in _PERIOD_KEYWORDS:
        end = _now()
        return end - _PERIOD_KEYWORDS[value], end
    if "/" in value:
        start_str, _, end_str = value.partition("/")
        try:
            start = datetime.fromisoformat(start_str)
            end = datetime.fromisoformat(end_str)
        except ValueError as exc:
            error = [
                {
                    "field": "period",
                    "msg": (
                        "ISO 8601 interval could not be parsed. "
                        "Expected '<start>/<end>' with ISO-8601 datetimes."
                    ),
                }
            ]
            raise ValueError(f"get_narrative validation failed: {error}") from exc
        return start, end
    error = [
        {
            "field": "period",
            "msg": (
                "must be one of 'last_week', 'last_month', 'last_quarter' "
                "or an ISO 8601 interval '<start>/<end>'"
            ),
        }
    ]
    raise ValueError(f"get_narrative validation failed: {error}")


def _make_lifespan(
    mcp: FastMCP,
    engine: Engine,
    synthesizer: NarrativeSynthesizer | None,
    *,
    run_reeval_on_startup: bool,
) -> Callable[[FastAPI], AbstractAsyncContextManager[None]]:
    """Build the FastAPI lifespan handler.

    Enters the FastMCP session manager (so the streamable HTTP transport
    works when mounted under FastAPI), and optionally kicks off the
    startup re-eval pass as a background task that doesn't block the
    server from accepting connections.
    """

    @contextlib.asynccontextmanager
    async def _lifespan(_app: FastAPI) -> AsyncIterator[None]:
        async with mcp.session_manager.run():
            reeval_task: asyncio.Task[Any] | None = None
            if run_reeval_on_startup:
                reeval_task = asyncio.create_task(
                    asyncio.to_thread(
                        run_reeval,
                        engine._store,
                        synthesizer=synthesizer,
                    )
                )
                reeval_task.add_done_callback(_log_reeval_task_result)
            try:
                yield
            finally:
                if reeval_task is not None and not reeval_task.done():
                    reeval_task.cancel()
                    with contextlib.suppress(asyncio.CancelledError, Exception):
                        await reeval_task

    return _lifespan


def _log_reeval_task_result(task: asyncio.Task[Any]) -> None:
    """done-callback for the startup re-eval background task.

    Swallows exceptions so a failed re-eval never crashes the server.
    Cancellation (e.g. on shutdown) is not logged as an error.
    """
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        _logger.exception("reeval_task_failed", error=str(exc), exc_info=exc)


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


def create_app(
    engine: Engine,
    *,
    synthesizer: NarrativeSynthesizer | None = None,
    run_reeval_on_startup: bool = True,
) -> FastAPI:
    """Build a FastAPI app that hosts the MCP server and a health endpoint.

    The ``engine`` is captured in the closure of tool handlers; there is
    no module-level state and no singleton. Tests instantiate this factory
    with a fake engine to exercise the transport in isolation.

    When ``run_reeval_on_startup`` is true (the default), the lifespan
    kicks off a background re-eval pass after the server has started
    accepting connections. Pass False in tests that don't want the
    background task touching shared state.
    """
    # Collapse the default FastMCP path so mounting at "/mcp" exposes the
    # handler at "/mcp" rather than "/mcp/mcp".
    mcp = FastMCP("rivermind", streamable_http_path="/")

    @mcp.tool(
        name="record_observation",
        description=(
            "Record an observation in the temporal memory log. "
            "Use kind='fact' for supersedable key-value state (subject and "
            "attribute required; value is optional and may be omitted when "
            "content carries the whole payload). Use kind='event' for one-time "
            "occurrences (no supersession). Use kind='reflection' for "
            "subjective thoughts. observed_at is the world time the thing "
            "happened (ISO-8601 with timezone), not the current moment. "
            "Returns the new observation's id."
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
        description=(
            "Return the most recent narrative covering a time window. "
            "period can be a keyword ('last_week', 'last_month', 'last_quarter' "
            "are rolling windows ending now) or an ISO 8601 interval of the form "
            "'<start>/<end>'. topic is an exact match filter. "
            'Does NOT trigger synthesis. Returns {"narrative": null, "message": ...} '
            "when no narrative exists; fall back to get_timeline in that case. "
            "include_superseded opts in to older versions for audit."
        ),
    )
    @_log_tool_call("get_narrative")
    async def get_narrative(
        period: str,
        topic: str | None = None,
        include_superseded: bool = False,
    ) -> dict[str, Any]:
        start, end = _parse_period(period)
        result = engine.get_narrative(
            start,
            end,
            topic,
            include_superseded=include_superseded,
        )
        if result is None:
            return {
                "narrative": None,
                "message": "no narrative for the requested period and topic",
            }
        return {"narrative": result.model_dump(mode="json")}

    mcp_asgi = mcp.streamable_http_app()
    lifespan = _make_lifespan(mcp, engine, synthesizer, run_reeval_on_startup=run_reeval_on_startup)
    app = FastAPI(title="rivermind", version="0.0.1", lifespan=lifespan)

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

    app.mount("/mcp", mcp_asgi)
    app.state.mcp = mcp
    app.state.engine = engine
    return app
