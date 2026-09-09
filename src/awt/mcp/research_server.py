"""``awt-research`` -- read-only exploration of external sources.

This server never writes to the database. It exists so an agent can try a query
against arXiv, GitHub, Hacker News or any feed, see what comes back, and only
then hand the query that worked to ``awt-tracker`` to be polled on a schedule.
"""

from __future__ import annotations

from typing import Annotated, Any

from mcp.server.mcpserver import MCPServer
from pydantic import Field

from awt.mcp._shared import ResponseFormat, build_toolkit, read_only, run_tool, serve
from awt.pipeline import DEFAULT_LIMIT

INSTRUCTIONS = """\
Explore research sources before committing to a recurring tracker.

Typical flow:
1. `research_list_source_types` to see which adapters exist and what config each takes.
2. `research_search` to try a query and inspect the results.
3. Once a query looks right, create a tracker for it with the awt-tracker server.

Nothing here is persisted; every call is a fresh fetch.
"""

server = MCPServer(
    name="awt-research",
    title="Agentic Workflow Toolchain — Research",
    instructions=INSTRUCTIONS,
    version="0.1.0",
)
toolkit = build_toolkit()


@server.tool(
    name="research_list_source_types",
    description=(
        "List every source adapter this toolchain supports (arxiv, github_releases, "
        "hackernews, rss) together with the config fields each one requires. "
        "Call this before research_search or tracker_create to get the config shape right."
    ),
    annotations=read_only(open_world=False),
)
def research_list_source_types(
    response_format: Annotated[
        ResponseFormat, Field(description="'markdown' for reading, 'json' for parsing.")
    ] = "markdown",
) -> str:
    return run_tool(toolkit.list_source_types, response_format=response_format)


@server.tool(
    name="research_search",
    description=(
        "Run a one-off query against a source and return the matching items without "
        "saving anything. Use it to validate a query before turning it into a tracker. "
        "config must match the shape reported by research_list_source_types, e.g. "
        '{"query": "cat:cs.AI AND abs:agent"} for arxiv or {"repo": "owner/name"} for '
        "github_releases. Optional include/exclude keywords score and filter the results."
    ),
    annotations=read_only(idempotent=False),
)
def research_search(
    source_type: Annotated[
        str, Field(description="One of: arxiv, github_releases, hackernews, rss.")
    ],
    config: Annotated[
        dict[str, Any], Field(description="Adapter config, e.g. {'query': 'cat:cs.AI'}.")
    ],
    limit: Annotated[
        int, Field(description="Max items to return (1-100).", ge=1, le=100)
    ] = DEFAULT_LIMIT,
    include: Annotated[
        list[str] | None, Field(description="Keywords that must appear; also drives the score.")
    ] = None,
    exclude: Annotated[
        list[str] | None, Field(description="Keywords that veto an item outright.")
    ] = None,
    response_format: Annotated[
        ResponseFormat, Field(description="'markdown' or 'json'.")
    ] = "markdown",
) -> str:
    return run_tool(
        toolkit.search,
        source_type,
        config,
        limit=limit,
        include=include,
        exclude=exclude,
        response_format=response_format,
    )


@server.tool(
    name="research_preview_feed",
    description=(
        "Fetch an RSS 2.0 or Atom feed by URL and return its most recent entries. "
        "A shortcut for research_search with source_type='rss', useful for checking "
        "that a blog or changelog URL is really a feed before tracking it."
    ),
    annotations=read_only(idempotent=False),
)
def research_preview_feed(
    url: Annotated[str, Field(description="Absolute http(s) feed URL.")],
    limit: Annotated[int, Field(description="Max entries to return (1-100).", ge=1, le=100)] = 10,
    response_format: Annotated[
        ResponseFormat, Field(description="'markdown' or 'json'.")
    ] = "markdown",
) -> str:
    return run_tool(
        toolkit.search, "rss", {"url": url}, limit=limit, response_format=response_format
    )


def main() -> None:
    serve(server)


if __name__ == "__main__":
    main()
