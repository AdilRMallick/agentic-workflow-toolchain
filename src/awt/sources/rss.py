"""Generic RSS 2.0 / Atom adapter for blogs and changelogs."""

from __future__ import annotations

from typing import Any
from xml.etree import ElementTree

from awt.errors import SourceError
from awt.http import Fetcher
from awt.models import Item
from awt.sources.base import SourceAdapter, clean_text, parse_datetime, require_str

ATOM = "{http://www.w3.org/2005/Atom}"


class RssSource(SourceAdapter):
    name = "rss"
    summary = "Entries from any RSS 2.0 or Atom feed, in feed order."
    config_schema = {"url": 'required. Feed URL, e.g. "https://blog.example.com/feed.xml"'}

    def validate(self, config: dict[str, Any]) -> dict[str, Any]:
        return {"url": require_str(config, "url", example="https://blog.example.com/feed.xml")}

    def fetch(self, config: dict[str, Any], *, limit: int, fetch: Fetcher) -> list[Item]:
        cfg = self.validate(config)
        return parse_feed(fetch(cfg["url"]).text)[:limit]


def parse_feed(xml: str) -> list[Item]:
    """Parse RSS or Atom. The two formats differ enough to branch, not enough to split."""
    try:
        root = ElementTree.fromstring(xml)
    except ElementTree.ParseError as exc:
        raise SourceError(
            f"Feed is not well-formed XML: {exc}",
            hint="Confirm the URL serves a feed rather than an HTML page.",
        ) from exc

    entries = root.findall(f".//{ATOM}entry")
    if entries:
        return [_from_atom(entry) for entry in entries]

    items = root.findall(".//item")
    if not items:
        raise SourceError(
            "Feed contained no <item> or <entry> elements.",
            hint="The URL may point at an HTML page; look for a <link rel=alternate> feed URL.",
        )
    return [_from_rss(item) for item in items]


def _from_atom(entry: ElementTree.Element) -> Item:
    link = ""
    for candidate in entry.findall(f"{ATOM}link"):
        if candidate.get("rel") in (None, "alternate"):
            link = candidate.get("href", "")
            break
    title = clean_text(_child(entry, f"{ATOM}title"), limit=300)
    body = _child(entry, f"{ATOM}content") or _child(entry, f"{ATOM}summary")
    return Item(
        external_id=_child(entry, f"{ATOM}id") or link or title,
        title=title,
        url=link,
        summary=clean_text(body),
        published_at=parse_datetime(
            _child(entry, f"{ATOM}published") or _child(entry, f"{ATOM}updated")
        ),
        authors=tuple(
            clean_text(_child(author, f"{ATOM}name"), limit=120)
            for author in entry.findall(f"{ATOM}author")
        ),
    )


def _from_rss(item: ElementTree.Element) -> Item:
    link = _child(item, "link")
    title = clean_text(_child(item, "title"), limit=300)
    return Item(
        external_id=_child(item, "guid") or link or title,
        title=title,
        url=link,
        summary=clean_text(_child(item, "description")),
        published_at=parse_datetime(_child(item, "pubDate")),
        authors=tuple(a for a in [clean_text(_child(item, "author"), limit=120)] if a),
    )


def _child(node: ElementTree.Element, tag: str) -> str:
    found = node.find(tag)
    return (found.text or "").strip() if found is not None else ""
