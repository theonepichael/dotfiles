---
name: standup
description: "Gather assigned work, chat signal, calendar events, pending replies, git commits, and backlog activity into a daily standup draft, saved to a dated file. Use when the user says 'standup', 'prep for standup', or wants their daily status pulled together."
allowed-tools: [Read, Write, Glob, Grep, "Bash(python3 ~/.claude/scripts/standup.py:*)", "Bash(git log:*)"]
---

Read-only against every external system. Never comments on a ticket, posts a
message, or marks anything read — the only write is the standup file itself.
No autonomous scanning: this only runs when invoked.

## 1. Fetch

```
python3 ~/.claude/scripts/standup.py fetch
```

Returns one JSON object: `git_commits`, `backlog_in_progress`,
`backlog_recent_done`, `assigned_items`, `messages`, `calendar_events`,
`pending_items_open`, `pending_items_from_adapter`, `skipped`.

`skipped` lists every source that couldn't run (adapter not yet configured,
no git repos set up, `work_backlog_prefixes` missing, etc.) with a reason.
Tell the user what was skipped and why — don't silently produce a partial
standup as if it were complete. See `~/dotfiles/claude/scripts/standup_adapters.py`
for wiring in a real adapter once a source is consistently skipped because
the platform is now known.

## 2. Reconcile pending items

For each entry in `pending_items_open`, check whether `messages` or
`pending_items_from_adapter` shows it's been answered. Propose resolutions
to the user before applying — same propose-then-confirm shape as
`grill-me`, since "was this actually answered" is a judgment call, not a
pattern match:

```
python3 ~/.claude/scripts/standup.py pending resolve <id>
```

For anything in the fetched data that looks like a new item worth tracking
across days (an email/chat message still awaiting a reply, an access
request not yet approved) but isn't already in `pending_items_open`,
propose adding it:

```
python3 ~/.claude/scripts/standup.py pending add '{"id", "description", "source_ref", "kind"}'
```

`kind` is one of `email`, `chat`, `approval`.

## 3. Draft

Write the standup update from the reconciled data — what shipped
(`git_commits`, `backlog_recent_done`), what's in progress
(`backlog_in_progress`, `assigned_items`), anything noteworthy on the
calendar today (`calendar_events` — already filtered to non-recurring),
and anything still blocked (`pending_items_open`, post-reconciliation).
Skip a section entirely if its source had nothing or was skipped — don't
pad the draft to look complete.

## 4. Save and show

Write the draft to `~/.claude/data/standup/YYYY-MM-DD.md` (today's date).
Show the user the draft and the `skipped` list from step 1.
