# agy (Antigravity CLI) → Claude Code parity notes

Goal: make agy (Google's Antigravity CLI, the May 2026 Go rewrite of Gemini
CLI, Gemini-backed) feel like Claude Code for this workflow — same shared
instructions, same skills, same backlog/pending-items/git conventions.
Compiled 2026-07-28 by reading agy's own bundled customization docs
(`~/.gemini/antigravity-cli/builtin/skills/agy-customizations/`) and the
official migration docs at antigravity.google/docs/cli/gcli-migration.

---

## 1. Confirmed facts (read the docs before changing any of this)

- **Global rules file (CLAUDE.md-equivalent): `~/.gemini/GEMINI.md`.**
  Official docs: "The agent automatically consults and enforces your global
  constraints located at `~/.gemini/GEMINI.md`." No frontmatter support —
  always active, unlike skills' progressive disclosure. `install.sh` symlinks
  `claude/CLAUDE.md` straight to this path, same pattern as
  `~/.copilot/copilot-instructions.md`.
- **Global skills path: `~/.gemini/antigravity-cli/skills/<name>/SKILL.md`.**
  This is the *current* path — it moved from a *legacy* `~/.gemini/skills/`
  path. Before this port, all 5 skills existed as untracked, stale copies at
  the legacy path (dated 2026-07-28, already out of sync with the current
  claude/copilot content) — those are superseded by this port and should be
  removed once the new symlinks are confirmed working (not automated here;
  `install.sh`'s `symlink` helper only touches its own destination paths).
- **Skill frontmatter**: only `name` + `description` required, confirmed via
  agy's own `agy-customizations` skill docs. No `allowed-tools` equivalent
  observed — agy's skill files here carry no such field, unlike Claude
  Code's granular list or Copilot's blanket `allowed-tools: shell`.
- **Reference-material subdirectory**: agy's own docs specify `references/`
  (not `ref/`, which is this repo's `claude`/`copilot` convention) as the
  supported subdirectory for bulky per-skill documentation.
- **Non-interactive print mode**: `agy -p '<prompt>'` (confirmed via
  `agy --help` on the installed 1.1.8 build).
- **Workspace (project-scoped) rules and skills** are a separate mechanism
  from the above — `AGENTS.md`/`GEMINI.md` files discovered by walking from
  cwd up to the repo root, and `.agents/skills/` for project-local skills.
  Not wired by this repo (mirrors this repo's stance on Claude Code project
  `CLAUDE.md` files — personal global tooling, not per-project).

## 2. Skills — ported (`agy/skills/`)

All 5 (`dashboard`, `grill-me`, `make-skill`, `second-opinion`, `standup`)
ported from the current `claude/commands/`/`copilot/skills/` content as of
2026-07-28, not from the stale legacy-path copies that predated this doc.
`grill-me`, `standup`, and `second-opinion` needed no agy-specific rewording
beyond dropping `allowed-tools` — their bodies were already harness-neutral
after the same-day consolidation pass that introduced "the shared
instructions file" phrasing. `dashboard` and `make-skill` got small
agy-specific adjustments (verified `-p` flag, `references/` vs `ref/`,
Trigger section reflecting agy's progressive-disclosure model instead of
Claude Code's model-invoked/user-invoked split or Copilot's pure
description-match).

## 3. Known gaps / not yet decided

- **Whether agy supports an explicit user-typed invocation** of a skill
  (something slash-command-like) separate from model-decision activation —
  the docs read so far only confirm "the model (or the user) explicitly
  decides to activate it," without spelling out the user-side mechanism.
  Verify empirically (e.g. does typing a skill's name in-conversation
  trigger it, or is there a dedicated command).
- **No session-start-hook equivalent confirmed.** `dashboard`'s Claude
  Code/Copilot versions both note a SessionStart hook auto-renders the
  dashboard; agy's version doesn't claim this, since no such wiring has been
  set up or confirmed possible here. agy's customization docs do list
  `hooks.json` as a supported customization type (`Lifecycle Event`
  scope) — revisit if this is wanted.
- **MCP config format differs**: `~/.gemini/config/mcp_config.json` with a
  `serverUrl` key, replacing inline `~/.gemini/settings.json` declarations
  from the Gemini CLI era. Not touched by this port; this repo doesn't
  manage MCP server config for any harness today.
- **Structured multi-choice prompt**: not confirmed to exist for agy. All
  ported skills treat agy like Copilot/opencode (plain-text questions with a
  stated recommendation) per CLAUDE.md's "Asking the user to choose"
  convention, pending confirmation either way.

## 4. Install state on this machine (versioning note)

Locally installed `agy` is version 1.1.8 (`agy changelog`). `~/.antigravity/`
does **not** exist on this machine — confirms the install is still on the
legacy `~/.gemini/` layout, not a fully-migrated `~/.antigravity/` one that
some third-party sources describe for newer installs. `agy inspect`, which a
couple of blog posts (not official docs) claimed as a diagnostic command,
does **not** exist in this build's `--help` output — don't trust secondhand
descriptions of agy's CLI surface without confirming against the actual
installed binary's `--help`/`--version`. Re-verify all of the above if/when
this machine's `agy` migrates to the `~/.antigravity/` layout.
