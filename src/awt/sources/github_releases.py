"""GitHub releases adapter -- the dependency-tracking half of the toolchain."""

from __future__ import annotations

import json
import os
import re
from typing import Any

from awt.errors import ConfigError, SourceError
from awt.http import Fetcher
from awt.models import Item
from awt.sources.base import SourceAdapter, clean_text, parse_datetime, require_str

API = "https://api.github.com"
_REPO_RE = re.compile(r"^[A-Za-z0-9._-]+/[A-Za-z0-9._-]+$")


class GitHubReleasesSource(SourceAdapter):
    name = "github_releases"
    summary = "Releases published for a GitHub repository, newest first."
    config_schema = {
        "repo": 'required. "owner/name", e.g. "modelcontextprotocol/python-sdk"',
        "include_prereleases": "optional bool, default false",
    }

    def validate(self, config: dict[str, Any]) -> dict[str, Any]:
        repo = require_str(config, "repo", example="modelcontextprotocol/python-sdk")
        if not _REPO_RE.match(repo):
            raise ConfigError(
                f"Repository {repo!r} is not in owner/name form.",
                hint='Use the short form, e.g. "anthropics/claude-code", not a full URL.',
            )
        return {"repo": repo, "include_prereleases": bool(config.get("include_prereleases", False))}

    def fetch(self, config: dict[str, Any], *, limit: int, fetch: Fetcher) -> list[Item]:
        cfg = self.validate(config)
        url = f"{API}/repos/{cfg['repo']}/releases?per_page={min(limit, 100)}"
        headers = {"Accept": "application/vnd.github+json"}
        token = os.environ.get("GITHUB_TOKEN", "").strip()
        if token:
            headers["Authorization"] = f"Bearer {token}"
        return parse_releases(
            fetch(url, headers).text,
            repo=cfg["repo"],
            include_prereleases=cfg["include_prereleases"],
        )


def parse_releases(payload: str, *, repo: str, include_prereleases: bool = False) -> list[Item]:
    try:
        releases = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise SourceError(
            f"GitHub returned a non-JSON response for {repo}: {exc}",
            hint="An unauthenticated client is limited to 60 requests/hour; set GITHUB_TOKEN.",
        ) from exc
    if not isinstance(releases, list):
        message = (
            releases.get("message", "unknown error")
            if isinstance(releases, dict)
            else "unexpected payload"
        )
        raise SourceError(
            f"GitHub returned an error for {repo}: {message}",
            hint="Confirm the repository exists and is public, or set GITHUB_TOKEN for private access.",
        )

    items: list[Item] = []
    for release in releases:
        if not isinstance(release, dict) or release.get("draft"):
            continue
        if release.get("prerelease") and not include_prereleases:
            continue
        tag = str(release.get("tag_name") or release.get("id") or "")
        if not tag:
            continue
        items.append(
            Item(
                external_id=f"{repo}@{tag}",
                title=f"{repo} {release.get('name') or tag}".strip(),
                url=str(release.get("html_url") or f"https://github.com/{repo}/releases/tag/{tag}"),
                summary=clean_text(str(release.get("body") or "")),
                published_at=parse_datetime(
                    release.get("published_at") or release.get("created_at")
                ),
                authors=(str((release.get("author") or {}).get("login", "")),)
                if release.get("author")
                else (),
            )
        )
    return items
