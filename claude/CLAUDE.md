# User Preferences

## Git

- Use conventional commits: `type(scope): description` — types: `feat`, `fix`, `refactor`, `chore`, `docs`, `test`, `perf`, `ci`

## Scripts

- Always use `#!/usr/bin/env <lang>` shebangs (e.g. `#!/usr/bin/env python3`, `#!/usr/bin/env zsh`)

## Python

- Use **uv** for all project management — never pip, virtualenv, or poetry
- `uv add` / `uv remove` / `uv sync` / `uv run` for everything
- `pyproject.toml` only, no `requirements.txt`
- Use **ruff** for linting and formatting (`uv run ruff check .` / `uv run ruff format .`)
