# dotfiles

Cross-platform setup for macOS and Linux/WSL. Clone and run `install.sh` to go from zero to working.

## Quick start

```sh
git clone <repo-url> ~/dotfiles
cd ~/dotfiles
chmod +x install.sh
./install.sh
```

Before running, create `~/.secrets` with your API keys (not tracked in git):

```sh
export ANTHROPIC_API_KEY="sk-ant-..."
```

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
2. Installs NVM (if missing)
3. Symlinks all common dotfiles (backs up any existing non-symlink files to `*.bak`)
4. Installs vim-plug (if missing)

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
