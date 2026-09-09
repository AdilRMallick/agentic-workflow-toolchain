# Architecture

How the pieces fit, and why they are arranged this way.

## The shape of the problem

Recurring research is not hard, it is repetitive: the same four questions, asked weekly,
against sources that mostly return what they returned last time. Almost all of the value
is in the *difference* between this week and last. So the system is built around one
operation — **fetch, then subtract what has already been seen** — and everything else
(sources, scoring, digests, agents) hangs off that.

## Data flow

```
config/trackers.toml            declarative: what to watch
        │  awt sync
        ▼
     trackers ──────► SourceAdapter.fetch(config, limit, fetch) ──► [Item, …]
                                                                      │
                                              pipeline.select()  ◄────┘   score, filter, sort
                                                     │
                                    store.record_items()  ─────────────►  new items only
                                                     │                     (UNIQUE tracker+external_id)
                                              digest.build_digest()
                                                     │
                                          Markdown ──┴── JSON
```

A `Run` row is written for every poll, successful or not, so a tracker that has gone quiet
can be distinguished from a tracker that has been failing for three weeks. That
distinction is the whole reason run history exists.

## Decisions

**Adapters never call the network.** Every `SourceAdapter.fetch` takes a `Fetcher`
callable. Production passes `UrllibFetcher`; tests pass a canned dictionary. This is the
single most load-bearing decision in the codebase: the entire suite runs offline and
deterministically, so CI can never go red because arXiv was slow.

**Standard library only at runtime.** SQLite, `urllib`, `xml.etree`, `tomllib`. The MCP
SDK is the only third-party dependency, and it is imported solely by `awt/mcp/*`, so the
CLI works even where the SDK is not installed. Fewer dependencies, fewer scheduled runs
broken by an unrelated upgrade.

**Tool logic lives in `tools.py`, not in the servers.** `Toolkit` returns a `ToolResponse`
carrying both structured data and Markdown; the MCP servers and the CLI are thin adapters
over it. An agent and a cron job therefore execute the same code path, and the tool layer
is testable without an MCP client. Formatting is written once, not twice.

**Two servers, split by blast radius.** `awt-research` is read-only and open-world;
`awt-tracker` owns the database. A client can grant the research server broadly and gate
the tracker server's mutations — a split that is only meaningful if it is enforced by
which tools exist where, which is why it is a server boundary and not a naming convention.

**A fresh SQLite connection per tool call.** The MCP SDK may dispatch sync tools on worker
threads, and `sqlite3` connections are not shareable across threads. Opening per call is
cheap, removes the thread-affinity problem entirely, and lets a CLI invocation and a
running server operate on one database at the same time.

**Scoring is dumb on purpose.** Word-boundary keyword matching, a fraction of terms hit,
a small bonus for a title match. No embeddings, no model call. A digest has to be
byte-reproducible in CI and explainable to whoever reads it — "it matched two of your
three keywords, one in the title" is a reason a person can act on, and tune.

**Failures are values, not exceptions.** `poll_tracker` catches source errors and returns
a failed `PollResult`. One broken feed must never abort a sweep across twenty healthy
ones. Errors carry a `hint` naming the next step, which is what both the CLI and the MCP
tools surface. The cost is that a caller has to check `status` — so `awt poll` exits
non-zero when anything failed, and the scheduled workflows annotate the digest and fail
the run rather than publishing a silent gap.

**State is committed, not cached.** The scheduled workflows commit `.awt/toolchain.db`.
Deduplication is only true if the database survives between runs, and a cache that can be
evicted turns "what's new" into "what's new, probably". A committed database also makes
every digest reproducible from the repository alone.

## Extending it

Adding a source is one file:

1. Subclass `SourceAdapter` in `src/awt/sources/`; implement `validate` (raise `ConfigError`
   with a hint) and `fetch` (return `Item`s, newest first).
2. Register the instance in `_ADAPTERS` in `src/awt/sources/__init__.py`.
3. Add a fixture under `tests/fixtures/` and a parse test.

The CLI, both MCP servers, `awt sync`, and the scheduled workflows pick it up with no
further change — they all read the registry rather than enumerating source types.

## What is deliberately absent

- **No scheduler.** `cron` and GitHub Actions already do this well.
- **No full-text search.** The store answers "what is new for this tracker"; anything more
  belongs in whatever reads the digest.
- **No summarization in the core.** `awt digest` renders exactly what was found. Prose is
  the `digest-writer` agent's job, and it is optional — the deterministic path has to keep
  working without any model access.
