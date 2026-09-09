"""Adapter parsing and config validation, all against recorded payloads."""

from __future__ import annotations

import pytest
from tests.conftest import FakeFetcher, fixture

from awt.errors import ConfigError, SourceError
from awt.sources import describe_sources, get_adapter, source_types
from awt.sources.arxiv import ArxivSource, parse_arxiv_feed
from awt.sources.base import clean_text, parse_datetime
from awt.sources.github_releases import GitHubReleasesSource, parse_releases
from awt.sources.hackernews import HackerNewsSource, parse_hits
from awt.sources.rss import RssSource, parse_feed


def test_registry_exposes_every_adapter() -> None:
    assert source_types() == ["arxiv", "github_releases", "hackernews", "rss"]
    assert {source["source_type"] for source in describe_sources()} == set(source_types())


def test_unknown_source_type_lists_the_alternatives() -> None:
    with pytest.raises(ConfigError) as excinfo:
        get_adapter("twitter")
    assert "arxiv" in excinfo.value.hint


class TestArxiv:
    def test_parses_entries(self) -> None:
        items = parse_arxiv_feed(fixture("arxiv.xml"))
        assert len(items) == 2
        first = items[0]
        # The title spans two lines in the feed; whitespace must be collapsed.
        assert first.title == "Benchmarking Tool-Using Agents Across Long Horizons"
        assert first.url == "http://arxiv.org/abs/2401.00001v2"
        assert first.authors == ("Ada Lovelace", "Alan Turing")
        assert first.published_at is not None
        assert first.published_at.year == 2024
        assert "<b>" not in first.summary and "reproducible" in first.summary

    def test_version_suffix_is_stripped_so_v1_and_v2_dedupe(self) -> None:
        items = parse_arxiv_feed(fixture("arxiv.xml"))
        assert items[0].external_id == "2401.00001"

    def test_malformed_xml_is_actionable(self) -> None:
        with pytest.raises(SourceError) as excinfo:
            parse_arxiv_feed("<html>rate limited</html")
        assert excinfo.value.hint

    def test_query_is_required(self) -> None:
        with pytest.raises(ConfigError):
            ArxivSource().validate({})

    def test_sort_by_is_checked(self) -> None:
        with pytest.raises(ConfigError) as excinfo:
            ArxivSource().validate({"query": "x", "sort_by": "citations"})
        assert "submittedDate" in excinfo.value.hint

    def test_fetch_builds_a_sorted_query(self, fetcher: FakeFetcher) -> None:
        items = ArxivSource().fetch({"query": "cat:cs.AI"}, limit=5, fetch=fetcher)
        url = fetcher.calls[0][0]
        assert "max_results=5" in url
        assert "sortBy=submittedDate" in url
        assert len(items) == 2


class TestGitHubReleases:
    def test_skips_drafts_and_prereleases_by_default(self) -> None:
        items = parse_releases(fixture("github_releases.json"), repo="acme/widget")
        assert [item.external_id for item in items] == ["acme/widget@v1.4.0"]

    def test_prereleases_can_be_opted_in(self) -> None:
        items = parse_releases(
            fixture("github_releases.json"), repo="acme/widget", include_prereleases=True
        )
        assert [item.external_id for item in items] == [
            "acme/widget@v1.4.0",
            "acme/widget@v1.5.0-rc1",
        ]

    def test_drafts_are_never_included(self) -> None:
        items = parse_releases(
            fixture("github_releases.json"), repo="acme/widget", include_prereleases=True
        )
        assert all("v1.6.0" not in item.external_id for item in items)

    def test_api_error_object_is_reported_with_a_hint(self) -> None:
        with pytest.raises(SourceError) as excinfo:
            parse_releases('{"message": "Not Found"}', repo="acme/missing")
        assert "Not Found" in excinfo.value.message
        assert "GITHUB_TOKEN" in excinfo.value.hint

    def test_repo_must_be_owner_slash_name(self) -> None:
        with pytest.raises(ConfigError) as excinfo:
            GitHubReleasesSource().validate({"repo": "https://github.com/acme/widget"})
        assert "owner/name" in excinfo.value.message

    def test_token_is_sent_when_present(
        self, fetcher: FakeFetcher, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("GITHUB_TOKEN", "secret-token")
        GitHubReleasesSource().fetch({"repo": "acme/widget"}, limit=5, fetch=fetcher)
        assert fetcher.calls[0][1]["Authorization"] == "Bearer secret-token"

    def test_no_auth_header_without_a_token(
        self, fetcher: FakeFetcher, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("GITHUB_TOKEN", raising=False)
        GitHubReleasesSource().fetch({"repo": "acme/widget"}, limit=5, fetch=fetcher)
        assert "Authorization" not in fetcher.calls[0][1]


class TestHackerNews:
    def test_parses_hits(self) -> None:
        items = parse_hits(fixture("hackernews.json"))
        assert [item.external_id for item in items] == ["39000001", "39000002"]

    def test_min_points_filters(self) -> None:
        items = parse_hits(fixture("hackernews.json"), min_points=100)
        assert [item.external_id for item in items] == ["39000001"]

    def test_story_without_url_links_to_the_discussion(self) -> None:
        items = parse_hits(fixture("hackernews.json"))
        assert items[1].url == "https://news.ycombinator.com/item?id=39000002"

    def test_min_points_must_be_an_integer(self) -> None:
        with pytest.raises(SourceError):
            HackerNewsSource().validate({"query": "x", "min_points": "many"})


class TestRss:
    def test_parses_rss_two(self) -> None:
        items = parse_feed(fixture("rss.xml"))
        assert len(items) == 2
        assert items[0].external_id == "acme-widget-2"
        assert items[0].summary == "Widget 2.0 adds streaming support."
        assert items[0].published_at is not None
        assert items[0].published_at.day == 5

    def test_falls_back_to_link_when_guid_is_missing(self) -> None:
        items = parse_feed(fixture("rss.xml"))
        assert items[1].external_id == "https://acme.example/blog/v1-deprecation"

    def test_parses_atom(self) -> None:
        items = parse_feed(fixture("atom.xml"))
        assert items[0].external_id == "tag:acme.example,2024:post-7"
        # rel="edit" must not win over rel="alternate".
        assert items[0].url == "https://acme.example/posts/7"
        assert items[0].authors == ("Infra Team",)

    def test_html_page_is_rejected_with_guidance(self) -> None:
        with pytest.raises(SourceError) as excinfo:
            parse_feed("<html><body>Not a feed</body></html>")
        assert "feed" in excinfo.value.hint.lower()

    def test_url_is_required(self) -> None:
        with pytest.raises(ConfigError):
            RssSource().validate({})


class TestHelpers:
    def test_clean_text_truncates_with_an_ellipsis(self) -> None:
        assert clean_text("x" * 100, limit=10).endswith("…")
        assert len(clean_text("x" * 100, limit=10)) == 10

    def test_clean_text_handles_none(self) -> None:
        assert clean_text(None) == ""

    @pytest.mark.parametrize(
        "raw",
        ["2024-01-02T18:30:00Z", "2024-01-02T18:30:00+00:00", "Tue, 02 Jan 2024 18:30:00 GMT"],
    )
    def test_parse_datetime_accepts_common_formats(self, raw: str) -> None:
        parsed = parse_datetime(raw)
        assert parsed is not None
        assert (parsed.year, parsed.month, parsed.day) == (2024, 1, 2)

    def test_parse_datetime_returns_none_for_junk(self) -> None:
        assert parse_datetime("last tuesday") is None
        assert parse_datetime(None) is None

    def test_naive_datetimes_are_treated_as_utc(self) -> None:
        parsed = parse_datetime("2024-01-02T18:30:00")
        assert parsed is not None and parsed.tzinfo is not None
