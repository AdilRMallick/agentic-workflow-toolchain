"""arXiv adapter, backed by the public Atom export API."""

from __future__ import annotations

from typing import Any
from urllib.parse import quote_plus
from xml.etree import ElementTree

from awt.errors import ConfigError, SourceError
from awt.http import Fetcher
from awt.models import Item
from awt.sources.base import SourceAdapter, clean_text, parse_datetime, require_str

API = "http://export.arxiv.org/api/query"
NS = {"atom": "http://www.w3.org/2005/Atom"}
_SORT_FIELDS = frozenset({"submittedDate", "lastUpdatedDate", "relevance"})


class ArxivSource(SourceAdapter):
    name = "arxiv"
    summary = "New arXiv preprints matching a search query, newest submission first."
    config_schema = {
        "query": 'required. arXiv search syntax, e.g. "cat:cs.AI AND abs:agent evaluation"',
        "sort_by": "optional. submittedDate (default), lastUpdatedDate, or relevance",
    }

    def validate(self, config: dict[str, Any]) -> dict[str, Any]:
        query = require_str(config, "query", example="cat:cs.AI AND abs:agent")
        sort_by = str(config.get("sort_by") or "submittedDate")
        if sort_by not in _SORT_FIELDS:
            raise ConfigError(
                f"Unknown sort_by {sort_by!r}.",
                hint=f"Use one of: {', '.join(sorted(_SORT_FIELDS))}.",
            )
        return {"query": query, "sort_by": sort_by}

    def fetch(self, config: dict[str, Any], *, limit: int, fetch: Fetcher) -> list[Item]:
        cfg = self.validate(config)
        url = (
            f"{API}?search_query={quote_plus(cfg['query'])}"
            f"&start=0&max_results={limit}"
            f"&sortBy={cfg['sort_by']}&sortOrder=descending"
        )
        return parse_arxiv_feed(fetch(url).text)


def parse_arxiv_feed(xml: str) -> list[Item]:
    try:
        root = ElementTree.fromstring(xml)
    except ElementTree.ParseError as exc:
        raise SourceError(
            f"arXiv returned malformed XML: {exc}",
            hint="arXiv occasionally rate limits with an HTML error page; retry in a minute.",
        ) from exc

    items: list[Item] = []
    for entry in root.findall("atom:entry", NS):
        raw_id = _text(entry, "atom:id")
        if not raw_id:
            continue
        # Drop the version suffix so v1 and v2 of a paper are the same item.
        external_id = raw_id.rsplit("/", 1)[-1].split("v")[0] if "/abs/" in raw_id else raw_id
        items.append(
            Item(
                external_id=external_id,
                title=clean_text(_text(entry, "atom:title"), limit=300),
                url=_link(entry) or raw_id,
                summary=clean_text(_text(entry, "atom:summary")),
                published_at=parse_datetime(_text(entry, "atom:published")),
                authors=tuple(
                    clean_text(_text(author, "atom:name"), limit=120)
                    for author in entry.findall("atom:author", NS)
                ),
            )
        )
    return items


def _text(node: ElementTree.Element, path: str) -> str:
    found = node.find(path, NS)
    return (found.text or "") if found is not None else ""


def _link(entry: ElementTree.Element) -> str:
    for link in entry.findall("atom:link", NS):
        if link.get("rel") in (None, "alternate"):
            return link.get("href", "")
    return ""
