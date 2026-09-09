---
description: Triage new dependency releases for breaking changes.
argument-hint: "[tracker names…]"
allowed-tools: mcp__awt-tracker__tracker_poll, mcp__awt-tracker__tracker_items, mcp__awt-tracker__tracker_list, mcp__awt-tracker__tracker_mark_reviewed, Task, Read, Grep, Glob
---

Triage the release backlog for: **$ARGUMENTS** (empty means every `github_releases` tracker).

Use the `release-watcher` agent. Every "breaking change" claim must be grounded in code in
this repository — cite the file and line, or classify it as noise.
