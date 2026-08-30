# Pi → Claude Code parity notes

Goal: make Pi (`earendil-works/pi`, npm `@earendil-works/pi-coding-agent`,
formerly `badlogic/pi-mono`) feel like Claude Code for this workflow — same
shared instructions, same skills, same backlog/pending-items/git
conventions — as a 5th harness alongside Claude Code, Copilot, opencode,
and agy. Compiled 2026-08-30 from the docs bundled with the installed
package (`~/.npm-global/lib/node_modules/@earendil-works/pi-coding-agent/docs/`,
version 0.84.4 at time of writing) and from live probes against that same
install, not from search-engine summaries.

---

## 1. Confirmed facts (read the docs before changing any of this)

- **Global instructions file: `~/.pi/agent/AGENTS.md`, with `CLAUDE.md` read
  as a fallback name** (`docs/usage.md`'s Context Files section: "Pi loads
  `AGENTS.md` or `CLAUDE.md` at startup from: `~/.pi/agent/AGENTS.md` for
  global instructions..."). `links.toml` symlinks `claude/CLAUDE.md` to this
  path (as `AGENTS.md`, not `CLAUDE.md` — do not shadow `AGENTS.md` with a
  same-directory `CLAUDE.md`; when both are considered for a directory the
  doc names `AGENTS.md` first). No frontmatter support — always active,
  same as every other harness's shared-instructions file.
- **Prompt templates are the command layer** (`docs/prompt-templates.md`):
  Markdown files, filename becomes the command name (`review.md` → `/review`),
  frontmatter `description` (optional — first non-empty line is the
  fallback) and `argument-hint` (optional). Placeholder syntax (`$1`, `$@`/
  `$ARGUMENTS`, `${1:-default}`, `${@:N}`, `${@:N:L}`) matches opencode's
  exactly. Global discovery root: `~/.pi/agent/prompts/*.md`, non-recursive.
  This repo's `pi/prompts/*.md` are ported from `opencode/command/*.md` —
  the two harnesses' command-file conventions are close enough that porting
  is close to a straight copy, only differing where a body explicitly
  invoked an opencode-only mechanism (see §4).
- **Skills**: Pi implements the [Agent Skills
  standard](https://agentskills.io/specification) (`docs/skills.md`), and
  explicitly supports pointing at *another* harness's skill directory via
  the `skills` setting — its own docs give `~/.claude/skills` as the
  worked example. **This repo points Pi at `agy/skills/` instead of
  duplicating a `pi/skills/` tree** (`pi/settings.json`'s `skills` array —
  see §3). Skills register as `/skill:name` commands
  (`enableSkillCommands`, default `true`) and are also model-invoked from
  an `<available_skills>` XML block in the system prompt.
- **Extensions**: TypeScript modules, auto-discovered from
  `~/.pi/agent/extensions/*.ts` (global) or `.pi/extensions/*.ts`
  (project-local, only after project trust) — no `settings.json` entry
  needed once symlinked into the discovery root, unlike skills (see §3).
  Loaded via [jiti](https://github.com/unjs/jiti), so plain `.ts` works with
  no compile step. Full event list in `docs/extensions.md`.
- **No built-in permission system, no sub-agents, no plan mode**
  (`docs/usage.md`'s "Design Principles": "it intentionally does not
  include built-in MCP, sub-agents, permission popups, plan mode, to-dos,
  or background bash. You can build or install those workflows as
  extensions or packages"). This is the single biggest behavioral gap from
  opencode/Claude Code and shapes every prompt file that used to lean on
  opencode's `adversary` agent or its `permission.bash` config — see §4
  and §5.
- **Built-in tools**: `read`, `bash`, `powershell` (Windows), `edit`,
  `write`, `grep`, `find`, `ls` (`docs/usage.md`). No question/select tool
  among them — every prompt file that needs a multi-choice decision from
  the user states the options in plain text with a recommendation, the same
  convention Copilot/agy use, per CLAUDE.md's harnesses-without-a-widget
  rule.
- **Headless invocation**: `pi -p [--no-session] [--provider <name>]
  [--model <pattern>] "<prompt>"` (`docs/usage.md`'s CLI Reference).
  `--list-models [search]` checks whether a model id actually resolves
  before handing off to it (used by `backlog-item.md`'s model-handoff
  branch instead of guessing).

## 2. Prompts — ported (`pi/prompts/`)

All 8 files from `opencode/command/*.md` (`dashboard`, `grill-me`,
`make-skill`, `second-opinion`, `spec`, `standup`, `backlog-item`,
`to-tickets`) ported 2026-08-30. `second-opinion.md` is machine-generated
by `claude/scripts/gen_second_opinion.py` from `templates/second_opinion.md.tmpl`
— never hand-edit it; edit the template or `gen_second_opinion.py`'s
`HARNESS_TABLE` entry for `pi/prompts/second-opinion.md` and regenerate.
`dashboard.md`, `standup.md`, and `to-tickets.md` ported with no
opencode-specific rewording — their bodies were already harness-neutral.
`grill-me.md`, `spec.md`, `backlog-item.md`, and `make-skill.md` needed real
rewrites wherever the opencode original invoked a mechanism Pi doesn't
have:

- Any reference to opencode's native Task-tool subagent spawn (grill-me's
  `--auto` critique path, spec's audit-pass step) became a plain
  `second_opinion.py` critique-loop call — Pi has no sub-agent mechanism at
  all (§1), so there's no "prefer the native path, fall back to
  second_opinion.py" framing the way opencode's version has; it's the only
  path.
- Any reference to opencode's native skill tool (`skill({ name: "..." })`)
  became `/skill:name` — Pi's real, documented skill-invocation command
  (`docs/skills.md`'s "Skill Commands"), not a guess.
- `make-skill.md` was rewritten from scratch (not a straight port) since
  opencode's version is about authoring *opencode* skills at
  `opencode/skills/<name>/SKILL.md` — Pi has no such directory of its own
  (§3), so the Pi version documents authoring at `agy/skills/<name>/SKILL.md`
  instead, the file Pi actually reads.
- `backlog-item.md`'s "different/cheaper model" handoff branch uses Pi's
  confirmed `--list-models` and `pi -p --provider/--model` invocation
  (§1) instead of opencode's `opencode models` / `opencode run --auto -m`.
  No `--auto`-equivalent flag is needed for headless tool execution — Pi
  has no built-in permission-prompt system to bypass in the first place
  (§1); the only gate is whatever `permission-gate.ts` (§5) itself denies
  or blocks.

## 3. Skills — no duplication (`pi/settings.json` points at `agy/skills/`)

**No `pi/skills/` directory exists in this repo.** `pi/settings.json`
(copy-once like `claude/settings.json` — Pi rewrites parts of this file
live via `/settings`, `/model` Ctrl+S, etc., same detach risk that file's
comment warns about) sets:

```json
{
  "skills": ["/home/yanil/dotfiles/agy/skills"]
}
```

Confirmed live: `pi -p --skill /home/yanil/dotfiles/agy/skills
--no-context-files "list every skill available"` (provider `opencode-go`,
model `kimi-k2.6`) discovered all 8 real skill names (`backlog-item`,
`dashboard`, `grill-me`, `make-skill`, `second-opinion`, `spec`, `standup`,
`to-tickets`) with no duplication and no name-must-match-directory issue —
`docs/skills.md` explicitly documents that Pi, unlike the Agent Skills
standard it otherwise implements, does not require a skill's `name` to
match its parent directory, "because that requirement is suboptimal for
shared skill directories used across multiple agent harnesses." That's this
repo's exact use case.

`agy/skills/*/SKILL.md` bodies needed no Pi-specific rewording beyond what
was already true for agy/Copilot — they were already written in the
harness-neutral "the shared instructions file's ..." / plain-text-question
style every non-Claude harness shares.

## 4. Delegation mechanics — `/skill:name`

Documented in `docs/skills.md`'s "Skill Commands" section: every discovered
skill registers as a `/skill:<name>` command (`/skill:grill-me`,
`/skill:second-opinion`, `/skill:spec`), and arguments after the command are
appended to the skill content as `User: <args>`. The model can also load a
skill on its own once the topic matches the skill's `description` in the
`<available_skills>` system-prompt block ("How Skills Work"), without a
user typing the slash form at all. `pi/prompts/backlog-item.md`,
`grill-me.md`, and `spec.md` all delegate this way now — see §2.

## 5. Extensions (`pi/extensions/`)

Three extensions, all verified live end-to-end against a real Pi session
(provider `opencode-go`, model `kimi-k2.6`), not just read through:

### `guard-rails.ts`

Enforces safety and workflow guard rails:
- **`rm -rf` confirmation gate**: blocks recursive force deletions (`rm -rf`, `rm -fr`, `rm -r -f`, `--recursive --force`) unless confirmed via `ctx.ui.confirm()`. In headless `-p` mode (`!ctx.hasUI`), blocks outright.
- **`sudo` confirmation gate**: blocks privileged commands unless confirmed via `ctx.ui.confirm()`. In headless `-p` mode, blocks outright.
- **`git commit` main/master branch protection (worktree policy)**: resolves the target git working directory and branch. If attempting `git commit` directly on `main` or `master`, blocks with a guidance message enforcing feature branches or worktrees. Allowed on feature branches.
- **`write`/`edit` protected path protection**: blocks modifications to `.env` (and `.env.*`), `.git` internal files, and `node_modules`.
- **Toggle commands**: `/guard-rails-disable` and `/guard-rails-enable` for session-scoped override.

**Verified live**:
1. `rm -rf /tmp/test_guard_file.txt` in headless `-p` mode was blocked and preserved on disk.
2. `sudo id` in headless `-p` mode was blocked.
3. `git commit -m 'test commit'` on branch `main` in a test repo was blocked with house policy guidance. Switching to `feat-test` allowed the commit to succeed.
4. `write` tool calls to `/tmp/.env`, `/tmp/.git/config`, and `/tmp/node_modules/pkg/index.js` were all blocked by guard-rails, while safe file writes succeeded.

### `permission-gate.ts`

Replicates `opencode.jsonc`'s `permission.bash` allowlist — the same rule
set, re-expressed for Pi's `tool_call` event instead of a declarative
config Pi has no equivalent of (§1: no built-in permission system at all).
Only bash is gated, matching `opencode.jsonc`'s actual current scope (no
deny entries exist there today, only allow + a `"*": "ask"` default);
`opencode.jsonc`'s separate `external_directory` permission type has no
direct Pi analog and is out of scope.

On the "ask" tier (anything not on the allowlist), the extension checks
`ctx.hasUI` (`docs/extensions.md`'s ExtensionContext: `true` in TUI and RPC
modes, `false` in print mode `-p` and JSON mode) — `true` prompts via
`ctx.ui.confirm()`, `false` blocks outright since there's nothing to
confirm through. This corrects an assumption from planning: RPC mode
*does* have `ctx.hasUI === true` (it has its own extension-UI protocol,
`docs/rpc.md`), unlike `-p`/JSON mode — checking `ctx.hasUI` directly,
rather than hardcoding a list of "headless" mode names, is what makes this
correct regardless.

**Verified live**: an allowlisted command (`echo hello-from-allowlist`)
ran and printed its output normally; a non-allowlisted command
(`touch not-allowed-marker.txt`) run in headless `-p` mode was blocked —
the model's own turn reported "The command was blocked: **Blocked by
permission-gate (no UI to confirm through in this mode): touch
not-allowed-marker.txt**" and no file was created.

### `ruff-format-on-edit.ts`

Runs `uv run ruff format` + `uv run ruff check --fix` on any `.py` file the
`write`/`edit` tools touch, inside a uv/ruff project (detected by walking
up for `pyproject.toml`) — same mechanism as the Claude Code/Copilot/agy
hooks, and the same corrected event mechanics `opencode/plugin/ruff-format-on-edit.ts`
already found for opencode's analogous hook, independently re-confirmed
here for Pi's own event shapes (`docs/extensions.md`'s Tool Events):

- `tool_execution_end` does **not** carry the file path — only
  `{ toolCallId, toolName, result, isError }`.
- The path is only available on **`tool_execution_start`**, which carries
  `{ toolCallId, toolName, args }` — for `write` and `edit`, `args.path`.
- Implementation: stash `args.path` in a `Map` keyed by `toolCallId` on
  `tool_execution_start`; on the matching `tool_execution_end`
  (`isError === false`), look up the path, run ruff, delete the map entry.

**Verified live**: a scratch uv/ruff project with `def   add(a,b):` /
`return a+b` in `sample.py`; a real Pi edit adding one unrelated comment
line above the `def`; the file came back as `def add(a, b):` /
`return a + b` — ruff format ran automatically after the edit, not just
on the specific lines the model touched.

## 6. second-opinion / dev_status recap backend

`llm_backends.py`'s `BACKEND_PRIORITY` is `["agy", "pi", "opencode",
"copilot"]` — `pi` sits right after `agy`, ahead of `opencode`/`copilot`,
per explicit user preference during this port (not derived from any
technical ranking). `run_pi()` invokes `pi -p --no-session --provider
opencode-go --model <id> "<prompt>"` — `opencode-go` is hardcoded as the
provider since it's the only one confirmed authenticated on this machine
(`pi auth check --provider opencode-go --json` -> `ready`) and the one
every configured model-pool entry resolves through. `SECOND_OPINION_PI_MODEL`
/ `SECOND_OPINION_PI_MODEL_POOL` / `SECOND_OPINION_PI_TIMEOUT_SECONDS`
follow the same contract as the other three backends.

Unlike opencode's `adversary` agent (`"permission": "deny"`, forcing a
swapped-in model to return only prose), `run_pi()` passes no
tool-restriction flag — no equivalent restricted-permission invocation for
Pi has been built or verified. `_raise_on_emitted_tool_call()` is the only
backstop against a model leaking an attempted tool call as text; it cannot
catch Pi actually taking a real tool action instead of returning a
critique. Revisit if this ever becomes a real problem in practice —
`--tools`/`--no-tools`/`--exclude-tools` (§1) are the documented levers,
just not wired into `run_pi()` yet.

## 7. Config file location reference

| File | Global | Project |
|---|---|---|
| Main settings | `~/.pi/agent/settings.json` | `.pi/settings.json` |
| Instructions | `~/.pi/agent/AGENTS.md` (or `CLAUDE.md`) | `AGENTS.md` (or `CLAUDE.md`), walking up from cwd |
| Prompt templates | `~/.pi/agent/prompts/*.md` | `.pi/prompts/*.md` |
| Skills | `~/.pi/agent/skills/`, `~/.agents/skills/`, plus `settings.json`'s `skills` array | `.pi/skills/`, `.agents/skills/` |
| Extensions | `~/.pi/agent/extensions/*.ts` | `.pi/extensions/*.ts` |
| Sessions | `~/.pi/agent/sessions/` | — |
| Trust decisions | `~/.pi/agent/trust.json` | — |

Global and project settings merge (project overrides global on nested
keys), same pattern as Claude Code's `~/.claude/settings.json` +
`.claude/settings.json`.

## 8. Out of scope for this port

- **Keybindings / TUI customization** — no `pi/keybindings.json` equivalent
  of opencode's `tui.json` exists in this repo; nothing in the original
  plan named a specific rebind worth doing, and none has come up since.
- **A dashboard-on-`session_start` extension** — Pi supports the
  `session_start` event (§1, `docs/extensions.md`), so this is technically
  buildable, but it's a real engineering task, not config parity, and
  wasn't part of this port's scope (mirrors opencode's own deferred
  hooks→plugin dashboard port, `opencode/CLAUDE_CODE_PARITY.md` §5). Revisit
  as its own backlog item if resuming a `grill-me`/`backlog-item`
  mark-pending-execution flow manually (§2) becomes a real friction point.
- **A restricted-permission invocation for `run_pi()`** — see §6.

## Sources

- `~/.npm-global/lib/node_modules/@earendil-works/pi-coding-agent/docs/` —
  the full doc set bundled with the installed npm package, version 0.84.4:
  `usage.md`, `prompt-templates.md`, `skills.md`, `extensions.md`,
  `settings.md`, `security.md` in particular.
- `pi --help` (installed 0.84.4) — top-level flag reference, cross-checked
  against `docs/usage.md`'s CLI Reference.
- Live probes against the installed 0.84.4 build, provider `opencode-go`,
  model `kimi-k2.6`: skill discovery, `permission-gate.ts`'s allow/block
  paths, `ruff-format-on-edit.ts`'s auto-fix path — all in this doc's own
  sections above, not asserted separately.
