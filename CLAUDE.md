# agentic-workflow-toolchain

Agents and MCP servers that automate recurring research and tracking workflows. A
**tracker** is a stored query, polled on a schedule, deduplicated against everything seen
before. See `docs/architecture.md` for the design and `README.md` for usage.

## Commands

```bash
pytest                       # full suite; always offline
ruff check . && ruff format --check .
mypy                         # strict, must stay clean
awt sync config/trackers.example.toml && awt poll && awt digest
```

The `SessionStart` hook installs the package and syncs the example trackers, so `awt`
works from the first turn.

## Where things go

| Change | File |
| --- | --- |
| A new source (arXiv, RSS, …) | `src/awt/sources/`, registered in `sources/__init__.py` |
| Tool behaviour for agents *or* the CLI | `src/awt/tools.py` — never in a server module |
| A new MCP tool | `src/awt/tools.py`, then a thin wrapper in `src/awt/mcp/` |
| Persistence | `src/awt/store.py` |

## Conventions that matter

- **Adapters take a `Fetcher`; they never call the network directly.** Every test runs
  offline. A test that needs the network is a test that will flake in CI — use a fixture
  in `tests/fixtures/` and the `fetcher` fixture instead.
- **Tool logic lives in `tools.py`.** The MCP servers and the CLI are both thin adapters
  over `Toolkit`, so an agent and a cron job run the same code. Putting logic in a server
  module makes it unreachable from the CLI and untestable without an MCP client.
- **Errors carry a hint.** Raise `AwtError` subclasses with `hint=` naming the next
  concrete step. That hint is what an agent and a CI log actually see.
- **A failing source is a value, not an exception.** `poll_tracker` returns a failed
  `PollResult`; one broken feed must not abort a sweep. If you add a code path that can
  raise during a poll, catch it there.
- **Scoring stays deterministic.** No model calls in `pipeline.py`. Digests must be
  reproducible in CI and explainable to whoever reads them.
- Adding or renaming an MCP tool means updating `tests/test_mcp_servers.py` — the tool
  name sets in that file are the contract.

## Gotchas

- The MCP SDK is v2 (`mcp.server.mcpserver.MCPServer`, snake_case `ToolAnnotations`
  fields), not the v1 `FastMCP` API.
- Never pass `datetime` objects to `sqlite3`; the store writes ISO-8601 UTC strings
  because the sqlite3 adapters are deprecated.
- `pytest` runs with `filterwarnings = ["error"]`. A new `DeprecationWarning` fails the suite.
