"""Digest assembly and Markdown rendering."""

from __future__ import annotations

from datetime import UTC, datetime

from tests.conftest import make_item

from awt.digest import build_digest, render_markdown
from awt.models import Tracker
from awt.store import Store

STAMP = datetime(2024, 5, 6, 7, 30, tzinfo=UTC)


def seed(store: Store, name: str = "papers", count: int = 2) -> Tracker:
    tracker = store.add_tracker(
        Tracker(name=name, source_type="arxiv", config={"query": "cat:cs.AI"})
    )
    store.record_items(
        tracker,
        [
            make_item(
                f"{name}-{n}",
                f"Paper {n}",
                summary=f"Summary {n}",
                score=1.0 - n / 10,
                published_at=datetime(2024, 5, n + 1, tzinfo=UTC),
            )
            for n in range(count)
        ],
    )
    return tracker


def test_digest_groups_by_tracker(store: Store) -> None:
    seed(store, "papers")
    seed(store, "releases")
    digest = build_digest(store, generated_at=STAMP)

    assert [section.tracker for section in digest.sections] == ["papers", "releases"]
    assert digest.item_count == 4


def test_reviewed_items_drop_out_of_the_digest(store: Store) -> None:
    seed(store, "papers", count=3)
    store.mark_reviewed("papers", ["papers-0"])
    digest = build_digest(store, generated_at=STAMP)
    assert digest.item_count == 2


def test_reviewed_items_reappear_when_asked_for(store: Store) -> None:
    seed(store, "papers", count=3)
    store.mark_reviewed("papers")
    assert build_digest(store, generated_at=STAMP).item_count == 0
    assert build_digest(store, unreviewed_only=False, generated_at=STAMP).item_count == 3


def test_empty_trackers_are_omitted_unless_requested(store: Store) -> None:
    store.add_tracker(Tracker(name="quiet", source_type="rss", config={"url": "https://e/f"}))
    assert build_digest(store, generated_at=STAMP).sections == []
    assert len(build_digest(store, include_empty=True, generated_at=STAMP).sections) == 1


def test_per_tracker_cap_reports_the_remainder(store: Store) -> None:
    seed(store, "papers", count=5)
    digest = build_digest(store, per_tracker=2, generated_at=STAMP)
    section = digest.sections[0]
    assert len(section.items) == 2
    assert section.total == 5
    assert section.truncated == 3


def test_markdown_has_a_heading_stamp_and_links(store: Store) -> None:
    seed(store, "papers", count=1)
    markdown = render_markdown(build_digest(store, title="Weekly", generated_at=STAMP))

    assert markdown.startswith("# Weekly")
    assert "_Generated 2024-05-06 07:30 UTC_" in markdown
    assert "## papers `arxiv`" in markdown
    assert "[Paper 0](https://example.com/papers-0)" in markdown
    assert "Summary 0" in markdown


def test_markdown_can_omit_summaries(store: Store) -> None:
    seed(store, "papers", count=1)
    markdown = render_markdown(build_digest(store, generated_at=STAMP), summaries=False)
    assert "Summary 0" not in markdown


def test_truncation_is_visible_in_the_markdown(store: Store) -> None:
    seed(store, "papers", count=5)
    markdown = render_markdown(build_digest(store, per_tracker=2, generated_at=STAMP))
    assert "_… and 3 more_" in markdown


def test_empty_digest_says_so_rather_than_rendering_a_stub(store: Store) -> None:
    markdown = render_markdown(build_digest(store, generated_at=STAMP))
    assert "Nothing new since the last review." in markdown


def test_item_count_is_singular_when_it_should_be(store: Store) -> None:
    seed(store, "papers", count=1)
    assert "**1 new item**" in render_markdown(build_digest(store, generated_at=STAMP))


def test_partial_scores_are_shown_but_perfect_ones_are_not(store: Store) -> None:
    tracker = store.add_tracker(
        Tracker(name="papers", source_type="rss", config={"url": "https://e/f"})
    )
    store.record_items(
        tracker, [make_item("weak", "Weak", score=0.5), make_item("strong", "Strong", score=1.0)]
    )
    markdown = render_markdown(build_digest(store, generated_at=STAMP))
    assert "score 0.50" in markdown
    assert "score 1.00" not in markdown


def test_digest_serializes_for_json_consumers(store: Store) -> None:
    seed(store, "papers", count=1)
    payload = build_digest(store, generated_at=STAMP).as_dict()
    assert payload["item_count"] == 1
    assert payload["sections"][0]["items"][0]["title"] == "Paper 0"
