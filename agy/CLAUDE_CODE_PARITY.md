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

## 3. Verification results (probed 2026-07-28, agy 1.1.8)

Resolved against `agy --help`, `agy help`, `agy agent`, and agy's own bundled
customization docs (`~/.gemini/antigravity-cli/builtin/skills/agy-customizations/
docs/{skills,hooks,rules}.md`). A live end-to-end invocation probe (`agy -p` →
"run the dashboard skill") was attempted but **blocked by quota** ("Individual
quota reached... Resets in 162h12m18s"), so skill discovery *from the new path*
is confirmed by install-state symlink resolution + the renamed-away stale dir
(no longer in the discovery path) rather than by a successful model-driven run.
Re-probe with a live invocation once quota resets to fully close it out.

- **No explicit user-typed skill invocation** (no slash-command or dedicated
  subcommand). `agy --help`/`agy help` list only `agent`(s)/`changelog`/`help`/
  `install`/`models`/`plugin`(s)/`update` — there is **no `skill` subcommand**
  and no flag for invoking a skill by name. `skills.md` describes **only**
  model-decision activation: "The primary agent reads this `description` to
  decide whether to activate the skill for a given user prompt." So for agy
  there is no Claude-Code-style `/dashboard` user trigger — the user-side
  mechanism, to the extent one exists, is just naming the workflow in plain
  language and relying on the model to match the skill's `description`. The
  ported skills already phrase their Triggers in those terms, so no change
  needed.
- **No SessionStart hook event type.** `hooks.md` lists exactly five events:
  `PreToolUse`, `PostToolUse`, `PreInvocation`, `PostInvocation`, `Stop`.
  There is no `SessionStart` (or `OnSessionStart`/`Startup`) event. The
  nearest, `PreInvocation`, fires before **every** model invocation, not once
  per session, so it can't cleanly drive a one-shot dashboard auto-render
  without a self-clearing guard (e.g. a flag file the hook checks then
  unlinks) — and that fires on the *first* invocation only by side effect,
  not by design. **Conclusion: not worth wiring** an auto-render hook for
  agy; `dashboard`'s agy version correctly makes no SessionStart claim.
- **MCP config format differs**: `~/.gemini/config/mcp_config.json` with a
  `serverUrl` key, replacing inline `~/.gemini/settings.json` declarations
  from the Gemini CLI era. Not touched by this port; this repo doesn't
  manage MCP server config for any harness today. (Unchanged from prior.)
- **No structured multi-choice prompt widget.** Nothing in `--help` (`only
  `--json-schema`, which constrains *print-mode output shape*, not
  interactive user prompts), `agy help`, the agent/plugin subcommands, or
  the customization docs exposes an AskUserQuestion-style multi-choice
  surface. **Confirmed: agy is in the Copilot/opencode tier** (plain-text
  questions with a stated recommendation), not the Claude Code tier. The
  ported skills' existing treatment (plain-text question, recommendation
  first) is correct and needs no change.

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
