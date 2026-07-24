# opencode → Claude Code parity notes

Goal: make opencode (running GLM-5.2) feel like Claude Code — same muscle
memory for keybinds, same instruction-file behavior, same slash commands,
same session-resume workflow. Compiled 2026-07-24 from opencode's official
docs (opencode.ai/docs/*).

This is a planning/reference doc, not applied config yet. Once decisions
below are made, the actual config files should live alongside this one
(`opencode/agent/`, `opencode/command/`, `opencode/plugin/`, `opencode/tui.json`,
`opencode/opencode.json`) mirroring the existing `claude/` and `copilot/`
directories in this repo, and get symlinked into `~/.config/opencode/` by
`install.sh` the same way `claude/settings.json` etc. are wired up.

---

## 1. Already matches — no work needed

| Claude Code behavior | opencode equivalent | Notes |
|---|---|---|
| `CLAUDE.md` project/global instructions | Reads `CLAUDE.md` (project) and `~/.claude/CLAUDE.md` (global) as **legacy fallbacks** automatically | Our existing global `~/.claude/CLAUDE.md` is already being picked up — nothing to port |
| `Esc` interrupts current turn | `session_interrupt: escape` (default) | Exact match |
| `Shift+Tab` cycles Default→AcceptEdits→Plan→Auto | `Tab` / `Shift+Tab` → `agent_cycle` / `agent_cycle_reverse`, cycles Build ↔ Plan | Same gesture, but only 2 states by default vs Claude Code's 4 — see §3 |
| `Ctrl+G` opens external editor | `editor_open: <leader>e` (default) | Same feature, different key — rebind if wanted, see §4 |
| `Ctrl+C` / `Ctrl+D` exit | `app_exit: ctrl+c,ctrl+d,<leader>q` (default) | Exact match |
| `Ctrl+V` paste (incl. images) | `input_paste: ctrl+v` (default) | Works with opencode-vision (see `meta-glm-vision-opencode`, done) |
| `claude --continue` / `--resume` | `opencode run --continue` / `-c`, `--session`/`-s <id>` | Same concept, different flag names |
| Subagent naming (Explore, general-purpose) | Built-in subagents: `general`, `explore`, `scout` | opencode's `explore` is close to Claude Code's `Explore` agent already |

---

## 2. Slash commands — DONE (already ported)

**Status (2026-07-24): already done**, ahead of this doc. All 5 commands
from `claude/commands/` (`grill-me`, `make-skill`, `second-opinion`,
`standup`, `status`) exist in `~/.config/opencode/commands/`, dated
2026-07-23 — a real adaptation pass, not a blind copy: dropped `name`
(redundant with filename), dropped `argument-hint` and `allowed-tools`
(neither exists in opencode's command frontmatter — tool access is
controlled per-agent instead, see §3), reworded `make-skill`'s description
for opencode/SKILL.md terminology, and correctly stripped the
SessionStart-hook caveat from `status.md` (opencode has no hooks
equivalent — see §5). No further action needed on this item.

Original notes kept below for reference on the format itself.

opencode's custom-command format is nearly identical to Claude Code's:

- **Location**: `~/.config/opencode/commands/` (global) or `.opencode/commands/` (project) — vs Claude Code's `~/.claude/commands/` / `.claude/commands/`
- **Format**: markdown file, filename → command name (`test.md` → `/test`)
- **Frontmatter**: `description`, `agent` (optional agent override), `model` (optional), `subtask` (force subagent)
- **Placeholders**: `$ARGUMENTS` / `$1`, `$2`, ... for positional args; `` !`shell cmd` `` for shell output injection; `@filepath` for file content — all the same syntax Claude Code commands use

**Action**: copy `~/dotfiles/claude/commands/*.md` → `~/dotfiles/opencode/command/*.md`, diff frontmatter fields (Claude Code's frontmatter schema differs slightly — needs a pass per file), fix anything that doesn't map (e.g. `allowed-tools` has no direct opencode equivalent — closest is per-agent `permission` config).

Custom commands can also live inline in `opencode.json` under a `"command"` key if a file-based approach isn't preferred.

---

## 3. Extra permission-mode states (Claude Code's 4-way cycle) — DECIDED: skip

**Decision (2026-07-24): not doing this.** Build/Plan's 2-state Tab cycle is
fine as-is — no need to replicate Claude Code's 4-mode Default/AcceptEdits/
Plan/Auto cycle. Section kept for reference in case this gets revisited.

Claude Code's Shift+Tab cycles four named modes: **Default → AcceptEdits →
Plan → Auto**. opencode ships only Build (full access) and Plan (edit/bash →
`ask`) as primary agents.

**Plan**: define additional primary agents in `opencode.json` (or as markdown
files in `~/.config/opencode/agents/`) with `mode: primary` and different
`permission` blocks:

```json
{
  "agent": {
    "auto": {
      "mode": "primary",
      "description": "Auto-accept edits, still ask before bash",
      "permission": { "edit": "allow", "bash": "ask" }
    },
    "yolo": {
      "mode": "primary",
      "description": "Bypass permissions entirely",
      "permission": { "*": "allow" }
    }
  }
}
```

`agent_cycle` (Tab) should then cycle through all `mode: primary` agents —
**needs verification once configured**: does Tab cycle built-ins + custom in
a stable, predictable order? Not confirmed from docs alone.

Permission value reference: `"allow"` / `"ask"` / `"deny"`, wildcard `*` for
global default, per-tool override (`read`, `edit`, `bash`, `grep`, `glob`,
`webfetch`, `websearch`, `external_directory`, `task`, `skill`). Bash also
supports pattern rules: `{"bash": {"*": "ask", "git *": "allow", "rm *": "deny"}}`.

CLI-level equivalent of "bypass everything for this run": `opencode run --auto`
(auto-approves anything not explicitly `deny`d — different semantics from
setting everything to `allow`, since explicit `deny` rules still apply).

---

## 4. Keybind conflicts to resolve

Full default keybind list pulled from `opencode.ai/docs/keybinds`. Verified
against Claude Code's actual shortcuts (corrected from an earlier draft of
this doc, which had `Ctrl+R`/`Ctrl+O` swapped):

- **Claude Code `Ctrl+O`** = transcript/log viewer (verbose tool output,
  thinking tokens). **Clean 1:1 in opencode** — `session_toggle_generic_tool_output`
  is unbound by default. **Done (2026-07-24)**: bound to `ctrl+o` in
  `opencode/tui.json`, symlinked into `~/.config/opencode/tui.json` via
  `install.sh`:
  ```json
  { "$schema": "https://opencode.ai/tui.json", "keybinds": { "session_toggle_generic_tool_output": "ctrl+o" } }
  ```

- **Claude Code `Ctrl+R`** = fuzzy search through past prompt history
  (`Ctrl+S` cycles scope: session/project/all). opencode's `Ctrl+R` is
  `session_rename` by default — genuinely different feature, and **no clean
  opencode equivalent for history *search* exists** (`history_previous`/
  `history_next` on Up/Down only step through recent entries, no fuzzy
  search; confirmed open feature request `anomalyco/opencode#5062`, not
  implemented in core).

  **Decision (2026-07-24): gap accepted, not pursuing.** Considered
  `opencode-history-search` (npm plugin) as a substitute, but it's an
  agent-invoked tool ("search my history for X" in chat), not an inline
  `Ctrl+R`-style popup — different enough interaction model that it's not
  worth installing just to approximate the muscle memory. Revisit if
  opencode ships native history search later.

- **Claude Code `Ctrl+G`** = open external editor. Already matches
  functionally: opencode's `editor_open` is bound to `<leader>e`. Same
  feature, different key — rebind `editor_open` to `ctrl+g` if the muscle
  memory matters more than the leader-key consistency.

- **Leader key** — **decided (2026-07-24): keep default `ctrl+x`, no
  remap needed.** No collision with tmux prefix (`C-a`, confirmed via
  `~/dotfiles/tmux/`). User wants the leader shortcuts as-is — nothing to
  configure, they already work out of the box. Reference list of what it
  gates (new
  session `<leader>n`, model list `<leader>m`, agent list `<leader>a`,
  sidebar toggle `<leader>b`, session export `<leader>x`, compact
  `<leader>c`, undo/redo `<leader>u`/`<leader>r`, etc.) — Claude Code has no
  leader-key paradigm at all, so this is pure upside: opencode-only
  shortcuts on top of everything else.

Config file: `tui.json` (global: `~/.config/opencode/tui.json`, project:
`.opencode/tui.json` — mirrors `opencode.json`'s global/project split).
Syntax: `"action": "key"`, comma-separated for multiple bindings, array
form also accepted, `"action": "none"` or `false` to disable a bind.

Other keybinds worth knowing about (not necessarily changes, just
awareness — full list in this doc's source research, available on request):
- `messages_undo` / `messages_redo`: `<leader>u` / `<leader>r` — opencode's
  loose analog to Claude Code's double-Esc rewind-to-edit-previous-message,
  though the trigger gesture differs
- `model_cycle_recent`: `f2` — quick model switch
- `session_child_cycle` / `session_child_first`: navigate subagent/session
  tree — no direct Claude Code equivalent (Claude Code doesn't expose this)

---

## 5. Hooks → plugin system (bigger lift, separate decision)

Claude Code hooks (`settings.json` — `SessionStart`, `PreToolUse`,
`PostToolUse`, etc., declarative JSON shelling out to scripts) have **no
declarative equivalent** in opencode. The analog is a TypeScript **plugin**
system:

- **Location**: `~/.config/opencode/plugin/*.ts` (global) or
  `.opencode/plugin/*.ts` (project) — or an npm package referenced in config
- **Shape**: an async function receiving `PluginInput` (SDK client, project
  info, project dir, git worktree root, server URL, a Bun shell for running
  commands), returning a hooks object
- **25+ lifecycle events** documented, including `chat.message`,
  `chat.params`, `permission.ask`, `tool.execute.before` /
  `tool.execute.after` — the opencode-vision plugin we already installed
  uses `experimental.chat.messages.transform` as one example of this system
- Two config surfaces exist: hand-written `.ts` files, or npm packages
  declared in `opencode.json`

**Not scoped in this doc**: whether to actually port the SessionStart
backlog-dashboard hook (`dev_status.py` dashboard render) into an opencode
plugin. That's a real engineering task (TypeScript, not JSON config) and
should be its own decision/backlog item once the keybind/command/agent
parity work above is done and evaluated.

---

## 6. Config file location reference

| File | Global | Project |
|---|---|---|
| Main config | `~/.config/opencode/opencode.json` | `opencode.json` in repo root |
| Keybinds/TUI | `~/.config/opencode/tui.json` | `.opencode/tui.json` |
| Commands | `~/.config/opencode/commands/` | `.opencode/commands/` |
| Agents | `~/.config/opencode/agents/` (or inline in `opencode.json`) | inline in project `opencode.json` |
| Plugins | `~/.config/opencode/plugin/` | `.opencode/plugin/` |
| Instructions | `~/.config/opencode/AGENTS.md` (or legacy `~/.claude/CLAUDE.md`) | `AGENTS.md` (or legacy `CLAUDE.md`) |

Global and project configs **merge** (project overrides global on
conflicting keys), same pattern as Claude Code's `~/.claude/settings.json` +
`.claude/settings.json`.

---

## 7. Open decisions (need user input before implementing)

1. ~~Which extra permission-mode agents to define beyond Build/Plan~~ —
   **decided: none, 2-state Build/Plan cycle is fine as-is (see §3)**
2. ~~Bind `session_toggle_generic_tool_output` → `ctrl+o` (no conflict,
   low-risk) — yes/no?~~ **Done (2026-07-24): yes — applied in
   opencode/tui.json, wired through install.sh** (see §4)
3. ~~Remap the leader key off `ctrl+x`~~ — **decided: no, keep default**
   (no tmux collision, user wants the shortcuts as-is)
4. ~~Scope of command porting~~ — **decided/done: all 5 commands already
   ported (see §2)**
5. ~~Whether to pursue the hooks→plugin port at all right now, or leave
   opencode without dashboard/backlog integration for the time being.~~
   **Decided (2026-07-24): defer the port** (real TypeScript lift, not
   config parity work) — captured as its own backlog item
   `meta-opencode-sessionstart-plugin` so it survives independently of this
   doc. Revisit when there's a real need or when the opencode plugin API
   stabilizes further.

## Sources

- https://opencode.ai/docs/keybinds/
- https://opencode.ai/docs/agents/
- https://opencode.ai/docs/permissions/
- https://opencode.ai/docs/config/
- https://opencode.ai/docs/rules/
- https://opencode.ai/docs/commands/
- https://opencode.ai/docs/cli/
