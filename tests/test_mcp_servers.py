"""Contract tests for both MCP servers.

These exercise the servers the way a client does -- list the tools, then call
them by name with a dict of arguments -- so a rename or a schema regression
fails here rather than in Claude Code.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

from awt.mcp import research_server, tracker_server
from awt.tools import Toolkit

RESEARCH_TOOLS = {"research_list_source_types", "research_search", "research_preview_feed"}
TRACKER_TOOLS = {
    "tracker_list",
    "tracker_create",
    "tracker_set_enabled",
    "tracker_delete",
    "tracker_poll",
    "tracker_items",
    "tracker_mark_reviewed",
    "tracker_runs",
    "tracker_digest",
    "tracker_stats",
}


@pytest.fixture(autouse=True)
def bound_toolkit(monkeypatch: pytest.MonkeyPatch, db_path: Path, fetcher) -> Toolkit:
    """Point both module-level servers at a temp database and the fake fetcher."""
    toolkit = Toolkit(db_path, fetcher=fetcher)
    monkeypatch.setattr(research_server, "toolkit", toolkit)
    monkeypatch.setattr(tracker_server, "toolkit", toolkit)
    return toolkit


def call(server: Any, tool: str, /, **arguments: Any) -> str:
    """Call a tool the way a client does. Positional-only so a tool argument
    named ``name`` cannot collide with this helper's own parameters."""
    result = asyncio.run(server.call_tool(tool, arguments))
    return "\n".join(block.text for block in result.content if hasattr(block, "text"))


def tools(server: Any) -> dict[str, Any]:
    return {tool.name: tool for tool in asyncio.run(server.list_tools())}


class TestRegistration:
    def test_research_server_exposes_its_tools(self) -> None:
        assert set(tools(research_server.server)) == RESEARCH_TOOLS

    def test_tracker_server_exposes_its_tools(self) -> None:
        assert set(tools(tracker_server.server)) == TRACKER_TOOLS

    def test_names_are_prefixed_so_the_servers_can_coexist(self) -> None:
        assert all(name.startswith("research_") for name in RESEARCH_TOOLS)
        assert all(name.startswith("tracker_") for name in TRACKER_TOOLS)

    @pytest.mark.parametrize("server", [research_server.server, tracker_server.server])
    def test_every_tool_is_documented(self, server: Any) -> None:
        for name, tool in tools(server).items():
            assert tool.description, f"{name} has no description"
            assert len(tool.description) > 80, f"{name} description is too thin to guide an agent"

    @pytest.mark.parametrize("server", [research_server.server, tracker_server.server])
    def test_every_parameter_is_documented(self, server: Any) -> None:
        for name, tool in tools(server).items():
            for field, schema in tool.input_schema.get("properties", {}).items():
                assert schema.get("description"), f"{name}.{field} has no description"

    @pytest.mark.parametrize("server", [research_server.server, tracker_server.server])
    def test_every_tool_carries_annotations(self, server: Any) -> None:
        for name, tool in tools(server).items():
            assert tool.annotations is not None, f"{name} has no annotations"
            assert tool.annotations.read_only_hint is not None

    def test_read_only_tools_are_marked_read_only(self) -> None:
        registered = tools(tracker_server.server)
        for name in (
            "tracker_list",
            "tracker_items",
            "tracker_digest",
            "tracker_stats",
            "tracker_runs",
        ):
            assert registered[name].annotations.read_only_hint is True

    def test_only_delete_is_marked_destructive(self) -> None:
        registered = tools(tracker_server.server)
        destructive = {
            name for name, tool in registered.items() if tool.annotations.destructive_hint
        }
        assert destructive == {"tracker_delete"}

    def test_research_tools_never_claim_to_write(self) -> None:
        for tool in tools(research_server.server).values():
            assert tool.annotations.read_only_hint is True

    def test_poll_is_the_open_world_tracker_tool(self) -> None:
        registered = tools(tracker_server.server)
        assert registered["tracker_poll"].annotations.open_world_hint is True
        assert registered["tracker_list"].annotations.open_world_hint is False

    def test_servers_carry_instructions_for_the_agent(self) -> None:
        assert "research" in (research_server.server.instructions or "").lower()
        assert "tracker_poll" in (tracker_server.server.instructions or "")


class TestResearchCalls:
    def test_list_source_types(self) -> None:
        assert "arxiv" in call(research_server.server, "research_list_source_types")

    def test_search_returns_results(self) -> None:
        out = call(
            research_server.server,
            "research_search",
            source_type="arxiv",
            config={"query": "cat:cs.AI"},
        )
        assert "Benchmarking Tool-Using Agents" in out

    def test_search_json_format(self) -> None:
        out = call(
            research_server.server,
            "research_search",
            source_type="arxiv",
            config={"query": "cat:cs.AI"},
            response_format="json",
        )
        assert json.loads(out)["count"] == 2

    def test_preview_feed_is_a_shortcut_for_rss(self) -> None:
        out = call(research_server.server, "research_preview_feed", url="https://acme.example/rss")
        assert "Widget 2.0 released" in out

    def test_a_bad_config_returns_guidance_not_an_exception(self) -> None:
        out = call(
            research_server.server, "research_search", source_type="github_releases", config={}
        )
        assert "ConfigError" in out
        assert "repo" in out

    def test_an_unknown_source_type_lists_the_valid_ones(self) -> None:
        out = call(research_server.server, "research_search", source_type="twitter", config={})
        assert "arxiv" in out

    def test_research_calls_never_write_to_the_database(self, bound_toolkit: Toolkit) -> None:
        call(research_server.server, "research_search", source_type="arxiv", config={"query": "x"})
        assert json.loads(bound_toolkit.stats().render("json"))["stats"]["items"] == 0


class TestTrackerCalls:
    def test_full_workflow_through_the_server(self) -> None:
        created = call(
            tracker_server.server,
            "tracker_create",
            name="papers",
            source_type="arxiv",
            config={"query": "cat:cs.AI"},
        )
        assert "papers" in created

        polled = call(tracker_server.server, "tracker_poll", response_format="json")
        assert json.loads(polled)["new_items"] == 2

        digest = call(tracker_server.server, "tracker_digest", title="Weekly")
        assert digest.startswith("# Weekly")

        reviewed = call(
            tracker_server.server, "tracker_mark_reviewed", name="papers", response_format="json"
        )
        assert json.loads(reviewed)["marked_reviewed"] == 2
        assert "Nothing new" in call(tracker_server.server, "tracker_digest")

    def test_items_report_pagination_metadata(self) -> None:
        call(
            tracker_server.server,
            "tracker_create",
            name="news",
            source_type="hackernews",
            config={"query": "mcp"},
        )
        call(tracker_server.server, "tracker_poll")
        page = json.loads(
            call(
                tracker_server.server, "tracker_items", name="news", limit=1, response_format="json"
            )
        )
        assert page["has_more"] is True
        assert page["next_offset"] == 1

    def test_poll_failure_is_reported_inside_the_result(
        self, monkeypatch: pytest.MonkeyPatch, db_path: Path
    ) -> None:
        from tests.conftest import BoomFetcher

        from awt.errors import SourceError

        monkeypatch.setattr(
            tracker_server,
            "toolkit",
            Toolkit(db_path, fetcher=BoomFetcher(SourceError("upstream down", hint="retry soon"))),
        )
        call(
            tracker_server.server,
            "tracker_create",
            name="papers",
            source_type="arxiv",
            config={"query": "x"},
        )
        out = call(tracker_server.server, "tracker_poll")
        assert "upstream down" in out
        assert "retry soon" in out

    def test_acting_on_an_unknown_tracker_returns_a_hint(self) -> None:
        out = call(tracker_server.server, "tracker_items", name="ghost")
        assert "NotFoundError" in out

    def test_out_of_range_arguments_are_rejected_by_the_schema(self) -> None:
        with pytest.raises(Exception, match="(?i)valid|error"):
            call(tracker_server.server, "tracker_items", name="papers", limit=9999)

    def test_disabled_trackers_are_skipped_by_poll(self) -> None:
        call(
            tracker_server.server,
            "tracker_create",
            name="papers",
            source_type="arxiv",
            config={"query": "x"},
        )
        call(tracker_server.server, "tracker_set_enabled", name="papers", enabled=False)
        assert (
            json.loads(call(tracker_server.server, "tracker_poll", response_format="json"))[
                "polled"
            ]
            == 0
        )

    def test_delete_removes_everything(self) -> None:
        call(
            tracker_server.server,
            "tracker_create",
            name="papers",
            source_type="arxiv",
            config={"query": "x"},
        )
        call(tracker_server.server, "tracker_poll")
        call(tracker_server.server, "tracker_delete", name="papers")
        stats = json.loads(call(tracker_server.server, "tracker_stats", response_format="json"))
        assert stats["stats"]["trackers"] == 0
        assert stats["stats"]["items"] == 0


class TestSharedWiring:
    def test_database_path_honours_the_env_var(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from awt.mcp._shared import database_path

        monkeypatch.setenv("AWT_DB", "/tmp/custom-awt.db")
        assert str(database_path()) == "/tmp/custom-awt.db"

    def test_database_path_falls_back_to_the_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from awt.mcp._shared import database_path
        from awt.tools import DEFAULT_DB

        monkeypatch.delenv("AWT_DB", raising=False)
        assert database_path() == DEFAULT_DB
