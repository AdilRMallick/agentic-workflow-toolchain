---
name: weekly-digest
description: Run the full recurring research workflow end to end — sync trackers from config, poll every source, write the digest, publish it, and clear the backlog. Use for the weekly or daily digest, when the user asks to "run the digest" or "catch me up", or when reproducing locally what the scheduled GitHub Actions workflow does.
---

# Weekly digest

The recurring workflow this toolchain exists for. Five steps, in order — each one depends
on the last.

## 1. Sync trackers from config

```bash
awt sync config/trackers.toml     # falls back to config/trackers.example.toml
```

Trackers live in version control so a scheduled run needs no agent to know what to watch.
If the user asks to track something new, add it to the TOML file, not just the database —
otherwise it works locally and silently never runs in CI.

## 2. Poll

```bash
awt poll
```

`awt poll` exits non-zero when any source failed. **Check the failures before continuing.**
A digest assembled over a broken feed looks exactly like a quiet week, which is the single
most misleading thing this tool can produce.

```bash
awt runs --limit 10               # history, including the error for each failed run
```

Common causes: GitHub rate limiting without `GITHUB_TOKEN` (403), arXiv throttling (429),
or a blog that moved its feed URL (404). Each error carries a hint naming the fix.

## 3. Write the digest

```bash
awt digest --title "Week of $(date +%Y-%m-%d)" --output digests/$(date +%Y-%m-%d).md
```

That is the mechanical version. For anything a person will read, hand the backlog to the
`digest-writer` agent instead — it finds the throughline across trackers and cuts what does
not earn its place. The raw command output is a correct list, not a good digest.

## 4. Publish

Whatever the user asked for: commit the file under `digests/`, open a GitHub issue, or
just print it. Committing gives the digests a searchable history, which is usually what
someone wants six months later.

## 5. Clear the backlog — last, and only after publishing

```bash
awt review <tracker>              # or: awt review <tracker> --ids <id> <id>
```

Only after the digest is delivered. Reviewing is not reversible, and an item cleared
before anyone read it is gone from every future digest.

## Judgement

- **Never** mark items reviewed without confirming the digest reached the user.
- A tracker that has been silent for weeks is a bug report, not a quiet field. Check
  `awt runs <name>` before assuming there is nothing to say.
- Report failed sources in the digest itself. The reader needs to know the digest is
  partial; a silent gap reads as "nothing happened".
- Deduplication is already handled — an item is reported once, ever. Do not re-poll hoping
  for more; poll, then read what came back.
