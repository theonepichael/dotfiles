# AGENTS.md — this repo

Project-specific pointers for agents working *in* this repo. Not to be
confused with `claude/global-instructions.md`: that file is the user's
global, cross-project instructions — authored here, then symlinked out to
`~/.claude/CLAUDE.md`, `~/.copilot/copilot-instructions.md`,
`~/.gemini/GEMINI.md`, `~/.pi/agent/AGENTS.md` (see `links.toml`), and read
directly by opencode as its own global fallback.
General workflow conventions (backlog via `dev_status.py`, git
worktree-first policy, verification standards, etc.) are already loaded from
there — this file doesn't repeat them, only points at what's specific to
this repo.

## Standards

- **House style** (Python/shell conventions, type hints, CLI
  ergonomics, config/secrets, logging, testing tiers, formatting/linting) —
  `STYLE.md`.
- **Harness script interface inventory** — `INTERFACES.md`, generated from
  source. Fix the docstrings/argparse definitions when an interface
  changes, not the file directly. Regenerate with
  `python3 claude/scripts/gen_interfaces.py`, or check for staleness with
  `--check` (exit 1 if stale — this is what the test suite asserts).
- **User-facing documentation** — `README.md`.
- **Shared cross-harness coding-agent toolkit** — see `agent-toolkit`
  (contains shared skills, hooks, MCP configs, and multi-harness documentation).
- **Changelog of breaking/internal tooling changes** — `CHANGELOG.md`.

## Directory instructions — read before working in these

Each line names a hazard and where the details live. These are signposts,
not the rules themselves; the mechanics stay in the directory file.

- **`test/`** — every test runs under a sandboxed `HOME` with real
  subprocess calls and production-path writes blocked. Read
  `test/AGENTS.md` before writing one.
- **`claude/scripts/`** — standard library only, and the module docstrings
  are generated source, not commentary. See `claude/scripts/AGENTS.md`.

## The `AGENTS.md` + `CLAUDE.md` convention

Every directory carrying agent instructions holds a real `AGENTS.md` and a
`CLAUDE.md` **symlink** pointing at it. One source of truth, two names.

This is not stylistic. No single filename reaches every harness this repo
supports — measured 2026-08-30 against the installed binaries (see
`README.md` for the full table):

- Claude Code reads `CLAUDE.md` and ignores `AGENTS.md` entirely.
- opencode reads `AGENTS.md` and never `CLAUDE.md`.
- Pi prefers `AGENTS.md`, falling back to `CLAUDE.md`.
- Copilot reads either.

`AGENTS.md` is the canonical name because it is the one opencode requires;
`CLAUDE.md` is a symlink because it is the one Claude Code requires. Both
resolve to the same bytes, so they cannot drift.

**Adding a directory file later?** Same pair, no exceptions —
`test/test_agents_md_links.py` discovers directories rather than listing
them, so it covers a new one with no edit. Write the `AGENTS.md`, then
`ln -s AGENTS.md CLAUDE.md` beside it. The test asserts the link is
committed as a symlink (git mode `120000`) pointing at its own sibling.

Only Claude Code attaches a nested file automatically, when it touches a
file in that directory. Pi, opencode and Copilot load one only when started
with the working directory inside it; agy loads no project-level file at
all. That asymmetry is why the index above exists.

**Windows.** This repo installs on macOS and Linux/WSL only — WSL checkouts
live on the Linux side, where symlinks work normally. A Windows-native
checkout with `core.symlinks=false` would materialise each `CLAUDE.md` as a
regular file containing the single line `AGENTS.md`, and a harness there
would load that one line as the entire instruction set. That platform is
unsupported rather than guarded against.
