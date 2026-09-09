---
description: Turn a topic into a validated tracker.
argument-hint: "<topic or feed URL>"
allowed-tools: mcp__awt-research__research_list_source_types, mcp__awt-research__research_search, mcp__awt-research__research_preview_feed, mcp__awt-tracker__tracker_list, mcp__awt-tracker__tracker_create, Task, Read, Edit
---

Set up recurring tracking for: **$ARGUMENTS**

Use the `research-scout` agent. It must validate the query with `research_search` and show
the results before creating anything — an unvalidated tracker fails silently forever.

If `config/trackers.toml` exists, add the tracker there as well so the scheduled workflows
pick it up rather than it living only in the local database.
