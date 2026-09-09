"""Source adapter contract plus the parsing helpers every adapter shares."""

from __future__ import annotations

import html
import re
from abc import ABC, abstractmethod
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any, ClassVar

from awt.errors import ConfigError
from awt.http import Fetcher
from awt.models import Item

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")
SUMMARY_LIMIT = 600


class SourceAdapter(ABC):
    """Turns a tracker config into a list of normalized :class:`Item` results.

    Adapters are stateless. They receive a ``Fetcher`` so the caller decides
    whether a call hits the network, a cache, or a test fixture.
    """

    name: ClassVar[str]
    summary: ClassVar[str]
    config_schema: ClassVar[dict[str, str]]

    @abstractmethod
    def validate(self, config: dict[str, Any]) -> dict[str, Any]:
        """Return a normalized config or raise :class:`ConfigError`."""

    @abstractmethod
    def fetch(self, config: dict[str, Any], *, limit: int, fetch: Fetcher) -> list[Item]:
        """Fetch at most ``limit`` items, newest first."""

    def describe(self) -> dict[str, Any]:
        return {"source_type": self.name, "summary": self.summary, "config": self.config_schema}


def require_str(config: dict[str, Any], key: str, *, example: str) -> str:
    value = config.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(
            f"Config field {key!r} is required and must be a non-empty string.",
            hint=f'Example: {{"{key}": "{example}"}}',
        )
    return value.strip()


def clean_text(raw: str | None, *, limit: int = SUMMARY_LIMIT) -> str:
    """Strip markup and collapse whitespace so summaries stay cheap to read."""
    if not raw:
        return ""
    text = _WS_RE.sub(" ", html.unescape(_TAG_RE.sub(" ", raw))).strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def parse_datetime(raw: str | int | float | None) -> datetime | None:
    """Best-effort date parsing across ISO-8601, RFC-822 and unix timestamps."""
    if raw is None or raw == "":
        return None
    if isinstance(raw, (int, float)):
        return datetime.fromtimestamp(float(raw), tz=UTC)

    text = raw.strip()
    iso = text.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(iso)
    except ValueError:
        try:
            parsed = parsedate_to_datetime(text)
        except (TypeError, ValueError):
            return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)
