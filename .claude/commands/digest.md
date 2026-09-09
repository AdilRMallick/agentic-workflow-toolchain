---
description: Poll every tracker and write up what is new.
argument-hint: "[tracker names…]"
allowed-tools: mcp__awt-tracker__tracker_poll, mcp__awt-tracker__tracker_digest, mcp__awt-tracker__tracker_list, mcp__awt-tracker__tracker_runs, Task
---

Produce the current digest for: **$ARGUMENTS** (empty means every enabled tracker).

1. `tracker_poll` the requested trackers so the digest reflects this moment, not the last run.
2. If any tracker reports a failure, name it and its hint at the top — a digest built on a
   silently broken source is worse than no digest.
3. Hand the backlog to the `digest-writer` agent for the write-up.

Do not mark anything reviewed. Offer that as a follow-up.
