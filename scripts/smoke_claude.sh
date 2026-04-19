#!/usr/bin/env bash
# Smoke-test a running rivermind server end-to-end.
#
# Usage:
#     ./scripts/smoke_claude.sh [http://host:port]
#
# Exits 0 with a "PASS" line when every check succeeds. Exits non-zero
# with "FAIL: <reason>" otherwise. Meant to be wired into the Claude
# Desktop quickstart so users can confirm the server is reachable before
# pointing a real MCP client at it.

set -euo pipefail

BASE_URL="${1:-http://127.0.0.1:8080}"
MCP_URL="${BASE_URL%/}/mcp/"

fail() {
    echo "FAIL: $1" >&2
    exit 1
}

json_get() {
    # Read stdin, print the value at a given dotted path, or empty string.
    python3 -c "import json, sys; d = json.load(sys.stdin); parts = sys.argv[1].split('.'); [setattr(sys.modules['__main__'], 'd', d) for _ in [0]];
for p in parts:
    d = d.get(p) if isinstance(d, dict) else None
print('' if d is None else d)" "$1"
}

# --- 1. /health ------------------------------------------------------------

HEALTH_STATUS_CODE=$(curl -s -o /tmp/rivermind_smoke_health.json -w '%{http_code}' "${BASE_URL%/}/health" || true)
if [[ "$HEALTH_STATUS_CODE" != "200" ]]; then
    fail "/health returned HTTP $HEALTH_STATUS_CODE (is the server running at $BASE_URL ?)"
fi

HEALTH_STATUS=$(json_get "status" < /tmp/rivermind_smoke_health.json)
HEALTH_VERSION=$(json_get "schema_version" < /tmp/rivermind_smoke_health.json)
if [[ "$HEALTH_STATUS" != "ok" ]]; then
    fail "/health reported status=$HEALTH_STATUS (expected ok)"
fi
if [[ -z "$HEALTH_VERSION" || "$HEALTH_VERSION" == "0" ]]; then
    fail "/health reported schema_version=$HEALTH_VERSION (expected >= 1)"
fi

# --- 2. MCP initialize handshake ------------------------------------------

INIT_BODY='{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"smoke","version":"0"}}}'
INIT_RAW=$(curl -s -N -X POST "$MCP_URL" \
    -H 'Content-Type: application/json' \
    -H 'Accept: application/json, text/event-stream' \
    --data "$INIT_BODY" || true)

if [[ -z "$INIT_RAW" ]]; then
    fail "initialize POST to $MCP_URL returned empty response"
fi

# Extract session id from response headers (re-run with -i to capture them).
SESSION_ID=$(curl -s -i -X POST "$MCP_URL" \
    -H 'Content-Type: application/json' \
    -H 'Accept: application/json, text/event-stream' \
    --data "$INIT_BODY" | awk 'BEGIN{IGNORECASE=1} /^mcp-session-id:/ {print $2}' | tr -d '\r\n' || true)

if [[ -z "$SESSION_ID" ]]; then
    fail "MCP initialize did not return an mcp-session-id header"
fi

# --- 3. tools/list ---------------------------------------------------------

TOOLS_BODY='{"jsonrpc":"2.0","id":2,"method":"tools/list"}'
TOOLS_RAW=$(curl -s -X POST "$MCP_URL" \
    -H 'Content-Type: application/json' \
    -H 'Accept: application/json, text/event-stream' \
    -H "mcp-session-id: $SESSION_ID" \
    --data "$TOOLS_BODY" || true)

# SSE frames wrap the JSON-RPC payload in `data: ...` lines. Strip those.
TOOLS_JSON=$(printf '%s' "$TOOLS_RAW" | awk '/^data: /{sub(/^data: /, ""); print}' | head -n1)
if [[ -z "$TOOLS_JSON" ]]; then
    TOOLS_JSON="$TOOLS_RAW"
fi

TOOL_NAMES=$(printf '%s' "$TOOLS_JSON" | python3 -c "
import json, sys
try:
    d = json.loads(sys.stdin.read())
except Exception as exc:
    print('parse_error:' + str(exc))
    raise SystemExit
tools = d.get('result', {}).get('tools', [])
print(' '.join(sorted(t.get('name', '') for t in tools)))
" || true)

EXPECTED='get_current_state get_narrative get_timeline record_observation'
if [[ "$TOOL_NAMES" != "$EXPECTED" ]]; then
    fail "tools/list returned [$TOOL_NAMES], expected [$EXPECTED]"
fi

echo "PASS ($BASE_URL, schema_version=$HEALTH_VERSION, 4 tools registered)"
