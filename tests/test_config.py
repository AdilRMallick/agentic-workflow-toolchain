"""Trackers-as-code: parsing and reconciliation."""

from __future__ import annotations

from pathlib import Path

import pytest

from awt.config import load_trackers, sync
from awt.errors import ConfigError
from awt.models import Tracker
from awt.store import Store

SAMPLE = """
[[tracker]]
name        = "mcp-releases"
source_type = "github_releases"
config      = { repo = "acme/widget" }

[[tracker]]
name        = "agent-evals"
source_type = "arxiv"
config      = { query = "cat:cs.AI" }
include     = ["evaluation"]
exclude     = ["survey"]
min_score   = 0.4
enabled     = false
"""


def write(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "trackers.toml"
    path.write_text(body, encoding="utf-8")
    return path


class TestLoad:
    def test_parses_every_field(self, tmp_path: Path) -> None:
        trackers = load_trackers(write(tmp_path, SAMPLE))
        assert [t.name for t in trackers] == ["mcp-releases", "agent-evals"]

        evals = trackers[1]
        assert evals.include == ("evaluation",)
        assert evals.exclude == ("survey",)
        assert evals.min_score == 0.4
        assert evals.enabled is False

    def test_config_is_normalized_by_the_adapter(self, tmp_path: Path) -> None:
        trackers = load_trackers(write(tmp_path, SAMPLE))
        # The arxiv adapter fills in the default sort order.
        assert trackers[1].config["sort_by"] == "submittedDate"

    def test_the_shipped_example_is_valid(self) -> None:
        trackers = load_trackers(Path("config/trackers.example.toml"))
        assert len(trackers) >= 4
        assert len({t.name for t in trackers}) == len(trackers)

    def test_missing_file_points_at_the_example(self, tmp_path: Path) -> None:
        with pytest.raises(ConfigError) as excinfo:
            load_trackers(tmp_path / "absent.toml")
        assert "example" in excinfo.value.hint

    def test_invalid_toml_is_reported(self, tmp_path: Path) -> None:
        with pytest.raises(ConfigError) as excinfo:
            load_trackers(write(tmp_path, '[[tracker]]\nname = "unterminated'))
        assert "valid TOML" in excinfo.value.message

    def test_empty_file_is_rejected(self, tmp_path: Path) -> None:
        with pytest.raises(ConfigError) as excinfo:
            load_trackers(write(tmp_path, "# nothing here\n"))
        assert "[[tracker]]" in excinfo.value.hint

    def test_a_bad_adapter_config_fails_at_parse_time(self, tmp_path: Path) -> None:
        body = (
            '[[tracker]]\nname = "x"\nsource_type = "github_releases"\nconfig = { repo = "nope" }\n'
        )
        with pytest.raises(ConfigError) as excinfo:
            load_trackers(write(tmp_path, body))
        assert "owner/name" in excinfo.value.message

    def test_unknown_keys_are_caught_rather_than_ignored(self, tmp_path: Path) -> None:
        body = '[[tracker]]\nname = "x"\nsource_type = "rss"\nconfig = { url = "https://e/f" }\nincldue = ["typo"]\n'
        with pytest.raises(ConfigError) as excinfo:
            load_trackers(write(tmp_path, body))
        assert "incldue" in excinfo.value.message

    def test_duplicate_names_are_caught(self, tmp_path: Path) -> None:
        body = (
            SAMPLE
            + '\n[[tracker]]\nname = "agent-evals"\nsource_type = "arxiv"\nconfig = { query = "y" }\n'
        )
        with pytest.raises(ConfigError) as excinfo:
            load_trackers(write(tmp_path, body))
        assert "more than once" in excinfo.value.message

    def test_missing_required_keys_are_caught(self, tmp_path: Path) -> None:
        with pytest.raises(ConfigError) as excinfo:
            load_trackers(write(tmp_path, '[[tracker]]\nname = "x"\n'))
        assert "source_type" in excinfo.value.message


class TestSync:
    def test_first_sync_creates_everything(self, store: Store, tmp_path: Path) -> None:
        report = sync(store, load_trackers(write(tmp_path, SAMPLE)))
        assert sorted(report.created) == ["agent-evals", "mcp-releases"]
        assert len(store.list_trackers()) == 2

    def test_resyncing_an_unchanged_file_is_a_no_op(self, store: Store, tmp_path: Path) -> None:
        trackers = load_trackers(write(tmp_path, SAMPLE))
        sync(store, trackers)
        report = sync(store, trackers)

        assert report.created == []
        assert sorted(report.unchanged) == ["agent-evals", "mcp-releases"]
        assert report.changed is False

    def test_a_changed_query_updates_the_tracker(self, store: Store, tmp_path: Path) -> None:
        sync(store, load_trackers(write(tmp_path, SAMPLE)))
        changed = SAMPLE.replace('repo = "acme/widget"', 'repo = "acme/other"')
        report = sync(store, load_trackers(write(tmp_path, changed)))

        assert report.updated == ["mcp-releases"]
        assert store.get_tracker("mcp-releases").config["repo"] == "acme/other"

    def test_sync_preserves_items_for_unchanged_trackers(
        self, store: Store, tmp_path: Path
    ) -> None:
        from tests.conftest import make_item

        trackers = load_trackers(write(tmp_path, SAMPLE))
        sync(store, trackers)
        store.record_items(store.get_tracker("agent-evals"), [make_item("keep")])

        sync(store, trackers)
        assert store.items("agent-evals")[1] == 1

    def test_undeclared_trackers_survive_without_prune(self, store: Store, tmp_path: Path) -> None:
        store.add_tracker(Tracker(name="manual", source_type="rss", config={"url": "https://e/f"}))
        report = sync(store, load_trackers(write(tmp_path, SAMPLE)))

        assert report.deleted == []
        assert "manual" in {t.name for t in store.list_trackers()}

    def test_prune_removes_undeclared_trackers(self, store: Store, tmp_path: Path) -> None:
        store.add_tracker(Tracker(name="manual", source_type="rss", config={"url": "https://e/f"}))
        report = sync(store, load_trackers(write(tmp_path, SAMPLE)), prune=True)

        assert report.deleted == ["manual"]
        assert "manual" not in {t.name for t in store.list_trackers()}

    def test_enabled_flag_from_the_file_is_applied(self, store: Store, tmp_path: Path) -> None:
        sync(store, load_trackers(write(tmp_path, SAMPLE)))
        assert store.get_tracker("agent-evals").enabled is False
