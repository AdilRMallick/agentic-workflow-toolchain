"""Digest rendering.

A digest is the artifact a recurring workflow produces: the new items for each
tracker, newest and highest-scoring first, as Markdown that reads well in a
GitHub issue, a committed file, or an agent's context window.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from awt.models import Item
from awt.store import Store, utcnow

DEFAULT_PER_TRACKER = 10


@dataclass(slots=True)
class Section:
    tracker: str
    source_type: str
    items: list[Item] = field(default_factory=list)
    total: int = 0

    @property
    def truncated(self) -> int:
        return max(self.total - len(self.items), 0)

    def as_dict(self) -> dict[str, Any]:
        return {
            "tracker": self.tracker,
            "source_type": self.source_type,
            "total": self.total,
            "shown": len(self.items),
            "items": [item.as_dict() for item in self.items],
        }


@dataclass(slots=True)
class Digest:
    title: str
    generated_at: datetime
    sections: list[Section] = field(default_factory=list)

    @property
    def item_count(self) -> int:
        return sum(len(section.items) for section in self.sections)

    def as_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "generated_at": self.generated_at.isoformat(),
            "item_count": self.item_count,
            "sections": [section.as_dict() for section in self.sections],
        }


def build_digest(
    store: Store,
    *,
    title: str = "Research digest",
    names: Sequence[str] | None = None,
    unreviewed_only: bool = True,
    per_tracker: int = DEFAULT_PER_TRACKER,
    include_empty: bool = False,
    generated_at: datetime | None = None,
) -> Digest:
    trackers = [store.get_tracker(name) for name in names] if names else store.list_trackers()
    sections: list[Section] = []
    for tracker in trackers:
        items, total = store.items(tracker.name, unreviewed_only=unreviewed_only, limit=per_tracker)
        if not items and not include_empty:
            continue
        sections.append(
            Section(tracker=tracker.name, source_type=tracker.source_type, items=items, total=total)
        )
    return Digest(title=title, generated_at=generated_at or utcnow(), sections=sections)


def render_markdown(digest: Digest, *, summaries: bool = True) -> str:
    stamp = digest.generated_at.strftime("%Y-%m-%d %H:%M UTC")
    lines = [f"# {digest.title}", "", f"_Generated {stamp}_", ""]

    if not digest.sections:
        lines += ["Nothing new since the last review.", ""]
        return "\n".join(lines)

    plural = "item" if digest.item_count == 1 else "items"
    lines += [
        f"**{digest.item_count} new {plural}** across "
        f"{len(digest.sections)} tracker{'' if len(digest.sections) == 1 else 's'}.",
        "",
    ]

    for section in digest.sections:
        lines += [f"## {section.tracker} `{section.source_type}`", ""]
        for item in section.items:
            lines.append(f"- [{item.title or item.url}]({item.url}){_meta(item)}")
            if summaries and item.summary:
                lines.append(f"  - {item.summary}")
        if section.truncated:
            lines.append(f"- _… and {section.truncated} more_")
        lines.append("")

    return "\n".join(lines)


def _meta(item: Item) -> str:
    bits: list[str] = []
    if item.published_at:
        bits.append(item.published_at.strftime("%Y-%m-%d"))
    if item.authors:
        shown = ", ".join(a for a in item.authors[:3] if a)
        if shown:
            bits.append(shown + (" et al." if len(item.authors) > 3 else ""))
    if item.score and item.score < 1.0:
        bits.append(f"score {item.score:.2f}")
    return f" — {' · '.join(bits)}" if bits else ""
