"""Wiring shared by both MCP servers."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any, Literal

from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations

from awt.tools import DEFAULT_DB, Toolkit, ToolResponse, safe_call

ResponseFormat = Literal["markdown", "json"]
DB_ENV_VAR = "AWT_DB"


def database_path() -> Path:
    """Resolve the database location, honouring ``AWT_DB``.

    Both servers and the CLI read the same variable so an agent, a scheduled
    workflow, and a developer at a terminal all act on one database.
    """
    return Path(os.environ.get(DB_ENV_VAR) or DEFAULT_DB)


def build_toolkit() -> Toolkit:
    return Toolkit(database_path())


def respond(response: ToolResponse, response_format: str) -> str:
    return response.render(response_format)


def run_tool(fn: Any, *args: Any, response_format: str = "markdown", **kwargs: Any) -> str:
    """Call a toolkit method and render it, turning known errors into guidance."""
    return safe_call(fn, *args, **kwargs).render(response_format)


def read_only(*, idempotent: bool = True, open_world: bool = True) -> ToolAnnotations:
    return ToolAnnotations(
        read_only_hint=True,
        destructive_hint=False,
        idempotent_hint=idempotent,
        open_world_hint=open_world,
    )


def mutating(
    *, destructive: bool = False, idempotent: bool = False, open_world: bool = False
) -> ToolAnnotations:
    return ToolAnnotations(
        read_only_hint=False,
        destructive_hint=destructive,
        idempotent_hint=idempotent,
        open_world_hint=open_world,
    )


def serve(server: MCPServer) -> None:
    """Run over stdio. Diagnostics go to stderr; stdout carries the protocol."""
    print(f"[{server.name}] database: {database_path()}", file=sys.stderr)
    server.run("stdio")
