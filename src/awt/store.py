"""SQLite persistence.

Everything the toolchain remembers lives in one file, so a scheduled workflow
can restore state by checking out a single artifact. Timestamps are written as
ISO-8601 UTC strings rather than relying on sqlite3's deprecated adapters.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from awt.errors import ConfigError, DuplicateError, NotFoundError
from awt.models import Item, Run, Tracker, valid_tracker_name
from awt.sources import get_adapter

SCHEMA_VERSION = 1

_SCHEMA = """
CREATE TABLE IF NOT EXISTS trackers (
    id          INTEGER PRIMARY KEY,
    name        TEXT NOT NULL UNIQUE,
    source_type TEXT NOT NULL,
    config      TEXT NOT NULL DEFAULT '{}',
    include     TEXT NOT NULL DEFAULT '[]',
    exclude     TEXT NOT NULL DEFAULT '[]',
    min_score   REAL NOT NULL DEFAULT 0.0,
    enabled     INTEGER NOT NULL DEFAULT 1,
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS items (
    id            INTEGER PRIMARY KEY,
    tracker_id    INTEGER NOT NULL REFERENCES trackers(id) ON DELETE CASCADE,
    external_id   TEXT NOT NULL,
    title         TEXT NOT NULL,
    url           TEXT NOT NULL,
    summary       TEXT NOT NULL DEFAULT '',
    authors       TEXT NOT NULL DEFAULT '[]',
    published_at  TEXT,
    score         REAL NOT NULL DEFAULT 0.0,
    first_seen_at TEXT NOT NULL,
    reviewed_at   TEXT,
    UNIQUE (tracker_id, external_id)
);

CREATE TABLE IF NOT EXISTS runs (
    id          INTEGER PRIMARY KEY,
    tracker_id  INTEGER NOT NULL REFERENCES trackers(id) ON DELETE CASCADE,
    started_at  TEXT NOT NULL,
    finished_at TEXT,
    status      TEXT NOT NULL,
    fetched     INTEGER NOT NULL DEFAULT 0,
    new_items   INTEGER NOT NULL DEFAULT 0,
    error       TEXT
);

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_items_unreviewed ON items (tracker_id, reviewed_at);
CREATE INDEX IF NOT EXISTS idx_items_first_seen ON items (first_seen_at);
CREATE INDEX IF NOT EXISTS idx_runs_tracker ON runs (tracker_id, started_at DESC);
"""


def utcnow() -> datetime:
    return datetime.now(tz=UTC)


def _iso(value: datetime | None) -> str | None:
    return value.astimezone(UTC).isoformat() if value else None


def _from_iso(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


class Store:
    """Thin, explicit data access layer. No ORM, no lazy loading, no surprises."""

    def __init__(self, path: str | Path = ":memory:") -> None:
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.path)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._conn.execute("PRAGMA journal_mode = WAL")
        self._migrate()

    def _migrate(self) -> None:
        with self._conn:
            self._conn.executescript(_SCHEMA)
            self._conn.execute(
                "INSERT OR REPLACE INTO meta (key, value) VALUES ('schema_version', ?)",
                (str(SCHEMA_VERSION),),
            )

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> Store:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # ---------------------------------------------------------------- trackers

    def add_tracker(self, tracker: Tracker) -> Tracker:
        if not valid_tracker_name(tracker.name):
            raise ConfigError(
                f"Tracker name {tracker.name!r} is not usable.",
                hint="Use lowercase letters, digits, dots, dashes or underscores (max 64 characters).",
            )
        config = get_adapter(tracker.source_type).validate(dict(tracker.config))
        created = tracker.created_at or utcnow()
        try:
            with self._conn:
                self._conn.execute(
                    """INSERT INTO trackers
                       (name, source_type, config, include, exclude, min_score, enabled, created_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        tracker.name,
                        tracker.source_type,
                        json.dumps(config),
                        json.dumps(list(tracker.include)),
                        json.dumps(list(tracker.exclude)),
                        float(tracker.min_score),
                        int(tracker.enabled),
                        _iso(created),
                    ),
                )
        except sqlite3.IntegrityError as exc:
            raise DuplicateError(
                f"A tracker named {tracker.name!r} already exists.",
                hint="Pick another name, or delete the existing tracker first.",
            ) from exc
        return self.get_tracker(tracker.name)

    def get_tracker(self, name: str) -> Tracker:
        row = self._conn.execute("SELECT * FROM trackers WHERE name = ?", (name,)).fetchone()
        if row is None:
            known = ", ".join(t.name for t in self.list_trackers()) or "none yet"
            raise NotFoundError(
                f"No tracker named {name!r}.",
                hint=f"Existing trackers: {known}.",
            )
        return _row_to_tracker(row)

    def list_trackers(self, *, enabled_only: bool = False) -> list[Tracker]:
        sql = "SELECT * FROM trackers"
        if enabled_only:
            sql += " WHERE enabled = 1"
        sql += " ORDER BY name"
        return [_row_to_tracker(row) for row in self._conn.execute(sql)]

    def set_enabled(self, name: str, enabled: bool) -> Tracker:
        tracker = self.get_tracker(name)
        with self._conn:
            self._conn.execute(
                "UPDATE trackers SET enabled = ? WHERE id = ?", (int(enabled), tracker.id)
            )
        return self.get_tracker(name)

    def delete_tracker(self, name: str) -> None:
        tracker = self.get_tracker(name)
        with self._conn:
            self._conn.execute("DELETE FROM trackers WHERE id = ?", (tracker.id,))

    # ------------------------------------------------------------------- items

    def record_items(self, tracker: Tracker, items: Sequence[Item]) -> list[Item]:
        """Insert items, ignoring ones already seen. Returns only what was new."""
        seen_at = _iso(utcnow())
        new: list[Item] = []
        with self._conn:
            for item in items:
                cursor = self._conn.execute(
                    """INSERT OR IGNORE INTO items
                       (tracker_id, external_id, title, url, summary, authors,
                        published_at, score, first_seen_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        tracker.id,
                        item.external_id,
                        item.title,
                        item.url,
                        item.summary,
                        json.dumps(list(item.authors)),
                        _iso(item.published_at),
                        float(item.score),
                        seen_at,
                    ),
                )
                if cursor.rowcount:
                    new.append(item)
        return new

    def items(
        self,
        name: str,
        *,
        unreviewed_only: bool = False,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[Item], int]:
        """Return a page of items plus the total matching count."""
        tracker = self.get_tracker(name)
        where = "WHERE tracker_id = ?"
        params: list[Any] = [tracker.id]
        if unreviewed_only:
            where += " AND reviewed_at IS NULL"

        total_row = self._conn.execute(
            f"SELECT COUNT(*) AS n FROM items {where}", params
        ).fetchone()
        rows = self._conn.execute(
            f"""SELECT * FROM items {where}
                ORDER BY score DESC, COALESCE(published_at, first_seen_at) DESC, id DESC
                LIMIT ? OFFSET ?""",
            (*params, max(limit, 0), max(offset, 0)),
        ).fetchall()
        return [_row_to_item(row) for row in rows], int(total_row["n"])

    def mark_reviewed(self, name: str, external_ids: Sequence[str] | None = None) -> int:
        """Mark items reviewed; ``None`` marks every unreviewed item for the tracker."""
        tracker = self.get_tracker(name)
        now = _iso(utcnow())
        with self._conn:
            if external_ids is None:
                cursor = self._conn.execute(
                    "UPDATE items SET reviewed_at = ? WHERE tracker_id = ? AND reviewed_at IS NULL",
                    (now, tracker.id),
                )
            else:
                ids = list(external_ids)
                if not ids:
                    return 0
                placeholders = ",".join("?" * len(ids))
                cursor = self._conn.execute(
                    f"""UPDATE items SET reviewed_at = ?
                        WHERE tracker_id = ? AND reviewed_at IS NULL
                          AND external_id IN ({placeholders})""",
                    (now, tracker.id, *ids),
                )
        return cursor.rowcount

    # -------------------------------------------------------------------- runs

    def start_run(self, tracker: Tracker) -> int:
        with self._conn:
            cursor = self._conn.execute(
                "INSERT INTO runs (tracker_id, started_at, status) VALUES (?, ?, 'running')",
                (tracker.id, _iso(utcnow())),
            )
        return int(cursor.lastrowid or 0)

    def finish_run(
        self,
        run_id: int,
        *,
        status: str,
        fetched: int = 0,
        new_items: int = 0,
        error: str | None = None,
    ) -> None:
        with self._conn:
            self._conn.execute(
                """UPDATE runs SET finished_at = ?, status = ?, fetched = ?, new_items = ?, error = ?
                   WHERE id = ?""",
                (_iso(utcnow()), status, fetched, new_items, error, run_id),
            )

    def runs(self, name: str | None = None, *, limit: int = 20) -> list[Run]:
        sql = """SELECT runs.*, trackers.name AS tracker_name
                 FROM runs JOIN trackers ON trackers.id = runs.tracker_id"""
        params: list[Any] = []
        if name is not None:
            sql += " WHERE trackers.name = ?"
            params.append(self.get_tracker(name).name)
        sql += " ORDER BY runs.started_at DESC, runs.id DESC LIMIT ?"
        params.append(max(limit, 0))
        return [_row_to_run(row) for row in self._conn.execute(sql, params)]

    def stats(self) -> dict[str, int]:
        one = self._conn.execute(
            """SELECT
                 (SELECT COUNT(*) FROM trackers)                              AS trackers,
                 (SELECT COUNT(*) FROM trackers WHERE enabled = 1)            AS enabled_trackers,
                 (SELECT COUNT(*) FROM items)                                 AS items,
                 (SELECT COUNT(*) FROM items WHERE reviewed_at IS NULL)       AS unreviewed_items,
                 (SELECT COUNT(*) FROM runs)                                  AS runs"""
        ).fetchone()
        return {
            key: int(one[key])
            for key in ("trackers", "enabled_trackers", "items", "unreviewed_items", "runs")
        }


@contextmanager
def open_store(path: str | Path) -> Iterator[Store]:
    store = Store(path)
    try:
        yield store
    finally:
        store.close()


def _row_to_tracker(row: sqlite3.Row) -> Tracker:
    return Tracker(
        id=int(row["id"]),
        name=row["name"],
        source_type=row["source_type"],
        config=json.loads(row["config"]),
        include=tuple(json.loads(row["include"])),
        exclude=tuple(json.loads(row["exclude"])),
        min_score=float(row["min_score"]),
        enabled=bool(row["enabled"]),
        created_at=_from_iso(row["created_at"]),
    )


def _row_to_item(row: sqlite3.Row) -> Item:
    return Item(
        external_id=row["external_id"],
        title=row["title"],
        url=row["url"],
        summary=row["summary"],
        published_at=_from_iso(row["published_at"]),
        authors=tuple(json.loads(row["authors"])),
        score=float(row["score"]),
    )


def _row_to_run(row: sqlite3.Row) -> Run:
    return Run(
        id=int(row["id"]),
        tracker=row["tracker_name"],
        started_at=_from_iso(row["started_at"]) or utcnow(),
        finished_at=_from_iso(row["finished_at"]),
        status=row["status"],
        fetched=int(row["fetched"]),
        new_items=int(row["new_items"]),
        error=row["error"],
    )
