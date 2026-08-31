# dotfiles

Cross-platform setup for macOS and Linux/WSL. Clone and run `install.sh` to go from zero to working.

> **⚠ Upgrading an existing machine? Roll back with the OLD installer first.**
> The installer was rewritten from zsh to Python, and the run history moved
> from `~/.local/state/dotfiles/history.tsv` (TSV) to `history.jsonl` (JSON
> Lines). `install.py` deliberately ships **no** reader for the old format,
> so **before pulling this change**, run the old script once on every
> machine that has ever been provisioned:
>
> ```sh
> cd ~/dotfiles && git pull --dry-run   # confirm you haven't pulled yet
> ./install.sh --rollback               # flushes the bash-era history.tsv
> git pull                              # now take the Python installer
> ./install.sh --harness=...            # re-provision as usual
> ```
>
> Skip this and any `history.tsv` left on disk becomes dead weight: nothing
> reads it, and the mutations it recorded are no longer rollback-able except
> by hand (the old logic survives only in git history). Nothing is
> *damaged* by skipping it — symlinks and copies keep working — you just
> lose the undo for everything recorded before the switch.

## Quick start

```sh
git clone <repo-url> ~/dotfiles
cd ~/dotfiles
chmod +x install.sh
./install.sh --harness=claude                        # personal machine, Claude Code
./install.sh --profile=work --harness=copilot         # work machine, Copilot only
./install.sh --harness=claude,opencode                # both harnesses, personal
./install.sh --harness=claude,agy                     # Claude Code + Antigravity CLI
./install.sh --harness=claude,pi                      # Claude Code + Pi
./install.sh --dry-run --harness=claude               # preview only, nothing written
```

`./install.sh` is a ~20-line POSIX bootstrap: it finds a Python 3.12+ on
PATH and hands off to **`install.py`**, which is the actual installer. Run
`python3 install.py --harness=...` directly if you prefer; the flags and
behavior are identical. If no Python 3.12+ is found the bootstrap fails
loudly rather than falling back to an older interpreter.

The dotfile→destination table lives in **`links.toml`** at the repo root,
not in the installer — add, move, or retire a mapping by editing that file
(each entry can be gated on `harness`, `platform`, `wsl`, and
`profile_exclude`; the file's header documents the schema). Only the three
copy-once seed files (Claude Code's `settings.json`, opencode's
`opencode.jsonc`, Pi's `settings.json`) and the WSL-side VS Code path need
real code.

`--harness` is required on every run — there's no default. Pick any
combination of `claude`, `copilot`, `opencode`, `agy`, `pi` (comma-separated);
`--profile` (personal by default) controls machine-level concerns like
watchcommit and personal API-key setup, and never restricts which harness(es)
you can choose. See "Harness selection" and "Work profile" below.

Add `--dry-run` to preview any run (including `--rollback`) without writing
or removing anything — no packages installed, no files touched, no history
recorded. Detection of what's already on the machine still runs for real, so
the preview reflects actual state.

`~/.secrets` (gitignored, sourced by `.zshrc` if present) is available for any
API keys or tokens you want on the shell PATH — nothing in this repo requires
it today. watchcommit uses the `claude` CLI under whatever account you're
logged into (`claude login`), not a key from `~/.secrets`.

If GitHub isn't reachable from the target machine, transfer the repo as a git
bundle instead (full history, offline-clonable):

```sh
# on a machine that has the repo
git -C ~/dotfiles bundle create /path/to/usb/dotfiles.bundle --all

# on the target machine
git clone /path/to/usb/dotfiles.bundle ~/dotfiles
# later, if GitHub opens up: git remote set-url origin <repo-url>
```

After a real transfer completes, mark it so the SessionStart hook stops
reminding you the machine is behind:

```sh
python3 ~/.claude/scripts/dotfiles_sync_check.py mark
```

Without a mark, nothing is checked — the hook only speaks up once a baseline
exists and HEAD has since moved ahead of it.

## Harness selection

`--harness` picks which coding-agent harness(es) get installed and wired up
— comma-separated, at least one of `claude`, `copilot`, `opencode`, `agy`,
`pi`. No default; every run states its intent explicitly. Selecting fewer harnesses
on a later run doesn't uninstall the ones left out — this script is purely
additive, same as everything else it does. Use `--rollback` (which reverses
every recorded run, not just the most recent) or manual cleanup to actually
remove something.

- **`claude`** — installs Claude Code (`npm i -g @anthropic-ai/claude-code`)
  and its `~/.claude/*` wiring (`CLAUDE.md`, `commands/*.md`,
  `settings.json` with its SessionStart hooks). The commands are
  `backlog-item`, `dashboard`, `draft-voice`, `grill-me`, `make-skill`,
  `second-opinion`, `skill-map`, `spec`, `standup` and `to-tickets`; the
  other harnesses port all but `draft-voice` and `skill-map`.
- **`copilot`** — installs [GitHub Copilot CLI](https://github.com/github/copilot-cli)
  (`npm i -g @github/copilot`) and its wiring:
  - **Shared instructions file**: `claude/CLAUDE.md` is symlinked to *both*
    `~/.claude/CLAUDE.md` and `~/.copilot/copilot-instructions.md` — no
    separate Copilot-specific instructions file to maintain, since the
    backlog/pending-items workflow is already tool-agnostic prose.
  - **`copilot/hooks/session-start.json`**: a `sessionStart` hook sharing a
    subset of Claude Code's `SessionStart` work (dashboard render,
    pending-plan consume, dotfiles-drift and seed-drift checks — not the
    git-log/watchcommit lines). The hook schema's handler is bash-only as
    wired here, so `links.toml` links it on macOS/Linux only; it is
    deliberately not installed on Windows.
  - **`copilot/skills/<name>/SKILL.md`**: ports of the Claude Code commands
    as skills — `backlog-item`, `dashboard`, `grill-me`, `make-skill`,
    `second-opinion`, `spec`, `standup`, `to-tickets`. (`draft-voice` and
    `skill-map` are Claude Code only.) Copilot skills fire when their
    `description` frontmatter matches the conversation, and can also be
    invoked by typing `/<skill-name>` (e.g. `/dashboard`) — confirmed live
    on Copilot CLI 1.0.79 (see `copilot/CLAUDE_CODE_PARITY.md`).
    `second-opinion` and `grill-me` also drop `AskUserQuestion` (Copilot has
    no structured multi-choice prompt) in favor of plain conversational
    back-and-forth.
  - **`copilot/hooks/post-tool-use.json`**: a `postToolUse` hook that runs
    `ruff format` then `ruff check --fix` on any `.py` file Copilot creates
    or edits, scoped to the nearest ancestor holding a `pyproject.toml`.
    Bash-only like the `sessionStart` hook, so `links.toml` also links it on
    macOS/Linux only.
  - **`copilot/aliases.zsh`** (symlinked to `~/.copilot_aliases`, sourced by
    `.zshrc` only when present): `copilot-work` launches `copilot` with
    `python3` shell calls and read-only `git` commands pre-approved.
    Copilot's `--allow-tool` wildcard matching only works on single-word
    command stems (`git`, `gh` — confirmed via `copilot help permissions`),
    not full paths like `python3 ~/.claude/scripts/dev_status.py`, so this
    pre-approves `python3` broadly rather than scoping to just the shared
    scripts. Tighten this once Copilot ships richer prefix matching.
  - **Deliberately excluded**: Gmail/Calendar/Drive MCP servers are not
    configured under Copilot, per the work profile's
    no-personal-data-on-work-hardware rule.
- **`opencode`** — wires `~/.config/opencode/tui.json`, the user-typed
  commands (`backlog-item`, `dashboard`, `grill-me`, `make-skill`,
  `second-opinion`, `spec`, `standup`, `to-tickets`) plus three
  model-invoked skills (`grill-me`, `second-opinion`, `spec`),
  `opencode/plugin/ruff-format-on-edit.ts` (formats and lint-fixes edited
  Python), and
  `opencode.jsonc` (the bash permission policy, copy-once-and-report-drift
  same as Claude's `settings.json`): the shared workflow scripts
  (`dev_status.py`, `grill.py`, `second_opinion.py`, the drift/sync
  checks), read-only Git inspection, this repo's everyday `uv` commands
  (`uv sync`, `uv run pytest`, `uv run ruff check/format`), and a
  read-only system/process/generic-utility inspection tier (`ls`, `cat`,
  `grep`, `find`, `stat`, `df`, `du`, `jq`, `echo`, `systemctl status`,
  `lsof`, `ps`, `pgrep`, `ss`, etc. — state-observing, no destructive/
  network/process-control/commit side effect) are pre-approved; everything
  else falls through to the catch-all ask, so commits, pushes, destructive
  filesystem operations, network calls, and process control all require
  explicit approval. This includes common dev utilities with no documented
  need in this repo's workflow — `sleep`, `mkdir`, `cp`, `mv`, bare
  `pytest`/`ruff` (use the documented `uv run` variants), `git add`/
  `stash`/`branch` — and previously-permitted interactive conveniences
  will now prompt as a result. `git worktree*` is the highest-frequency
  instance: `git worktree add` is on this repo's own critical path for
  every new task (this file's worktree-first policy), so this friction is
  felt on the very next task, not just in theory — a future re-add
  requires updating this policy and the matching `_APPROVED_BASH_PATTERNS`
  literal in `test/test_install.py` together, in the same commit.

  The read-only-utility tier isn't uniformly side-effect-free: `find`
  (`-delete`/`-exec`), `sed -n` (GNU sed's `e` command), and `env`
  (`env FOO=bar <command>`) can each run an arbitrary command despite
  predating this policy's tightening — known, frozen gaps, not silently
  asserted safe, left alone because fixing them is a separate item.

  `install.py`'s `opencode_bypass_drift` extends this with a curated,
  install-time-enforced list of 15 stronger bypass patterns — `xargs`/
  `awk` plus `git --no-pager`, `uv`, `node -e`, `python3 -c/-m/-`,
  `npm install`/`npx`, `sqlite3`, `opencode run`, `copilot`, `nohup` — each
  either takes an arbitrary command as its own argument or broadens an
  otherwise-narrow, already-approved command into a wider category, not
  individually-risky commands worth pre-approving. This list is a
  snapshot of known bypass shapes, not a taxonomy: a future bypass-shaped
  tool not on it (`perl -e`, `ssh`, etc.) isn't automatically caught by
  either this check or the seed's policy-compliance test — only the next
  policy review catches that, same as any other undocumented addition.
  `opencode` is never installed on a work machine at all — see "Work
  profile" below.
- **`agy`** — wires config for [Antigravity CLI](https://antigravity.google/docs/cli)
  (Google's Gemini-backed CLI); the binary itself is assumed already
  installed, same as `opencode` — this script only wires its config:
  - **Shared instructions file**: `claude/CLAUDE.md` is symlinked to
    `~/.gemini/GEMINI.md`, agy's global-rules path. agy reads no `CLAUDE.md`
    anywhere, so it needs a real link of its own.
  - **`agy/skills/<name>/SKILL.md`**: ports of `backlog-item`, `dashboard`,
    `grill-me`, `make-skill`, `second-opinion`, `spec`, `standup` and
    `to-tickets`, symlinked into
    `~/.gemini/antigravity-cli/skills/<name>/SKILL.md` — agy's current
    global skills path. agy skills fire on description match and, as of agy
    1.1.12, also expand typed `/<skill-name>` invocations (probed live; see
    `agy/CLAUDE_CODE_PARITY.md`). agy has no `AskUserQuestion`-style prompt,
    so the ported skills use plain conversational back-and-forth for
    judgment calls.
  - **`agy/hooks.json`** plus **`agy/hooks/agy-elapsed.js`**: a `PostToolUse`
    hook that runs `ruff format` and `ruff check --fix` on edited Python,
    and the status-line elapsed-time script.
  - agy has no `SessionStart`-equivalent hook event, so there's no
    auto-render-dashboard wiring for it, unlike Claude Code and Copilot.
  - agy loads **no project-level instruction file at all** — not
    `GEMINI.md`, not `AGENTS.md`, not `CLAUDE.md` (probed against agy 1.1.22,
    2026-08-30). Only the global `~/.gemini/GEMINI.md` link reaches it.
  - See `agy/CLAUDE_CODE_PARITY.md` for the full verification notes.
- **`pi`** — wires config for [Pi](https://www.npmjs.com/package/@earendil-works/pi-coding-agent)
  (`@earendil-works/pi-coding-agent`); the binary itself is assumed already
  installed, same as `opencode`/`agy` — this script only wires its config:
  - **Shared instructions file**: `claude/CLAUDE.md` is symlinked to
    `~/.pi/agent/AGENTS.md` — Pi looks specifically under `~/.pi/agent/`
    for its global instructions, with no fallback to `~/.claude/CLAUDE.md`,
    so it needs a real link of its own — same as agy's `GEMINI.md` link and
    opencode's `AGENTS.md` link.
  - **`pi/prompts/*.md`** (`backlog-item`, `dashboard`, `grill-me`,
    `make-skill`, `second-opinion`, `spec`, `standup`, `to-tickets`),
    symlinked into `~/.pi/agent/prompts/*.md` — Pi's command layer
    ("prompt templates"), invoked as `/name`.
  - **`pi/extensions/*.ts`**, symlinked into `~/.pi/agent/extensions/*.ts`.
    Three shape the session itself — `permission-gate.ts`,
    `ruff-format-on-edit.ts`, and `guard-rails.ts` (rm -rf/sudo confirmation
    gates, blocks `git commit` on `main`/`master`, protects `.env`/`.git`/
    `node_modules` from writes). `custom-footer.ts` and
    `philosophy-header.ts` adjust the UI. The rest expose this repo's
    workflow scripts as native Pi tools: `dev-status-tool.ts`,
    `grill-tool.ts`, `second-opinion-tool.ts`, `standup-tool.ts`,
    `to-tickets-tool.ts` and `vitals-promotion-tool.ts` wrap
    `dev_status.py`, `grill.py`, `second_opinion.py`, `standup.py`,
    `to_tickets_runner.py` and `vitals_promotion.py` respectively.
    `question-tool.ts` supplies the structured multi-choice prompt Pi
    otherwise lacks, and `delegate-tool.ts` hands a task off to another
    harness's executor. Every extension needs its own `links.toml` entry or
    it is never installed — `test/test_pi_extension_links.py` enforces that.
  - **A TypeScript toolchain unique to this directory** — `bun`, `oxlint`,
    `prettier` and `tsc`, with specs under `pi/test/`. Run from `pi/` via
    `bun run test|typecheck|lint|format:check`; `test/test_pi_ts_checks.py`
    drives all four from the Python suite, and skips them when
    `pi/node_modules` is missing. See `pi/AGENTS.md`.
  - **No `pi/skills/` directory** — `pi/settings.json`'s `skills` array
    points straight at `agy/skills/` instead of duplicating a skills tree.
  - **`pi/settings.json`**, copy-once-and-report-drift same as Claude's
    `settings.json` (no bash permission allowlist to seed — that lives in
    `permission-gate.ts` instead).
  - Pi has no structured multi-choice prompt either, so its ported skills
    use plain conversational back-and-forth for judgment calls, same as
    Copilot/agy.
  - See `pi/CLAUDE_CODE_PARITY.md` for the full verification notes.

The shared `~/.claude/scripts/*.py` (dev_status, grill, second_opinion,
standup, etc.) are symlinked regardless of which harness(es) are selected —
all five harnesses' skills/hooks call these same paths.

### Instruction files: which harness reads what

No single filename reaches all five. Measured 2026-08-30 against the
versions named, by running each harness non-interactively in a fixture git
repo holding an `AGENTS.md` and a `CLAUDE.md` at both the root and a
subdirectory, each with a distinct token, and asking which tokens were in
context with tools forbidden:

| Harness | Version | Filenames it loads | Where it looks |
|---|---|---|---|
| Claude Code | 2.1.251 | `CLAUDE.md` only — `AGENTS.md` is ignored even when no `CLAUDE.md` exists | cwd, plus a nested file **attached automatically** when it touches a file in that directory |
| Pi | 0.84.4 | `AGENTS.md` preferred, `CLAUDE.md` when `AGENTS.md` is absent (`AGENTS.override.md` beats both) | cwd and its parents, at startup only |
| opencode | 1.18.25 | project: `AGENTS.md` only, a project `CLAUDE.md` is never loaded; global: `~/.claude/CLAUDE.md` | cwd up to the project root, plus the global fallback above |
| Copilot | 1.0.80 | `CLAUDE.md`, `GEMINI.md` and `AGENTS.md` — all of them | git root and cwd only |
| agy | 1.1.22 | none at project level | global `~/.gemini/GEMINI.md` only |

The opencode and Claude Code rows are each corroborated in the binary
itself, and the naming convention below depends only on those two. The
Copilot and agy rows rest on a single probe apiece; nothing depends on them.

This is why every directory in this repo that carries agent instructions
holds a real `AGENTS.md` plus a `CLAUDE.md` symlink pointing at it —
`AGENTS.md` is the only name opencode reads, `CLAUDE.md` the only name
Claude Code reads, and a symlink makes them the same bytes. See `AGENTS.md`
at the repo root.

Note the second column of consequences: only Claude Code picks up a nested
file on its own. Running any other harness from the repo root leaves the
per-directory files unread, which is why the root `AGENTS.md` carries a
one-line index of each directory's main hazard.

The six `second-opinion` skill/command copies listed above are generated
from one canonical body template (`templates/second_opinion.md.tmpl`) by
`claude/scripts/gen_second_opinion.py` — do not hand-edit them; edit the
template (shared wording) or the script's per-harness parameter table
(harness-specific wording), then regenerate.

`second_opinion.py review --model-index N` rotates the critique model across
a per-machine pool configured via `SECOND_OPINION_AGY_MODEL_POOL` /
`_PI_MODEL_POOL` / `_OPENCODE_MODEL_POOL` / `_COPILOT_MODEL_POOL` (agy, pi,
opencode, and copilot all share the same indexed-pool contract). An explicit
`--model-index` picks the pool entry for that call even when a single-model override
(`SECOND_OPINION_<BACKEND>_MODEL`) is also set; without it, the override (or
the backend default) applies. **Breaking:** an explicit `--model-index` is now
a hard error if the selected backend's pool is unset/empty or the index is
out of range — it no longer silently falls back to the default model.

## Work profile

`--profile=work` controls machine-level concerns — with one exception
(opencode) it doesn't restrict which harness(es) you can choose
(`--profile=work --harness=claude` is honored exactly as stated):

- **watchcommit is excluded entirely** — no binary, no agent. It auto-pushes
  to a personal remote under your personal Claude account login; that stays
  off work hardware. Commit manually there.
- **opencode is excluded entirely, full stop** — `--profile=work
  --harness=opencode` is rejected outright at argument-parsing time, not
  just tightened. No `opencode.jsonc`, no commands, no `tui.json`.
- Claude settings are seeded from `claude/settings.work.json` — same hooks,
  but no `skipDangerousModePermissionPrompt` and no model pin.
- `~/.secrets` is still sourced if present, for work-issued tokens only.
- A profile marker is written (`~/.local/state/dotfiles/profile`); later runs
  with `--profile=personal` (the default) on that machine refuse unless
  `--force` is passed.

## Failures, skips, and rollback

The installer never aborts on a failed step. Anything that can't run (blocked
curl, offline apt, missing sudo) is skipped and listed in a loud end-of-run
summary with the reason; exit code is 1 if anything was skipped. Output is
colorized when stdout is a terminal — green for what was done, yellow for
skips and drift, red for hard errors — and plain when piped, when
`NO_COLOR` is set, or under `TERM=dumb`.

Every file mutation (symlink created, file backed up, file copied) is recorded
in `~/.local/state/dotfiles/history.jsonl` (JSON Lines, one object per
mutation), appended to on every run rather than overwritten — so `--rollback`
reverses **every** run recorded there, not just the most recent one. See the
migration note at the top of this file if you have a pre-Python
`history.tsv` on the machine: it is not read by `install.py`. If you've run
the installer several times (wrong profile, experimenting, whatever) and want
back to a clean slate:

```sh
./install.sh --rollback                          # reverses every recorded run's file mutations
./install.sh --profile=work --harness=claude      # then re-run correctly
```

A full rollback deletes the history file once everything's undone, so the
next run starts fresh rather than carrying forward already-reversed entries.

Rollback also skips-and-reports rather than aborting when something doesn't
match what it expected — e.g. a symlink it created now points somewhere else
(something else has since claimed that path) or a backup file is missing
(already restored, or removed outside `install.sh`). Those get logged as
`SKIPPED` lines and a summary count at the end, same as the main install
flow; everything else still gets rolled back.

Packages are never uninstalled by rollback — they're identical across
profiles, so a wrong-profile run's real footprint is entirely file-level.

For a true blank-slate undo — not the pre-dotfiles originals restored, but
none of it, dotfiles or prior config, left behind at all — add `--wipe`:

```sh
./install.sh --rollback --wipe        # full rollback to a blank slate: originals discarded, nvim/watchcommit state swept
```

`--wipe` deletes `.bak` backups instead of restoring them, and additionally
sweeps state the installer creates but never records in the manifest: nvim's
runtime directories (`~/.local/share/nvim`, `~/.local/state/nvim`,
`~/.cache/nvim`) and, on Linux, the watchcommit systemd `--user` service
(disabled and stopped). Packages are still never touched. The macOS
watchcommit launchd agent, Rectangle preferences, and the Caps Lock→Escape
remap are deliberately out of scope — none of them have a clean
filesystem-delete equivalent.

## Auditing the symlinks (`--check-links`)

`--check-links` compares the live symlinks against `links.toml` and prints
what it finds. It is strictly read-only — nothing is created, removed, or
repointed — so it is safe to run at any time:

```sh
./install.sh --check-links                     # audit every harness's entries
./install.sh --check-links --harness=claude    # scope it to one harness
```

This covers most of a gap neither of the flags above does. `--rollback` only
inspects destinations the history recorded, and only asks whether the link's
target string still matches what was recorded; `--depart` compares against a
snapshot taken at install time. Neither notices that a link's repo-side
source was deleted or renamed — and a plain re-run does not either, since the
installer never checks that a source exists before linking to it (Unix
happily creates a dangling symlink). The one exception is a destination
`links.toml` has stopped producing entirely: a plain run now cleans that up
automatically (see **orphaned** below) instead of only `--check-links`
reporting it.

Four buckets are reported:

- **broken-source** — the link points exactly where `links.toml` says, but
  that file no longer exists in the repo. A dangling symlink.
- **wrong-target** — the destination is a symlink, but to something other
  than its `links.toml` source (typically a source renamed without the live
  link being updated).
- **not-a-symlink** — a real file or directory sits where a link belongs.
  The next install run would back it up and replace it.
- **orphaned** — a symlink an earlier run recorded in the history that no
  `links.toml` entry produces anymore, still sitting on disk. A plain
  install run (not just `--check-links`) now removes these automatically —
  the symlink, its manifest entry, and its now-possibly-empty parent
  directory — and prints each removal; `--check-links` still only reports
  them, changing nothing, since it is read-only by design.

`--harness` and `--profile` scope which entries are considered; with neither,
every harness's entries are checked. Widening cannot produce false positives,
because every bucket requires the destination to already exist on disk — an
entry for a harness this machine never provisioned has nothing to report on.
Destinations gated off by platform, WSL, or profile are still exempt from the
orphan check, since a gated-off entry has not been removed from `links.toml`.

**Running it from a worktree.** The audit compares against the checkout it was
launched from, so from a worktree every live link legitimately points at the
main checkout instead. Rather than reporting all of them as wrong targets,
links resolving to the *same file in another checkout of this repo* are
collapsed into one informational note and excluded from the exit code:

```
note: 28 link(s) point into /home/you/dotfiles rather than this checkout
(/home/you/dotfiles-my-branch) — you are running from a worktree, so those
entries were not audited. Re-run --check-links from that checkout to include
them.
```

A link into another checkout that is *dangling* is still reported as
`broken-source` — a dead link is a real problem whichever checkout it aims at.

**Exit codes.** **0** — nothing wrong. **1** — at least one finding.
**2** — `links.toml` itself could not be read, or the flag was combined with
another one (only `--harness` and `--profile` are allowed alongside it).

## Departure mode (`--depart`)

`--depart` removes or restores everything a **future** install run owns on
this machine — files, symlinks, packages, runtimes, and services — leaving
no local, user-facing trace that `install.sh` was ever run. It's scoped to
Ubuntu/WSL (apt) and Fedora (dnf); it does nothing on macOS.

```sh
./install.sh --depart              # preflight + interactive DEPART confirmation
./install.sh --depart --dry-run    # preview only, never prompts, never mutates
./install.sh --depart --yes        # skip the confirmation prompt
```

**Future installs only.** Departure reasons entirely from a baseline
snapshot captured automatically at the *start* of a normal install run
(before any package, symlink, or service mutation happens) — it does not
and cannot recover a machine set up before this feature existed, and it
never reads `history.jsonl` (that log stays exactly what it always was: the
input to `--rollback`, untouched by `--depart`). A machine with no recorded
baseline refuses immediately (exit 2) rather than guessing.

**Not forensic erasure.** This is local-footprint cleanup — installed
files, symlinks, packages, runtimes, and services — not a guarantee against
shell history, package-manager logs, or any record outside what
`install.sh` itself created. If you need an actual guaranteed-pristine
reset, see [Nuclear reset (WSL)](#nuclear-reset-wsl) below; `--depart`
deliberately doesn't attempt that.

**Conservative by design.** Every real run prints a full preflight report
before doing anything, grouping every tracked item into four buckets:

- **owned** — this installer introduced it (or modified it, like an
  appended-to `.zshrc`); safe to remove or restore, and the only bucket
  `--depart` ever mutates.
- **preserved** — never touched by this installer at all; left alone.
- **drifted** — baseline is known, but something about live state doesn't
  match what departure expects (edited in a way that isn't a clean append,
  removed outside the installer, etc.); reported, never guessed at.
- **unresolved** — baseline capture failed, a referenced blob is missing, or
  live state can't be read; reported, never treated as license to act.

A real (non-`--dry-run`) run requires typing the exact token `DEPART` at a
prompt, unless `--yes` is passed; a non-interactive real run without
`--yes` refuses (exit 2). Partial completion is retried safely — a crash or
interrupted run picks back up via its own ledger
(`~/.local/state/dotfiles/departure.jsonl`) and never re-attempts an
already-completed action. An advisory lock
(`~/.local/state/dotfiles/departure.lock`) prevents two `--depart` runs
from racing each other, and self-recovers from a stale lock left by a
crashed process (checked by PID *and* process start time, so a reused PID
after a crash doesn't falsely read as still-live).

Exit codes: **0** — fully clean, nothing left. **1** — attempted, but some
item(s) remain unresolved or drifted (re-run `--depart` to retry). **2** —
refused outright: no baseline, a wrong/EOF confirmation token, or another
`--depart` already running.

Package removal never runs a broad `apt-get autoremove`/`purge` — only the
specific package(s) a tracked transaction introduced, and only when a
reverse-dependency probe (`apt-cache rdepends` / `dnf repoquery
--whatrequires`) confirms nothing else installed still needs it. A package
this installer *upgraded* (rather than freshly installed) is downgraded
back toward its original version instead of removed.

**WSL-only: Windows-side VS Code settings.** Under WSL with a Windows-side
`code` CLI on PATH, `--depart` also tracks the Windows-side
`settings.json`/`keybindings.json` this installer seeded (and their
`.bak` siblings, if `--reseed` ever created one) as **owned**. A preflight
`owned`/`remove` for one of these files can still land as **unresolved** at
execution time if native Windows VS Code is running (or its status can't be
verified) — it's never removed out from under a running editor. Close VS
Code first to avoid a second `--depart` re-run.

## Nuclear reset (WSL)

`--depart` cleans up what this installer put on the machine. It is
**not** a guarantee of a pristine system — it doesn't know about, and
can't undo, anything you or another tool did outside of it. The only
mechanism that's an actual guarantee rather than best-effort is
unregistering the whole WSL distro and recreating it from a stock image.
This is genuinely destructive and entirely separate from `install.sh
--depart` — nothing in this repo triggers it automatically.

**This deletes the entire Linux filesystem for the distro, irreversibly.**
Anything not backed up elsewhere — SSH keys, git repos with unpushed
commits, shell history, dotfiles you edited locally and never committed —
is gone. Back up first:

```powershell
# From Windows PowerShell, back up anything you care about first, e.g.:
wsl -d Ubuntu -- tar czf /mnt/c/Users/<you>/wsl-backup.tar.gz -C /home/<you> .
```

Then, from Windows PowerShell (not from inside WSL — you can't unregister
the distro you're currently running commands in):

```powershell
wsl --list --verbose              # confirm the exact distro name (case-sensitive)
wsl --unregister <DistroName>     # irreversibly deletes it — no confirmation prompt
wsl --install -d <DistroName>     # recreate from the same stock image (e.g. Ubuntu, Ubuntu-24.04)
```

After `wsl --install` completes and you've created the new Linux user
account, `install.sh` starts from a genuinely blank slate — no leftover
history file, no baseline, nothing for `--depart` to even refuse cleanly
against, because there's nothing there at all.

## What's included

| File | Destination |
|------|-------------|
| `vim/.vimrc` | `~/.vimrc` |
| `nvim/` | `~/.config/nvim` |
| `zsh/.zshrc` | `~/.zshrc` |
| `zsh/.zprofile` | `~/.zprofile` (macOS only) |
| `zsh/.common_shell_aliases` | `~/.common_shell_aliases` |
| `shell/.poshtheme.omp.json` | `~/.poshtheme.omp.json` |
| `karabiner/karabiner.json` | `~/.config/karabiner/karabiner.json` (macOS only) |
| `tmux/.tmux.conf` | `~/.tmux.conf` |
| `vscode/settings.json` | macOS: `~/Library/Application Support/Code/User/settings.json`; WSL: Windows-side `AppData/Roaming/Code/User/settings.json` (derived from the `code` CLI on PATH); native Linux: `~/.config/Code/User/settings.json` |
| `vscode/keybindings.json` | same per-OS destination as `settings.json`, `keybindings.json` |
| `claude/CLAUDE.md` | `~/.claude/CLAUDE.md` and, with `--harness=copilot`, also `~/.copilot/copilot-instructions.md` |
| `copilot/aliases.zsh` | `~/.copilot_aliases` (only with `--harness=copilot`) |
| `opencode/opencode.jsonc` | `~/.config/opencode/opencode.jsonc` (only with `--harness=opencode`; never on `--profile=work`) |
| `pi/settings.json` | `~/.pi/agent/settings.json` (only with `--harness=pi`) |
| `scripts/watchcommit.py` | `~/.local/bin/watchcommit` |
| `launchd/com.user.watchcommit.plist` | `~/Library/LaunchAgents/com.user.watchcommit.plist` (macOS only) |
| `systemd/watchcommit.service` | `~/.config/systemd/user/watchcommit.service` (Linux/WSL only) |

Everything is symlinked — edits in `~/dotfiles` take effect immediately. The
full, authoritative table is `links.toml`; the rows above are the
highlights. Four things are copied instead of symlinked:
`claude/settings.json` (and its `.work` variant), `opencode/opencode.jsonc`
(no `.work` variant — `--profile=work --harness=opencode` is rejected
outright, so only one variant of that seed exists), and `pi/settings.json`
(also no `.work` variant — pi has no work-profile restriction, but nothing
in its settings needs tightening for work hardware either) are copy-once
seeds, because each tool rewrites its file in place once live; the WSL-side
VS Code `settings.json`/`keybindings.json` are copied for an unrelated
reason — Windows can't read a WSL-side symlink through `DrvFs`.

Use `--adopt --harness=...` to pull all drifted selected copy-once files back
into the repo. Adoption requires each repo seed to be tracked and clean, makes
no `.bak` or history entry, and leaves the seed as an unstaged Git change to
commit. Missing live files are ignored; empty or unreadable files are skipped.
For opencode, malformed JSONC and live `xargs *`/`awk *` allowlist bypasses are
refused rather than written back. `--adopt` cannot be combined with
`--reseed`, `--rollback`, `--depart`, or `--check-links`.

## The installer does

### Both platforms
1. Installs packages: tmux, zoxide, eza, bat, ripgrep, lsd, ncdu, tldr, oh-my-posh, neovim, fd, uv, ruff
2. Installs NVM and Node/npm — only if `claude` and/or `copilot` is in `--harness`
   (`opencode`, `agy`, and `pi` all manage their own runtime separately, not
   installed by this script)
3. Installs the harness(es) named in `--harness` (`claude`: Claude Code via
   `npm i -g @anthropic-ai/claude-code`; `copilot`: GitHub Copilot CLI via
   `npm i -g @github/copilot`; `opencode`/`agy`/`pi`: assumed already
   installed, this script only wires their config) and their
   `~/.claude`/`~/.copilot`/`~/.config/opencode`/`~/.pi/agent` wiring — see
   "Harness selection" above
4. Symlinks every applicable `links.toml` entry (backs up any existing
   non-symlink files to `*.bak`)
5. Seeds `~/.claude/settings.json` (copy-once — if it already exists, drift from
   the repo seed is reported in the summary, never overwritten unless
   `--reseed` is passed) — only when `claude` is selected
6. Seeds `~/.config/opencode/opencode.jsonc` (the bash permission allowlist,
   profile-specific) the same copy-once way — only when `opencode` is selected;
   `--adopt` reverses the direction for intentional live edits
7. Seeds `~/.pi/agent/settings.json` the same copy-once way — only when `pi`
   is selected
8. Installs vim-plug (if missing)
9. Bootstraps Neovim plugins (`lazy.nvim` sync) if `nvim` on PATH is >=0.11

### macOS only
- Installs Homebrew (if missing) — supports both Apple Silicon (`/opt/homebrew`) and Intel (`/usr/local`)
- Installs casks: Karabiner-Elements, Rectangle, Ghostty, VS Code, AltTab, JetBrainsMono Nerd Font
- Symlinks macOS-specific configs (`.zprofile`, karabiner, launchd)
- Imports Rectangle preferences
- Sets Caps Lock → Escape via macOS keyboard modifier mapping
- Loads the watchcommit launchd agent

### Linux/WSL only
- Installs packages via `apt` (Debian/Ubuntu) or `dnf` (Fedora)
- Creates `~/.local/bin/bat` shim (Ubuntu ships bat as `batcat`)
- Installs uv via astral.sh if not present
- Installs oh-my-posh to `~/.local/bin` via official installer
- Installs JetBrainsMono Nerd Font (pinned version, see below) to
  `~/.local/share/fonts/JetBrainsMonoNerdFont`, then `fc-cache -f`
- Enables and starts the watchcommit systemd `--user` service, and runs
  `loginctl enable-linger` so it keeps running after you close the last
  WSL/SSH session (skipped with a note if `systemd --user` isn't available —
  e.g. WSL without `systemd=true` in `/etc/wsl.conf`)

### Nerd Font versioning
The JetBrainsMono Nerd Font is pinned to a specific release
(`NERD_FONT_VERSION` near the top of `install.py`) rather
than always fetching latest — every machine ends up with byte-identical font
files, and reinstalls are reproducible. A version-marker file
(`~/.local/share/fonts/JetBrainsMonoNerdFont/.nerd-fonts-version`) makes
re-runs skip the download/extract instead of redoing it every time. To
upgrade: bump `NERD_FONT_VERSION` in `install.py` and re-run — the marker
mismatch triggers a fresh download.

## Keyboard setup (Karabiner-Elements)

All remapping goes through Karabiner — no macOS modifier key overrides needed (only Caps Lock → Escape is set at the OS level).

**complex_modifications**:
- `Ctrl+C/V/X/Z/A/S/W/T/F/L` → equivalent `Cmd` shortcuts (non-terminal apps only)

Terminal apps excluded from Ctrl rules: Ghostty, iTerm2, Kitty, WezTerm, Alacritty, Terminal.app

The physical `Cmd` key works as `Cmd` natively. No global modifier swap — the Ctrl→Cmd complex rules handle the Linux-style shortcuts directly.

After install, open Karabiner-Elements and grant **Input Monitoring** and **Accessibility** permissions in System Settings. Without these, none of the remapping will work.

## App switcher (AltTab)

AltTab replaces the macOS app switcher with a Windows/Linux-style one that shows all windows (including minimized), previews them, and restores them on switch.

After install:
1. Open AltTab → grant **Accessibility** permission
2. Preferences → Controls → set trigger to `Option + Tab`

AltTab intercepts `Option+Tab` directly, so no Karabiner rule is needed.

## Rectangle shortcuts

| Action | Shortcut |
|--------|----------|
| Left half | `Cmd+Left` |
| Right half | `Cmd+Right` |
| Maximize | `Cmd+Shift+Up` |
| Previous display | `Cmd+Shift+Left` |
| Next display | `Cmd+Shift+Right` |

## watchcommit

Polls `~/dotfiles` every 90 seconds, detects git changes, generates a conventional commit message by shelling out to the `claude` CLI (`--model haiku`), and commits + pushes automatically. Uses whatever account `claude` is already logged into on the machine (Pro, Max, or API key) — no separate `ANTHROPIC_API_KEY` needed. Runs as a background agent — launchd on macOS (starts on login, restarts on crash), systemd `--user` on Linux/WSL (starts on login, `Restart=always`, kept alive after logout via `loginctl enable-linger`). Logs to `/tmp/watchcommit.log` on both platforms.

```sh
# Tail the log
tail -f /tmp/watchcommit.log

# macOS: stop/start manually
launchctl unload ~/Library/LaunchAgents/com.user.watchcommit.plist
launchctl load ~/Library/LaunchAgents/com.user.watchcommit.plist

# Linux/WSL: stop/start manually
systemctl --user stop watchcommit.service
systemctl --user start watchcommit.service
systemctl --user status watchcommit.service

# Run manually against a different repo
watchcommit /path/to/other/repo
```

Requires `claude login` to have been run at least once on the machine (any
plan) — the launchd/systemd agent runs as the same user and reuses that
session, no separate config needed.

- **macOS**: the launchd agent inherits your login session's credentials the
  same way a Terminal-launched `claude` would. If `claude` normally stores
  its session in the macOS Keychain rather than a plain credentials file,
  a background LaunchAgent may not be able to read it without the session
  unlocked, or may need a one-time Keychain access grant — not verified on
  real macOS hardware yet, so check `/tmp/watchcommit.log` after the first
  install for an auth error if commits aren't showing up.
- **Linux/WSL**: the systemd unit needs `systemd=true` under `[boot]` in
  `/etc/wsl.conf` (`wsl --shutdown` from Windows to apply) — without it,
  the installer skips the service and you're back to running `watchcommit`
  manually in a terminal.

## Notes

- **Intel Mac**: `install.py` and `.zprofile` both detect `/usr/local/bin/brew` automatically
- **Linux/WSL**: `.zprofile` is not symlinked; secrets and NVM are sourced from `.zshrc` instead
- **NVM**: installed via the official script, not Homebrew. Restart your shell after install
- **vim plugins**: run `:PlugInstall` inside vim after first launch
- **Secrets**: `~/.secrets` is gitignored — create it manually on each new machine
- **Tool state**: `~/.claude/data` (dev_status backlog, grill sessions) is
  per-machine by design and never packaged — a new machine starts fresh
- **Tests**: script tests live in `claude/scripts/test_*.py` alongside the
  scripts; they are not deployed to `~/.claude`
- **installer tests**, two tiers:
  - *fast* — pytest suites under `test/`. `test_install.py` covers argument
    validation, the symlink engine, the history/rollback engine and the
    copy-once + drift logic; `test_depart*.py` cover departure mode;
    `test_lint.py` gates the pinned Ruff configuration; and the rest guard
    cross-harness invariants (`test_pi_extension_links.py`,
    `test_pi_ts_checks.py`, `test_second_opinion_docs.py`,
    `test_agents_md_links.py`, `test_conftest_guards.py`,
    `test_agy_elapsed.py`, `test_watchcommit_repo_default.py`).
    Everything — script tests and installer tests — runs from the repo root
    with `uv run pytest` (pytest is pinned in the uv dev dependency group);
    they touch nothing real, because the repo-root `conftest.py` sandboxes
    `HOME` and blocks unmarked subprocess calls and production-path writes
    for the whole suite. See `test/AGENTS.md`.
  - *lifecycle* — `test/run.sh` runs `test/scenarios.sh` (fresh install,
    rollback, backup-and-restore, work profile + guard, `--force`,
    argument errors) inside throwaway Docker containers, one Ubuntu (apt
    branch) and one Fedora (dnf branch), so real package managers get
    exercised without touching the real machine. Requires Docker. Drives
    the current `history.jsonl` manifest path
    (`~/.local/state/dotfiles/history.jsonl`) through the same
    rollback/backup engine as the fast tier, but against real installs
    inside the container rather than a throwaway `HOME` with stubbed
    subprocesses.
