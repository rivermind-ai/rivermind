"""Integration tests for the MCP FastAPI transport."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

import pytest
import structlog
from fastapi.testclient import TestClient
from starlette.routing import Mount

from rivermind.adapters.stores.sqlite import SQLiteMemoryStore
from rivermind.adapters.transports import mcp as mcp_module
from rivermind.adapters.transports.mcp import create_app
from rivermind.core.engine import Engine
from rivermind.core.models import Kind, Observation

if TYPE_CHECKING:
    from collections.abc import Generator
    from pathlib import Path


_OBSERVED_AT_ISO = "2026-04-18T12:00:00+00:00"


def _tool_payload(result: Any) -> Any:
    """Unwrap the `(content, structured)` tuple FastMCP returns from `call_tool`."""
    return result[1] if isinstance(result, tuple) else result


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
async def test_remaining_tool_stubs_return_not_implemented(engine: Engine) -> None:
    app = create_app(engine)
    mcp = app.state.mcp
    for name in ("get_current_state", "get_narrative"):
        payload = _tool_payload(await mcp.call_tool(name, {}))
        assert payload["status"] == "not_implemented"


@pytest.mark.asyncio
async def test_tool_call_emits_structured_log(engine: Engine) -> None:
    cap = structlog.testing.LogCapture()
    structlog.configure(processors=[cap])
    try:
        app = create_app(engine)
        mcp = app.state.mcp
        await mcp.call_tool("get_current_state", {})
    finally:
        structlog.reset_defaults()

    events = {entry["event"] for entry in cap.entries}
    assert "tool_call_start" in events
    assert "tool_call_end" in events
    ends = [e for e in cap.entries if e["event"] == "tool_call_end"]
    assert ends[-1]["tool"] == "get_current_state"
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


# ---- record_observation tool: happy path ---------------------------------


@pytest.mark.asyncio
async def test_record_observation_fact_persists_and_returns_id(engine: Engine) -> None:
    app = create_app(engine)
    payload = _tool_payload(
        await app.state.mcp.call_tool(
            "record_observation",
            {
                "kind": "fact",
                "content": "user works at Acme",
                "observed_at": _OBSERVED_AT_ISO,
                "subject": "user",
                "attribute": "employer",
                "value": "Acme",
            },
        )
    )
    assert payload["id"].startswith("obs-")

    start = datetime(2026, 4, 18, 11, tzinfo=UTC)
    end = datetime(2026, 4, 18, 13, tzinfo=UTC)
    timeline = engine.get_timeline(start, end)
    assert [o.id for o in timeline] == [payload["id"]]
    persisted = timeline[0]
    assert persisted.content == "user works at Acme"
    assert persisted.subject == "user"
    assert persisted.attribute == "employer"
    assert persisted.value == "Acme"


@pytest.mark.asyncio
async def test_record_observation_event_succeeds_without_subject(engine: Engine) -> None:
    app = create_app(engine)
    payload = _tool_payload(
        await app.state.mcp.call_tool(
            "record_observation",
            {
                "kind": "event",
                "content": "visited HQ",
                "observed_at": _OBSERVED_AT_ISO,
            },
        )
    )
    assert payload["id"].startswith("obs-")


@pytest.mark.asyncio
async def test_record_observation_reflection_succeeds(engine: Engine) -> None:
    app = create_app(engine)
    payload = _tool_payload(
        await app.state.mcp.call_tool(
            "record_observation",
            {
                "kind": "reflection",
                "content": "the week went well",
                "observed_at": _OBSERVED_AT_ISO,
            },
        )
    )
    assert payload["id"].startswith("obs-")


@pytest.mark.asyncio
async def test_record_observation_accepts_valid_session_id(engine: Engine) -> None:
    app = create_app(engine)
    payload = _tool_payload(
        await app.state.mcp.call_tool(
            "record_observation",
            {
                "kind": "event",
                "content": "hi",
                "observed_at": _OBSERVED_AT_ISO,
                "session_id": "12345678-1234-1234-1234-123456789012",
            },
        )
    )
    assert payload["id"].startswith("obs-")


# ---- record_observation tool: sad path ------------------------------------


async def _expect_validation_error(
    engine: Engine,
    args: dict[str, Any],
    *,
    expected_field: str,
) -> None:
    app = create_app(engine)
    with pytest.raises(Exception) as excinfo:
        await app.state.mcp.call_tool("record_observation", args)
    msg = str(excinfo.value)
    # Either FastMCP's JSON-schema pre-validation or our handler's post-validation
    # should reject the call; both carry a "validation" signal plus the field name.
    assert "validation" in msg.lower()
    assert expected_field in msg


@pytest.mark.asyncio
async def test_fact_missing_subject_is_rejected(engine: Engine) -> None:
    await _expect_validation_error(
        engine,
        {
            "kind": "fact",
            "content": "missing fields",
            "observed_at": _OBSERVED_AT_ISO,
            "attribute": "x",
            "value": "y",
        },
        expected_field="subject",
    )


@pytest.mark.asyncio
async def test_fact_missing_attribute_is_rejected(engine: Engine) -> None:
    await _expect_validation_error(
        engine,
        {
            "kind": "fact",
            "content": "missing fields",
            "observed_at": _OBSERVED_AT_ISO,
            "subject": "user",
            "value": "y",
        },
        expected_field="attribute",
    )


@pytest.mark.asyncio
async def test_fact_missing_value_is_rejected(engine: Engine) -> None:
    await _expect_validation_error(
        engine,
        {
            "kind": "fact",
            "content": "missing fields",
            "observed_at": _OBSERVED_AT_ISO,
            "subject": "user",
            "attribute": "role",
        },
        expected_field="value",
    )


@pytest.mark.asyncio
async def test_content_empty_is_rejected(engine: Engine) -> None:
    await _expect_validation_error(
        engine,
        {"kind": "event", "content": "", "observed_at": _OBSERVED_AT_ISO},
        expected_field="content",
    )


@pytest.mark.asyncio
async def test_content_too_long_is_rejected(engine: Engine) -> None:
    await _expect_validation_error(
        engine,
        {"kind": "event", "content": "x" * 2001, "observed_at": _OBSERVED_AT_ISO},
        expected_field="content",
    )


@pytest.mark.asyncio
async def test_subject_too_long_is_rejected(engine: Engine) -> None:
    await _expect_validation_error(
        engine,
        {
            "kind": "fact",
            "content": "x",
            "observed_at": _OBSERVED_AT_ISO,
            "subject": "a" * 101,
            "attribute": "role",
            "value": "dev",
        },
        expected_field="subject",
    )


@pytest.mark.asyncio
async def test_attribute_too_long_is_rejected(engine: Engine) -> None:
    await _expect_validation_error(
        engine,
        {
            "kind": "fact",
            "content": "x",
            "observed_at": _OBSERVED_AT_ISO,
            "subject": "user",
            "attribute": "a" * 101,
            "value": "dev",
        },
        expected_field="attribute",
    )


@pytest.mark.asyncio
async def test_malformed_observed_at_is_rejected(engine: Engine) -> None:
    await _expect_validation_error(
        engine,
        {"kind": "event", "content": "x", "observed_at": "not-a-date"},
        expected_field="observed_at",
    )


@pytest.mark.asyncio
async def test_bad_kind_is_rejected(engine: Engine) -> None:
    app = create_app(engine)
    with pytest.raises(Exception) as excinfo:
        await app.state.mcp.call_tool(
            "record_observation",
            {
                "kind": "preference",
                "content": "x",
                "observed_at": _OBSERVED_AT_ISO,
            },
        )
    # FastMCP's own JSON-Schema enum validation rejects this before the handler runs.
    assert "kind" in str(excinfo.value).lower() or "enum" in str(excinfo.value).lower()


@pytest.mark.asyncio
async def test_bad_session_id_is_rejected(engine: Engine) -> None:
    await _expect_validation_error(
        engine,
        {
            "kind": "event",
            "content": "x",
            "observed_at": _OBSERVED_AT_ISO,
            "session_id": "not-a-uuid",
        },
        expected_field="session_id",
    )


# ---- get_timeline tool: happy path ----------------------------------------


async def _record(engine: Engine, app_state: Any, *, offset: int, **extra: Any) -> str:
    anchor = datetime(2026, 4, 18, 12, 0, 0, tzinfo=UTC) + timedelta(seconds=offset)
    args = {
        "kind": "event",
        "content": f"event at offset {offset}",
        "observed_at": anchor.isoformat(),
        **extra,
    }
    payload = _tool_payload(await app_state.mcp.call_tool("record_observation", args))
    return payload["id"]


@pytest.mark.asyncio
async def test_get_timeline_returns_observations_ascending(engine: Engine) -> None:
    app = create_app(engine)
    for offset in [2, 0, 1]:
        await _record(engine, app.state, offset=offset)

    result = _tool_payload(
        await app.state.mcp.call_tool(
            "get_timeline",
            {"start": "2026-04-18T11:00:00+00:00", "end": "2026-04-18T13:00:00+00:00"},
        )
    )
    observed_ats = [o["observed_at"] for o in result["observations"]]
    assert observed_ats == sorted(observed_ats)
    assert result["next_cursor"] is None


@pytest.mark.asyncio
async def test_get_timeline_topic_filters_via_fts(engine: Engine) -> None:
    app = create_app(engine)
    await _record(engine, app.state, offset=0, content="visited Acme HQ")
    await _record(engine, app.state, offset=1, content="lunch with a friend")
    result = _tool_payload(
        await app.state.mcp.call_tool(
            "get_timeline",
            {
                "start": "2026-04-18T11:00:00+00:00",
                "end": "2026-04-18T13:00:00+00:00",
                "topic": "Acme",
            },
        )
    )
    assert len(result["observations"]) == 1
    assert "Acme" in result["observations"][0]["content"]


@pytest.mark.asyncio
async def test_get_timeline_limit_and_cursor(engine: Engine) -> None:
    app = create_app(engine)
    for offset in range(5):
        await _record(engine, app.state, offset=offset)
    result = _tool_payload(
        await app.state.mcp.call_tool(
            "get_timeline",
            {
                "start": "2026-04-18T11:00:00+00:00",
                "end": "2026-04-18T13:00:00+00:00",
                "limit": 3,
            },
        )
    )
    assert len(result["observations"]) == 3
    # Full page → cursor is the last returned observed_at
    assert result["next_cursor"] == result["observations"][-1]["observed_at"]


@pytest.mark.asyncio
async def test_get_timeline_cursor_null_when_page_not_full(engine: Engine) -> None:
    app = create_app(engine)
    await _record(engine, app.state, offset=0)
    result = _tool_payload(
        await app.state.mcp.call_tool(
            "get_timeline",
            {
                "start": "2026-04-18T11:00:00+00:00",
                "end": "2026-04-18T13:00:00+00:00",
                "limit": 10,
            },
        )
    )
    assert len(result["observations"]) == 1
    assert result["next_cursor"] is None


@pytest.mark.asyncio
async def test_get_timeline_excludes_superseded_by_default(engine: Engine) -> None:
    newer = engine.record_observation(_fact_observation("obs-newer", 60, "Acme"))
    engine.record_observation(_fact_observation("obs-older", 0, "Globex", superseded_by=newer))
    app = create_app(engine)
    result = _tool_payload(
        await app.state.mcp.call_tool(
            "get_timeline",
            {"start": "2026-04-18T11:00:00+00:00", "end": "2026-04-18T13:00:00+00:00"},
        )
    )
    ids = {o["id"] for o in result["observations"]}
    assert ids == {"obs-newer"}


@pytest.mark.asyncio
async def test_get_timeline_include_superseded_returns_all(engine: Engine) -> None:
    newer = engine.record_observation(_fact_observation("obs-newer", 60, "Acme"))
    engine.record_observation(_fact_observation("obs-older", 0, "Globex", superseded_by=newer))
    app = create_app(engine)
    result = _tool_payload(
        await app.state.mcp.call_tool(
            "get_timeline",
            {
                "start": "2026-04-18T11:00:00+00:00",
                "end": "2026-04-18T13:00:00+00:00",
                "include_superseded": True,
            },
        )
    )
    ids = {o["id"] for o in result["observations"]}
    assert ids == {"obs-newer", "obs-older"}


# ---- get_timeline tool: sad path ------------------------------------------


@pytest.mark.asyncio
async def test_get_timeline_bad_start_is_rejected(engine: Engine) -> None:
    app = create_app(engine)
    with pytest.raises(Exception) as excinfo:
        await app.state.mcp.call_tool(
            "get_timeline",
            {"start": "not-a-date", "end": "2026-04-18T13:00:00+00:00"},
        )
    msg = str(excinfo.value)
    assert "validation" in msg.lower() and "start" in msg


@pytest.mark.asyncio
async def test_get_timeline_limit_too_large_is_rejected(engine: Engine) -> None:
    app = create_app(engine)
    with pytest.raises(Exception) as excinfo:
        await app.state.mcp.call_tool(
            "get_timeline",
            {
                "start": "2026-04-18T11:00:00+00:00",
                "end": "2026-04-18T13:00:00+00:00",
                "limit": 501,
            },
        )
    msg = str(excinfo.value).lower()
    assert "validation" in msg and "limit" in msg


def _fact_observation(
    id_: str,
    offset: int,
    value: str,
    *,
    superseded_by: str | None = None,
) -> Observation:
    anchor = datetime(2026, 4, 18, 12, 0, 0, tzinfo=UTC) + timedelta(seconds=offset)
    return Observation(
        id=id_,
        content=f"fact at offset {offset} valued {value}",
        kind=Kind.FACT,
        subject="user",
        attribute="employer",
        value=value,
        observed_at=anchor,
        superseded_by=superseded_by,
    )
