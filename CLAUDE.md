# CLAUDE.md — this repo

Project-specific pointers for agents working *in* this repo. Not to be
confused with `claude/CLAUDE.md`: that file is the user's global,
cross-project instructions — authored here, then symlinked out to
`~/.claude/CLAUDE.md`, `~/.copilot/copilot-instructions.md`, and
`~/.gemini/GEMINI.md` (see `links.toml`). General workflow conventions
(backlog via `dev_status.py`, git worktree-first policy, verification
standards, etc.) are already loaded from there — this file doesn't repeat
them, only points at what's specific to this repo.

## Standards

- **House style** (Python/shell conventions, type hints, CLI ergonomics,
  config/secrets, logging, testing tiers, formatting/linting) — `STYLE.md`.
- **Harness script interface inventory** — `INTERFACES.md`, generated from
  source. Fix the docstrings/argparse definitions when an interface
  changes, not the file directly. Regenerate with
  `python3 claude/scripts/gen_interfaces.py`, or check for staleness with
  `--check` (exit 1 if stale — this is what the test suite asserts).
- **User-facing command/behavior differences across harnesses** (Claude
  Code, Copilot, opencode, agy) — `README.md`.
