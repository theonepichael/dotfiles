#!/usr/bin/env zsh
set -eo pipefail

DOTFILES="$(cd "$(dirname "$0")" && pwd)"

# ============================================================================
# Homebrew
# ============================================================================

if ! command -v brew &>/dev/null; then
  echo "==> Installing Homebrew..."
  /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
fi

# Works for both Apple Silicon (/opt/homebrew) and Intel (/usr/local)
if [[ -x /opt/homebrew/bin/brew ]]; then
  eval "$(/opt/homebrew/bin/brew shellenv)"
elif [[ -x /usr/local/bin/brew ]]; then
  eval "$(/usr/local/bin/brew shellenv)"
fi

# ============================================================================
# Packages
# ============================================================================

echo "==> Installing formulae..."
brew install \
  python@3.13 uv ruff \
  tmux zoxide eza bat ripgrep lsd ncdu tldr \
  oh-my-posh

echo "==> Installing casks..."
brew install --cask karabiner-elements rectangle ghostty visual-studio-code alt-tab

# NVM (not in Homebrew — uses its own installer)
if ! command -v nvm &>/dev/null && [[ ! -d "$HOME/.nvm" ]]; then
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

symlink scripts/watchcommit.py    ~/.local/bin/watchcommit
symlink launchd/com.user.watchcommit.plist ~/Library/LaunchAgents/com.user.watchcommit.plist
symlink vim/.vimrc                ~/.vimrc
symlink zsh/.zshrc                ~/.zshrc
symlink zsh/.zprofile             ~/.zprofile
symlink zsh/.common_shell_aliases ~/.common_shell_aliases
symlink shell/.poshtheme.omp.json ~/.poshtheme.omp.json
symlink karabiner/karabiner.json  ~/.config/karabiner/karabiner.json
symlink tmux/.tmux.conf           ~/.tmux.conf
symlink claude/CLAUDE.md          ~/.claude/CLAUDE.md
symlink vscode/settings.json      "$HOME/Library/Application Support/Code/User/settings.json"
symlink vscode/keybindings.json   "$HOME/Library/Application Support/Code/User/keybindings.json"

# ============================================================================
# Rectangle
# ============================================================================

echo "==> Importing Rectangle preferences..."
defaults import com.knollsoft.Rectangle "$DOTFILES/rectangle/com.knollsoft.Rectangle.plist"

# ============================================================================
# macOS: Caps Lock → Escape (via keyboard modifier mapping)
# ============================================================================
# Uses Python to find and update the ByHost GlobalPreferences plist directly,
# which avoids hardcoding the keyboard ID or hardware UUID.

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

# ============================================================================
# vim-plug
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

# ============================================================================
# watchcommit (launchd agent)
# ============================================================================

echo "==> Loading watchcommit launchd agent..."
launchctl unload ~/Library/LaunchAgents/com.user.watchcommit.plist 2>/dev/null || true
launchctl load ~/Library/LaunchAgents/com.user.watchcommit.plist

echo ""
echo "Done! A few manual steps required:"
echo "  1. Log out and back in for Caps Lock → Escape to take effect"
echo "  2. Open Karabiner-Elements → grant Input Monitoring + Accessibility permissions"
echo "  3. Open Rectangle → grant Accessibility permission"
echo "  4. Set ANTHROPIC_API_KEY in your environment to use watchcommit"
