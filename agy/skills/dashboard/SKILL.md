---
name: dashboard
description: "surfaces backlog and pending items as a dashboard. use when the user says 'dashboard', 'status', 'what's pending', 'show backlog', 'where we at', 'what am i working on', 'open items', or any variant of checking current work status. Renamed from 'status' to avoid colliding with a built-in status-style command."
---
Run:

```bash
python3 ~/.claude/scripts/dev_status.py render
```

Display stdout verbatim — do not narrate, do not reformat. The item-map on stderr is for you, not the user: use it to resolve natural language ("mark 3 done", "work on 2") into the shared instructions file's backlog commands, passing the integer N straight through.

Mutating commands (`start`, `done`, `update`) echo what they resolved to on stderr, e.g. `[done] 3 → meta-email-py: Build email.py...`. Numbers shift as items change, so check that the echoed summary matches the item the user meant. If it doesn't, revert (`update <slug> '{"status": "open"}'` or similar), re-render, and ask.
