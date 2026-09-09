"""The toolkit both MCP servers expose.

Tool bodies live here, not in the server modules, for two reasons: the logic
stays testable without an MCP client, and the CLI can call exactly what an
agent calls. Server modules are thin adapters over this file.

Every method returns a :class:`ToolResponse` carrying both a structured payload
and a Markdown rendering, so a caller picks the representation it wants without
the formatting being written twice.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from awt.digest import build_digest, render_markdown
from awt.errors import AwtError, ConfigError
from awt.http import Fetcher, UrllibFetcher
from awt.models import Item, Tracker
from awt.pipeline import DEFAULT_LIMIT, poll_all, select
from awt.sources import describe_sources, get_adapter
from awt.store import Store

DEFAULT_DB = Path(".awt/toolchain.db")
MAX_LIMIT = 100


@dataclass(frozen=True, slots=True)
class ToolResponse:
    data: dict[str, Any]
    markdown: str

    def render(self, response_format: str = "markdown") -> str:
        if response_format == "json":
            return json.dumps(self.data, indent=2, sort_keys=False)
        if response_format != "markdown":
            raise ConfigError(
                f"Unknown response_format {response_format!r}.",
                hint='Use "markdown" (default) or "json".',
            )
        return self.markdown


def _clamp(limit: int, *, default: int = DEFAULT_LIMIT) -> int:
    if limit <= 0:
        return default
    return min(limit, MAX_LIMIT)


def _as_terms(value: Sequence[str] | None) -> tuple[str, ...]:
    return tuple(str(term).strip() for term in (value or []) if str(term).strip())


def _item_lines(items: Sequence[Item]) -> list[str]:
    return [
        f"- [{item.title or item.url}]({item.url})"
        + (f" — {item.published_at:%Y-%m-%d}" if item.published_at else "")
        + (f" · score {item.score:.2f}" if item.score and item.score < 1.0 else "")
        for item in items
    ]


class Toolkit:
    """Stateless facade over the store; a connection is opened per call.

    Per-call connections keep the toolkit safe to use from the SDK's worker
    threads and let a CLI invocation and a running server share one database.
    """

    def __init__(self, db_path: str | Path = DEFAULT_DB, *, fetcher: Fetcher | None = None) -> None:
        self.db_path = Path(db_path)
        self.fetcher: Fetcher = fetcher or UrllibFetcher()

    def _store(self) -> Store:
        return Store(self.db_path)

    # ------------------------------------------------------------- research

    def list_source_types(self) -> ToolResponse:
        """Describe every source adapter and the config fields it accepts."""
        sources = describe_sources()
        lines = ["# Available source types", ""]
        for source in sources:
            lines += [f"## `{source['source_type']}`", "", source["summary"], ""]
            lines += [f"- `{key}` — {desc}" for key, desc in source["config"].items()]
            lines.append("")
        return ToolResponse({"source_types": sources}, "\n".join(lines).rstrip() + "\n")

    def search(
        self,
        source_type: str,
        config: dict[str, Any],
        *,
        limit: int = DEFAULT_LIMIT,
        include: Sequence[str] | None = None,
        exclude: Sequence[str] | None = None,
    ) -> ToolResponse:
        """Query a source once without persisting anything.

        This is the exploration step: try a query, look at what comes back, then
        turn the query that worked into a tracker.
        """
        adapter = get_adapter(source_type)
        count = _clamp(limit)
        items = adapter.fetch(dict(config or {}), limit=count, fetch=self.fetcher)

        probe = Tracker(
            name="probe",
            source_type=source_type,
            config=dict(config or {}),
            include=_as_terms(include),
            exclude=_as_terms(exclude),
        )
        kept = select(probe, items)[:count]

        header = f"# `{source_type}` search — {len(kept)} of {len(items)} fetched"
        markdown = "\n".join([header, "", *(_item_lines(kept) or ["_No matching results._"]), ""])
        return ToolResponse(
            {
                "source_type": source_type,
                "config": dict(config or {}),
                "fetched": len(items),
                "count": len(kept),
                "items": [item.as_dict() for item in kept],
            },
            markdown,
        )

    # -------------------------------------------------------------- trackers

    def create_tracker(
        self,
        name: str,
        source_type: str,
        config: dict[str, Any],
        *,
        include: Sequence[str] | None = None,
        exclude: Sequence[str] | None = None,
        min_score: float = 0.0,
    ) -> ToolResponse:
        """Register a recurring query. Validates the config before storing it."""
        tracker = Tracker(
            name=name.strip().lower(),
            source_type=source_type,
            config=dict(config or {}),
            include=_as_terms(include),
            exclude=_as_terms(exclude),
            min_score=float(min_score),
        )
        with self._store() as store:
            created = store.add_tracker(tracker)
        return ToolResponse(
            {"created": created.as_dict()},
            f"Created tracker **{created.name}** (`{created.source_type}`). "
            f"Run `tracker_poll` to collect its first items.\n",
        )

    def list_trackers(self, *, enabled_only: bool = False) -> ToolResponse:
        """List configured trackers with their unreviewed backlog."""
        with self._store() as store:
            trackers = store.list_trackers(enabled_only=enabled_only)
            backlog = {
                tracker.name: store.items(tracker.name, unreviewed_only=True, limit=0)[1]
                for tracker in trackers
            }

        if not trackers:
            return ToolResponse(
                {"trackers": [], "count": 0},
                "No trackers configured yet. Use `tracker_create` to add one.\n",
            )

        lines = [
            "| tracker | source | unreviewed | enabled | filters |",
            "| --- | --- | --: | --- | --- |",
        ]
        for tracker in trackers:
            filters = ", ".join(f"+{term}" for term in tracker.include)
            filters += ("; " if filters and tracker.exclude else "") + ", ".join(
                f"-{term}" for term in tracker.exclude
            )
            lines.append(
                f"| {tracker.name} | `{tracker.source_type}` | {backlog[tracker.name]} | "
                f"{'yes' if tracker.enabled else 'no'} | {filters or '—'} |"
            )
        payload = [
            {**tracker.as_dict(), "unreviewed": backlog[tracker.name]} for tracker in trackers
        ]
        return ToolResponse({"trackers": payload, "count": len(payload)}, "\n".join(lines) + "\n")

    def delete_tracker(self, name: str) -> ToolResponse:
        """Delete a tracker and every item recorded for it."""
        with self._store() as store:
            store.delete_tracker(name)
        return ToolResponse({"deleted": name}, f"Deleted tracker **{name}** and its items.\n")

    def set_enabled(self, name: str, enabled: bool) -> ToolResponse:
        """Enable or disable a tracker without losing its history."""
        with self._store() as store:
            tracker = store.set_enabled(name, enabled)
        state = "enabled" if tracker.enabled else "disabled"
        return ToolResponse({"tracker": tracker.as_dict()}, f"Tracker **{name}** is now {state}.\n")

    def poll(
        self,
        names: Sequence[str] | None = None,
        *,
        limit: int = DEFAULT_LIMIT,
    ) -> ToolResponse:
        """Fetch each tracker's source and record what has not been seen before.

        A failing source is reported in the result rather than raised, so one
        broken feed never aborts a sweep.
        """
        with self._store() as store:
            results = poll_all(
                store, fetch=self.fetcher, limit=_clamp(limit), names=list(names) if names else None
            )

        total_new = sum(result.new_count for result in results)
        failures = [result for result in results if result.status == "error"]

        lines = [f"# Poll — {total_new} new item(s) across {len(results)} tracker(s)", ""]
        for result in results:
            if result.status == "error":
                lines += [f"## {result.tracker} — **failed**", "", f"{result.error}"]
                if result.hint:
                    lines.append(f"_{result.hint}_")
                lines.append("")
                continue
            lines += [
                f"## {result.tracker} — {result.new_count} new (of {result.fetched} fetched)",
                "",
            ]
            lines += _item_lines(result.new_items) or ["_Nothing new._"]
            lines.append("")

        return ToolResponse(
            {
                "polled": len(results),
                "new_items": total_new,
                "failed": len(failures),
                "results": [result.as_dict() for result in results],
            },
            "\n".join(lines),
        )

    def items(
        self,
        name: str,
        *,
        unreviewed_only: bool = True,
        limit: int = 20,
        offset: int = 0,
    ) -> ToolResponse:
        """Page through a tracker's items, highest score first."""
        count = _clamp(limit, default=20)
        with self._store() as store:
            items, total = store.items(
                name, unreviewed_only=unreviewed_only, limit=count, offset=offset
            )

        has_more = offset + len(items) < total
        header = (
            f"# {name} — showing {len(items)} of {total}"
            f"{' unreviewed' if unreviewed_only else ''} item(s)"
        )
        lines = [header, ""]
        for item in items:
            lines.append(f"- [{item.title or item.url}]({item.url}) `{item.external_id}`")
            if item.summary:
                lines.append(f"  - {item.summary}")
        if not items:
            lines.append("_Nothing to show._")
        if has_more:
            lines += ["", f"_More available: pass offset={offset + len(items)}._"]

        return ToolResponse(
            {
                "tracker": name,
                "total": total,
                "count": len(items),
                "offset": offset,
                "has_more": has_more,
                "next_offset": offset + len(items) if has_more else None,
                "items": [item.as_dict() for item in items],
            },
            "\n".join(lines) + "\n",
        )

    def mark_reviewed(self, name: str, external_ids: Sequence[str] | None = None) -> ToolResponse:
        """Clear items from the backlog. Omit ``external_ids`` to clear all of them."""
        ids = list(external_ids) if external_ids is not None else None
        with self._store() as store:
            updated = store.mark_reviewed(name, ids)
        scope = "all unreviewed items" if ids is None else f"{len(ids)} requested item(s)"
        return ToolResponse(
            {"tracker": name, "marked_reviewed": updated},
            f"Marked {updated} item(s) reviewed on **{name}** ({scope}).\n",
        )

    def runs(self, name: str | None = None, *, limit: int = 20) -> ToolResponse:
        """Show recent poll history, including failures. Useful for debugging a quiet tracker."""
        with self._store() as store:
            history = store.runs(name, limit=_clamp(limit, default=20))

        lines = [
            "| tracker | started | status | fetched | new | error |",
            "| --- | --- | --- | --: | --: | --- |",
        ]
        for run in history:
            lines.append(
                f"| {run.tracker} | {run.started_at:%Y-%m-%d %H:%M} | {run.status} | "
                f"{run.fetched} | {run.new_items} | {run.error or '—'} |"
            )
        markdown = "\n".join(lines) + "\n" if history else "No runs recorded yet.\n"
        return ToolResponse(
            {"runs": [run.as_dict() for run in history], "count": len(history)}, markdown
        )

    def digest(
        self,
        names: Sequence[str] | None = None,
        *,
        title: str = "Research digest",
        unreviewed_only: bool = True,
        per_tracker: int = 10,
        summaries: bool = True,
    ) -> ToolResponse:
        """Render the Markdown digest that scheduled workflows publish."""
        with self._store() as store:
            digest = build_digest(
                store,
                title=title,
                names=list(names) if names else None,
                unreviewed_only=unreviewed_only,
                per_tracker=_clamp(per_tracker, default=10),
            )
        return ToolResponse(digest.as_dict(), render_markdown(digest, summaries=summaries))

    def stats(self) -> ToolResponse:
        """Counts across the whole database -- a cheap health check for a workflow."""
        with self._store() as store:
            counts = store.stats()
        lines = ["| metric | value |", "| --- | --: |"]
        lines += [f"| {key.replace('_', ' ')} | {value} |" for key, value in counts.items()]
        return ToolResponse(
            {"stats": counts, "database": str(self.db_path)}, "\n".join(lines) + "\n"
        )


def safe_call(fn: Callable[..., ToolResponse], *args: Any, **kwargs: Any) -> ToolResponse:
    """Run a toolkit method, converting toolchain errors into an agent-readable response.

    MCP tools should report failure inside the result rather than as a protocol
    error, and the message should say what to do next.
    """
    try:
        return fn(*args, **kwargs)
    except AwtError as exc:
        return ToolResponse(
            exc.as_dict(), f"**{type(exc).__name__}:** {exc.message}\n\n{exc.hint}\n"
        )


__all__ = ["DEFAULT_DB", "MAX_LIMIT", "ToolResponse", "Toolkit", "safe_call"]
