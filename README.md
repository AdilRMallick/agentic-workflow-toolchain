# Agentic Workflow Toolchain

Custom agents and MCP servers that automate recurring research and tracking workflows.

The premise: the questions worth asking repeatedly — *what shipped in my dependencies this
week, what new papers touch my problem, who is discussing this* — are not hard, they are
just tedious to ask over and over. This toolchain makes each one a **tracker**: a stored
query that is polled on a schedule, deduplicated against everything seen before, and
rendered as a digest of only what is new.

Three front ends drive the same core, so nothing is agent-only or CI-only:

| Front end | Used by | Entry point |
| --- | --- | --- |
| Two MCP servers | Claude Code, any MCP client | `awt serve research` / `awt serve tracker` |
| A CLI | humans, GitHub Actions | `awt` |
| Scheduled workflows | GitHub Actions | `.github/workflows/` |

---

## Quick start

```bash
pip install -e '.[dev]'

awt sources                                   # what can be tracked
awt sync config/trackers.example.toml         # load the example trackers
awt poll                                      # fetch sources, record what is new
awt digest --title "This week"                # render only the new items
awt review --all                              # clear the backlog
```

The database defaults to `.awt/toolchain.db`; override it with `--db` or `$AWT_DB`.

## Source adapters

| `source_type` | Tracks | Config |
| --- | --- | --- |
| `arxiv` | new preprints matching a query | `query`, optional `sort_by` |
| `github_releases` | releases for a repository | `repo`, optional `include_prereleases` |
| `hackernews` | stories matching a query | `query`, optional `min_points` |
| `rss` | any RSS 2.0 or Atom feed | `url` |

Adding a source means writing one `SourceAdapter` and registering it in
`src/awt/sources/__init__.py`. The CLI, both MCP servers, and the scheduled workflows pick
it up with no further change.

## Trackers as code

Trackers are declared in TOML and committed, so a scheduled run needs no agent to know
what to track. `awt sync` reconciles the database with the file:

```toml
[[tracker]]
name        = "mcp-releases"
source_type = "github_releases"
config      = { repo = "modelcontextprotocol/python-sdk" }

[[tracker]]
name        = "agent-evals"
source_type = "arxiv"
config      = { query = "cat:cs.AI AND abs:agent" }
include     = ["evaluation", "benchmark"]
exclude     = ["survey"]
min_score   = 0.5
```

`include` keywords must appear for an item to be kept and set its 0–1 score; `exclude`
keywords veto an item outright. Scoring is deliberately simple and deterministic — a
digest has to be reproducible in CI and explainable to whoever reads it.

`sync` distinguishes two kinds of edit. *Retuning* — `include`, `exclude`, `min_score`,
`enabled` — changes which fetched results are kept, so the tracker is updated in place and
its items and review history survive. *Re-aiming* — `source_type` or `config` — changes
what is fetched at all, so the tracker is recreated and its stored items dropped, because
they answered a different question. The distinction matters for scheduled runs: nudging a
threshold must not resurface everything the reader already reviewed.

Retuning applies to what future polls keep; items already recorded keep the score they were
stored with. Raising `min_score` narrows what arrives next, it does not retroactively purge
a backlog — use `awt review` for that.

## MCP servers

Two servers, split by whether a call touches the outside world or the database.

**`awt-research`** — read-only, no persistence. Explore before committing to a tracker.

`research_list_source_types` · `research_search` · `research_preview_feed`

**`awt-tracker`** — owns the database and the recurring workflow.

`tracker_list` · `tracker_create` · `tracker_set_enabled` · `tracker_delete` ·
`tracker_poll` · `tracker_items` · `tracker_mark_reviewed` · `tracker_runs` ·
`tracker_digest` · `tracker_stats`

Every tool takes `response_format` (`markdown` for reading, `json` for parsing), carries
MCP annotations (`readOnlyHint`, `destructiveHint`, …), and returns failures *inside* the
result with a hint naming the next step rather than raising a protocol error.

An item matched by several trackers is listed once in a digest, under the tracker whose
filters scored it highest — it stays recorded against every tracker that matched it, since
each answers its own question, but nobody wants to meet the same paper three times in one
read. Pass `--no-dedupe` (or `dedupe: false`) to see every match.

`.mcp.json` registers both servers, so `claude` picks them up in this repo automatically.

## Agents and commands

`.claude/` ships the agent side of the toolchain:

- **agents** — `research-scout` (turn a topic into validated trackers), `release-watcher`
  (triage dependency releases for breaking changes), `digest-writer` (turn a backlog into
  prose worth reading)
- **commands** — `/digest`, `/track`, `/triage`
- **skills** — `weekly-digest`, the end-to-end recurring workflow
- **hooks** — a `SessionStart` hook that installs the package and reports tracker state

## Scheduled workflows

| Workflow | Schedule | Does |
| --- | --- | --- |
| `ci.yml` | push / PR | ruff, mypy, pytest on 3.11 and 3.12 |
| `research-digest.yml` | Mondays 07:00 UTC | sync, poll, open a digest issue, commit the digest |
| `release-watch.yml` | daily 06:00 UTC | poll release trackers, open an issue only when something shipped |

Both scheduled jobs run the deterministic CLI path end to end. `research-digest.yml`
additionally hands the result to Claude Code for a prose write-up, but only when
`ANTHROPIC_API_KEY` is set and only as a rewrite of an already-complete digest — so the
automation still produces its artifact with no model access at all.

Each job commits `.awt/toolchain.db` alongside its output. Deduplication is only true if
the database survives between runs, and an evictable cache would turn "what's new" into
"what's new, probably".

## Layout

```
src/awt/
  models.py      Tracker, Item, Run
  store.py       SQLite persistence and dedup
  sources/       one module per adapter, all offline-testable
  pipeline.py    fetch → score → filter → persist
  digest.py      Markdown rendering
  config.py      trackers.toml parsing and reconciliation
  tools.py       the toolkit both MCP servers and the CLI call
  mcp/           two thin stdio servers
  cli.py         awt
```

Source adapters never call the network directly: they take a `Fetcher`, which keeps the
entire test suite offline and the runtime free of third-party dependencies at runtime —
the MCP SDK is imported only by `awt/mcp/*`.

`docs/architecture.md` covers the design decisions and how to add a source.

## Development

```bash
ruff check . && ruff format --check .
mypy
pytest --cov=awt
```

221 tests, no network access, 97% line coverage. `tests/test_mcp_servers.py` calls both
servers the way a client does — list the tools, then invoke them by name — so a renamed
tool or a dropped annotation fails in CI rather than in Claude Code.

MIT licensed.
