"""Domain model.

Three records carry the whole system:

``Tracker``  a recurring question ("new arXiv papers on agent evaluation")
``Item``     one result seen for a tracker, deduplicated by ``external_id``
``Run``      one poll of a tracker, kept so runs can be audited after the fact
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")


def valid_tracker_name(name: str) -> bool:
    """Tracker names double as CLI arguments and file stems, so keep them tame."""
    return bool(_NAME_RE.match(name))


@dataclass(frozen=True, slots=True)
class Item:
    """A single result from a source, normalized across adapters."""

    external_id: str
    title: str
    url: str
    summary: str = ""
    published_at: datetime | None = None
    authors: tuple[str, ...] = ()
    score: float = 0.0

    def with_score(self, score: float) -> Item:
        return Item(
            external_id=self.external_id,
            title=self.title,
            url=self.url,
            summary=self.summary,
            published_at=self.published_at,
            authors=self.authors,
            score=score,
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "external_id": self.external_id,
            "title": self.title,
            "url": self.url,
            "summary": self.summary,
            "published_at": self.published_at.isoformat() if self.published_at else None,
            "authors": list(self.authors),
            "score": round(self.score, 4),
        }


@dataclass(frozen=True, slots=True)
class Tracker:
    """A recurring query against one source."""

    name: str
    source_type: str
    config: dict[str, Any] = field(default_factory=dict)
    include: tuple[str, ...] = ()
    exclude: tuple[str, ...] = ()
    min_score: float = 0.0
    enabled: bool = True
    id: int | None = None
    created_at: datetime | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "source_type": self.source_type,
            "config": self.config,
            "include": list(self.include),
            "exclude": list(self.exclude),
            "min_score": self.min_score,
            "enabled": self.enabled,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


@dataclass(frozen=True, slots=True)
class Run:
    """The outcome of one poll, successful or not."""

    tracker: str
    started_at: datetime
    status: str
    fetched: int = 0
    new_items: int = 0
    error: str | None = None
    finished_at: datetime | None = None
    id: int | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "tracker": self.tracker,
            "started_at": self.started_at.isoformat(),
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
            "status": self.status,
            "fetched": self.fetched,
            "new_items": self.new_items,
            "error": self.error,
        }
