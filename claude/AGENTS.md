# AGENTS.md — claude

<!-- Paired with CLAUDE.md symlink in the same directory -->

## Responsibilities & Boundary

Holds personal instruction overlays, output styles, and cross-harness instruction
composition sources. Harness-runtime Python scripts live in child `claude/scripts/`
(which carries its own local `AGENTS.md`).

## Hazards & Signposts

- There is no `CORE_INSTRUCTIONS.md` here, on purpose: the shareable core is
  authored in agent-toolkit and read live from its checkout. Edit it there.
- `global-instructions.md` is generated and committed: composed from
  agent-toolkit's `claude/CORE_INSTRUCTIONS.md` + `personal-overlay.md` by
  `claude/scripts/gen_core_instructions.py`. It stays current without a
  manual step: `scripts/install-with-agent-toolkit.sh` reruns the composer
  after every install, and watchcommit's pre-tick hook
  (`scripts/watchcommit-pre-tick`) reruns it after each clean background
  pull, so a committed core edit lands within a couple of polls. Never
  hand-edit it; the next recompose overwrites it.
- The composed file records the agent-toolkit commit its core came from
  (`<!-- agent-toolkit source: <sha> -->`), and the composer only moves it
  forward along agent-toolkit's history. A machine whose agent-toolkit is
  behind skips the write instead of rolling the shared file back, so two
  machines never fight over it. Pull agent-toolkit there, or pass `--force`
  for a deliberate rollback.
- If two machines still commit different compositions in the same poll,
  the rebase is resolved by a git merge driver (`.gitattributes` names it;
  the composer registers its command in this repo's local git config on
  every write). It keeps whichever side's agent-toolkit source is forward,
  and never text-merges. When it can't decide (overlays differ, histories
  diverged, a hand-edited core), the path stays unmerged with no conflict
  markers: inspect `git show :2:claude/global-instructions.md` and `:3:`,
  keep the side you mean or abort and pull agent-toolkit -- don't just
  recompose and `git add`.
- `claude/scripts/` — runtime scripts and tools. Read `claude/scripts/AGENTS.md` before
  editing scripts there.

## Local Conventions

After editing `personal-overlay.md`, run `python3 claude/scripts/gen_core_instructions.py`
to recompose (`--check` reports staleness without writing).
`test_gen_core_instructions.py` fails if the committed overlay portion no
longer matches `personal-overlay.md`.
