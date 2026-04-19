# Architecture

Rivermind is a memory layer for LLMs, exposed via MCP. Three-tier layout: pure data models and interfaces in `core/`, concrete implementations in `adapters/`, and a thin entry point that wires them together.

This document describes the system as it currently exists. If something here conflicts with the code, the code is correct and this doc is a bug.

## The data model

Three tables back everything. Two are projections, meaning they can be dropped and rebuilt from the third.

### Observations — the append-only log

Every memory starts here. One row per thing the system was told, permanently, with bi-temporal timestamps so you can ask both "what was true then?" and "what did we know then?".

Each observation has a `kind`:

- `fact` — a supersedable `(subject, attribute) → value` assertion. "User works at Acme."
- `event` — a one-time occurrence. "Finished a book on April 20."
- `reflection` — a subjective note. "The book was better than expected."

Only `fact` observations participate in supersession: a new fact with the same `(subject, attribute)` replaces an older one via a `superseded_by` pointer. Events and reflections are permanent record; they are never superseded.

Schema: `src/rivermind/adapters/stores/migrations/001_initial.sql`.
Model: `src/rivermind/core/models.py` (`Observation`, `Kind`).

### State — current-truth projection

A flat `(subject, attribute) → current_value` table. One row per slot, always reflecting the latest fact. Used by `get_current_state` as the anti-hallucination read path. Each row carries `source_observation` so a caller can always drill back to the observation that produced the state.

State is derived. If it drifts or corrupts, drop it and recompute from observations. The write-path projector (`src/rivermind/core/projectors/state.py`) upserts a state row whenever `Engine.record_observation` lands a fact; the store's stale-drop guard ensures late-arriving observations do not clobber newer state.

### Narratives — synthesized summaries

LLM-generated free-form summaries over a time window, optionally scoped to a topic. A narrative has a `[period_start, period_end]` window, a list of source observation ids, and a `generated_at` timestamp. Read via `get_narrative(period, topic)`.

Narratives are read-only at the tool layer; synthesis is a separate concern (not yet implemented).

## The five seams

Only five interfaces exist in `core/`. Everything else stays concrete.

1. **`MemoryStore`** (Protocol) — persistence for all three tables. Methods: `save_observation`, `get_observations(start, end, topic?, limit?, include_superseded?)`, `upsert_state`, `get_state`, `list_states(subject?, attribute?)`, `save_narrative`, `get_narratives(period_start, period_end, topic?, include_superseded?)`, `schema_version()`. All synchronous.
2. **`Embedder`** (Protocol) — text → vector. Declared now to avoid a later refactor; not used in v0.1.
3. **`Extractor`** (Protocol) — excerpt → `Observation`. Used only by narrative synthesis.
4. **`Transport`** — not a Protocol, a loose callable alias. Deliberately under-specified so core does not couple to FastAPI, MCP SDK, or any framework type.
5. **`Kind` vocabulary** — the `fact | event | reflection` `StrEnum`. The vocabulary itself is a seam because extending it (`preference`, `goal`, …) is one of the more plausible future changes.

Defined in: `src/rivermind/core/interfaces.py`, `src/rivermind/core/models.py`.

## The dependency rule

**`core/` does not import from `adapters/`.** Interfaces are defined in `core/`; concrete implementations live in `adapters/`; wiring happens at startup in a small entry-point module that sees both sides.

This is the rule that keeps storage swappable. Today the only backend is SQLite; swapping to Postgres should be one adapter file, not a rewrite.

## The engine

One class. Transports call into it; nothing else contains business logic.

- `Engine(store: MemoryStore, extractor: Extractor | None = None)` — dependency injection; no module globals.
- `record_observation(obs) -> id`
- `get_timeline(start, end, topic?, limit?, include_superseded?)`
- `get_current_state(subject?, attribute?)`
- `get_narrative(period_start, period_end, topic?, include_superseded?)`
- `schema_version()`

Lives in: `src/rivermind/core/engine.py`. Pure core — imports only from `rivermind.core.*`. No FastAPI, no MCP SDK.

## The SQLite adapter

`SQLiteMemoryStore` in `src/rivermind/adapters/stores/sqlite.py` is the first and currently only concrete `MemoryStore`.

- Single-file database. Default location `~/.rivermind/rivermind.db`.
- One long-lived connection with `check_same_thread=False`. Python's `sqlite3` serializes access internally.
- PRAGMAs: `journal_mode=WAL`, `foreign_keys=ON`.
- FTS5 virtual table `observations_fts` mirrors `observations.content`; kept in sync by schema triggers. Powers the `topic` parameter on `get_observations` without needing an embedding provider.
- `upsert_state` drops stale writes via `ON CONFLICT ... WHERE excluded.current_since > state.current_since`.
- Constructor applies pending schema migrations by default (`migrate=True`).

## Migrations

Forward-only. Numbered SQL files in `src/rivermind/adapters/stores/migrations/`; the runner (`migrations.py`) applies any whose version is greater than the DB's recorded `schema_version`. Refuses to run against a DB whose version is newer than what the code ships (prevents downgrade corruption).

Currently one migration: `001_initial.sql`.

## The MCP transport

`src/rivermind/adapters/transports/mcp.py` exposes `create_app(engine) -> FastAPI`. Internals:

- FastMCP mounted at `/mcp` over streamable HTTP.
- FastMCP's session-manager lifespan is wrapped into FastAPI's `lifespan` so the task group is alive when requests arrive.
- Plain `/health` endpoint returning `{"status": "ok", "schema_version": N}` via `engine.schema_version()`.
- Four tools are registered, each wrapped by a structured-logging decorator that emits `tool_call_start` / `tool_call_end` / `tool_call_error` with `tool`, `request_id`, `duration_ms`:
  - `record_observation(kind, content, observed_at, subject?, attribute?, value?, session_id?)` — fact observations require `subject + attribute` (matching the DB CHECK); `value` is optional.
  - `get_timeline(start, end, topic?, limit?, include_superseded?)` — returns `{observations, next_cursor}`.
  - `get_current_state(subject?, attribute?)` — returns `{states: [...]}`.
  - `get_narrative(period, topic?, include_superseded?)` — `period` is one of `last_week | last_month | last_quarter` or an ISO 8601 interval `<start>/<end>`. Returns `{narrative}` on hit, `{narrative: null, message}` on miss. Does not trigger synthesis.

No module globals; tests create fake engines and pass them into the factory.

## Entry point

`python -m rivermind` (`src/rivermind/__main__.py`) is a minimal bootstrap: construct `SQLiteMemoryStore`, wrap with `Engine`, call `create_app`, run under uvicorn. Takes `--host`, `--port`, `--db`. A proper Click CLI is planned but not yet built; keep this file thin so it can be replaced without churn.

## Testing

`tests/` has three subdirectories:

- `unit/` — pure-Python tests that do not touch SQLite or HTTP. Covers models, Engine with a recording fake store, Protocol conformance, smoke.
- `integration/` — tests that hit a real SQLite file and/or the FastAPI app. Covers the migration SQL, the migration runner, the SQLite adapter, and the MCP transport over in-process `mcp.call_tool`.
- `contract/memory_store.py` — a reusable `MemoryStoreContractTests` class. Any adapter imports and subclasses it, provides a `store` fixture, and automatically inherits the full suite. Today only `SQLiteMemoryStore` exercises it; a Postgres adapter will get the suite for free.

Shared fixtures in `tests/conftest.py`: deterministic `now` / `t`, scratch `tmp_db_path`, canonical `seeded_observations`.

## What isn't built yet

The architecture above assumes all the pieces but some are still placeholders:

- **Observation-level supersession on write path.** The state projector is wired, but nothing sets `superseded_by` on the previous fact observation when a newer one arrives. That means `include_superseded=False` on `get_observations` does not yet hide old facts, even though the state table reflects only the latest.
- **Narrative synthesis.** The read path for `get_narrative` exists, but no worker writes narratives yet. `get_narrative` returns `null` in practice.
- **`rivermind` CLI (Click).** `python -m rivermind` is the stand-in.
- **Embedder backends.** Protocol declared, not used.
- **Extractor backends.** Protocol declared, reserved for narrative synthesis.
- **Postgres adapter / cloud tier.** Deliberately deferred to post-v0.1.

---

*This document grows with the code. If something here conflicts with what the code does, the code is correct and this doc is a bug — file an issue.*
