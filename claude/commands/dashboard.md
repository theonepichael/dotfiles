---
name: dashboard
description: "surfaces backlog and pending items as a dashboard. use when the user says 'dashboard', 'what's pending', 'show backlog', 'where we at', 'what am i working on', 'open items', or any variant of checking current work status. Renamed from /status to avoid colliding with Claude Code's built-in /status (plan usage/rate-limit view) — a naming collision with a built-in command can silently break custom command loading. (session start is covered by a SessionStart hook — do not run this again unprompted.)"
---
Run:

```bash
python3 ~/.claude/scripts/dev_status.py render
python3 ~/.claude/scripts/vitals_promotion.py --needs-review-summary
```

Display both outputs verbatim — do not narrate, do not reformat. The item-map on stderr is for you, not the user: use it to resolve natural language ("mark 3 done", "work on 2") into the CLAUDE.md backlog commands, passing the integer N straight through.

Mutating commands (`start`, `done`, `update`, `review`, `approve`, `reject`) echo what they resolved to on stderr, e.g. `[done] 3 → meta-email-py: Build email.py...`. Numbers shift as items change, so check that the echoed summary matches the item the user meant. If it doesn't, revert (`update <slug> '{"status": "open"}'` or similar), re-render, and ask. For `start`/`done` that same `update <slug> '{"status": "open"}'` revert still applies; if a `review`/`approve`/`reject` resolved to the wrong item, there is no direct revert: `approve`'s status change isn't a plain field patch and re-running `update` on `status` is refused for in-review items too. Ask the user how to proceed rather than attempting an automatic fix.
