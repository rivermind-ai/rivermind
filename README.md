# rivermind

[![CI](https://github.com/rivermind-ai/rivermind/actions/workflows/ci.yml/badge.svg)](https://github.com/rivermind-ai/rivermind/actions/workflows/ci.yml)

Rivermind is a temporal memory layer for LLMs. It runs as a local MCP server over SQLite, stores bi-temporal observations (facts, events, reflections), and gives any MCP client cross-session recall without custom retrieval code.

## Install

```bash
git clone https://github.com/rivermind-ai/rivermind.git
cd rivermind
make install
source .venv/bin/activate
```

## Quick start

```bash
rivermind init                 # creates ~/.rivermind/rivermind.db
rivermind serve                # starts http://127.0.0.1:8080 (MCP at /mcp)
./scripts/smoke_claude.sh      # verifies the server is reachable
```

To wire this into Claude Desktop (three minutes from clone to Claude remembering what you told it), see the [Claude Desktop quickstart](./docs/quickstart-claude.md).

## Narrative synthesis (optional)

Narratives require an LLM API key. Without one, `serve` and `reeval` still work: compaction and state rebuild run, synthesis is skipped with a warning.

Get a key from the provider of your choice:

- Anthropic: [console.anthropic.com/settings/keys](https://console.anthropic.com/settings/keys)
- OpenAI: [platform.openai.com/api-keys](https://platform.openai.com/api-keys)

Export it in your shell (default provider is Anthropic; set `RIVERMIND_LLM_PROVIDER=openai` if the key is an OpenAI one):

```bash
export RIVERMIND_API_KEY=sk-ant-...           # or sk-... for OpenAI
export RIVERMIND_LLM_PROVIDER=anthropic       # or "openai" (default: anthropic)
```

To persist it across terminal sessions, append those two lines to `~/.zshrc` (or `~/.bashrc`) and `source` the file. Verify with `echo $RIVERMIND_API_KEY`.

## Commands

All subcommands accept a global `--db PATH` (default `~/.rivermind/rivermind.db`). Run `rivermind --help` for the full tree, or `rivermind <command> --help` for per-command flags.

| Command | Purpose |
|---------|---------|
| `rivermind init` | Create the DB and apply migrations. |
| `rivermind serve [--host] [--port] [--no-reeval]` | Run the FastAPI + MCP server. |
| `rivermind timeline [--start] [--end] [--topic] [--limit] [--json]` | Print observations in a time window. |
| `rivermind state [--subject] [--attribute] [--rebuild] [--json]` | Print state rows, or rebuild the projection. |
| `rivermind reeval` | Run narrative synthesis + compaction + state rebuild for overdue weeks. |
| `rivermind export --out PATH [--format json\|sqlite]` | Dump observations, state, and narratives. |
| `rivermind import --from PATH` | Load a prior rivermind JSON export. |

## Development

```bash
make install
make dev        # run the server against a local dev DB
make test       # pytest
make lint       # ruff check + ruff format --check + mypy
```

## Documentation

- [Claude Desktop quickstart](./docs/quickstart-claude.md) — connect Claude to a local Rivermind server.
- [Architecture](./docs/ARCHITECTURE.md) — system structure, updated as the code is written.

## License

MIT — see [LICENSE](./LICENSE).

## Code of Conduct

This project follows the [Contributor Covenant](./CODE_OF_CONDUCT.md) v2.1.
