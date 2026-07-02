---
name: status
description: "surfaces backlog and pending items as a dashboard. use when the user says 'status', 'what's pending', 'show backlog', 'dashboard', 'what am i working on', 'open items', or any variant of checking current work status. (session start is covered by a SessionStart hook — do not run this again unprompted.)"
---
Run:

```bash
python3 ~/.claude/scripts/dev_status.py render
```

Display stdout as-is. The item-map on stderr is for your reference — use it to resolve natural language like "mark 3 done" or "work on 2":

- "mark N done" → `python3 ~/.claude/scripts/dev_status.py done N`
- "work on N" → `python3 ~/.claude/scripts/dev_status.py start N`
- "details on N" → `python3 ~/.claude/scripts/dev_status.py show N`

Pass the integer N directly — the script resolves it internally against current state. Do not look up the slug from the item-map. Do not narrate. Do not reformat the dashboard output.

Mutating commands (`start`, `done`, `update`) echo what they resolved to on stderr, e.g. `[done] 3 → meta-email-py: Build email.py...`. Numbers shift as items change, so check that the echoed summary matches the item the user meant. If it doesn't, revert (`update <slug> '{"status": "open"}'` or similar), re-render, and ask.
