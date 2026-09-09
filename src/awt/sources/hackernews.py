"""Hacker News adapter via the Algolia search API."""

from __future__ import annotations

import json
from typing import Any
from urllib.parse import quote_plus

from awt.errors import SourceError
from awt.http import Fetcher
from awt.models import Item
from awt.sources.base import SourceAdapter, clean_text, parse_datetime, require_str

API = "https://hn.algolia.com/api/v1/search_by_date"


class HackerNewsSource(SourceAdapter):
    name = "hackernews"
    summary = "Hacker News stories matching a keyword query, newest first."
    config_schema = {
        "query": 'required. Free-text query, e.g. "model context protocol"',
        "min_points": "optional int, default 0. Drops stories below this score.",
    }

    def validate(self, config: dict[str, Any]) -> dict[str, Any]:
        query = require_str(config, "query", example="model context protocol")
        try:
            min_points = int(config.get("min_points", 0) or 0)
        except (TypeError, ValueError) as exc:
            raise SourceError(
                f"min_points must be an integer, got {config.get('min_points')!r}.",
                hint='Example: {"query": "agents", "min_points": 50}',
            ) from exc
        return {"query": query, "min_points": max(min_points, 0)}

    def fetch(self, config: dict[str, Any], *, limit: int, fetch: Fetcher) -> list[Item]:
        cfg = self.validate(config)
        url = f"{API}?query={quote_plus(cfg['query'])}&tags=story&hitsPerPage={limit}"
        return parse_hits(fetch(url).text, min_points=cfg["min_points"])


def parse_hits(payload: str, *, min_points: int = 0) -> list[Item]:
    try:
        data = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise SourceError(
            f"Hacker News search returned a non-JSON response: {exc}",
            hint="The Algolia API throttles bursts; retry after a short pause.",
        ) from exc

    items: list[Item] = []
    for hit in data.get("hits", []):
        object_id = str(hit.get("objectID") or "")
        if not object_id:
            continue
        points = int(hit.get("points") or 0)
        if points < min_points:
            continue
        discussion = f"https://news.ycombinator.com/item?id={object_id}"
        items.append(
            Item(
                external_id=object_id,
                title=clean_text(hit.get("title") or hit.get("story_title"), limit=300),
                url=str(hit.get("url") or discussion),
                summary=clean_text(hit.get("story_text") or "")
                or f"{points} points · {discussion}",
                published_at=parse_datetime(hit.get("created_at")),
                authors=(str(hit.get("author") or ""),) if hit.get("author") else (),
            )
        )
    return items
