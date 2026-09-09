"""Shared fixtures. Every test runs offline: no test may touch the network."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from awt.http import Response
from awt.models import Item, Tracker
from awt.store import Store
from awt.tools import Toolkit

FIXTURES = Path(__file__).parent / "fixtures"


def fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


class FakeFetcher:
    """A ``Fetcher`` that answers from a canned map and records what was asked.

    Matching is by substring so a test can key on ``"api.github.com"`` without
    reproducing the exact query string the adapter builds.
    """

    def __init__(self, responses: dict[str, str] | None = None, *, status: int = 200) -> None:
        self.responses = responses or {}
        self.status = status
        self.calls: list[tuple[str, dict[str, str]]] = []

    def __call__(self, url: str, headers: dict[str, str] | None = None) -> Response:
        self.calls.append((url, dict(headers or {})))
        for needle, body in self.responses.items():
            if needle in url:
                return Response(url=url, status=self.status, body=body.encode("utf-8"))
        raise AssertionError(f"FakeFetcher has no response registered for {url!r}")


class BoomFetcher:
    """A fetcher that always fails, for exercising error paths."""

    def __init__(self, exc: Exception) -> None:
        self.exc = exc

    def __call__(self, url: str, headers: dict[str, str] | None = None) -> Response:
        raise self.exc


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "toolchain.db"


@pytest.fixture
def store(db_path: Path) -> Iterator[Store]:
    with Store(db_path) as opened:
        yield opened


@pytest.fixture
def fetcher() -> FakeFetcher:
    return FakeFetcher(
        {
            "export.arxiv.org": fixture("arxiv.xml"),
            "api.github.com": fixture("github_releases.json"),
            "hn.algolia.com": fixture("hackernews.json"),
            "acme.example/rss": fixture("rss.xml"),
            "acme.example/atom": fixture("atom.xml"),
        }
    )


@pytest.fixture
def toolkit(db_path: Path, fetcher: FakeFetcher) -> Toolkit:
    return Toolkit(db_path, fetcher=fetcher)


@pytest.fixture
def tracker() -> Tracker:
    return Tracker(
        name="agent-evals",
        source_type="arxiv",
        config={"query": "cat:cs.AI", "sort_by": "submittedDate"},
        include=("agent", "benchmark"),
        exclude=("survey",),
    )


def make_item(external_id: str = "i1", title: str = "Title", **kwargs: object) -> Item:
    defaults: dict[str, object] = {
        "url": f"https://example.com/{external_id}",
        "summary": "",
    }
    defaults.update(kwargs)
    return Item(external_id=external_id, title=title, **defaults)  # type: ignore[arg-type]
