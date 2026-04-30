# dotfiles

macOS setup. Clone and run `install.sh` to go from zero to working.

## Quick start

```sh
git clone <repo-url> ~/dotfiles
cd ~/dotfiles
chmod +x install.sh
./install.sh
```

## What's included

| File | Destination |
|------|-------------|
| `vim/.vimrc` | `~/.vimrc` |
| `zsh/.zshrc` | `~/.zshrc` |
| `zsh/.zprofile` | `~/.zprofile` |
| `zsh/.common_shell_aliases` | `~/.common_shell_aliases` |
| `shell/.poshtheme.omp.json` | `~/.poshtheme.omp.json` |
| `karabiner/karabiner.json` | `~/.config/karabiner/karabiner.json` |
| `tmux/.tmux.conf` | `~/.tmux.conf` |
| `vscode/settings.json` | `~/Library/Application Support/Code/User/settings.json` |
| `vscode/keybindings.json` | `~/Library/Application Support/Code/User/keybindings.json` |
| `claude/CLAUDE.md` | `~/.claude/CLAUDE.md` |

Everything is symlinked — edits in `~/dotfiles` take effect immediately.

## install.sh does

1. Installs Homebrew (if missing) — supports both Apple Silicon and Intel
2. Installs packages: Python 3.13, uv, ruff, tmux, zoxide, eza, bat, ripgrep, lsd, ncdu, tldr, oh-my-posh
3. Installs casks: Karabiner-Elements, Rectangle, Ghostty, VS Code
4. Installs NVM (if missing)
5. Symlinks all dotfiles (backs up any existing non-symlink files to `*.bak`)
6. Imports Rectangle preferences
7. Sets Caps Lock → Escape via macOS keyboard modifier mapping
8. Installs vim-plug (if missing)

## Keyboard setup (Karabiner-Elements)

All remapping goes through Karabiner — no macOS modifier key overrides needed.

**simple_modifications** (system-wide):
- Option ↔ Command (both sides) — makes the keyboard feel like a PC layout

**complex_modifications** (non-terminal apps only):
- `Ctrl+C/V/X/Z/A/S/W/T/F/L` → equivalent `Cmd` shortcuts

Terminal apps excluded: Ghostty, iTerm2, Kitty, WezTerm, Alacritty, Terminal.app

After install, open Karabiner-Elements and grant **Input Monitoring** and **Accessibility** permissions in System Settings. Without these, none of the remapping will work.

## Rectangle shortcuts

| Action | Shortcut |
|--------|----------|
| Left half | `Cmd+Left` |
| Right half | `Cmd+Right` |
| Maximize | `Cmd+Shift+Up` |
| Previous display | `Cmd+Shift+Left` |
| Next display | `Cmd+Shift+Right` |

These use the physical Command key (post-Karabiner swap = physical Option key).

## Notes

- **Keyboard ID in macOS**: `install.sh` detects the ByHost plist automatically — no hardcoded IDs
- **NVM**: installed via the official script, not Homebrew. Restart your shell after install
- **vim plugins**: run `:PlugInstall` inside vim after first launch
