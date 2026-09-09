"""Scoring, filtering, and the poll loop's failure isolation."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from tests.conftest import BoomFetcher, FakeFetcher, make_item

from awt.errors import SourceError
from awt.models import Tracker
from awt.pipeline import poll_all, poll_tracker, score_item, select
from awt.store import Store


def item(title: str, summary: str = "") -> object:
    return make_item("x", title, summary=summary)


class TestScoring:
    def test_no_include_terms_keeps_everything(self) -> None:
        assert score_item(item("Anything"), [], []) == 1.0

    def test_all_terms_present_in_the_title_scores_full(self) -> None:
        assert score_item(item("Agent benchmark results"), ["agent", "benchmark"], []) == 1.0

    def test_partial_match_scores_proportionally(self) -> None:
        # One of two terms, matched in the body only: 0.5 with no title bonus.
        score = score_item(item("Results", "an agent appears here"), ["agent", "benchmark"], [])
        assert score == pytest.approx(0.5)

    def test_title_hits_earn_a_bonus_over_body_hits(self) -> None:
        in_title = score_item(item("Agent results", "nothing"), ["agent", "benchmark"], [])
        in_body = score_item(item("Results", "agent"), ["agent", "benchmark"], [])
        assert in_title > in_body

    def test_no_matching_term_scores_zero(self) -> None:
        assert score_item(item("Unrelated"), ["agent"], []) == 0.0

    def test_exclude_vetoes_even_a_perfect_match(self) -> None:
        assert (
            score_item(item("Agent benchmark survey"), ["agent", "benchmark"], ["survey"]) == -1.0
        )

    def test_matching_is_word_bounded(self) -> None:
        # "ai" must not match inside "chainsaw" or "said".
        assert score_item(item("Chainsaw maintenance", "he said"), ["ai"], []) == 0.0
        assert score_item(item("AI systems"), ["ai"], []) == 1.0

    def test_matching_is_case_insensitive(self) -> None:
        assert score_item(item("AGENT Benchmarks"), ["agent"], []) == 1.0

    def test_blank_terms_are_ignored(self) -> None:
        assert score_item(item("Anything"), ["", "  "], []) == 1.0


class TestSelect:
    def test_drops_vetoed_and_unmatched_items(self, tracker: Tracker) -> None:
        kept = select(
            tracker,
            [
                make_item("keep", "Agent benchmark harness"),
                make_item("veto", "Agent benchmark survey"),
                make_item("miss", "Unrelated topic"),
            ],
        )
        assert [i.external_id for i in kept] == ["keep"]

    def test_min_score_filters(self) -> None:
        strict = Tracker(
            name="t",
            source_type="rss",
            config={"url": "https://e/f"},
            include=("agent", "benchmark"),
            min_score=0.6,
        )
        kept = select(strict, [make_item("partial", "Results", summary="agent only")])
        assert kept == []

    def test_results_are_sorted_best_first(self, tracker: Tracker) -> None:
        old = datetime(2024, 1, 1, tzinfo=UTC)
        new = datetime(2024, 6, 1, tzinfo=UTC)
        kept = select(
            tracker,
            [
                make_item("weak", "Results", summary="agent", published_at=new),
                make_item("strong-old", "Agent benchmark", published_at=old),
                make_item("strong-new", "Agent benchmark", published_at=new),
            ],
        )
        assert [i.external_id for i in kept] == ["strong-new", "strong-old", "weak"]

    def test_scores_are_written_onto_the_kept_items(self, tracker: Tracker) -> None:
        kept = select(tracker, [make_item("keep", "Agent benchmark harness")])
        assert kept[0].score == 1.0

    def test_items_without_a_date_do_not_break_sorting(self, tracker: Tracker) -> None:
        kept = select(
            tracker, [make_item("a", "Agent benchmark"), make_item("b", "Agent benchmark")]
        )
        assert len(kept) == 2


class TestPoll:
    def test_records_new_items_and_a_successful_run(
        self, store: Store, fetcher: FakeFetcher
    ) -> None:
        saved = store.add_tracker(
            Tracker(name="papers", source_type="arxiv", config={"query": "cat:cs.AI"})
        )
        result = poll_tracker(store, saved, fetch=fetcher)

        assert result.status == "ok"
        assert result.fetched == 2
        assert result.new_count == 2
        assert store.runs("papers")[0].status == "ok"

    def test_a_second_poll_finds_nothing_new(self, store: Store, fetcher: FakeFetcher) -> None:
        saved = store.add_tracker(
            Tracker(name="papers", source_type="arxiv", config={"query": "cat:cs.AI"})
        )
        poll_tracker(store, saved, fetch=fetcher)
        second = poll_tracker(store, saved, fetch=fetcher)

        assert second.fetched == 2
        assert second.new_count == 0

    def test_tracker_filters_apply_during_a_poll(self, store: Store, fetcher: FakeFetcher) -> None:
        saved = store.add_tracker(
            Tracker(
                name="papers",
                source_type="arxiv",
                config={"query": "cat:cs.AI"},
                exclude=("survey",),
            )
        )
        result = poll_tracker(store, saved, fetch=fetcher)
        assert result.new_count == 1
        assert "Survey" not in result.new_items[0].title

    def test_source_failure_is_recorded_not_raised(self, store: Store) -> None:
        saved = store.add_tracker(
            Tracker(name="papers", source_type="arxiv", config={"query": "cat:cs.AI"})
        )
        result = poll_tracker(
            store, saved, fetch=BoomFetcher(SourceError("upstream is down", hint="retry later"))
        )

        assert result.status == "error"
        assert result.error == "upstream is down"
        assert result.hint == "retry later"
        assert store.runs("papers")[0].status == "error"

    def test_an_unexpected_adapter_error_is_contained(self, store: Store) -> None:
        saved = store.add_tracker(
            Tracker(name="papers", source_type="arxiv", config={"query": "cat:cs.AI"})
        )
        result = poll_tracker(store, saved, fetch=BoomFetcher(ValueError("bug")))
        assert result.status == "error"
        assert "ValueError" in (result.error or "")

    def test_one_broken_tracker_does_not_abort_the_sweep(self, store: Store) -> None:
        store.add_tracker(
            Tracker(name="good", source_type="rss", config={"url": "https://acme.example/rss"})
        )
        store.add_tracker(
            Tracker(name="bad", source_type="rss", config={"url": "https://down.example/rss"})
        )

        fetcher = FakeFetcher(
            {
                "acme.example/rss": "<rss version='2.0'><channel><item><title>Hi</title><link>https://a/1</link></item></channel></rss>"
            }
        )
        # The unregistered "down.example" URL makes FakeFetcher raise.
        results = poll_all(store, fetch=fetcher)

        by_name = {result.tracker: result for result in results}
        assert by_name["good"].status == "ok"
        assert by_name["bad"].status == "error"

    def test_disabled_trackers_are_skipped(self, store: Store, fetcher: FakeFetcher) -> None:
        store.add_tracker(Tracker(name="papers", source_type="arxiv", config={"query": "x"}))
        store.set_enabled("papers", False)
        assert poll_all(store, fetch=fetcher) == []

    def test_named_trackers_are_polled_even_when_disabled(
        self, store: Store, fetcher: FakeFetcher
    ) -> None:
        store.add_tracker(Tracker(name="papers", source_type="arxiv", config={"query": "x"}))
        store.set_enabled("papers", False)
        results = poll_all(store, fetch=fetcher, names=["papers"])
        assert [result.tracker for result in results] == ["papers"]
