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
- other prefixes as appropriate for the project

Infer all fields from the current conversation.
Only include files actually relevant to picking up the work later.
Omit related_files if there is nothing meaningful to put in them (use []).

When the user pauses mid-task (says "let's stop here", "I need to step away",
"we'll come back to this", etc.), offer to save a backlog item before ending.

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
