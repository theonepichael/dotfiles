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

## Work profile

`./install.sh --work` provisions a work machine:

- **watchcommit is excluded entirely** — no binary, no agent. It auto-pushes
  to a personal remote under your personal Claude account login; that stays
  off work hardware. Commit manually there.
- Claude settings are seeded from `claude/settings.work.json` — same hooks and
  statusline, but no `skipDangerousModePermissionPrompt` and no model pin.
- `~/.secrets` is still sourced if present, for work-issued tokens only.
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
| `systemd/watchcommit.service` | `~/.config/systemd/user/watchcommit.service` (Linux/WSL only) |

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
- Enables and starts the watchcommit systemd `--user` service, and runs
  `loginctl enable-linger` so it keeps running after you close the last
  WSL/SSH session (skipped with a note if `systemd --user` isn't available —
  e.g. WSL without `systemd=true` in `/etc/wsl.conf`)

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
  `install.sh` skips the service and you're back to running `watchcommit`
  manually in a terminal.

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
