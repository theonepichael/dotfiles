# CLAUDE.md

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

When work is complete on a backlog item, mark it done with the above.

When writing to stored fields (`summary`, `context`, `next_steps`, `related_files[].note`)
and prose cross-references, use slugs for any item references — never raw hex IDs.

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
