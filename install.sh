#!/usr/bin/env zsh
# No set -e: every unit runs skip-and-report — a blocked installer or offline
# package mirror must not abort the rest of the run. Failures are collected
# and printed loudly in the end-of-run summary.

DOTFILES="$(cd "$(dirname "$0")" && pwd)"
OS="$(uname -s)"

STATE_DIR="$HOME/.local/state/dotfiles"
MANIFEST="$STATE_DIR/last-run.tsv"
PROFILE_MARKER="$STATE_DIR/profile"

is_mac()   { [[ "$OS" == "Darwin" ]] }
is_linux() { [[ "$OS" == "Linux" ]] }

usage() {
  cat <<'EOF'
usage: ./install.sh [--work] [--rollback] [--force]

  --work      provision a work machine: excludes watchcommit and personal
              API-key setup; seeds Claude settings from settings.work.json
  --rollback  reverse the previous run's file mutations (symlinks, copies,
              backups) using the manifest, then exit. Packages are reported
              but never uninstalled.
  --force     override the work-profile guard on a machine previously
              provisioned with --work

Exits 0 if every step ran, 1 if any step was skipped (see summary).
EOF
}

PROFILE=personal
FORCE=0
ROLLBACK=0
for arg in "$@"; do
  case "$arg" in
    --work)     PROFILE=work ;;
    --rollback) ROLLBACK=1 ;;
    --force)    FORCE=1 ;;
    -h|--help)  usage; exit 0 ;;
    *)          echo "unknown argument: $arg" >&2; usage >&2; exit 2 ;;
  esac
done

# ============================================================================
# Skip-and-report plumbing
# ============================================================================

typeset -a SKIPPED
note_skip() {  # note_skip <step> <reason>
  SKIPPED+=("$1 — $2")
  echo "  !! SKIPPED: $1 — $2"
}

# ============================================================================
# Run manifest (drives --rollback)
# ============================================================================

manifest_init() {
  mkdir -p "$STATE_DIR"
  printf 'run\t%s\t%s\n' "$(date -Iseconds)" "$PROFILE" > "$MANIFEST"
}

record() {  # record <action> <path> [extra]  (paths never contain tabs)
  printf '%s\t%s\t%s\n' "$1" "$2" "${3:-}" >> "$MANIFEST"
}

do_rollback() {
  if [[ ! -f "$MANIFEST" ]]; then
    echo "no manifest at $MANIFEST — nothing to roll back" >&2
    exit 1
  fi
  local -a lines
  lines=("${(@f)$(<"$MANIFEST")}")
  echo "==> Rolling back run recorded at $MANIFEST"
  local i action a b
  for (( i = ${#lines}; i >= 1; i-- )); do
    IFS=$'\t' read -r action a b <<< "${lines[i]}"
    case "$action" in
      symlink-created)
        [[ -L "$a" ]] && rm "$a" && echo "  removed symlink $a" ;;
      file-copied)
        [[ -f "$a" ]] && rm "$a" && echo "  removed $a" ;;
      file-backed-up)
        [[ -e "$b" ]] && mv "$b" "$a" && echo "  restored $a from $b" ;;
      package-installed)
        echo "  package left installed (profile-independent): $a" ;;
      run)
        echo "  (run was: $a, profile: $b)" ;;
    esac
  done
  rm "$MANIFEST"
  echo "Rollback complete. Re-run ./install.sh with the intended profile."
  exit 0
}

(( ROLLBACK )) && do_rollback

# ============================================================================
# Work-profile guard
# ============================================================================

if [[ "$PROFILE" == "personal" && -f "$PROFILE_MARKER" ]] \
   && [[ "$(<"$PROFILE_MARKER")" == "work" ]] && (( ! FORCE )); then
  echo "This machine is provisioned as WORK (marker: $PROFILE_MARKER)." >&2
  echo "Pass --work, or --force to provision as personal anyway." >&2
  exit 2
fi

manifest_init
echo "==> Installing with profile: $PROFILE"

# ============================================================================
# Packages
# ============================================================================

if is_mac; then
  if ! command -v brew &>/dev/null; then
    echo "==> Installing Homebrew..."
    /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)" \
      || note_skip "Homebrew" "installer failed (network blocked?)"
  fi

  if [[ -x /opt/homebrew/bin/brew ]]; then
    eval "$(/opt/homebrew/bin/brew shellenv)"
  elif [[ -x /usr/local/bin/brew ]]; then
    eval "$(/usr/local/bin/brew shellenv)"
  fi

  if command -v brew &>/dev/null; then
    echo "==> Installing formulae..."
    if brew install \
        python@3.13 uv ruff \
        tmux zoxide eza bat ripgrep lsd ncdu tldr \
        oh-my-posh; then
      record package-installed "brew formulae"
    else
      note_skip "brew formulae" "brew install failed"
    fi

    echo "==> Installing casks..."
    if brew install --cask karabiner-elements rectangle ghostty visual-studio-code alt-tab; then
      record package-installed "brew casks"
    else
      note_skip "brew casks" "brew install --cask failed"
    fi
  else
    note_skip "brew formulae + casks" "brew unavailable"
  fi

elif is_linux; then
  echo "==> Updating apt package lists..."
  sudo apt-get update || note_skip "apt update" "apt-get update failed (offline or blocked?)"

  echo "==> Installing packages (apt)..."
  if sudo apt-get install -y \
      tmux zoxide eza bat lsd ncdu tldr ripgrep unzip; then
    record package-installed "apt packages"
  else
    note_skip "apt packages" "apt-get install failed"
  fi

  # Ubuntu ships bat as batcat; shim it
  if command -v batcat &>/dev/null && ! command -v bat &>/dev/null; then
    mkdir -p ~/.local/bin
    ln -sf "$(which batcat)" ~/.local/bin/bat
    record symlink-created ~/.local/bin/bat
    echo "  shimmed bat → batcat"
  fi

  # uv (not in apt)
  if ! command -v uv &>/dev/null; then
    echo "==> Installing uv..."
    if curl -LsSf https://astral.sh/uv/install.sh | sh; then
      record package-installed "uv"
      [[ -f "$HOME/.local/bin/env" ]] && source "$HOME/.local/bin/env"
    else
      note_skip "uv" "installer failed (network blocked?)"
    fi
  fi

  # ruff via uv tool
  if command -v uv &>/dev/null; then
    uv tool install ruff && record package-installed "ruff" \
      || note_skip "ruff" "uv tool install failed"
  else
    note_skip "ruff" "uv unavailable"
  fi

  # oh-my-posh via official installer
  if ! command -v oh-my-posh &>/dev/null; then
    echo "==> Installing oh-my-posh..."
    if curl -s https://ohmyposh.dev/install.sh | bash -s -- -d ~/.local/bin; then
      record package-installed "oh-my-posh"
    else
      note_skip "oh-my-posh" "installer failed (network blocked, or unzip missing?)"
    fi
  fi
fi

# NVM (both platforms — uses its own installer)
if [[ ! -d "$HOME/.nvm" ]]; then
  echo "==> Installing NVM..."
  curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/HEAD/install.sh | bash \
    || note_skip "NVM" "installer failed (network blocked?)"
fi

# Claude Code (needs node/npm from nvm)
echo "==> Installing Claude Code..."
if ! command -v npm &>/dev/null; then
  export NVM_DIR="$HOME/.nvm"
  [[ -s "$NVM_DIR/nvm.sh" ]] && source "$NVM_DIR/nvm.sh"
  if ! command -v npm &>/dev/null && command -v nvm &>/dev/null; then
    nvm install --lts || note_skip "node" "nvm install --lts failed"
  fi
fi
if command -v npm &>/dev/null; then
  if npm install -g @anthropic-ai/claude-code; then
    record package-installed "@anthropic-ai/claude-code"
  else
    note_skip "Claude Code" "npm install failed (registry blocked?)"
  fi
else
  note_skip "Claude Code" "npm unavailable (NVM install failed or skipped)"
fi

# ============================================================================
# Symlinks
# ============================================================================

echo "==> Symlinking dotfiles..."

symlink() {
  local src="$DOTFILES/$1" dst="$2" was_link=0
  if ! mkdir -p "$(dirname "$dst")"; then
    note_skip "symlink $dst" "could not create parent directory"
    return 1
  fi
  [[ -L "$dst" ]] && was_link=1
  if [[ -e "$dst" && ! -L "$dst" ]]; then
    if mv "$dst" "$dst.bak"; then
      record file-backed-up "$dst" "$dst.bak"
      echo "  Backing up $dst → $dst.bak"
    else
      note_skip "symlink $dst" "could not back up existing file"
      return 1
    fi
  fi
  if ln -sf "$src" "$dst"; then
    (( was_link )) || record symlink-created "$dst"
    echo "  linked $dst"
  else
    note_skip "symlink $dst" "ln failed"
  fi
}

# Common (both platforms)
symlink vim/.vimrc                ~/.vimrc
symlink zsh/.zshrc                ~/.zshrc
symlink zsh/.common_shell_aliases ~/.common_shell_aliases
symlink shell/.poshtheme.omp.json ~/.poshtheme.omp.json
symlink tmux/.tmux.conf           ~/.tmux.conf
symlink claude/CLAUDE.md          ~/.claude/CLAUDE.md
symlink claude/commands/status.md   ~/.claude/commands/status.md
symlink claude/commands/grill-me.md ~/.claude/commands/grill-me.md
symlink claude/commands/make-skill.md ~/.claude/commands/make-skill.md
symlink claude/commands/standup.md  ~/.claude/commands/standup.md
symlink claude/commands/second-opinion.md ~/.claude/commands/second-opinion.md
symlink claude/scripts/dev_status.py      ~/.claude/scripts/dev_status.py
symlink claude/scripts/gen_claude_completion.py ~/.claude/scripts/gen_claude_completion.py
symlink claude/scripts/grill.py           ~/.claude/scripts/grill.py
symlink claude/scripts/second_opinion.py  ~/.claude/scripts/second_opinion.py
symlink claude/scripts/standup.py         ~/.claude/scripts/standup.py
symlink claude/scripts/standup_adapters.py ~/.claude/scripts/standup_adapters.py
symlink claude/hooks/gsd-statusline.js    ~/.claude/hooks/gsd-statusline.js

# watchcommit: personal machines only — it auto-pushes to a personal remote
# under your personal Claude account login, which has no place on work hardware.
if [[ "$PROFILE" == "work" ]]; then
  echo "  watchcommit: excluded (work profile)"
else
  symlink scripts/watchcommit.py ~/.local/bin/watchcommit
  is_linux && symlink systemd/watchcommit.service ~/.config/systemd/user/watchcommit.service
fi

# settings.json is copied, not symlinked — Claude Code rewrites it in place,
# which would replace a symlink with a plain file and silently detach it.
# Copy-once; if the live file exists, report drift instead of touching it.
SETTINGS_SEED="$DOTFILES/claude/settings.json"
[[ "$PROFILE" == "work" ]] && SETTINGS_SEED="$DOTFILES/claude/settings.work.json"
SETTINGS_DRIFT=""
if [[ ! -f ~/.claude/settings.json ]]; then
  mkdir -p ~/.claude
  if cp "$SETTINGS_SEED" ~/.claude/settings.json; then
    record file-copied ~/.claude/settings.json
    echo "  copied ~/.claude/settings.json (from ${SETTINGS_SEED:t})"
  else
    note_skip "settings.json seed" "copy failed"
  fi
else
  SETTINGS_DRIFT="$(python3 - "$SETTINGS_SEED" "$HOME/.claude/settings.json" <<'PYEOF'
import json
import sys

seed, live = (json.load(open(p)) for p in sys.argv[1:3])
print(", ".join(k for k in sorted(set(seed) | set(live)) if seed.get(k) != live.get(k)))
PYEOF
)"
fi

# macOS-only (.zprofile has Homebrew shellenv; not needed on Linux)
if is_mac; then
  symlink zsh/.zprofile             ~/.zprofile
  symlink karabiner/karabiner.json  ~/.config/karabiner/karabiner.json
  symlink vscode/settings.json      "$HOME/Library/Application Support/Code/User/settings.json"
  symlink vscode/keybindings.json   "$HOME/Library/Application Support/Code/User/keybindings.json"
  if [[ "$PROFILE" != "work" ]]; then
    symlink launchd/com.user.watchcommit.plist ~/Library/LaunchAgents/com.user.watchcommit.plist
  fi
fi

# ============================================================================
# macOS: Rectangle prefs, Caps Lock → Escape, launchd agent
# ============================================================================

if is_mac; then
  echo "==> Importing Rectangle preferences..."
  defaults import com.knollsoft.Rectangle "$DOTFILES/rectangle/com.knollsoft.Rectangle.plist" \
    || note_skip "Rectangle preferences" "defaults import failed"

  echo "==> Setting Caps Lock → Escape..."
  python3 - << 'PYEOF' || note_skip "Caps Lock → Escape" "plist rewrite failed"
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

  if [[ "$PROFILE" != "work" ]]; then
    echo "==> Loading watchcommit launchd agent..."
    launchctl unload ~/Library/LaunchAgents/com.user.watchcommit.plist 2>/dev/null || true
    launchctl load ~/Library/LaunchAgents/com.user.watchcommit.plist \
      || note_skip "watchcommit agent" "launchctl load failed"
  fi
fi

# ============================================================================
# Linux/WSL: watchcommit systemd --user service
# ============================================================================

if is_linux && [[ "$PROFILE" != "work" ]]; then
  if command -v systemctl &>/dev/null && systemctl --user show-environment &>/dev/null; then
    echo "==> Enabling watchcommit systemd user service..."
    systemctl --user daemon-reload
    if systemctl --user enable --now watchcommit.service; then
      # Without lingering, the service dies when the last WSL/SSH session
      # closes — enable-linger keeps the user manager (and this unit) up.
      loginctl enable-linger "$USER" 2>/dev/null \
        || echo "  note: loginctl enable-linger failed — service won't survive full logout"
    else
      note_skip "watchcommit service" "systemctl --user enable --now failed"
    fi
  else
    note_skip "watchcommit service" "systemd --user unavailable (enable systemd in /etc/wsl.conf?)"
  fi
fi

# ============================================================================
# vim-plug (both platforms)
# ============================================================================

if [[ ! -f "$HOME/.vim/autoload/plug.vim" ]]; then
  echo "==> Installing vim-plug..."
  if curl -fLo ~/.vim/autoload/plug.vim --create-dirs \
      https://raw.githubusercontent.com/junegunn/vim-plug/master/plug.vim; then
    record file-copied ~/.vim/autoload/plug.vim
    echo "  Run :PlugInstall inside vim to install plugins"
  else
    note_skip "vim-plug" "download failed (network blocked?)"
  fi
fi

# ============================================================================
# Profile marker (guards later no-flag runs on a work machine)
# ============================================================================

if [[ "$PROFILE" == "work" && ! -f "$PROFILE_MARKER" ]]; then
  echo "work" > "$PROFILE_MARKER"
  record file-copied "$PROFILE_MARKER"
fi

# ============================================================================
# Summary
# ============================================================================

echo ""
echo "════════ Install summary — profile: $PROFILE ════════"
if (( ${#SKIPPED} )); then
  echo "⚠ ${#SKIPPED} step(s) DID NOT run:"
  for s in "${SKIPPED[@]}"; do
    echo "  ✗ $s"
  done
else
  echo "✓ all steps completed"
fi
if [[ -n "$SETTINGS_DRIFT" ]]; then
  echo "⚠ ~/.claude/settings.json drifted from ${SETTINGS_SEED:t}: $SETTINGS_DRIFT"
  echo "  (copy-once by design — port changes manually if wanted)"
fi
echo "  rollback available: ./install.sh --rollback (manifest: $MANIFEST)"

echo ""
echo "Manual steps:"
if is_mac; then
  echo "  - Log out and back in for Caps Lock → Escape to take effect"
  echo "  - Open Karabiner-Elements → grant Input Monitoring + Accessibility"
  echo "  - Open Rectangle → grant Accessibility permission"
fi
if [[ "$PROFILE" == "work" ]]; then
  echo "  - ~/.secrets is sourced if present — for work-issued tokens only;"
  echo "    do NOT put a personal ANTHROPIC_API_KEY on this machine"
else
  echo "  - Run 'claude login' if you haven't, so watchcommit can generate commit messages"
fi
is_linux && echo "  - Restart your shell to pick up the new config"

exit $(( ${#SKIPPED} > 0 ))
