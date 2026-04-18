# Architecture

Rivermind is a memory layer for LLMs, exposed via MCP.

This document describes the system as it currently exists. It is updated as code is written — not ahead of it.

## Current status

Early development. No architecture to document yet.

## Structure

- `src/rivermind/core/` — data models, interfaces, engine. No framework dependencies.
- `src/rivermind/adapters/` — concrete implementations (storage, transports, extractors).
- `tests/` — unit and integration tests.

## The one rule

`core/` does not import from `adapters/`. Interfaces are defined in `core/`; implementations live in `adapters/`; wiring happens at startup.

---

*This document grows with the code. If something here conflicts with what the code does, the code is correct and this doc is a bug — file an issue.*
