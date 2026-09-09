"""Polling pipeline: fetch, score, filter, persist.

The scoring rule is deliberately simple and deterministic. An agent reading a
digest should be able to explain why an item is on the list without guessing,
and the same inputs must always produce the same digest in CI.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from awt.errors import AwtError
from awt.http import Fetcher, UrllibFetcher
from awt.models import Item, Tracker
from awt.sources import get_adapter
from awt.store import Store

DEFAULT_LIMIT = 25
TITLE_BONUS = 0.25


def _terms(raw: Sequence[str]) -> list[str]:
    return [term.lower().strip() for term in raw if term and term.strip()]


def _contains(haystack: str, term: str) -> bool:
    """Word-boundary match so "ai" does not fire on "chain"."""
    return re.search(rf"(?<!\w){re.escape(term)}(?!\w)", haystack) is not None


def score_item(item: Item, include: Sequence[str], exclude: Sequence[str]) -> float:
    """Score an item in ``[0, 1]``; ``-1`` means an exclude term vetoed it.

    With no include terms every non-vetoed item scores 1.0, so a tracker
    without keywords behaves as a plain feed.
    """
    title = item.title.lower()
    body = f"{title} {item.summary.lower()}"

    for term in _terms(exclude):
        if _contains(body, term):
            return -1.0

    include_terms = _terms(include)
    if not include_terms:
        return 1.0

    hits = [term for term in include_terms if _contains(body, term)]
    if not hits:
        return 0.0

    base = len(hits) / len(include_terms)
    bonus = TITLE_BONUS if any(_contains(title, term) for term in hits) else 0.0
    return min(1.0, base + bonus)


def select(tracker: Tracker, items: Sequence[Item]) -> list[Item]:
    """Score every item, drop the ones the tracker filters out, sort best first."""
    kept: list[Item] = []
    for item in items:
        score = score_item(item, tracker.include, tracker.exclude)
        if score < 0:
            continue
        if tracker.include and score <= 0:
            continue
        if score < tracker.min_score:
            continue
        kept.append(item.with_score(score))
    kept.sort(key=lambda i: (-i.score, -(i.published_at.timestamp() if i.published_at else 0.0)))
    return kept


@dataclass(slots=True)
class PollResult:
    """What one poll of one tracker did -- the unit both the CLI and MCP report."""

    tracker: str
    status: str = "ok"
    fetched: int = 0
    kept: int = 0
    new_items: list[Item] = field(default_factory=list)
    error: str | None = None
    hint: str | None = None

    @property
    def new_count(self) -> int:
        return len(self.new_items)

    def as_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "tracker": self.tracker,
            "status": self.status,
            "fetched": self.fetched,
            "kept": self.kept,
            "new_count": self.new_count,
            "new_items": [item.as_dict() for item in self.new_items],
        }
        if self.error:
            payload["error"] = self.error
        if self.hint:
            payload["hint"] = self.hint
        return payload


def poll_tracker(
    store: Store,
    tracker: Tracker,
    *,
    fetch: Fetcher | None = None,
    limit: int = DEFAULT_LIMIT,
) -> PollResult:
    """Poll one tracker and persist what is new.

    Source failures are recorded as failed runs rather than raised: one broken
    feed must not abort a scheduled sweep over twenty healthy ones.
    """
    fetcher = fetch or UrllibFetcher()
    adapter = get_adapter(tracker.source_type)
    run_id = store.start_run(tracker)

    try:
        fetched = adapter.fetch(dict(tracker.config), limit=limit, fetch=fetcher)
    except AwtError as exc:
        store.finish_run(run_id, status="error", error=exc.message)
        return PollResult(tracker=tracker.name, status="error", error=exc.message, hint=exc.hint)
    except Exception as exc:  # noqa: BLE001 - a bad adapter must not kill the sweep
        message = f"{type(exc).__name__}: {exc}"
        store.finish_run(run_id, status="error", error=message)
        return PollResult(
            tracker=tracker.name,
            status="error",
            error=message,
            hint="This is an adapter bug; re-run with `awt poll --verbose` for the traceback.",
        )

    kept = select(tracker, fetched)
    new_items = store.record_items(tracker, kept)
    store.finish_run(run_id, status="ok", fetched=len(fetched), new_items=len(new_items))
    return PollResult(
        tracker=tracker.name,
        status="ok",
        fetched=len(fetched),
        kept=len(kept),
        new_items=new_items,
    )


def poll_all(
    store: Store,
    *,
    fetch: Fetcher | None = None,
    limit: int = DEFAULT_LIMIT,
    names: Sequence[str] | None = None,
) -> list[PollResult]:
    """Poll every enabled tracker, or just the named ones."""
    if names:
        trackers = [store.get_tracker(name) for name in names]
    else:
        trackers = store.list_trackers(enabled_only=True)
    fetcher = fetch or UrllibFetcher()
    return [poll_tracker(store, tracker, fetch=fetcher, limit=limit) for tracker in trackers]
