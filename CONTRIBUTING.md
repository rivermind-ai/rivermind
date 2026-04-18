# Contributing to Rivermind

Thanks for helping. Rivermind is a solo-maintained project in its early days; contributions are welcome but please read this before opening a PR.

## Scope

Rivermind is a **temporal memory layer for LLMs**, delivered as an MCP server. It is deliberately small. See [docs/ARCHITECTURE.md](./docs/ARCHITECTURE.md) for structure and the constraints that shape it (one database, no graph store, event-sourced with rebuildable projections). Proposals that cut against those constraints will be closed unless the use case is strong — the project optimizes for simplicity.

## Dev setup

Python 3.11 or 3.12 (nothing older).

```bash
git clone https://github.com/rivermind-ai/rivermind.git
cd rivermind
make install
source .venv/bin/activate
```

## Running checks

Run everything CI runs, locally, before pushing:

```bash
make lint   # ruff check + ruff format --check + mypy
make test   # pytest with coverage
```

Autoformat with `make format`. Run `make` with no arguments to see all available targets.

## Branching

- `main` is protected. All work happens on feature branches.
- Name branches `<scope>/<short-slug>` — e.g. `core/observations-schema`, `docs/readme-usage`, `fix/sqlite-pool-timeout`.
- Keep branches small and focused. One logical change per PR.

## Commits

- Single-line commit messages. No body, no bullets, no trailers.
- Imperative mood: `Add X`, not `Added X` or `Adding X`.
- If one line can't describe the change, the PR is too big — split it.

## Pull requests

- Fill in the PR template: what / how tested / breaking changes.
- CI must pass: ruff check, ruff format --check, mypy, pytest on Python 3.11 and 3.12.
- Match existing style. Don't refactor neighboring code unless it's the point of the PR.
- Drive-by fixes to unrelated files belong in their own PR.

## Issues

- **Bug reports** need a minimal reproduction.
- **Feature requests** need a concrete use case. "Would be nice" is not a use case.
- **Architectural suggestions**: open a discussion before a PR. Rivermind has load-bearing rules that aren't obvious from the code.

## Code of Conduct

By contributing, you agree to follow the [Code of Conduct](./CODE_OF_CONDUCT.md).
