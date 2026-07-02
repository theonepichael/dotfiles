#!/usr/bin/env zsh
set -eo pipefail

DOTFILES="$(cd "$(dirname "$0")" && pwd)"
OS="$(uname -s)"

is_mac()   { [[ "$OS" == "Darwin" ]] }
is_linux() { [[ "$OS" == "Linux" ]] }

# ============================================================================
# Packages
# ============================================================================

if is_mac; then
  if ! command -v brew &>/dev/null; then
    echo "==> Installing Homebrew..."
    /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
  fi

  if [[ -x /opt/homebrew/bin/brew ]]; then
    eval "$(/opt/homebrew/bin/brew shellenv)"
  elif [[ -x /usr/local/bin/brew ]]; then
    eval "$(/usr/local/bin/brew shellenv)"
  fi

  echo "==> Installing formulae..."
  brew install \
    python@3.13 uv ruff \
    tmux zoxide eza bat ripgrep lsd ncdu tldr \
    oh-my-posh

  echo "==> Installing casks..."
  brew install --cask karabiner-elements rectangle ghostty visual-studio-code alt-tab

elif is_linux; then
  echo "==> Installing packages (apt)..."
  sudo apt-get install -y \
    tmux zoxide eza bat lsd ncdu tldr ripgrep

  # Ubuntu ships bat as batcat; shim it
  if command -v batcat &>/dev/null && ! command -v bat &>/dev/null; then
    mkdir -p ~/.local/bin
    ln -sf "$(which batcat)" ~/.local/bin/bat
    echo "  shimmed bat → batcat"
  fi

  # uv (not in apt)
  if ! command -v uv &>/dev/null; then
    echo "==> Installing uv..."
    curl -LsSf https://astral.sh/uv/install.sh | sh
    [[ -f "$HOME/.local/bin/env" ]] && source "$HOME/.local/bin/env"
  fi

  # ruff via uv tool
  uv tool install ruff

  # oh-my-posh via official installer
  if ! command -v oh-my-posh &>/dev/null; then
    echo "==> Installing oh-my-posh..."
    curl -s https://ohmyposh.dev/install.sh | bash -s -- -d ~/.local/bin
  fi
fi

# NVM (both platforms — uses its own installer)
if [[ ! -d "$HOME/.nvm" ]]; then
  echo "==> Installing NVM..."
  curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/HEAD/install.sh | bash
fi

# ============================================================================
# Symlinks
# ============================================================================

echo "==> Symlinking dotfiles..."

symlink() {
  local src="$DOTFILES/$1" dst="$2"
  mkdir -p "$(dirname "$dst")"
  if [[ -e "$dst" && ! -L "$dst" ]]; then
    echo "  Backing up $dst → $dst.bak"
    mv "$dst" "$dst.bak"
  fi
  ln -sf "$src" "$dst"
  echo "  linked $dst"
}

# Common (both platforms)
symlink scripts/watchcommit.py    ~/.local/bin/watchcommit
symlink vim/.vimrc                ~/.vimrc
symlink zsh/.zshrc                ~/.zshrc
symlink zsh/.common_shell_aliases ~/.common_shell_aliases
symlink shell/.poshtheme.omp.json ~/.poshtheme.omp.json
symlink tmux/.tmux.conf           ~/.tmux.conf
symlink claude/CLAUDE.md          ~/.claude/CLAUDE.md
symlink claude/commands/status.md   ~/.claude/commands/status.md
symlink claude/commands/grill-me.md ~/.claude/commands/grill-me.md
symlink claude/scripts/dev_status.py      ~/.claude/scripts/dev_status.py
symlink claude/scripts/test_dev_status.py ~/.claude/scripts/test_dev_status.py
symlink claude/scripts/gen_claude_completion.py ~/.claude/scripts/gen_claude_completion.py
symlink claude/hooks/gsd-statusline.js    ~/.claude/hooks/gsd-statusline.js

# settings.json is copied, not symlinked — Claude Code rewrites it in place,
# which would replace a symlink with a plain file and silently detach it.
if [[ ! -f ~/.claude/settings.json ]]; then
  cp "$DOTFILES/claude/settings.json" ~/.claude/settings.json
  echo "  copied ~/.claude/settings.json"
fi

# macOS-only (.zprofile has Homebrew shellenv; not needed on Linux)
if is_mac; then
  symlink zsh/.zprofile             ~/.zprofile
  symlink karabiner/karabiner.json  ~/.config/karabiner/karabiner.json
  symlink launchd/com.user.watchcommit.plist ~/Library/LaunchAgents/com.user.watchcommit.plist
  symlink vscode/settings.json      "$HOME/Library/Application Support/Code/User/settings.json"
  symlink vscode/keybindings.json   "$HOME/Library/Application Support/Code/User/keybindings.json"
fi

# ============================================================================
# macOS: Rectangle prefs, Caps Lock → Escape, launchd agent
# ============================================================================

if is_mac; then
  echo "==> Importing Rectangle preferences..."
  defaults import com.knollsoft.Rectangle "$DOTFILES/rectangle/com.knollsoft.Rectangle.plist"

  echo "==> Setting Caps Lock → Escape..."
  python3 - << 'PYEOF'
import glob, os, plistlib

caps_to_esc = [
    {
        'HIDKeyboardModifierMappingSrc': 30064771129,  # Caps Lock
        'HIDKeyboardModifierMappingDst': 30064771113,  # Escape
    }
]

plists = glob.glob(os.path.expanduser(
    '~/Library/Preferences/ByHost/.GlobalPreferences.*.plist'))

if not plists:
    print("  No ByHost GlobalPreferences plist found — skipping")
    raise SystemExit(0)

for path in plists:
    with open(path, 'rb') as f:
        prefs = plistlib.load(f)
    for key in list(prefs):
        if 'modifiermapping' in key:
            prefs[key] = caps_to_esc
    with open(path, 'wb') as f:
        plistlib.dump(prefs, f)
    print(f"  Updated {os.path.basename(path)}")
PYEOF

  echo "==> Loading watchcommit launchd agent..."
  launchctl unload ~/Library/LaunchAgents/com.user.watchcommit.plist 2>/dev/null || true
  launchctl load ~/Library/LaunchAgents/com.user.watchcommit.plist
fi

# ============================================================================
# vim-plug (both platforms)
# ============================================================================

if [[ ! -f "$HOME/.vim/autoload/plug.vim" ]]; then
  echo "==> Installing vim-plug..."
  curl -fLo ~/.vim/autoload/plug.vim --create-dirs \
    https://raw.githubusercontent.com/junegunn/vim-plug/master/plug.vim
  echo "  Run :PlugInstall inside vim to install plugins"
fi

# ============================================================================
# Done
# ============================================================================

echo ""
echo "Done!"
if is_mac; then
  echo "  Manual steps:"
  echo "  1. Log out and back in for Caps Lock → Escape to take effect"
  echo "  2. Open Karabiner-Elements → grant Input Monitoring + Accessibility"
  echo "  3. Open Rectangle → grant Accessibility permission"
  echo "  4. Set ANTHROPIC_API_KEY in ~/.secrets to use watchcommit"
elif is_linux; then
  echo "  Manual steps:"
  echo "  1. Restart your shell to pick up the new config"
  echo "  2. Set ANTHROPIC_API_KEY in ~/.secrets to use watchcommit"
fi
