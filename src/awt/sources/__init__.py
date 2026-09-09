"""Source adapter registry.

Adding a source means writing one adapter and registering it here; the CLI,
both MCP servers, and the scheduled workflows pick it up with no other change.
"""

from __future__ import annotations

from typing import Any

from awt.errors import ConfigError
from awt.sources.arxiv import ArxivSource
from awt.sources.base import SourceAdapter
from awt.sources.github_releases import GitHubReleasesSource
from awt.sources.hackernews import HackerNewsSource
from awt.sources.rss import RssSource

_ADAPTERS: dict[str, SourceAdapter] = {
    adapter.name: adapter
    for adapter in (ArxivSource(), GitHubReleasesSource(), HackerNewsSource(), RssSource())
}


def get_adapter(source_type: str) -> SourceAdapter:
    try:
        return _ADAPTERS[source_type]
    except KeyError:
        raise ConfigError(
            f"Unknown source type {source_type!r}.",
            hint=f"Available source types: {', '.join(source_types())}.",
        ) from None


def source_types() -> list[str]:
    return sorted(_ADAPTERS)


def describe_sources() -> list[dict[str, Any]]:
    return [_ADAPTERS[name].describe() for name in source_types()]


__all__ = [
    "ArxivSource",
    "GitHubReleasesSource",
    "HackerNewsSource",
    "RssSource",
    "SourceAdapter",
    "describe_sources",
    "get_adapter",
    "source_types",
]
