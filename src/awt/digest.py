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
    duplicates: int = 0
    """Items dropped because another tracker in this digest reported them better."""

    @property
    def truncated(self) -> int:
        """Items the per-tracker cap left out -- deduplication is not truncation."""
        return max(self.total - len(self.items) - self.duplicates, 0)

    def as_dict(self) -> dict[str, Any]:
        return {
            "tracker": self.tracker,
            "source_type": self.source_type,
            "total": self.total,
            "shown": len(self.items),
            "duplicates": self.duplicates,
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
    dedupe: bool = True,
    generated_at: datetime | None = None,
) -> Digest:
    """Assemble the digest.

    With ``dedupe`` (the default) an item matched by several trackers is listed
    once, under the tracker whose filters scored it highest. The item stays
    recorded against every tracker that matched it -- each answers its own
    question, and review state is per tracker -- but a reader should not meet
    the same paper three times in one digest.
    """
    trackers = [store.get_tracker(name) for name in names] if names else store.list_trackers()
    sections: list[Section] = []
    for tracker in trackers:
        items, total = store.items(tracker.name, unreviewed_only=unreviewed_only, limit=per_tracker)
        if not items and not include_empty:
            continue
        sections.append(
            Section(tracker=tracker.name, source_type=tracker.source_type, items=items, total=total)
        )

    if dedupe:
        _dedupe(sections)
        if not include_empty:
            sections = [section for section in sections if section.items]

    return Digest(title=title, generated_at=generated_at or utcnow(), sections=sections)


def _key(item: Item) -> str:
    """Two records are the same thing if they point at the same place."""
    return item.url or item.external_id


def _dedupe(sections: list[Section]) -> None:
    """Keep each item only in the section that scored it highest.

    Ties go to the earlier section, so the result is stable across runs.
    """
    best: dict[str, tuple[int, float]] = {}
    for index, section in enumerate(sections):
        for item in section.items:
            key = _key(item)
            if key not in best or item.score > best[key][1]:
                best[key] = (index, item.score)

    for index, section in enumerate(sections):
        kept = [item for item in section.items if best[_key(item)][0] == index]
        section.duplicates = len(section.items) - len(kept)
        section.items = kept


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
        if not section.items:
            lines += ["_Nothing new._", ""]
            continue
        for item in section.items:
            lines.append(f"- [{item.title or item.url}]({item.url}){_meta(item)}")
            if summaries and item.summary:
                lines.append(f"  - {item.summary}")
        if section.truncated:
            lines.append(f"- _… and {section.truncated} more_")
        if section.duplicates:
            plural = "" if section.duplicates == 1 else "s"
            lines.append(f"- _{section.duplicates} item{plural} listed under another tracker_")
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
