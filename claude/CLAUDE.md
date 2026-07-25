# CLAUDE.md

<!-- Shared with ~/.copilot/copilot-instructions.md (same symlink target). Gmail/
     Calendar/Drive MCP servers are deliberately not configured under Copilot CLI,
     per the --work profile's no-personal-data-on-work-hardware rule — intentional,
     not a gap. -->

## Workflow Behaviors

### Backlog

When the user says "add this as a backlog item" or a variation of it, run:

```bash
python3 ~/.claude/scripts/dev_status.py add '{"id": "<prefix-slug>", "summary": "<concise title>", "category": "<bug|feature|chore|research>", "context": "<what was happening>", "next_steps": "<what to pick up from>", "related_files": [{"path": "<abs path>", "note": "<note>"}]}'
```

The `id` field is **required**. Use a kebab-case slug with a project prefix:
- `ajhp-` for ai-job-hunter-pro items
- `meta-` for tooling / infrastructure items
- `fid-` for the workplace work items — this is also what `/standup`'s
  `work_backlog_prefixes` config filters on; keep the two in sync if the
  prefix ever changes
- other prefixes as appropriate for the project

Infer all fields from the current conversation.
Only include files actually relevant to picking up the work later.
Omit related_files if there is nothing meaningful to put in them (use []).

#### Proactive capture

Offer to add a backlog item (never add silently) when any of these occur:

- A bug, gap, or improvement is discovered but is out of scope for the current task
- The user defers something: "later", "eventually", "not now", "we should", "someday", "v2"
- A task finishes with loose ends (skipped tests, TODO comments, known rough edges)
- The user pauses mid-task ("let's stop here", "I need to step away", "we'll come back to this")
- The session is wrapping up and an unfinished thread hasn't been captured

If you catch yourself narrating a finding as an aside instead of stopping for it —
"worth noting", "separately", "out of scope for this", "a question for another day" —
that phrasing IS the trigger.

Don't rely on catching it mid-sentence — before sending any response, re-scan your own
draft for that trigger language. Anything found without a paired offer gets one added
before you send, not after. Several findings in one turn can share a single offer line;
none get dropped silently. (This closes the gap between noticing something and acting on
it — it can't make you notice something you never put into words in the first place.)

Protocol: draft the full add JSON yourself, then offer it as one line —
``Add to backlog? `ajhp-<slug>` — <summary>`` — and run the add only on confirmation.
At most one offer per distinct item; if declined, don't re-offer it.

To update, start, or complete an item — pass the integer directly to the script.
**Do not look up the slug in your context; the script resolves numbers internally.**

```bash
python3 ~/.claude/scripts/dev_status.py start <slug|N>
python3 ~/.claude/scripts/dev_status.py done <slug|N>
python3 ~/.claude/scripts/dev_status.py update <slug|N> '{"field": "value"}'
python3 ~/.claude/scripts/dev_status.py show <slug|N>
```

When passing a numeric position (not a slug) to `start`/`done`/`update`/`block`/
`unblock`/`pending update`, fetch the current rev first — the `item-map:` line of
`render` (or `# rev=N` of `list`/`show`) output — in the same tool-call step
immediately before the mutating call, and pass it as `--if-rev <N>`. The script
refuses (no write) if `--if-rev` is missing or stale on a numeric call, so this is
guidance for the fast path, not the safety net — a numeric call that omits it fails
loudly with a fresh render printed for retry, it never silently mutates the wrong
item. Slug-based calls are exempt and need nothing extra.

When work is complete on a backlog item, mark it done with the above.

`start`/`done`/`update` already render the full dashboard as part of their own
stdout — after running one, display that stdout to the user instead of just
narrating a one-line confirmation.

#### Reading an item before starting work

`start` only renders the dashboard (one-line summaries). It does NOT surface the
item's actionable detail — `context`, `next_steps`, and `related_files` are what
you actually pick the work up from. So before beginning work on an item the user
names ("work on 4", "let's pick up the truncation item", etc.), run `show` on it
first and read the full record:

```bash
python3 ~/.claude/scripts/dev_status.py show <slug|N>
```

Then, with that context in hand, actually act on it — e.g. open the listed
`related_files`, re-read the cited code, and ground the next step in the stored
`next_steps`. Do not start writing or editing a task item from the dashboard's
one-line summary alone. The `start` call can happen in the same batch as the
`show`, or immediately after — the point is to have the full record loaded
before any work begins, not merely to have marked it in-progress.

If `show` returns no `context`/`next_steps`/`related_files` (empty fields), say
so and ask the user to fill them in before proceeding — don't fabricate a plan
from the summary title.

When writing to stored fields (`summary`, `context`, `next_steps`, `related_files[].note`)
and prose cross-references, use slugs for any item references — never raw hex IDs.

### Pending Items

When helping the user send an email/message that expects a reply, or take an
action that depends on someone else's response (e.g. requesting API/access
approval), offer explicitly — never add silently: "want me to track this as
a pending item?"

```bash
python3 ~/.claude/scripts/dev_status.py pending add '{"id": "<slug>", "description": "<what you are waiting on>", "kind": "<email|chat|approval>", "source_ref": {...}, "context": "<why>", "next_steps": ["..."]}'
```

Status moves one step at a time — `waiting_for_reply` → `reply_received` →
`resolved` — never jump straight to `resolved`. A reply arriving means it
needs a look, not that it's closed:

```bash
python3 ~/.claude/scripts/dev_status.py pending update <slug|N> '{"status": "reply_received"}'
python3 ~/.claude/scripts/dev_status.py pending update <slug|N> '{"status": "resolved", "outcome": "<what happened>"}'
```

Same proactive-capture discipline as the backlog section above: if you catch
yourself narrating a send-and-wait action as an aside instead of asking,
that phrasing IS the trigger — offer before you send, not after — and
re-scan your own draft response before sending it, the same pre-send check
used for backlog capture.

## Git

- Use conventional commits: `type(scope): description` — types: `feat`, `fix`, `refactor`, `chore`, `docs`, `test`, `perf`, `ci`

## Scripts

- Always use `#!/usr/bin/env <lang>` shebangs (e.g. `#!/usr/bin/env python3`, `#!/usr/bin/env zsh`)

## Python

### Tooling

Always use `uv`. Never use `pip`, `poetry`, or `virtualenv` directly.

```bash
uv sync                          # install / sync deps from pyproject.toml
uv add <package>                 # add a dependency
uv run python <script>           # run a script in the project venv
uv run pytest                    # run tests
```

Format and lint with `ruff`:

```bash
uv run ruff format .             # auto-format
uv run ruff check .              # lint
uv run ruff check --fix .        # lint + auto-fix
```

### Coding Standards

- **Python version**: 3.12+
- **Type hints**: required on all function signatures (args and return type)
- **Formatter**: `ruff format` (88-char line length)
- **Linter**: `ruff check` — fix all warnings before committing
- **Imports**: stdlib → third-party → local, one blank line between groups
- **No `Any`** unless genuinely unavoidable; prefer `object` or a Union
- **Prefer `pathlib.Path`** over `os.path` string manipulation
