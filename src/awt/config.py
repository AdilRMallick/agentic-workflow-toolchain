"""Declarative tracker configuration.

Scheduled workflows should not need an agent in the loop to know *what* to
track. Trackers are declared in a TOML file committed next to the code, and
``awt sync`` reconciles the database with that file: new entries are created,
changed entries updated, and (with ``--prune``) removed entries deleted.
"""

from __future__ import annotations

import tomllib
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from awt.errors import ConfigError
from awt.models import Tracker
from awt.sources import get_adapter
from awt.store import Store

_KNOWN_KEYS = frozenset(
    {"name", "source_type", "config", "include", "exclude", "min_score", "enabled"}
)


def load_trackers(path: str | Path) -> list[Tracker]:
    """Parse a trackers TOML file into validated :class:`Tracker` objects."""
    file = Path(path)
    if not file.is_file():
        raise ConfigError(
            f"Config file {file} does not exist.",
            hint="Copy config/trackers.example.toml to config/trackers.toml to start.",
        )
    try:
        raw = tomllib.loads(file.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(
            f"{file} is not valid TOML: {exc}", hint="Check for unclosed quotes or brackets."
        ) from exc

    entries = raw.get("tracker")
    if not isinstance(entries, list) or not entries:
        raise ConfigError(
            f"{file} declares no trackers.",
            hint="Each tracker is a [[tracker]] table with name, source_type and config.",
        )

    trackers: list[Tracker] = []
    seen: set[str] = set()
    for index, entry in enumerate(entries):
        tracker = _to_tracker(entry, index=index, file=file)
        if tracker.name in seen:
            raise ConfigError(
                f"{file} declares tracker {tracker.name!r} more than once.",
                hint="Tracker names must be unique; rename or remove the duplicate.",
            )
        seen.add(tracker.name)
        trackers.append(tracker)
    return trackers


def _to_tracker(entry: Any, *, index: int, file: Path) -> Tracker:
    where = f"{file} [[tracker]] #{index + 1}"
    if not isinstance(entry, dict):
        raise ConfigError(f"{where} is not a table.", hint="Use [[tracker]] table syntax.")

    unknown = set(entry) - _KNOWN_KEYS
    if unknown:
        raise ConfigError(
            f"{where} has unknown key(s): {', '.join(sorted(unknown))}.",
            hint=f"Supported keys: {', '.join(sorted(_KNOWN_KEYS))}.",
        )

    name = str(entry.get("name") or "").strip().lower()
    source_type = str(entry.get("source_type") or "").strip()
    if not name or not source_type:
        raise ConfigError(
            f"{where} needs both 'name' and 'source_type'.",
            hint='Example: name = "agent-evals", source_type = "arxiv".',
        )

    config = entry.get("config") or {}
    if not isinstance(config, dict):
        raise ConfigError(
            f"{where} 'config' must be a table.",
            hint='Example: config = {{ repo = "owner/name" }}.',
        )

    # Fail on a bad adapter config at parse time, not at the next scheduled poll.
    validated = get_adapter(source_type).validate(dict(config))

    return Tracker(
        name=name,
        source_type=source_type,
        config=validated,
        include=tuple(str(term) for term in entry.get("include", [])),
        exclude=tuple(str(term) for term in entry.get("exclude", [])),
        min_score=float(entry.get("min_score", 0.0)),
        enabled=bool(entry.get("enabled", True)),
    )


@dataclass(slots=True)
class SyncReport:
    created: list[str] = field(default_factory=list)
    updated: list[str] = field(default_factory=list)
    recreated: list[str] = field(default_factory=list)
    unchanged: list[str] = field(default_factory=list)
    deleted: list[str] = field(default_factory=list)

    @property
    def changed(self) -> bool:
        return bool(self.created or self.updated or self.recreated or self.deleted)

    def as_dict(self) -> dict[str, list[str]]:
        return {
            "created": self.created,
            "updated": self.updated,
            "recreated": self.recreated,
            "unchanged": self.unchanged,
            "deleted": self.deleted,
        }

    def summary(self) -> str:
        return (
            f"{len(self.created)} created, {len(self.updated)} retuned, "
            f"{len(self.recreated)} recreated, {len(self.unchanged)} unchanged, "
            f"{len(self.deleted)} deleted"
        )


def sync(store: Store, trackers: Sequence[Tracker], *, prune: bool = False) -> SyncReport:
    """Reconcile the database with a declared set of trackers.

    Two kinds of change are treated differently, because they mean different
    things:

    *Retuning* -- include, exclude, min_score, enabled -- changes which of the
    fetched results are kept. The question is the same, so the tracker is
    updated in place and its items and run history survive.

    *Re-aiming* -- source_type or config -- changes what is fetched at all. The
    stored items answered a different question, so the tracker is recreated and
    they are dropped.

    The distinction matters for scheduled runs: raising a min_score by 0.1 must
    not resurface every item the reader has already reviewed.
    """
    report = SyncReport()
    declared = {tracker.name: tracker for tracker in trackers}
    existing = {tracker.name: tracker for tracker in store.list_trackers()}

    for name, tracker in declared.items():
        current = existing.get(name)
        if current is None:
            store.add_tracker(tracker)
            report.created.append(name)
        elif _re_aimed(current, tracker):
            store.delete_tracker(name)
            store.add_tracker(tracker)
            report.recreated.append(name)
        elif _retuned(current, tracker):
            store.update_tracker(
                name,
                include=tracker.include,
                exclude=tracker.exclude,
                min_score=tracker.min_score,
                enabled=tracker.enabled,
            )
            report.updated.append(name)
        else:
            report.unchanged.append(name)

    if prune:
        for name in existing:
            if name not in declared:
                store.delete_tracker(name)
                report.deleted.append(name)

    return report


def _re_aimed(current: Tracker, declared: Tracker) -> bool:
    """True when the tracker would fetch something different than before."""
    return current.source_type != declared.source_type or current.config != declared.config


def _retuned(current: Tracker, declared: Tracker) -> bool:
    """True when only the filters over the fetched results changed."""
    return (
        current.include != declared.include
        or current.exclude != declared.exclude
        or current.min_score != declared.min_score
        or current.enabled != declared.enabled
    )
