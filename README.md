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
`profile_exclude`; the file's header documents the schema). Only the two
copy-once seed files and the WSL-side VS Code path need real code.

`--harness` is required on every run — there's no default. Pick any
combination of `claude`, `copilot`, `opencode`, `agy` (comma-separated);
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
— comma-separated, at least one of `claude`, `copilot`, `opencode`, `agy`. No
default; every run states its intent explicitly. Selecting fewer harnesses
on a later run doesn't uninstall the ones left out — this script is purely
additive, same as everything else it does. Use `--rollback` (which reverses
every recorded run, not just the most recent) or manual cleanup to actually
remove something.

- **`claude`** — installs Claude Code (`npm i -g @anthropic-ai/claude-code`)
  and its `~/.claude/*` wiring (`CLAUDE.md`, `commands/*.md`,
  `settings.json`, the statusline hook).
- **`copilot`** — installs [GitHub Copilot CLI](https://github.com/github/copilot-cli)
  (`npm i -g @github/copilot`) and its wiring:
  - **Shared instructions file**: `claude/CLAUDE.md` is symlinked to *both*
    `~/.claude/CLAUDE.md` and `~/.copilot/copilot-instructions.md` — no
    separate Copilot-specific instructions file to maintain, since the
    backlog/pending-items workflow is already tool-agnostic prose.
  - **`copilot/hooks/session-start.json`**: a `sessionStart` hook running the
    same shell commands as Claude Code's `SessionStart` hook chain
    (dashboard render, dotfiles-drift check).
  - **`copilot/skills/<name>/SKILL.md`**: ports of all 5 Claude Code skills
    (dashboard, standup, second-opinion, grill-me, make-skill). Copilot
    skills are **description-matched, not typed-slash** — there's no
    `/dashboard` to type; the skill fires when its `description` frontmatter
    matches the conversation. `second-opinion` and `grill-me` also drop
    `AskUserQuestion` (Copilot has no structured multi-choice prompt) in
    favor of plain conversational back-and-forth.
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
- **`opencode`** — wires `~/.config/opencode/tui.json`, the `dashboard`/
  `grill-me` commands, and `opencode.jsonc` (the bash permission allowlist,
  copy-once-and-report-drift same as Claude's `settings.json`): `curl`,
  `npx`, `node -e`, `rm -f`, `kill`, `nohup` are all pre-approved. `xargs`
  and `awk` are removed regardless, since they're allowlist bypasses (each
  can invoke an arbitrary other command as its own argument), not
  individually-risky commands worth pre-approving. `opencode` is never
  installed on a work machine at all — see "Work profile" below.
- **`agy`** — wires config for [Antigravity CLI](https://antigravity.google/docs/cli)
  (Google's Gemini-backed CLI); the binary itself is assumed already
  installed, same as `opencode` — this script only wires its config:
  - **Shared instructions file**: `claude/CLAUDE.md` is symlinked to
    `~/.gemini/GEMINI.md`, agy's global-rules path (no `CLAUDE.md` fallback
    like opencode has, so it needs a real link of its own).
  - **`agy/skills/<name>/SKILL.md`**: ports of the dashboard, standup,
    second-opinion, grill-me, and make-skill skills (`backlog-item` not yet
    ported), symlinked into `~/.gemini/antigravity-cli/skills/<name>/SKILL.md`
    — agy's current global skills path. Like Copilot's, agy's skills are
    description-matched, not typed-slash: there's no `/dashboard` to type,
    and no `AskUserQuestion`-style prompt, so the ported skills use plain
    conversational back-and-forth for judgment calls.
  - agy has no `SessionStart`-equivalent hook event, so there's no
    auto-render-dashboard wiring for it, unlike Claude Code and Copilot.
  - See `agy/CLAUDE_CODE_PARITY.md` for the full verification notes.

The shared `~/.claude/scripts/*.py` (dev_status, grill, second_opinion,
standup, etc.) are symlinked regardless of which harness(es) are selected —
all four harnesses' skills/hooks call these same paths.

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
- Claude settings are seeded from `claude/settings.work.json` — same hooks and
  statusline, but no `skipDangerousModePermissionPrompt` and no model pin.
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
| `scripts/watchcommit.py` | `~/.local/bin/watchcommit` |
| `launchd/com.user.watchcommit.plist` | `~/Library/LaunchAgents/com.user.watchcommit.plist` (macOS only) |
| `systemd/watchcommit.service` | `~/.config/systemd/user/watchcommit.service` (Linux/WSL only) |

Everything is symlinked — edits in `~/dotfiles` take effect immediately. The
full, authoritative table is `links.toml`; the rows above are the
highlights. Three things are copied instead of symlinked:
`claude/settings.json` (and its `.work` variant) and `opencode/opencode.jsonc`
(no `.work` variant — `--profile=work --harness=opencode` is rejected
outright, so only one variant of that seed exists) are copy-once seeds,
because both tools rewrite the file in place once live; the WSL-side VS Code
`settings.json`/`keybindings.json` are copied for an unrelated reason —
Windows can't read a WSL-side symlink through `DrvFs`.

## The installer does

### Both platforms
1. Installs packages: tmux, zoxide, eza, bat, ripgrep, lsd, ncdu, tldr, oh-my-posh, neovim, fd, uv, ruff
2. Installs NVM and Node/npm — only if `claude` and/or `copilot` is in `--harness`
   (`opencode` manages its own runtime separately, not installed by this script)
3. Installs the harness(es) named in `--harness` (`claude`: Claude Code via
   `npm i -g @anthropic-ai/claude-code`; `copilot`: GitHub Copilot CLI via
   `npm i -g @github/copilot`; `opencode`: assumed already installed, this
   script only wires its config) and their `~/.claude`/`~/.copilot`/
   `~/.config/opencode` wiring — see "Harness selection" above
4. Symlinks every applicable `links.toml` entry (backs up any existing
   non-symlink files to `*.bak`)
5. Seeds `~/.claude/settings.json` (copy-once — if it already exists, drift from
   the repo seed is reported in the summary, never overwritten) — only when
   `claude` is selected
6. Seeds `~/.config/opencode/opencode.jsonc` (the bash permission allowlist,
   profile-specific) the same copy-once way — only when `opencode` is selected
7. Installs vim-plug (if missing)
8. Bootstraps Neovim plugins (`lazy.nvim` sync) if `nvim` on PATH is >=0.11

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
- **Tests**: live in `claude/scripts/` and run from the repo
  (`cd claude/scripts && pytest`); they are not deployed to `~/.claude`
- **installer tests**, two tiers:
  - *fast* — `test/test_install.py` (pytest) covers argument validation,
    the symlink engine, the history/rollback engine, and the copy-once +
    drift logic against a throwaway `HOME` with every subprocess stubbed.
    Run it with `uv run --with pytest pytest test/test_install.py`; takes
    under a second and touches nothing real.
  - *lifecycle* — `test/run.sh` runs `test/scenarios.sh` (fresh install,
    rollback, backup-and-restore, work profile + guard, `--force`,
    argument errors) inside throwaway Docker containers, one Ubuntu (apt
    branch) and one Fedora (dnf branch), so real package managers get
    exercised without touching the real machine. Requires Docker. Drives
    the current `history.jsonl` manifest path
