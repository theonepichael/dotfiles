# AGENTS.md — claude

<!-- Paired with CLAUDE.md symlink in the same directory -->

## Responsibilities & Boundary

Holds personal instruction overlays, output styles, and cross-harness instruction
composition sources. Harness-runtime Python scripts live in child `claude/scripts/`
(which carries its own local `AGENTS.md`).

## Hazards & Signposts

- `CORE_INSTRUCTIONS.md` is pulled, not hand-edited: authored upstream in
  `agent-toolkit` and synchronized via `claude/scripts/sync_from_agent_toolkit.py`.
  Direct edits will be overwritten.
- `global-instructions.md` is generated: composed from `CORE_INSTRUCTIONS.md` +
  `personal-overlay.md` by `claude/scripts/gen_core_instructions.py`.
- `claude/scripts/` — runtime scripts and tools. Read `claude/scripts/AGENTS.md` before
  editing scripts there.

## Local Conventions

When modifying prompt overlays, run `python3 claude/scripts/gen_core_instructions.py --check`
(or `--apply`) to verify composition integrity.
