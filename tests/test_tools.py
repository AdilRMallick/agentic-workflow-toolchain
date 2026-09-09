"""The toolkit: the exact surface both MCP servers and the CLI call."""

from __future__ import annotations

import json

import pytest

from awt.errors import ConfigError
from awt.tools import Toolkit, ToolResponse, safe_call


def payload(response: ToolResponse) -> dict:
    return json.loads(response.render("json"))


class TestResponseFormat:
    def test_markdown_is_the_default(self, toolkit: Toolkit) -> None:
        assert toolkit.stats().render().startswith("| metric")

    def test_json_is_parseable(self, toolkit: Toolkit) -> None:
        assert "trackers" in json.loads(toolkit.stats().render("json"))["stats"]

    def test_unknown_format_is_rejected_with_the_valid_options(self, toolkit: Toolkit) -> None:
        with pytest.raises(ConfigError) as excinfo:
            toolkit.stats().render("yaml")
        assert "markdown" in excinfo.value.hint


class TestResearch:
    def test_source_types_document_their_config(self, toolkit: Toolkit) -> None:
        data = payload(toolkit.list_source_types())
        arxiv = next(s for s in data["source_types"] if s["source_type"] == "arxiv")
        assert "query" in arxiv["config"]

    def test_search_returns_results_without_persisting(self, toolkit: Toolkit) -> None:
        data = payload(toolkit.search("arxiv", {"query": "cat:cs.AI"}))
        assert data["count"] == 2
        assert payload(toolkit.stats())["stats"]["items"] == 0

    def test_search_applies_include_and_exclude(self, toolkit: Toolkit) -> None:
        data = payload(toolkit.search("arxiv", {"query": "cat:cs.AI"}, exclude=["survey"]))
        assert data["fetched"] == 2
        assert data["count"] == 1

    def test_search_reports_no_results_clearly(self, toolkit: Toolkit) -> None:
        markdown = toolkit.search("arxiv", {"query": "x"}, include=["nonexistentterm"]).markdown
        assert "_No matching results._" in markdown

    def test_bad_config_is_an_actionable_response_not_a_crash(self, toolkit: Toolkit) -> None:
        response = safe_call(toolkit.search, "github_releases", {})
        assert response.data["error"] == "ConfigError"
        assert "repo" in response.markdown

    def test_limit_is_capped(self, toolkit: Toolkit, fetcher) -> None:
        toolkit.search("arxiv", {"query": "x"}, limit=5000)
        assert "max_results=100" in fetcher.calls[0][0]

    def test_non_positive_limit_falls_back_to_the_default(self, toolkit: Toolkit, fetcher) -> None:
        toolkit.search("arxiv", {"query": "x"}, limit=0)
        assert "max_results=25" in fetcher.calls[0][0]


class TestTrackerLifecycle:
    def test_create_validates_before_storing(self, toolkit: Toolkit) -> None:
        response = safe_call(toolkit.create_tracker, "bad", "github_releases", {"repo": "no-slash"})
        assert response.data["error"] == "ConfigError"
        assert payload(toolkit.list_trackers())["count"] == 0

    def test_create_normalizes_the_name(self, toolkit: Toolkit) -> None:
        data = payload(toolkit.create_tracker("  Agent-Evals  ", "arxiv", {"query": "cat:cs.AI"}))
        assert data["created"]["name"] == "agent-evals"

    def test_list_reports_the_unreviewed_backlog(self, toolkit: Toolkit) -> None:
        toolkit.create_tracker("papers", "arxiv", {"query": "cat:cs.AI"})
        toolkit.poll()
        entry = payload(toolkit.list_trackers())["trackers"][0]
        assert entry["unreviewed"] == 2

    def test_empty_list_tells_you_what_to_do_next(self, toolkit: Toolkit) -> None:
        assert "tracker_create" in toolkit.list_trackers().markdown

    def test_disable_pauses_polling_but_keeps_items(self, toolkit: Toolkit) -> None:
        toolkit.create_tracker("papers", "arxiv", {"query": "cat:cs.AI"})
        toolkit.poll()
        toolkit.set_enabled("papers", False)

        assert payload(toolkit.poll())["polled"] == 0
        assert payload(toolkit.items("papers"))["total"] == 2

    def test_delete_removes_the_tracker_and_its_items(self, toolkit: Toolkit) -> None:
        toolkit.create_tracker("papers", "arxiv", {"query": "cat:cs.AI"})
        toolkit.poll()
        toolkit.delete_tracker("papers")
        assert payload(toolkit.stats())["stats"] == {
            "trackers": 0,
            "enabled_trackers": 0,
            "items": 0,
            "unreviewed_items": 0,
            "runs": 0,
        }

    def test_acting_on_an_unknown_tracker_names_the_known_ones(self, toolkit: Toolkit) -> None:
        toolkit.create_tracker("papers", "arxiv", {"query": "cat:cs.AI"})
        response = safe_call(toolkit.items, "typo")
        assert response.data["error"] == "NotFoundError"
        assert "papers" in response.markdown


class TestPollAndReview:
    def test_poll_records_then_finds_nothing_new(self, toolkit: Toolkit) -> None:
        toolkit.create_tracker("papers", "arxiv", {"query": "cat:cs.AI"})
        first = payload(toolkit.poll())
        assert first["new_items"] == 2
        assert payload(toolkit.poll())["new_items"] == 0

    def test_poll_reports_failures_without_failing_the_call(self, db_path) -> None:
        from tests.conftest import BoomFetcher

        from awt.errors import SourceError

        broken = Toolkit(db_path, fetcher=BoomFetcher(SourceError("down", hint="retry")))
        broken.create_tracker("papers", "arxiv", {"query": "cat:cs.AI"})
        data = payload(broken.poll())

        assert data["failed"] == 1
        assert data["results"][0]["hint"] == "retry"
        assert "retry" in broken.poll().markdown

    def test_poll_can_target_named_trackers(self, toolkit: Toolkit) -> None:
        toolkit.create_tracker("papers", "arxiv", {"query": "cat:cs.AI"})
        toolkit.create_tracker("news", "hackernews", {"query": "mcp"})
        data = payload(toolkit.poll(["news"]))
        assert [result["tracker"] for result in data["results"]] == ["news"]

    def test_items_paginate_with_next_offset(self, toolkit: Toolkit) -> None:
        toolkit.create_tracker("news", "hackernews", {"query": "mcp"})
        toolkit.poll()

        first = payload(toolkit.items("news", limit=1))
        assert first["has_more"] is True
        assert first["next_offset"] == 1

        second = payload(toolkit.items("news", limit=1, offset=first["next_offset"]))
        assert second["has_more"] is False
        assert second["items"][0]["external_id"] != first["items"][0]["external_id"]

    def test_markdown_points_at_the_next_page(self, toolkit: Toolkit) -> None:
        toolkit.create_tracker("news", "hackernews", {"query": "mcp"})
        toolkit.poll()
        assert "offset=1" in toolkit.items("news", limit=1).markdown

    def test_mark_reviewed_clears_the_backlog(self, toolkit: Toolkit) -> None:
        toolkit.create_tracker("papers", "arxiv", {"query": "cat:cs.AI"})
        toolkit.poll()
        assert payload(toolkit.mark_reviewed("papers"))["marked_reviewed"] == 2
        assert payload(toolkit.items("papers"))["total"] == 0
        assert payload(toolkit.items("papers", unreviewed_only=False))["total"] == 2

    def test_runs_expose_history_for_debugging(self, toolkit: Toolkit) -> None:
        toolkit.create_tracker("papers", "arxiv", {"query": "cat:cs.AI"})
        toolkit.poll()
        data = payload(toolkit.runs())
        assert data["count"] == 1
        assert data["runs"][0]["status"] == "ok"

    def test_runs_reads_clearly_when_empty(self, toolkit: Toolkit) -> None:
        assert "No runs recorded yet." in toolkit.runs().markdown


class TestDigest:
    def test_digest_covers_what_a_poll_found(self, toolkit: Toolkit) -> None:
        toolkit.create_tracker("papers", "arxiv", {"query": "cat:cs.AI"})
        toolkit.poll()
        markdown = toolkit.digest(title="Weekly").markdown

        assert markdown.startswith("# Weekly")
        assert "Benchmarking Tool-Using Agents" in markdown

    def test_digest_does_not_mark_anything_reviewed(self, toolkit: Toolkit) -> None:
        toolkit.create_tracker("papers", "arxiv", {"query": "cat:cs.AI"})
        toolkit.poll()
        toolkit.digest()
        assert payload(toolkit.items("papers"))["total"] == 2

    def test_digest_is_empty_after_review(self, toolkit: Toolkit) -> None:
        toolkit.create_tracker("papers", "arxiv", {"query": "cat:cs.AI"})
        toolkit.poll()
        toolkit.mark_reviewed("papers")
        assert "Nothing new" in toolkit.digest().markdown


class TestStats:
    def test_stats_report_the_database_path(self, toolkit: Toolkit, db_path) -> None:
        assert payload(toolkit.stats())["database"] == str(db_path)
