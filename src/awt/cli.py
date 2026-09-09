"""``awt`` command line interface.

The CLI and the MCP servers are two front ends over one :class:`Toolkit`, so a
scheduled GitHub Actions job and an agent in Claude Code do exactly the same
work. Anything an agent can do here, CI can do unattended -- and vice versa.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Sequence
from pathlib import Path

from awt import __version__
from awt.config import load_trackers, sync
from awt.errors import AwtError
from awt.pipeline import DEFAULT_LIMIT
from awt.store import Store
from awt.tools import DEFAULT_DB, Toolkit, ToolResponse

DB_ENV_VAR = "AWT_DB"


def _db_path(args: argparse.Namespace) -> Path:
    return Path(args.db or os.environ.get(DB_ENV_VAR) or DEFAULT_DB)


def _json_arg(raw: str | None) -> dict[str, object]:
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise AwtError(
            f"--config is not valid JSON: {exc}",
            hint="""Quote it for the shell, e.g. --config '{"repo": "owner/name"}'.""",
        ) from exc
    if not isinstance(parsed, dict):
        raise AwtError(
            "--config must be a JSON object.", hint="""Example: '{"query": "cat:cs.AI"}'."""
        )
    return parsed


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="awt",
        description="Agentic Workflow Toolchain — recurring research and release tracking.",
    )
    parser.add_argument("--version", action="version", version=f"awt {__version__}")
    parser.add_argument(
        "--db", default=None, help=f"Database path (default: ${DB_ENV_VAR} or {DEFAULT_DB})."
    )
    parser.add_argument(
        "--format", choices=("markdown", "json"), default="markdown", help="Output format."
    )

    # The same two flags are accepted after the subcommand as well, so both
    # `awt --format json poll` and `awt poll --format json` work. SUPPRESS keeps
    # the subparser from overwriting a value given before the subcommand.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--db", default=argparse.SUPPRESS, help=argparse.SUPPRESS)
    common.add_argument(
        "--format", choices=("markdown", "json"), default=argparse.SUPPRESS, help=argparse.SUPPRESS
    )

    sub = parser.add_subparsers(dest="command", required=True)

    def command(name: str, help_text: str) -> argparse.ArgumentParser:
        return sub.add_parser(name, help=help_text, parents=[common])

    command("sources", "List available source adapters and their config fields.")

    search = command("search", "Run a one-off query without saving anything.")
    search.add_argument("source_type")
    search.add_argument("--config", required=True, help="Adapter config as JSON.")
    search.add_argument("--limit", type=int, default=DEFAULT_LIMIT)
    search.add_argument("--include", nargs="*", default=None)
    search.add_argument("--exclude", nargs="*", default=None)

    add = command("add", "Create a tracker.")
    add.add_argument("name")
    add.add_argument("source_type")
    add.add_argument("--config", required=True, help="Adapter config as JSON.")
    add.add_argument("--include", nargs="*", default=None)
    add.add_argument("--exclude", nargs="*", default=None)
    add.add_argument("--min-score", type=float, default=0.0, dest="min_score")

    listing = command("list", "List trackers.")
    listing.add_argument("--enabled-only", action="store_true", dest="enabled_only")

    remove = command("rm", "Delete a tracker and its items.")
    remove.add_argument("name")

    enable = command("enable", "Resume polling a tracker.")
    enable.add_argument("name")
    disable = command("disable", "Pause a tracker, keeping its history.")
    disable.add_argument("name")

    poll = command("poll", "Fetch sources and record new items.")
    poll.add_argument("names", nargs="*", help="Trackers to poll; default is all enabled.")
    poll.add_argument("--limit", type=int, default=DEFAULT_LIMIT)

    items = command("items", "Show items recorded for a tracker.")
    items.add_argument("name")
    items.add_argument("--all", action="store_true", help="Include already-reviewed items.")
    items.add_argument("--limit", type=int, default=20)
    items.add_argument("--offset", type=int, default=0)

    review = command("review", "Mark items reviewed.")
    review.add_argument("name")
    review.add_argument(
        "--ids", nargs="*", default=None, help="Item ids; default is all unreviewed."
    )

    runs = command("runs", "Show poll history, including failures.")
    runs.add_argument("name", nargs="?", default=None)
    runs.add_argument("--limit", type=int, default=20)

    digest = command("digest", "Render a Markdown digest of what is new.")
    digest.add_argument("names", nargs="*")
    digest.add_argument("--title", default="Research digest")
    digest.add_argument("--all", action="store_true", help="Include already-reviewed items.")
    digest.add_argument("--per-tracker", type=int, default=10, dest="per_tracker")
    digest.add_argument("--no-summaries", action="store_true", dest="no_summaries")
    digest.add_argument("--output", "-o", default=None, help="Write to a file instead of stdout.")

    sync_cmd = command("sync", "Reconcile the database with a trackers TOML file.")
    sync_cmd.add_argument("path", nargs="?", default="config/trackers.toml")
    sync_cmd.add_argument(
        "--prune", action="store_true", help="Delete trackers absent from the file."
    )

    command("stats", "Show database counts.")

    serve = command("serve", "Run an MCP server over stdio.")
    serve.add_argument("server", choices=("research", "tracker"))

    return parser


def _run(args: argparse.Namespace) -> int:
    if args.command == "serve":
        # Imported lazily so the rest of the CLI works without the MCP SDK present.
        if args.server == "research":
            from awt.mcp.research_server import main as serve_main
        else:
            from awt.mcp.tracker_server import main as serve_main
        serve_main()
        return 0

    db = _db_path(args)
    toolkit = Toolkit(db)

    if args.command == "sync":
        trackers = load_trackers(args.path)
        with Store(db) as store:
            report = sync(store, trackers, prune=args.prune)
        if args.format == "json":
            print(json.dumps(report.as_dict(), indent=2))
        else:
            print(f"Synced {args.path}: {report.summary()}")
        return 0

    response = _dispatch(toolkit, args)

    if args.command == "digest" and args.output:
        path = Path(args.output)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(response.render(args.format), encoding="utf-8")
        print(f"Wrote {path} ({response.data['item_count']} item(s)).")
        return 0

    print(response.render(args.format))
    return 1 if _has_failure(args, response) else 0


def _dispatch(toolkit: Toolkit, args: argparse.Namespace) -> ToolResponse:
    match args.command:
        case "sources":
            return toolkit.list_source_types()
        case "search":
            return toolkit.search(
                args.source_type,
                _json_arg(args.config),
                limit=args.limit,
                include=args.include,
                exclude=args.exclude,
            )
        case "add":
            return toolkit.create_tracker(
                args.name,
                args.source_type,
                _json_arg(args.config),
                include=args.include,
                exclude=args.exclude,
                min_score=args.min_score,
            )
        case "list":
            return toolkit.list_trackers(enabled_only=args.enabled_only)
        case "rm":
            return toolkit.delete_tracker(args.name)
        case "enable":
            return toolkit.set_enabled(args.name, True)
        case "disable":
            return toolkit.set_enabled(args.name, False)
        case "poll":
            return toolkit.poll(args.names or None, limit=args.limit)
        case "items":
            return toolkit.items(
                args.name, unreviewed_only=not args.all, limit=args.limit, offset=args.offset
            )
        case "review":
            return toolkit.mark_reviewed(args.name, args.ids)
        case "runs":
            return toolkit.runs(args.name, limit=args.limit)
        case "digest":
            return toolkit.digest(
                args.names or None,
                title=args.title,
                unreviewed_only=not args.all,
                per_tracker=args.per_tracker,
                summaries=not args.no_summaries,
            )
        case "stats":
            return toolkit.stats()
        case _:  # pragma: no cover - argparse rejects unknown commands first
            raise AwtError(f"Unhandled command {args.command!r}.")


def _has_failure(args: argparse.Namespace, response: ToolResponse) -> bool:
    """`awt poll` exits non-zero when a source failed, so CI notices a broken feed."""
    return args.command == "poll" and bool(response.data.get("failed"))


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return _run(args)
    except AwtError as exc:
        print(f"error: {exc.message}", file=sys.stderr)
        if exc.hint:
            print(f"hint: {exc.hint}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:  # pragma: no cover
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
