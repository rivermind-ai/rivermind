# rivermind

[![CI](https://github.com/rivermind-ai/rivermind/actions/workflows/ci.yml/badge.svg)](https://github.com/rivermind-ai/rivermind/actions/workflows/ci.yml)

> _Problem statement goes here — one or two sentences on what rivermind does and who it's for._

> ⚠️ **Under active development.** APIs, schemas, and on-disk formats are unstable and may change without notice. Pin to a commit if you depend on it.

## Install

```bash
git clone https://github.com/rivermind-ai/rivermind.git
cd rivermind
make install
source .venv/bin/activate
```

## Usage

Start the server and connect Claude Desktop in under three minutes: see the [Claude Desktop quickstart](./docs/quickstart-claude.md).

```bash
python -m rivermind            # starts on http://127.0.0.1:8080
./scripts/smoke_claude.sh      # verifies the server is reachable
```

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
