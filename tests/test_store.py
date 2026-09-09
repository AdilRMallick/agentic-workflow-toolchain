"""Persistence: dedup, pagination, review state, run history."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from tests.conftest import make_item

from awt.errors import ConfigError, DuplicateError, NotFoundError
from awt.models import Tracker
from awt.store import Store


def test_tracker_round_trips(store: Store, tracker: Tracker) -> None:
    store.add_tracker(tracker)
    loaded = store.get_tracker("agent-evals")
    assert loaded.id is not None
    assert loaded.source_type == "arxiv"
    assert loaded.include == ("agent", "benchmark")
    assert loaded.exclude == ("survey",)
    assert loaded.config["query"] == "cat:cs.AI"
    assert loaded.created_at is not None


def test_duplicate_names_are_rejected(store: Store, tracker: Tracker) -> None:
    store.add_tracker(tracker)
    with pytest.raises(DuplicateError) as excinfo:
        store.add_tracker(tracker)
    assert "already exists" in excinfo.value.message


def test_invalid_names_are_rejected(store: Store) -> None:
    with pytest.raises(ConfigError):
        store.add_tracker(
            Tracker(name="Has Spaces", source_type="rss", config={"url": "https://e/f"})
        )


def test_config_is_validated_on_insert(store: Store) -> None:
    with pytest.raises(ConfigError):
        store.add_tracker(Tracker(name="broken", source_type="github_releases", config={}))


def test_missing_tracker_lists_what_exists(store: Store, tracker: Tracker) -> None:
    store.add_tracker(tracker)
    with pytest.raises(NotFoundError) as excinfo:
        store.get_tracker("nope")
    assert "agent-evals" in excinfo.value.hint


def test_record_items_returns_only_new_ones(store: Store, tracker: Tracker) -> None:
    saved = store.add_tracker(tracker)
    first = store.record_items(saved, [make_item("a"), make_item("b")])
    assert len(first) == 2

    # The same ids again plus one genuinely new item.
    second = store.record_items(saved, [make_item("a"), make_item("b"), make_item("c")])
    assert [item.external_id for item in second] == ["c"]

    _, total = store.items("agent-evals")
    assert total == 3


def test_items_are_ordered_by_score_then_recency(store: Store, tracker: Tracker) -> None:
    saved = store.add_tracker(tracker)
    old = datetime(2024, 1, 1, tzinfo=UTC)
    new = datetime(2024, 6, 1, tzinfo=UTC)
    store.record_items(
        saved,
        [
            make_item("low", score=0.2, published_at=new),
            make_item("high-old", score=0.9, published_at=old),
            make_item("high-new", score=0.9, published_at=new),
        ],
    )
    items, _ = store.items("agent-evals")
    assert [item.external_id for item in items] == ["high-new", "high-old", "low"]


def test_pagination_reports_the_full_total(store: Store, tracker: Tracker) -> None:
    saved = store.add_tracker(tracker)
    store.record_items(saved, [make_item(f"i{n}", score=1.0 - n / 100) for n in range(10)])

    page, total = store.items("agent-evals", limit=4, offset=0)
    assert (len(page), total) == (4, 10)
    second, _ = store.items("agent-evals", limit=4, offset=4)
    assert {i.external_id for i in page}.isdisjoint({i.external_id for i in second})


def test_mark_reviewed_clears_the_backlog(store: Store, tracker: Tracker) -> None:
    saved = store.add_tracker(tracker)
    store.record_items(saved, [make_item("a"), make_item("b"), make_item("c")])

    assert store.mark_reviewed("agent-evals", ["a"]) == 1
    _, unreviewed = store.items("agent-evals", unreviewed_only=True, limit=0)
    assert unreviewed == 2

    assert store.mark_reviewed("agent-evals") == 2
    _, unreviewed = store.items("agent-evals", unreviewed_only=True, limit=0)
    assert unreviewed == 0


def test_marking_reviewed_twice_is_a_no_op(store: Store, tracker: Tracker) -> None:
    saved = store.add_tracker(tracker)
    store.record_items(saved, [make_item("a")])
    assert store.mark_reviewed("agent-evals", ["a"]) == 1
    assert store.mark_reviewed("agent-evals", ["a"]) == 0


def test_marking_an_empty_id_list_touches_nothing(store: Store, tracker: Tracker) -> None:
    saved = store.add_tracker(tracker)
    store.record_items(saved, [make_item("a")])
    assert store.mark_reviewed("agent-evals", []) == 0


def test_reviewed_items_are_not_reported_again_after_a_later_poll(
    store: Store, tracker: Tracker
) -> None:
    saved = store.add_tracker(tracker)
    store.record_items(saved, [make_item("a")])
    store.mark_reviewed("agent-evals")
    assert store.record_items(saved, [make_item("a")]) == []


def test_runs_record_success_and_failure(store: Store, tracker: Tracker) -> None:
    saved = store.add_tracker(tracker)
    ok = store.start_run(saved)
    store.finish_run(ok, status="ok", fetched=5, new_items=2)
    bad = store.start_run(saved)
    store.finish_run(bad, status="error", error="HTTP 500")

    history = store.runs("agent-evals")
    assert len(history) == 2
    assert {run.status for run in history} == {"ok", "error"}
    assert any(run.error == "HTTP 500" for run in history)
    assert all(run.finished_at is not None for run in history)


def test_update_tracker_retunes_filters_without_touching_items(
    store: Store, tracker: Tracker
) -> None:
    saved = store.add_tracker(tracker)
    store.record_items(saved, [make_item("a"), make_item("b")])
    store.mark_reviewed("agent-evals", ["a"])

    updated = store.update_tracker(
        "agent-evals", include=("agent",), exclude=(), min_score=0.75, enabled=False
    )

    assert updated.include == ("agent",)
    assert updated.exclude == ()
    assert updated.min_score == 0.75
    assert updated.enabled is False
    # Identity is untouched, and so is everything recorded under it.
    assert updated.id == saved.id
    assert updated.source_type == "arxiv"
    assert updated.config == saved.config
    assert store.items("agent-evals")[1] == 2
    assert store.items("agent-evals", unreviewed_only=True)[1] == 1


def test_update_tracker_rejects_an_unknown_name(store: Store) -> None:
    with pytest.raises(NotFoundError):
        store.update_tracker("ghost", include=(), exclude=(), min_score=0.0, enabled=True)


def test_disabling_keeps_the_tracker_out_of_enabled_listings(
    store: Store, tracker: Tracker
) -> None:
    store.add_tracker(tracker)
    store.set_enabled("agent-evals", False)
    assert store.list_trackers(enabled_only=True) == []
    assert len(store.list_trackers()) == 1


def test_deleting_a_tracker_cascades_to_items_and_runs(store: Store, tracker: Tracker) -> None:
    saved = store.add_tracker(tracker)
    store.record_items(saved, [make_item("a")])
    store.finish_run(store.start_run(saved), status="ok")

    store.delete_tracker("agent-evals")
    assert store.stats() == {
        "trackers": 0,
        "enabled_trackers": 0,
        "items": 0,
        "unreviewed_items": 0,
        "runs": 0,
    }


def test_state_survives_reopening_the_database(db_path, tracker: Tracker) -> None:
    with Store(db_path) as first:
        saved = first.add_tracker(tracker)
        first.record_items(saved, [make_item("a")])

    with Store(db_path) as second:
        assert second.stats()["items"] == 1
        assert second.get_tracker("agent-evals").include == ("agent", "benchmark")
