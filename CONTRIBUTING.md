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

## Releasing

Releases are automated by `.github/workflows/release.yml`. Pushing a tag of the form `v*` triggers a build, publishes the artifacts to TestPyPI, installs from TestPyPI to smoke-test the console script, and (for non-pre-release tags) promotes the same artifacts to PyPI and creates a GitHub Release with auto-generated notes.

**Pre-release tags go to TestPyPI only.** Anything with a hyphen or a PEP 440 pre-release suffix (`v0.1.0-alpha.1`, `v0.1.0a1`, `v0.1.0rc2`) stops after the smoke test; `publish-pypi` and `github-release` are skipped. Use this for dry runs.

### One-time setup (TestPyPI and PyPI)

Do this once per index before the first successful run. Trusted publishing means no secrets live in the repo.

1. Register an account on both [TestPyPI](https://test.pypi.org/) and [PyPI](https://pypi.org/).
2. Create the project page on each index (navigate to "Your projects" → "Add pending publisher" while the name is unclaimed). If `rivermind` is taken on either index, pick an alternative name and update `pyproject.toml`'s `name` field before proceeding.
3. For each index, configure the Trusted Publisher:
   - Owner: `rivermind-ai`
   - Repository: `rivermind`
   - Workflow: `release.yml`
   - Environment: (leave blank)

### Per-release checklist

1. Bump `pyproject.toml` `version` using PEP 440 syntax (`0.1.0a1` for the first alpha, `0.1.0` for a final).
2. Open a PR titled `Bump version to X.Y.Z`. Merge once CI is green.
3. Tag the merge commit on `main`:
   ```bash
   git checkout main && git pull
   git tag v0.1.0a1        # or v0.1.0-alpha.1; both normalize to the same Version
   git push origin v0.1.0a1
   ```
4. Watch the **Actions** tab. Expect `build` → `publish-testpypi` → `smoke-testpypi`. For a final release, `publish-pypi` and `github-release` also run.
5. If the tag and `pyproject.toml` version disagree, the workflow fails in the `build` job with a clear diff. Delete the tag, fix the version, retry.

## Code of Conduct

By contributing, you agree to follow the [Code of Conduct](./CODE_OF_CONDUCT.md).
