# dotfiles

Cross-platform setup for macOS and Linux/WSL. Clone and run `install.sh` to go from zero to working.

## Quick start

```sh
git clone <repo-url> ~/dotfiles
cd ~/dotfiles
chmod +x install.sh
./install.sh          # personal machine
./install.sh --work   # work machine (see "Work profile" below)
```

On a personal machine, create `~/.secrets` with your API keys before running (not tracked in git):

```sh
export ANTHROPIC_API_KEY="sk-ant-..."
```

If GitHub isn't reachable from the target machine, transfer the repo as a git
bundle instead (full history, offline-clonable):

```sh
# on a machine that has the repo
git -C ~/dotfiles bundle create /path/to/usb/dotfiles.bundle --all

# on the target machine
git clone /path/to/usb/dotfiles.bundle ~/dotfiles
# later, if GitHub opens up: git remote set-url origin <repo-url>
```

## Work profile

`./install.sh --work` provisions a work machine:

- **watchcommit is excluded entirely** — no binary, no agent. It auto-pushes
  to a personal remote with a personal API key; that stays off work hardware.
  Commit manually there.
- Claude settings are seeded from `claude/settings.work.json` — same hooks and
  statusline, but no `skipDangerousModePermissionPrompt` and no model pin.
- The manual-steps output drops the personal-API-key instruction. `~/.secrets`
  is still sourced if present, for work-issued tokens only.
- A profile marker is written (`~/.local/state/dotfiles/profile`); later runs
  *without* `--work` on that machine refuse unless `--force` is passed.

## Failures, skips, and rollback

`install.sh` never aborts on a failed step. Anything that can't run (blocked
curl, offline apt, missing sudo) is skipped and listed in a loud end-of-run
summary with the reason; exit code is 1 if anything was skipped.

Every file mutation (symlink created, file backed up, file copied) is recorded
in `~/.local/state/dotfiles/last-run.tsv`. If you ran with the wrong profile:

```sh
./install.sh --rollback   # reverses the last run's file mutations
./install.sh --work       # then re-run correctly
```

Packages are never uninstalled by rollback — they're identical across
profiles, so a wrong-profile run's real footprint is entirely file-level.

## What's included

| File | Destination |
|------|-------------|
| `vim/.vimrc` | `~/.vimrc` |
| `zsh/.zshrc` | `~/.zshrc` |
| `zsh/.zprofile` | `~/.zprofile` (macOS only) |
| `zsh/.common_shell_aliases` | `~/.common_shell_aliases` |
| `shell/.poshtheme.omp.json` | `~/.poshtheme.omp.json` |
| `karabiner/karabiner.json` | `~/.config/karabiner/karabiner.json` (macOS only) |
| `tmux/.tmux.conf` | `~/.tmux.conf` |
| `vscode/settings.json` | `~/Library/Application Support/Code/User/settings.json` (macOS only) |
| `vscode/keybindings.json` | `~/Library/Application Support/Code/User/keybindings.json` (macOS only) |
| `claude/CLAUDE.md` | `~/.claude/CLAUDE.md` |
| `scripts/watchcommit.py` | `~/.local/bin/watchcommit` |
| `launchd/com.user.watchcommit.plist` | `~/Library/LaunchAgents/com.user.watchcommit.plist` (macOS only) |

Everything is symlinked — edits in `~/dotfiles` take effect immediately.

## install.sh does

### Both platforms
1. Installs packages: tmux, zoxide, eza, bat, ripgrep, lsd, ncdu, tldr, oh-my-posh, uv, ruff
2. Installs NVM (if missing) and Claude Code (`npm i -g @anthropic-ai/claude-code`)
3. Symlinks all common dotfiles (backs up any existing non-symlink files to `*.bak`)
4. Seeds `~/.claude/settings.json` (copy-once — if it already exists, drift from
   the repo seed is reported in the summary, never overwritten)
5. Installs vim-plug (if missing)

### macOS only
- Installs Homebrew (if missing) — supports both Apple Silicon (`/opt/homebrew`) and Intel (`/usr/local`)
- Installs casks: Karabiner-Elements, Rectangle, Ghostty, VS Code, AltTab
- Symlinks macOS-specific configs (`.zprofile`, karabiner, vscode, launchd)
- Imports Rectangle preferences
- Sets Caps Lock → Escape via macOS keyboard modifier mapping
- Loads the watchcommit launchd agent

### Linux/WSL only
- Installs packages via `apt`
- Creates `~/.local/bin/bat` shim (Ubuntu ships bat as `batcat`)
- Installs uv via astral.sh if not present
- Installs oh-my-posh to `~/.local/bin` via official installer

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

Polls `~/dotfiles` every 90 seconds, detects git changes, generates a conventional commit message via Claude Haiku, and commits + pushes automatically. Runs as a background launchd agent on macOS (starts on login, restarts on crash). Logs to `/tmp/watchcommit.log`.

```sh
# Tail the log
tail -f /tmp/watchcommit.log

# macOS: stop/start manually
launchctl unload ~/Library/LaunchAgents/com.user.watchcommit.plist
launchctl load ~/Library/LaunchAgents/com.user.watchcommit.plist

# Run manually against a different repo
watchcommit /path/to/other/repo
```

Requires `ANTHROPIC_API_KEY` in `~/.secrets`.

## Notes

- **Intel Mac**: `install.sh` and `.zprofile` both detect `/usr/local/bin/brew` automatically
- **Linux/WSL**: `.zprofile` is not symlinked; secrets and NVM are sourced from `.zshrc` instead
- **NVM**: installed via the official script, not Homebrew. Restart your shell after install
- **vim plugins**: run `:PlugInstall` inside vim after first launch
- **Secrets**: `~/.secrets` is gitignored — create it manually on each new machine
- **Tool state**: `~/.claude/data` (dev_status backlog, grill sessions) is
  per-machine by design and never packaged — a new machine starts fresh
- **Tests**: live in `claude/scripts/` and run from the repo
  (`cd claude/scripts && pytest`); they are not deployed to `~/.claude`
- **install.sh tests**: `test/` runs the full install.sh lifecycle (fresh
  install, rollback, backup-and-restore, work profile + guard, --force,
  argument errors) inside a throwaway Docker container so it never touches
  the real machine. Requires Docker; run with `./test/run.sh` after any
  change to `install.sh`
