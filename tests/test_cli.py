"""End-to-end CLI behaviour, including exit codes CI depends on."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from tests.conftest import BoomFetcher, FakeFetcher

from awt.cli import main
from awt.errors import SourceError


@pytest.fixture(autouse=True)
def offline(monkeypatch: pytest.MonkeyPatch, fetcher: FakeFetcher) -> FakeFetcher:
    """No CLI test may reach the network: every Toolkit gets the fake fetcher."""
    monkeypatch.setattr("awt.tools.UrllibFetcher", lambda *a, **k: fetcher)
    return fetcher


@pytest.fixture
def run(db_path: Path, capsys: pytest.CaptureFixture[str]):
    def _run(*args: str) -> tuple[int, str, str]:
        code = main(["--db", str(db_path), *args])
        captured = capsys.readouterr()
        return code, captured.out, captured.err

    return _run


class TestBasics:
    def test_sources_lists_every_adapter(self, run) -> None:
        code, out, _ = run("sources")
        assert code == 0
        for name in ("arxiv", "github_releases", "hackernews", "rss"):
            assert name in out

    def test_json_format_is_parseable(self, run) -> None:
        _, out, _ = run("--format", "json", "stats")
        assert json.loads(out)["stats"]["trackers"] == 0

    def test_unknown_command_is_rejected_by_argparse(self, db_path: Path) -> None:
        with pytest.raises(SystemExit):
            main(["--db", str(db_path), "teleport"])

    def test_format_works_after_the_subcommand_too(
        self, db_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        # Global flags are easy to misplace; both positions must behave the same.
        main(["--db", str(db_path), "stats", "--format", "json"])
        after = capsys.readouterr().out
        main(["--db", str(db_path), "--format", "json", "stats"])
        before = capsys.readouterr().out
        assert json.loads(after) == json.loads(before)

    def test_db_works_after_the_subcommand_too(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        target = tmp_path / "after.db"
        main(["stats", "--db", str(target), "--format", "json"])
        assert json.loads(capsys.readouterr().out)["database"] == str(target)

    def test_a_flag_after_the_subcommand_wins(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        target = tmp_path / "wins.db"
        main(["--db", str(tmp_path / "loses.db"), "stats", "--db", str(target), "--format", "json"])
        assert json.loads(capsys.readouterr().out)["database"] == str(target)


class TestTrackerLifecycle:
    def test_add_then_list(self, run) -> None:
        assert run("add", "papers", "arxiv", "--config", '{"query": "cat:cs.AI"}')[0] == 0
        _, out, _ = run("list")
        assert "papers" in out and "arxiv" in out

    def test_add_rejects_malformed_json_with_a_hint(self, run) -> None:
        code, _, err = run("add", "papers", "arxiv", "--config", "{not json}")
        assert code == 2
        assert "valid JSON" in err
        assert "hint:" in err

    def test_add_rejects_a_json_array(self, run) -> None:
        code, _, err = run("add", "papers", "arxiv", "--config", "[1, 2]")
        assert code == 2
        assert "JSON object" in err

    def test_invalid_source_config_exits_two(self, run) -> None:
        code, _, err = run("add", "x", "github_releases", "--config", '{"repo": "nope"}')
        assert code == 2
        assert "owner/name" in err

    def test_include_and_exclude_are_stored(self, run) -> None:
        run(
            "add",
            "papers",
            "arxiv",
            "--config",
            '{"query": "cat:cs.AI"}',
            "--include",
            "agent",
            "benchmark",
            "--exclude",
            "survey",
        )
        _, out, _ = run("--format", "json", "list")
        tracker = json.loads(out)["trackers"][0]
        assert tracker["include"] == ["agent", "benchmark"]
        assert tracker["exclude"] == ["survey"]

    def test_disable_then_enable(self, run) -> None:
        run("add", "papers", "arxiv", "--config", '{"query": "cat:cs.AI"}')
        run("disable", "papers")
        _, out, _ = run("--format", "json", "list", "--enabled-only")
        assert json.loads(out)["count"] == 0

        run("enable", "papers")
        _, out, _ = run("--format", "json", "list", "--enabled-only")
        assert json.loads(out)["count"] == 1

    def test_rm_deletes(self, run) -> None:
        run("add", "papers", "arxiv", "--config", '{"query": "cat:cs.AI"}')
        run("rm", "papers")
        _, out, _ = run("--format", "json", "stats")
        assert json.loads(out)["stats"]["trackers"] == 0

    def test_removing_an_unknown_tracker_exits_two(self, run) -> None:
        code, _, err = run("rm", "ghost")
        assert code == 2
        assert "ghost" in err


class TestWorkflow:
    def test_poll_then_digest_then_review(self, run) -> None:
        run("add", "papers", "arxiv", "--config", '{"query": "cat:cs.AI"}')

        code, out, _ = run("poll")
        assert code == 0
        assert "2 new item(s)" in out

        _, out, _ = run("digest", "--title", "Weekly")
        assert out.startswith("# Weekly")
        assert "Benchmarking Tool-Using Agents" in out

        _, out, _ = run("review", "papers")
        assert "Marked 2 item(s) reviewed" in out

        _, out, _ = run("digest")
        assert "Nothing new" in out

    def test_polling_twice_finds_nothing_new(self, run) -> None:
        run("add", "papers", "arxiv", "--config", '{"query": "cat:cs.AI"}')
        run("poll")
        _, out, _ = run("poll")
        assert "0 new item(s)" in out

    def test_poll_exits_nonzero_when_a_source_fails(
        self, run, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        run("add", "papers", "arxiv", "--config", '{"query": "cat:cs.AI"}')
        monkeypatch.setattr(
            "awt.tools.UrllibFetcher",
            lambda *a, **k: BoomFetcher(SourceError("down", hint="retry")),
        )
        code, out, _ = run("poll")
        assert code == 1
        assert "failed" in out

    def test_search_does_not_persist(self, run) -> None:
        code, out, _ = run("search", "arxiv", "--config", '{"query": "cat:cs.AI"}')
        assert code == 0
        assert "Benchmarking" in out
        _, stats, _ = run("--format", "json", "stats")
        assert json.loads(stats)["stats"]["items"] == 0

    def test_items_paginate(self, run) -> None:
        run("add", "news", "hackernews", "--config", '{"query": "mcp"}')
        run("poll")
        _, out, _ = run("--format", "json", "items", "news", "--limit", "1")
        page = json.loads(out)
        assert page["has_more"] is True and page["next_offset"] == 1

    def test_runs_show_history(self, run) -> None:
        run("add", "papers", "arxiv", "--config", '{"query": "cat:cs.AI"}')
        run("poll")
        _, out, _ = run("runs")
        assert "papers" in out and "ok" in out

    def test_review_accepts_specific_ids(self, run) -> None:
        run("add", "papers", "arxiv", "--config", '{"query": "cat:cs.AI"}')
        run("poll")
        _, out, _ = run("review", "papers", "--ids", "2401.00001")
        assert "Marked 1 item(s) reviewed" in out


class TestDigestOutput:
    def test_digest_writes_a_file_and_creates_parents(self, run, tmp_path: Path) -> None:
        run("add", "papers", "arxiv", "--config", '{"query": "cat:cs.AI"}')
        run("poll")
        target = tmp_path / "nested" / "digest.md"

        code, out, _ = run("digest", "--output", str(target))
        assert code == 0
        assert target.read_text(encoding="utf-8").startswith("# Research digest")
        assert "2 item(s)" in out

    def test_digest_respects_the_per_tracker_cap(self, run) -> None:
        run("add", "papers", "arxiv", "--config", '{"query": "cat:cs.AI"}')
        run("poll")
        _, out, _ = run("digest", "--per-tracker", "1")
        assert "_… and 1 more_" in out

    def test_no_summaries_flag(self, run) -> None:
        run("add", "papers", "arxiv", "--config", '{"query": "cat:cs.AI"}')
        run("poll")
        _, out, _ = run("digest", "--no-summaries")
        assert "long-horizon tasks" not in out


class TestSync:
    def test_sync_creates_then_reports_unchanged(self, run, tmp_path: Path) -> None:
        config = tmp_path / "trackers.toml"
        config.write_text(
            '[[tracker]]\nname = "papers"\nsource_type = "arxiv"\nconfig = { query = "cat:cs.AI" }\n',
            encoding="utf-8",
        )

        code, out, _ = run("sync", str(config))
        assert code == 0 and "1 created" in out

        _, out, _ = run("sync", str(config))
        assert "1 unchanged" in out

    def test_sync_prune_removes_undeclared_trackers(self, run, tmp_path: Path) -> None:
        config = tmp_path / "trackers.toml"
        config.write_text(
            '[[tracker]]\nname = "papers"\nsource_type = "arxiv"\nconfig = { query = "cat:cs.AI" }\n',
            encoding="utf-8",
        )
        run("sync", str(config))
        run("add", "manual", "rss", "--config", '{"url": "https://acme.example/rss"}')

        _, out, _ = run("--format", "json", "sync", str(config), "--prune")
        assert json.loads(out)["deleted"] == ["manual"]

    def test_sync_of_the_shipped_example_config(self, run) -> None:
        code, out, _ = run("sync", "config/trackers.example.toml")
        assert code == 0
        assert "created" in out

    def test_missing_config_exits_two(self, run, tmp_path: Path) -> None:
        code, _, err = run("sync", str(tmp_path / "absent.toml"))
        assert code == 2
        assert "does not exist" in err


class TestDatabaseSelection:
    def test_env_var_selects_the_database(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        target = tmp_path / "from-env.db"
        monkeypatch.setenv("AWT_DB", str(target))
        main(["add", "papers", "arxiv", "--config", '{"query": "cat:cs.AI"}'])
        capsys.readouterr()
        assert target.exists()

    def test_flag_beats_the_env_var(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        monkeypatch.setenv("AWT_DB", str(tmp_path / "env.db"))
        explicit = tmp_path / "flag.db"
        main(["--db", str(explicit), "stats"])
        capsys.readouterr()
        assert explicit.exists()
        assert not (tmp_path / "env.db").exists()
