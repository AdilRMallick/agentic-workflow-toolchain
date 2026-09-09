"""``awt-tracker`` -- the stateful half: recurring trackers, backlog, digests.

Where the research server explores, this server remembers. It owns the SQLite
database that turns a one-off query into a workflow: poll on a schedule, keep
only what has not been seen, and hand back a digest of the difference.
"""

from __future__ import annotations

from typing import Annotated, Any

from mcp.server.mcpserver import MCPServer
from pydantic import Field

from awt.mcp._shared import ResponseFormat, build_toolkit, mutating, read_only, run_tool, serve
from awt.pipeline import DEFAULT_LIMIT

INSTRUCTIONS = """\
Manage recurring research and release trackers backed by a local database.

Typical flow:
1. `tracker_create` registers a query (validate it first with the awt-research server).
2. `tracker_poll` fetches each source and records only items not seen before.
3. `tracker_digest` renders what is new; `tracker_mark_reviewed` clears the backlog.

`tracker_runs` shows poll history including failures — check it when a tracker
goes quiet, since a broken source is recorded rather than raised.
"""

server = MCPServer(
    name="awt-tracker",
    title="Agentic Workflow Toolchain — Tracker",
    instructions=INSTRUCTIONS,
    version="0.1.0",
)
toolkit = build_toolkit()


@server.tool(
    name="tracker_list",
    description=(
        "List configured trackers with their source type, filters, enabled state, and "
        "how many items are waiting to be reviewed. Start here to see what is tracked."
    ),
    annotations=read_only(open_world=False),
)
def tracker_list(
    enabled_only: Annotated[bool, Field(description="Skip disabled trackers.")] = False,
    response_format: Annotated[
        ResponseFormat, Field(description="'markdown' or 'json'.")
    ] = "markdown",
) -> str:
    return run_tool(
        toolkit.list_trackers, enabled_only=enabled_only, response_format=response_format
    )


@server.tool(
    name="tracker_create",
    description=(
        "Register a recurring query. The config is validated against the adapter "
        "immediately, so an invalid repo or missing query fails here rather than at "
        "the next scheduled poll. include keywords must appear in an item for it to be "
        "kept and determine its 0-1 score; exclude keywords veto an item outright."
    ),
    annotations=mutating(),
)
def tracker_create(
    name: Annotated[
        str,
        Field(description="Unique lowercase id, e.g. 'agent-evals'. [a-z0-9._-], max 64 chars."),
    ],
    source_type: Annotated[str, Field(description="arxiv, github_releases, hackernews, or rss.")],
    config: Annotated[
        dict[str, Any], Field(description="Adapter config, e.g. {'repo': 'owner/name'}.")
    ],
    include: Annotated[
        list[str] | None, Field(description="Required keywords; drive the score.")
    ] = None,
    exclude: Annotated[list[str] | None, Field(description="Keywords that veto an item.")] = None,
    min_score: Annotated[
        float, Field(description="Drop items scoring below this (0-1).", ge=0.0, le=1.0)
    ] = 0.0,
    response_format: Annotated[
        ResponseFormat, Field(description="'markdown' or 'json'.")
    ] = "markdown",
) -> str:
    return run_tool(
        toolkit.create_tracker,
        name,
        source_type,
        config,
        include=include,
        exclude=exclude,
        min_score=min_score,
        response_format=response_format,
    )


@server.tool(
    name="tracker_set_enabled",
    description=(
        "Enable or disable a tracker. A disabled tracker is skipped by tracker_poll "
        "but keeps its items and history — prefer this over deleting to pause a feed."
    ),
    annotations=mutating(idempotent=True),
)
def tracker_set_enabled(
    name: Annotated[str, Field(description="Tracker name.")],
    enabled: Annotated[bool, Field(description="True to resume polling, False to pause.")],
    response_format: Annotated[
        ResponseFormat, Field(description="'markdown' or 'json'.")
    ] = "markdown",
) -> str:
    return run_tool(toolkit.set_enabled, name, enabled, response_format=response_format)


@server.tool(
    name="tracker_delete",
    description=(
        "Permanently delete a tracker and every item and run recorded for it. "
        "To pause a tracker without losing history use tracker_set_enabled instead."
    ),
    annotations=mutating(destructive=True, idempotent=True),
)
def tracker_delete(
    name: Annotated[str, Field(description="Tracker name.")],
    response_format: Annotated[
        ResponseFormat, Field(description="'markdown' or 'json'.")
    ] = "markdown",
) -> str:
    return run_tool(toolkit.delete_tracker, name, response_format=response_format)


@server.tool(
    name="tracker_poll",
    description=(
        "Fetch each tracker's source and record items not seen before, returning only "
        "what is new. Omit names to poll every enabled tracker. Safe to call repeatedly: "
        "already-seen items are ignored. A source that fails is reported in the result "
        "with a hint instead of aborting the sweep."
    ),
    annotations=mutating(idempotent=True, open_world=True),
)
def tracker_poll(
    names: Annotated[
        list[str] | None, Field(description="Trackers to poll; omit for all enabled ones.")
    ] = None,
    limit: Annotated[
        int, Field(description="Max items to fetch per tracker (1-100).", ge=1, le=100)
    ] = DEFAULT_LIMIT,
    response_format: Annotated[
        ResponseFormat, Field(description="'markdown' or 'json'.")
    ] = "markdown",
) -> str:
    return run_tool(toolkit.poll, names, limit=limit, response_format=response_format)


@server.tool(
    name="tracker_items",
    description=(
        "Page through items recorded for one tracker, highest score first. Defaults to "
        "unreviewed items only. Returns has_more and next_offset — page rather than "
        "raising the limit when a tracker has a long backlog."
    ),
    annotations=read_only(open_world=False),
)
def tracker_items(
    name: Annotated[str, Field(description="Tracker name.")],
    unreviewed_only: Annotated[bool, Field(description="Only items not yet reviewed.")] = True,
    limit: Annotated[int, Field(description="Items per page (1-100).", ge=1, le=100)] = 20,
    offset: Annotated[int, Field(description="Items to skip; use next_offset to page.", ge=0)] = 0,
    response_format: Annotated[
        ResponseFormat, Field(description="'markdown' or 'json'.")
    ] = "markdown",
) -> str:
    return run_tool(
        toolkit.items,
        name,
        unreviewed_only=unreviewed_only,
        limit=limit,
        offset=offset,
        response_format=response_format,
    )


@server.tool(
    name="tracker_mark_reviewed",
    description=(
        "Clear items from a tracker's backlog once they have been read or written up. "
        "Pass external_ids to clear specific items, or omit it to clear all unreviewed "
        "items for the tracker. Reviewed items stay in the database and are never "
        "re-reported by a later poll."
    ),
    annotations=mutating(idempotent=True),
)
def tracker_mark_reviewed(
    name: Annotated[str, Field(description="Tracker name.")],
    external_ids: Annotated[
        list[str] | None,
        Field(description="Item ids from tracker_items; omit to clear the whole backlog."),
    ] = None,
    response_format: Annotated[
        ResponseFormat, Field(description="'markdown' or 'json'.")
    ] = "markdown",
) -> str:
    return run_tool(toolkit.mark_reviewed, name, external_ids, response_format=response_format)


@server.tool(
    name="tracker_runs",
    description=(
        "Show recent poll history — when each tracker last ran, how many items it "
        "fetched, and the error for any failed run. Check this first when a tracker "
        "stops producing items."
    ),
    annotations=read_only(open_world=False),
)
def tracker_runs(
    name: Annotated[str | None, Field(description="Limit to one tracker; omit for all.")] = None,
    limit: Annotated[int, Field(description="Max runs to return (1-100).", ge=1, le=100)] = 20,
    response_format: Annotated[
        ResponseFormat, Field(description="'markdown' or 'json'.")
    ] = "markdown",
) -> str:
    return run_tool(toolkit.runs, name, limit=limit, response_format=response_format)


@server.tool(
    name="tracker_digest",
    description=(
        "Render a Markdown digest of what is new, grouped by tracker and ordered by "
        "score. This is the artifact the scheduled workflows publish. An item matched by "
        "several trackers is listed once, under the tracker that scored it highest. Does "
        "not mark anything reviewed — call tracker_mark_reviewed once the digest is "
        "delivered."
    ),
    annotations=read_only(open_world=False),
)
def tracker_digest(
    names: Annotated[
        list[str] | None, Field(description="Trackers to include; omit for all.")
    ] = None,
    title: Annotated[str, Field(description="Heading for the digest.")] = "Research digest",
    unreviewed_only: Annotated[bool, Field(description="Only items not yet reviewed.")] = True,
    per_tracker: Annotated[
        int, Field(description="Max items per tracker (1-100).", ge=1, le=100)
    ] = 10,
    summaries: Annotated[bool, Field(description="Include each item's summary line.")] = True,
    dedupe: Annotated[
        bool, Field(description="List an item once, under its best-scoring tracker.")
    ] = True,
    response_format: Annotated[
        ResponseFormat, Field(description="'markdown' or 'json'.")
    ] = "markdown",
) -> str:
    return run_tool(
        toolkit.digest,
        names,
        title=title,
        unreviewed_only=unreviewed_only,
        per_tracker=per_tracker,
        summaries=summaries,
        dedupe=dedupe,
        response_format=response_format,
    )


@server.tool(
    name="tracker_stats",
    description=(
        "Counts of trackers, items, unreviewed items and runs in the database, plus the "
        "database path. A cheap health check for a scheduled workflow."
    ),
    annotations=read_only(open_world=False),
)
def tracker_stats(
    response_format: Annotated[
        ResponseFormat, Field(description="'markdown' or 'json'.")
    ] = "markdown",
) -> str:
    return run_tool(toolkit.stats, response_format=response_format)


def main() -> None:
    serve(server)


if __name__ == "__main__":
    main()
