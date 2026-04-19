# Claude Desktop Quickstart

Three minutes from clone to Claude remembering something you told it.

## Prerequisites

- **Python 3.11+**
- **Claude Desktop** (latest version from `https://claude.ai/download`)
- **Node.js** for `npx`. Claude Desktop needs it to spawn the `mcp-remote` bridge that forwards stdio to Rivermind's HTTP server.

## 1. Install

```bash
git clone https://github.com/rivermind-ai/rivermind.git
cd rivermind
make install
source .venv/bin/activate
```

Or once Rivermind is published:

```bash
pip install rivermind
```

## 2. Start the server

```bash
python -m rivermind
```

You should see uvicorn log lines ending with `Application startup complete.` and `Uvicorn running on http://127.0.0.1:8080`. The SQLite database is created at `~/.rivermind/rivermind.db` by default. Pass `--db ./rivermind.db` to keep it inside the repo instead.

Leave the server running in this terminal. Open a second one for the next steps.

![Server startup logs](./assets/quickstart-1-server.png)

## 3. Verify the server is reachable

```bash
./scripts/smoke_claude.sh
```

Expected output:

```
PASS (http://127.0.0.1:8080, schema_version=1, 4 tools registered)
```

If you see `FAIL: ...`, fix that first; Claude Desktop won't connect to a server that can't answer its own smoke test.

## 4. Configure Claude Desktop

Open Claude Desktop's config file. The path depends on your OS:

- **macOS:** `~/Library/Application Support/Claude/claude_desktop_config.json`
- **Windows:** `%APPDATA%\Claude\claude_desktop_config.json`
- **Linux:** `~/.config/Claude/claude_desktop_config.json`

The file may already exist with a `preferences` block and other settings. **Do not replace the file.** Merge the `mcpServers` block in. If you have no file yet, create one with only the block below.

Final shape (example with both your existing preferences and the new block):

```json
{
  "preferences": { "...your existing preferences...": true },
  "mcpServers": {
    "rivermind": {
      "command": "npx",
      "args": ["-y", "mcp-remote", "http://127.0.0.1:8080/mcp"]
    }
  }
}
```

If you're comfortable with a script, this one-liner merges correctly without touching your preferences:

```bash
python3 -c '
import json, pathlib, os
p = pathlib.Path.home() / "Library/Application Support/Claude/claude_desktop_config.json"
cfg = json.loads(p.read_text()) if p.exists() else {}
cfg.setdefault("mcpServers", {})["rivermind"] = {
    "command": "npx",
    "args": ["-y", "mcp-remote", "http://127.0.0.1:8080/mcp"],
}
p.parent.mkdir(parents=True, exist_ok=True)
p.write_text(json.dumps(cfg, indent=2) + "\n")
print("wrote", p)
'
```

**Quit Claude Desktop completely (Cmd+Q on macOS) and reopen it.** A window close is not enough, the config is only re-read on a full launch.

## 5. Confirm Claude sees the server

Open **Settings → Developer**. Under **Local MCP servers** you should see `rivermind` listed with a running status. If it says "No servers added," Claude has not picked up the config — revisit step 4 and make sure the file is valid JSON and contains the `mcpServers` key at the top level.

![rivermind listed under Local MCP servers](./assets/quickstart-2-server-listed.png)

You will **not** see Rivermind tools in the chat composer's `+` menu. MCP tools are invoked by Claude on demand during a chat; they are not browse-and-click.

## 6. Seed rivermind with what Claude already knows

If you've talked to Claude before and it has memory of you (via Claude's built-in memory, projects, or this conversation's scrollback), start by telling it to dump that into rivermind. This gives rivermind a real baseline to query against instead of an empty log.

In chat A:

> Record everything you know about me to rivermind. Use facts for key-value state (subject + attribute + value), events for one-time occurrences with dates, and reflections for subjective notes. Approximate dates where needed and note that in the content.

Claude will batch-call `record_observation` for each piece. Expand any tool call to verify the shape.

If Claude doesn't have prior memory of you, seed manually with a few lines:

> Record these to rivermind:
> - I'm currently reading "The Three-Body Problem" by Liu Cixin.
> - My 2026 reading goal is 24 books.
> - Science fiction is my favorite genre.

## 7. The magic moment

**Close chat A.** No scrollback context, no shared session memory.

Open a brand-new chat and ask:

> Check rivermind. What do you know about me?

Claude should call `get_current_state` and/or `get_timeline` and come back with the facts you seeded — including ones you never told it in *this* chat. That's the cross-session memory kicking in.

Try a few more to exercise the tools:

> Check rivermind. What am I reading right now?

> Check rivermind. What's my reading goal for this year?

> Check rivermind. What have I finished over the last month?

![New chat recalls via get_timeline](./assets/quickstart-3-recall.png)

That's the whole pitch. Cross-session memory without wiring anything into Claude's context window manually.

## 8. Prompt bank

Useful one-liners for daily use. All assume the server is running and Claude Desktop has rivermind configured.

**Record an ad-hoc fact:**

> Remember: I switched from paperback to Kindle on April 1, 2026. Record to rivermind.

**Record an event:**

> Log an event to rivermind: started reading "Project Hail Mary" today.

**Record a reflection:**

> Add a reflection to rivermind: "The Three-Body Problem" was excellent, the physics concepts landed in a way I didn't expect.

**Recall current state:**

> Check rivermind. What book am I on and what's my goal for the year?

**Recall history:**

> Check rivermind. Show me my reading timeline for the last quarter, chronologically.

**Topic search:**

> Check rivermind. Search the timeline for anything about science fiction.

**Narrative (once the synthesizer runs):**

> Check rivermind. Give me the narrative for last week.

> Note: narrative synthesis is a scheduled background job, not real-time. If no narrative exists yet, `get_narrative` returns null; fall back to `get_timeline` for the same period.

**Correct a mistake:**

> I made an error earlier. I actually finished "The Three-Body Problem" on April 20, not April 15. Record the correction to rivermind.

## Troubleshooting

**`python -m rivermind` fails with `ModuleNotFoundError: No module named 'rivermind'`.**
The venv is not activated. Run `source .venv/bin/activate` or use `make dev`.

**`./scripts/smoke_claude.sh` prints `FAIL: /health returned HTTP 000`.**
The server is not running or is bound to a different port. Check that `python -m rivermind` is still running in another terminal.

**Settings → Developer shows "No servers added."**
- The config file does not exist at the expected path, or the JSON is invalid (trailing commas silently break everything).
- The `mcpServers` key is nested inside another object by mistake. It must be a top-level key.
- You edited the file but did not fully quit Claude Desktop. Quit via Cmd+Q and reopen.

**Settings → Developer lists `rivermind` but with an error status.**
- Confirm `npx` is on the PATH (`which npx`). If not, install Node.js.
- From a third terminal, test the bridge yourself: `npx -y mcp-remote http://127.0.0.1:8080/mcp`. Ctrl+C after a second. If it prints an error at startup, that is the same error Claude is hitting.
- Open Claude Desktop's MCP logs: Settings → Developer → scroll for a log link, or on macOS check `~/Library/Logs/Claude/mcp*.log`.

**Claude sees the tools but calls to `record_observation` return errors with `value`.**
As of the current schema, the model requires `value` for facts. If the content already carries the whole payload, tell Claude to pass a short placeholder for `value` or omit the fact form and use an `event` or `reflection` instead. This behavior is tracked and will be relaxed.

**Claude called `record_observation` but `get_current_state` returns `[]`.**
This is expected at the current schema. `record_observation` writes to the append-only log; the state projection is not auto-populated yet. Use `get_timeline` in the meantime to verify what was written. The state projector will land in a follow-up change.

**The DB file at `~/.rivermind/rivermind.db` isn't being created.**
Run with `--db ./rivermind.db` and watch for permission errors. The default path creates `~/.rivermind/` on first run.

## What's next

- Run `python -m rivermind --help` to see available flags.
- Daily workflow: open a terminal, `source .venv/bin/activate`, `make dev`. That's it.
- If you want the server to start with your Mac / Windows / Linux session, wrap it in a launchd / startup-task / systemd unit. Out of scope for this quickstart.
