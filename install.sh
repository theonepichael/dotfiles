#!/usr/bin/env zsh
# No set -e: every unit runs skip-and-report — a blocked installer or offline
# package mirror must not abort the rest of the run. Failures are collected
# and printed loudly in the end-of-run summary.

DOTFILES="$(cd "$(dirname "$0")" && pwd)"
OS="$(uname -s)"

STATE_DIR="$HOME/.local/state/dotfiles"
MANIFEST="$STATE_DIR/history.tsv"
PROFILE_MARKER="$STATE_DIR/profile"

is_mac()   { [[ "$OS" == "Darwin" ]] }
is_linux() { [[ "$OS" == "Linux" ]] }
is_wsl()   { is_linux && { [[ -n "$WSL_DISTRO_NAME" ]] || grep -qi microsoft /proc/version 2>/dev/null } }

usage() {
  cat <<'EOF'
usage: ./install.sh --harness=<claude,copilot,opencode,agy>[,...] [--profile=personal|work] [--rollback] [--force] [--dry-run]

  --harness   required unless --rollback. Comma-separated, at least one of:
              claude, copilot, opencode, agy. No default — every run must
              state its intent explicitly. Purely additive: omitting a harness
              you previously selected does NOT uninstall or clean it up,
              it just skips re-provisioning it this run. Removal is a
              --rollback concern (reverses every run recorded in the
              history file, not just the most recent one) or manual cleanup.
  --profile   personal (default) or work. Controls machine-level concerns
              ONLY: excludes watchcommit, excludes personal API-key setup,
              and seeds tightened settings/permission files where a
              profile-specific variant exists (settings.work.json,
              opencode.work.jsonc). Never restricts which harness(es) you
              can choose — --profile=work --harness=claude is honored as
              stated.
  --rollback  reverse every file mutation (symlinks, copies, backups) ever
              recorded across all past runs, using the history file, then
              exit. Not limited to the most recent run — running install.sh
              several times over weeks and then rolling back undoes all of
              it in one shot, oldest run included. Packages are reported
              but never uninstalled. Must be used alone (no --harness,
              --profile, or --force) — except --dry-run, see below.
  --force     override the work-profile guard on a machine previously
              provisioned with --profile=work
  --dry-run   print what every step would do without doing it: no packages
              installed, no files written/symlinked/removed, no manifest
              written. Detection (what's already installed, which
              profile/harness branches apply) still runs for real, so the
              preview reflects actual machine state. The one flag allowed
              alongside --rollback, to preview an undo before running it.

Examples:
  ./install.sh --harness=claude
  ./install.sh --profile=work --harness=copilot
  ./install.sh --harness=claude,opencode
  ./install.sh --profile=work --harness=copilot,opencode
  ./install.sh --harness=claude,agy
  ./install.sh --dry-run --harness=claude
  ./install.sh --dry-run --rollback

Exits 0 if every step ran, 1 if any step was skipped (see summary).
EOF
}

PROFILE=personal
FORCE=0
ROLLBACK=0
DRY_RUN=0
typeset -a HARNESSES
HARNESS_SET=0
for arg in "$@"; do
  case "$arg" in
    --profile=*) PROFILE="${arg#--profile=}" ;;
    # Append, not overwrite: --harness=claude --harness=copilot must
    # accumulate both, not silently drop the first on the second flag.
    --harness=*) HARNESSES+=("${(s:,:)${arg#--harness=}}"); HARNESS_SET=1 ;;
    --rollback)  ROLLBACK=1 ;;
    --force)     FORCE=1 ;;
    --dry-run)   DRY_RUN=1 ;;
    -h|--help)   usage; exit 0 ;;
    *)           echo "unknown argument: $arg" >&2; usage >&2; exit 2 ;;
  esac
done

if [[ "$PROFILE" != "personal" && "$PROFILE" != "work" ]]; then
  echo "invalid --profile: $PROFILE (must be personal or work)" >&2
  exit 2
fi

# Empty --harness= (e.g. a stray trailing comma or a copy-paste mistake)
# gets its own message — checked before the generic validation loop below,
# since its catch-all case would otherwise match an empty string too and
# report it as an "unknown harness" with a blank name.
for h in "${HARNESSES[@]}"; do
  [[ -z "$h" ]] && { echo "--harness has an empty value — check for a stray comma" >&2; exit 2; }
done

for h in "${HARNESSES[@]}"; do
  case "$h" in
    claude|copilot|opencode|agy) ;;
    *) echo "unknown harness: $h (must be claude, copilot, opencode, and/or agy)" >&2; exit 2 ;;
  esac
done

# --rollback is an undo-only action — reject it combined with anything
# that implies a forward install (not just --harness; --profile/--force
# alongside --rollback would otherwise be silently ignored, misleading
# someone into thinking they rolled back "as work" or similar).
if (( ROLLBACK )) && { (( HARNESS_SET )) || [[ "$PROFILE" != personal ]] || (( FORCE )); }; then
  echo "--rollback must be used alone, with no other flags" >&2
  exit 2
fi

# --rollback doesn't need a harness; every other invocation does.
if (( ! ROLLBACK )) && (( ! HARNESS_SET )); then
  echo "no --harness specified — pass at least one of: claude, copilot, opencode, agy" >&2
  usage >&2
  exit 2
fi

has_harness() { (( ${HARNESSES[(Ie)$1]} )) }

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

# history.tsv is append-only across every run, never truncated — this is
# what makes --rollback a full-history undo instead of last-run-only: the
# do_rollback loop below already walks the whole file in reverse, so once
# old runs stop getting erased at the top of each new run, they're
# automatically included in the next rollback too. A full rollback deletes
# the file at the end, which is correct: at that point every recorded
# mutation really has been undone, so the next run starts a genuinely fresh
# history rather than one padded with already-reversed entries.
manifest_init() {
  if (( DRY_RUN )); then
    echo "  [dry-run] would record a new run in $MANIFEST"
    return
  fi
  mkdir -p "$STATE_DIR"
  printf 'run\t%s\t%s\n' "$(date -Iseconds)" "$PROFILE" >> "$MANIFEST"
}

record() {  # record <action> <path> [extra]  (paths never contain tabs)
  (( DRY_RUN )) && return
  printf '%s\t%s\t%s\n' "$1" "$2" "${3:-}" >> "$MANIFEST"
}

typeset -a ROLLBACK_SKIPPED
rollback_skip() {  # rollback_skip <message>  — mirrors note_skip, own tally
  ROLLBACK_SKIPPED+=("$1")
  echo "  !! SKIPPED: $1"
}

do_rollback() {
  if [[ ! -f "$MANIFEST" ]]; then
    echo "no manifest at $MANIFEST — nothing to roll back" >&2
    exit 1
  fi
  local -a lines
  lines=("${(@f)$(<"$MANIFEST")}")
  local run_count=0 line
  for line in "${lines[@]}"; do
    [[ "$line" == run$'\t'* ]] && (( run_count++ ))
  done
  if (( DRY_RUN )); then
    echo "==> Would roll back $run_count recorded run(s) from $MANIFEST"
  else
    echo "==> Rolling back $run_count recorded run(s) from $MANIFEST"
  fi
  # Tracks which backup paths this pass has already restored, so an older,
  # duplicate file-backed-up entry for the same path (recorded across an
  # earlier backup/rollback/reinstall cycle) is recognized as already
  # handled rather than misreported as a missing backup.
  typeset -A RESTORED
  local i action a b
  for (( i = ${#lines}; i >= 1; i-- )); do
    IFS=$'\t' read -r action a b <<< "${lines[i]}"
    case "$action" in
      symlink-created)
        # $b is the symlink's original target (recorded by symlink()); older
        # shim-created entries predate that field and leave it empty, so
        # they fall back to unconditionally removing whatever's there.
        if [[ -L "$a" ]]; then
          if [[ -n "$b" && "$(readlink "$a")" != "$b" ]]; then
            rollback_skip "symlink $a now points to $(readlink "$a"), not $b — something else has claimed this path, leaving it alone"
          elif (( DRY_RUN )); then
            echo "  [dry-run] would remove symlink $a"
          else
            rm "$a" && echo "  removed symlink $a"
          fi
        fi ;;
      file-copied)
        if (( DRY_RUN )); then
          [[ -f "$a" ]] && echo "  [dry-run] would remove $a"
        else
          [[ -f "$a" ]] && rm "$a" && echo "  removed $a"
        fi ;;
      file-backed-up)
        if [[ -e "$b" ]]; then
          if (( DRY_RUN )); then
            echo "  [dry-run] would restore $a from $b"
          else
            mv "$b" "$a" && echo "  restored $a from $b" && RESTORED[$b]=1
          fi
        elif [[ -z "${RESTORED[$b]:-}" ]]; then
          rollback_skip "backup $b for $a not found — already restored, or removed outside install.sh"
        fi ;;
      package-installed)
        echo "  package left installed (profile-independent): $a" ;;
      run)
        echo "  (run was: $a, profile: $b)" ;;
    esac
  done
  if (( DRY_RUN )); then
    echo "Dry run complete — nothing was changed. Re-run without --dry-run to roll back for real."
  else
    rm "$MANIFEST"
    echo "Rollback complete. Re-run ./install.sh with the intended profile."
  fi
  if (( ${#ROLLBACK_SKIPPED} )); then
    echo "⚠ ${#ROLLBACK_SKIPPED} rollback step(s) did not apply cleanly (see SKIPPED lines above)"
  fi
  exit $(( ${#ROLLBACK_SKIPPED} > 0 ))
}

(( ROLLBACK )) && do_rollback

# ============================================================================
# Work-profile guard
# ============================================================================

if [[ "$PROFILE" == "personal" && -f "$PROFILE_MARKER" ]] \
   && [[ "$(<"$PROFILE_MARKER")" == "work" ]] && (( ! FORCE )); then
  echo "This machine is provisioned as WORK (marker: $PROFILE_MARKER)." >&2
  echo "Pass --profile=work, or --force to provision as personal anyway." >&2
  exit 2
fi

manifest_init
if (( DRY_RUN )); then
  echo "==> DRY RUN — no changes will be made. Profile: $PROFILE"
else
  echo "==> Installing with profile: $PROFILE"
fi

# ============================================================================
# Packages
# ============================================================================

if is_mac; then
  if ! command -v brew &>/dev/null; then
    if (( DRY_RUN )); then
      # Homebrew itself is a real mutation, so it's skipped here too — which
      # means the "brew unavailable" branch below fires in dry-run on a
      # brew-less machine just like it would for real. Preview can only show
      # one install-time dependency deep; a real run is needed before the
      # formulae/casks step downstream of Homebrew can itself be previewed.
      echo "  [dry-run] would install Homebrew (curl raw.githubusercontent.com/Homebrew/install | bash)"
    else
      echo "==> Installing Homebrew..."
      /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)" \
        || note_skip "Homebrew" "installer failed (network blocked?)"
    fi
  fi

  if [[ -x /opt/homebrew/bin/brew ]]; then
    eval "$(/opt/homebrew/bin/brew shellenv)"
  elif [[ -x /usr/local/bin/brew ]]; then
    eval "$(/usr/local/bin/brew shellenv)"
  fi

  if command -v brew &>/dev/null; then
    if (( DRY_RUN )); then
      echo "  [dry-run] would install formulae: python@3.13 uv ruff tmux zoxide eza bat ripgrep lsd ncdu tldr oh-my-posh neovim fd"
      echo "  [dry-run] would install casks: karabiner-elements rectangle ghostty visual-studio-code alt-tab font-jetbrains-mono-nerd-font"
    else
      echo "==> Installing formulae..."
      if brew install \
          python@3.13 uv ruff \
          tmux zoxide eza bat ripgrep lsd ncdu tldr \
          oh-my-posh neovim fd; then
        record package-installed "brew formulae"
      else
        note_skip "brew formulae" "brew install failed"
      fi

      echo "==> Installing casks..."
      if brew install --cask karabiner-elements rectangle ghostty visual-studio-code alt-tab font-jetbrains-mono-nerd-font; then
        record package-installed "brew casks"
      else
        note_skip "brew casks" "brew install --cask failed"
      fi
    fi
  else
    note_skip "brew formulae + casks" "brew unavailable"
  fi

elif is_linux; then
  PKG_LIST=(tmux zoxide eza bat lsd ncdu tldr ripgrep unzip lsof xclip fontconfig neovim fd-find)

  if command -v dnf &>/dev/null; then
    if (( DRY_RUN )); then
      echo "  [dry-run] would run: sudo dnf makecache"
    else
      echo "==> Refreshing dnf package metadata..."
      sudo dnf makecache || note_skip "dnf makecache" "dnf makecache failed (offline or blocked?)"
    fi

    # One dnf call per package, same reasoning as the apt branch below: a
    # single missing/renamed package shouldn't block every package after it.
    echo "==> Installing packages (dnf)..."
    for pkg in "${PKG_LIST[@]}"; do
      if (( DRY_RUN )); then
        echo "  [dry-run] would run: sudo dnf install -y $pkg"
      elif sudo dnf install -y "$pkg"; then
        record package-installed "$pkg"
      else
        note_skip "dnf package: $pkg" "not available in this release's repos, or install failed"
      fi
    done
  else
    if (( DRY_RUN )); then
      echo "  [dry-run] would run: sudo apt-get update"
    else
      echo "==> Updating apt package lists..."
      sudo apt-get update || note_skip "apt update" "apt-get update failed (offline or blocked?)"
    fi

    # One apt-get call per package: apt-get install fails atomically on the
    # first unresolvable name, which would otherwise block every package after
    # it — e.g. eza/lsd don't exist before Ubuntu 24.04, which silently
    # skipped tmux/bat/ncdu/tldr/ripgrep/unzip too on 22.04 machines.
    echo "==> Installing packages (apt)..."
    for pkg in "${PKG_LIST[@]}"; do
      if (( DRY_RUN )); then
        echo "  [dry-run] would run: sudo apt-get install -y $pkg"
      elif sudo apt-get install -y "$pkg"; then
        record package-installed "$pkg"
      else
        note_skip "apt package: $pkg" "not available in this release's repos, or install failed"
      fi
    done
  fi

  # Ubuntu ships bat as batcat; shim it (Fedora's bat package installs the
  # `bat` binary directly, so this is a no-op there)
  if command -v batcat &>/dev/null && ! command -v bat &>/dev/null; then
    if (( DRY_RUN )); then
      echo "  [dry-run] would shim bat → batcat (~/.local/bin/bat)"
    else
      mkdir -p ~/.local/bin
      ln -sf "$(which batcat)" ~/.local/bin/bat
      record symlink-created ~/.local/bin/bat
      echo "  shimmed bat → batcat"
    fi
  fi

  # Same deal for fd: Debian/Ubuntu's fd-find package installs as fdfind
  # (name conflict with an unrelated existing `fd` package); Fedora's
  # fd-find installs the `fd` binary directly, so this is a no-op there.
  if command -v fdfind &>/dev/null && ! command -v fd &>/dev/null; then
    if (( DRY_RUN )); then
      echo "  [dry-run] would shim fd → fdfind (~/.local/bin/fd)"
    else
      mkdir -p ~/.local/bin
      ln -sf "$(which fdfind)" ~/.local/bin/fd
      record symlink-created ~/.local/bin/fd
      echo "  shimmed fd → fdfind"
    fi
  fi

  # uv (not in apt/dnf)
  if ! command -v uv &>/dev/null; then
    if (( DRY_RUN )); then
      echo "  [dry-run] would install uv (curl astral.sh/uv/install.sh | sh)"
    else
      echo "==> Installing uv..."
      if curl -LsSf https://astral.sh/uv/install.sh | sh; then
        record package-installed "uv"
        [[ -f "$HOME/.local/bin/env" ]] && source "$HOME/.local/bin/env"
      else
        note_skip "uv" "installer failed (network blocked?)"
      fi
    fi
  fi

  # ruff via uv tool
  if command -v uv &>/dev/null; then
    if (( DRY_RUN )); then
      echo "  [dry-run] would run: uv tool install ruff"
    else
      uv tool install ruff && record package-installed "ruff" \
        || note_skip "ruff" "uv tool install failed"
    fi
  else
    note_skip "ruff" "uv unavailable"
  fi

  # oh-my-posh via official installer
  if ! command -v oh-my-posh &>/dev/null; then
    if (( DRY_RUN )); then
      echo "  [dry-run] would install oh-my-posh (curl ohmyposh.dev/install.sh | bash -s -- -d ~/.local/bin)"
    else
      echo "==> Installing oh-my-posh..."
      if curl -s https://ohmyposh.dev/install.sh | bash -s -- -d ~/.local/bin; then
        record package-installed "oh-my-posh"
      else
        note_skip "oh-my-posh" "installer failed (network blocked, or unzip missing?)"
      fi
    fi
  fi

  # JetBrainsMono Nerd Font — not in apt/dnf as the patched (icon-glyph)
  # variant, so pull the pinned release directly. Pinned rather than
  # "latest" so every machine ends up with byte-identical fonts; bump
  # NERD_FONT_VERSION manually to update. Version-marker file makes this
  # idempotent without re-downloading/re-extracting 90+ files every run.
  NERD_FONT_VERSION="3.4.0"
  NERD_FONT_DIR="$HOME/.local/share/fonts/JetBrainsMonoNerdFont"
  NERD_FONT_MARKER="$NERD_FONT_DIR/.nerd-fonts-version"
  if [[ "$(cat "$NERD_FONT_MARKER" 2>/dev/null)" != "$NERD_FONT_VERSION" ]]; then
    if (( DRY_RUN )); then
      echo "  [dry-run] would install JetBrainsMono Nerd Font v${NERD_FONT_VERSION} to $NERD_FONT_DIR"
    else
      echo "==> Installing JetBrainsMono Nerd Font v${NERD_FONT_VERSION}..."
      NERD_FONT_TMP="$(mktemp -d)"
      if curl -fLo "$NERD_FONT_TMP/JetBrainsMono.zip" \
          "https://github.com/ryanoasis/nerd-fonts/releases/download/v${NERD_FONT_VERSION}/JetBrainsMono.zip" \
          && mkdir -p "$NERD_FONT_DIR" \
          && unzip -oq "$NERD_FONT_TMP/JetBrainsMono.zip" -d "$NERD_FONT_DIR" \
          && echo "$NERD_FONT_VERSION" > "$NERD_FONT_MARKER"; then
        record package-installed "JetBrainsMono Nerd Font v${NERD_FONT_VERSION}"
        fc-cache -f "$NERD_FONT_DIR" &>/dev/null
        echo "  installed to $NERD_FONT_DIR"
      else
        note_skip "JetBrainsMono Nerd Font" "download/extract failed (network blocked, or unzip missing?)"
      fi
      rm -rf "$NERD_FONT_TMP"
    fi
  fi
fi

# NVM + Node/npm — only needed by Claude Code and/or Copilot CLI; opencode
# and agy both manage their own runtime externally (this script never
# installs the opencode or agy binaries either — agy is a standalone Go
# binary, not an npm package). Not provisioned when opencode/agy are the
# only harness(es) selected.
if has_harness claude || has_harness copilot; then
  # NVM (both platforms — uses its own installer)
  if [[ ! -d "$HOME/.nvm" ]]; then
    if (( DRY_RUN )); then
      echo "  [dry-run] would install NVM (curl nvm-sh/nvm install.sh | bash)"
    else
      echo "==> Installing NVM..."
      curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/HEAD/install.sh | bash \
        || note_skip "NVM" "installer failed (network blocked?)"
    fi
  fi

  # Node/npm
  if ! command -v npm &>/dev/null; then
    export NVM_DIR="$HOME/.nvm"
    [[ -s "$NVM_DIR/nvm.sh" ]] && source "$NVM_DIR/nvm.sh"
    if ! command -v npm &>/dev/null && command -v nvm &>/dev/null; then
      if (( DRY_RUN )); then
        echo "  [dry-run] would run: nvm install --lts"
      else
        nvm install --lts || note_skip "node" "nvm install --lts failed"
      fi
    fi
  fi
fi

# Claude Code
if has_harness claude; then
  if command -v npm &>/dev/null; then
    if (( DRY_RUN )); then
      echo "  [dry-run] would run: npm install -g @anthropic-ai/claude-code"
    else
      echo "==> Installing Claude Code..."
      if npm install -g @anthropic-ai/claude-code; then
        record package-installed "@anthropic-ai/claude-code"
      else
        note_skip "Claude Code" "npm install failed (registry blocked?)"
      fi
    fi
  else
    note_skip "Claude Code" "npm unavailable (NVM install failed or skipped)"
  fi
else
  echo "  Claude Code: skipped (not in --harness)"
fi

# GitHub Copilot CLI
if has_harness copilot; then
  if command -v npm &>/dev/null; then
    if (( DRY_RUN )); then
      echo "  [dry-run] would run: npm install -g @github/copilot"
    else
      echo "==> Installing GitHub Copilot CLI..."
      if npm install -g @github/copilot; then
        record package-installed "@github/copilot"
      else
        note_skip "Copilot CLI" "npm install failed (registry blocked?)"
      fi
    fi
  else
    note_skip "Copilot CLI" "npm unavailable (NVM install failed or skipped)"
  fi
else
  echo "  Copilot CLI: skipped (not in --harness)"
fi

# ============================================================================
# Symlinks
# ============================================================================

echo "==> Symlinking dotfiles..."

symlink() {
  local src="$DOTFILES/$1" dst="$2" was_link=0

  if (( DRY_RUN )); then
    if [[ -L "$dst" ]]; then
      local current_target
      current_target="$(readlink "$dst")"
      if [[ "$current_target" == "$src" ]]; then
        echo "  [dry-run] $dst already correctly linked → $src, no-op"
      else
        echo "  [dry-run] would relink $dst → $src (currently → $current_target)"
      fi
    elif [[ -e "$dst" ]]; then
      echo "  [dry-run] would back up $dst → $dst.bak, then link $dst → $src"
    else
      echo "  [dry-run] would link $dst → $src"
    fi
    return 0
  fi

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
    (( was_link )) || record symlink-created "$dst" "$src"
    echo "  linked $dst"
  else
    note_skip "symlink $dst" "ln failed"
  fi
}

# Common (both platforms)
symlink vim/.vimrc                ~/.vimrc
symlink nvim                      ~/.config/nvim
symlink zsh/.zshrc                ~/.zshrc
symlink zsh/.common_shell_aliases ~/.common_shell_aliases
symlink shell/.poshtheme.omp.json ~/.poshtheme.omp.json
symlink tmux/.tmux.conf           ~/.tmux.conf

# Claude Code-specific wiring
if has_harness claude; then
  symlink claude/CLAUDE.md          ~/.claude/CLAUDE.md
  symlink claude/commands/dashboard.md   ~/.claude/commands/dashboard.md
  symlink claude/commands/grill-me.md ~/.claude/commands/grill-me.md
  symlink claude/commands/make-skill.md ~/.claude/commands/make-skill.md
  symlink claude/commands/standup.md  ~/.claude/commands/standup.md
  symlink claude/commands/second-opinion.md ~/.claude/commands/second-opinion.md
  symlink claude/hooks/gsd-statusline.js    ~/.claude/hooks/gsd-statusline.js
fi

# Shared scripts — needed by Claude Code's hooks AND Copilot's AND opencode's
# hooks/skills (opencode's own dashboard.md/grill-me.md commands call these
# same paths), so these stay symlinked regardless of which harness(es) are
# selected.
symlink claude/scripts/dev_status.py      ~/.claude/scripts/dev_status.py
symlink claude/scripts/gen_claude_completion.py ~/.claude/scripts/gen_claude_completion.py
symlink claude/scripts/grill.py           ~/.claude/scripts/grill.py
symlink claude/scripts/second_opinion.py  ~/.claude/scripts/second_opinion.py
symlink claude/scripts/standup.py         ~/.claude/scripts/standup.py
symlink claude/scripts/standup_adapters.py ~/.claude/scripts/standup_adapters.py
symlink claude/scripts/dotfiles_sync_check.py ~/.claude/scripts/dotfiles_sync_check.py

# GitHub Copilot CLI wiring
if has_harness copilot; then
  symlink claude/CLAUDE.md ~/.copilot/copilot-instructions.md
  symlink copilot/hooks/session-start.json ~/.copilot/hooks/session-start.json
  symlink copilot/skills/dashboard/SKILL.md       ~/.copilot/skills/dashboard/SKILL.md
  symlink copilot/skills/standup/SKILL.md         ~/.copilot/skills/standup/SKILL.md
  symlink copilot/skills/second-opinion/SKILL.md  ~/.copilot/skills/second-opinion/SKILL.md
  symlink copilot/skills/grill-me/SKILL.md        ~/.copilot/skills/grill-me/SKILL.md
  symlink copilot/skills/make-skill/SKILL.md      ~/.copilot/skills/make-skill/SKILL.md
  symlink copilot/aliases.zsh ~/.copilot_aliases
fi

# opencode. All 5 commands (dashboard, grill-me, make-skill, second-opinion,
# standup) live in the repo at opencode/command/ and are symlinked into
# ~/.config/opencode/commands/ (see opencode/CLAUDE_CODE_PARITY.md §2 for
# the format, the official-docs-informed frontmatter schema, and the history
# of the staged pull-into-repo).
# AGENTS.md is intentionally not managed here — opencode already reads
# ~/.claude/CLAUDE.md directly as a legacy fallback when no AGENTS.md exists
# (see CLAUDE_CODE_PARITY.md §1), so creating one here would just duplicate
# the same content under a second path for no behavioral gain.
OPENCODE_JSONC_DRIFT=""
if has_harness opencode; then
  symlink opencode/tui.json ~/.config/opencode/tui.json
  symlink opencode/command/dashboard.md      ~/.config/opencode/commands/dashboard.md
  symlink opencode/command/grill-me.md       ~/.config/opencode/commands/grill-me.md
  symlink opencode/command/make-skill.md     ~/.config/opencode/commands/make-skill.md
  symlink opencode/command/second-opinion.md ~/.config/opencode/commands/second-opinion.md
  symlink opencode/command/standup.md        ~/.config/opencode/commands/standup.md

  # opencode.jsonc holds the bash permission allowlist (opencode's equivalent
  # of claude/settings.json's permissions block) — copied, not symlinked, on
  # the same reasoning as settings.json below: opencode likely rewrites this
  # file in place as permissions get approved live, which would detach a
  # symlink silently. Copy-once; if the live file exists, report drift.
  # Profile-specific seed, same pattern as settings.json/settings.work.json:
  # opencode.work.jsonc additionally drops a handful of individually-risky
  # (but not allowlist-bypassing) commands that opencode.jsonc keeps for
  # personal-machine convenience.
  OPENCODE_SEED="$DOTFILES/opencode/opencode.jsonc"
  [[ "$PROFILE" == "work" ]] && OPENCODE_SEED="$DOTFILES/opencode/opencode.work.jsonc"
  if [[ ! -f ~/.config/opencode/opencode.jsonc ]]; then
    if (( DRY_RUN )); then
      echo "  [dry-run] would copy ~/.config/opencode/opencode.jsonc (from ${OPENCODE_SEED:t})"
    else
      mkdir -p ~/.config/opencode
      if cp "$OPENCODE_SEED" ~/.config/opencode/opencode.jsonc; then
        record file-copied ~/.config/opencode/opencode.jsonc
        echo "  copied ~/.config/opencode/opencode.jsonc (from ${OPENCODE_SEED:t})"
      else
        note_skip "opencode.jsonc seed" "copy failed"
      fi
    fi
  else
    OPENCODE_JSONC_DRIFT="$(python3 - "$OPENCODE_SEED" "$HOME/.config/opencode/opencode.jsonc" <<'PYEOF'
import json
import sys

seed, live = (json.load(open(p)) for p in sys.argv[1:3])
drift = sorted(k for k in set(seed) | set(live) if seed.get(k) != live.get(k))

# xargs/awk are allowlist bypasses (xargs invokes arbitrary commands as its
# own argument; awk has a system() call) — call these out specifically
# rather than folding them into the generic top-level key list, since they
# live nested under permission.bash and a top-level diff would just say
# "permission" changed without saying which command pattern moved.
seed_bash = seed.get("permission", {}).get("bash", {})
live_bash = live.get("permission", {}).get("bash", {})
bypass_gone = [k for k in ("xargs *", "awk *") if k in live_bash and k not in seed_bash]
if bypass_gone:
    print(f"SECURITY: {', '.join(bypass_gone)} still allowed in your live "
          f"opencode.jsonc (allowlist bypass) — delete the file and re-run to fix")
elif drift:
    print(", ".join(drift))
PYEOF
)"
  fi
fi

# agy (Google Antigravity CLI, Gemini-backed). GEMINI.md is agy's own
# CLAUDE.md-equivalent global rules file — unlike opencode (which reads
# ~/.claude/CLAUDE.md directly as a legacy fallback, needing no file of its
# own here), agy specifically looks for ~/.gemini/GEMINI.md, so it needs a
# real symlink. Skills live under ~/.gemini/antigravity-cli/skills/ per
# agy's current (post-migration) global-skills path — NOT the legacy bare
# ~/.gemini/skills/ path some skills may have been manually copied into
# before this wiring existed; see agy/CLAUDE_CODE_PARITY.md.
if has_harness agy; then
  symlink claude/CLAUDE.md ~/.gemini/GEMINI.md
  symlink agy/skills/dashboard/SKILL.md       ~/.gemini/antigravity-cli/skills/dashboard/SKILL.md
  symlink agy/skills/standup/SKILL.md         ~/.gemini/antigravity-cli/skills/standup/SKILL.md
  symlink agy/skills/second-opinion/SKILL.md  ~/.gemini/antigravity-cli/skills/second-opinion/SKILL.md
  symlink agy/skills/grill-me/SKILL.md        ~/.gemini/antigravity-cli/skills/grill-me/SKILL.md
  symlink agy/skills/make-skill/SKILL.md      ~/.gemini/antigravity-cli/skills/make-skill/SKILL.md
fi

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
# Skipped entirely when Claude Code isn't selected — nothing else reads this file.
SETTINGS_DRIFT=""
if has_harness claude; then
  SETTINGS_SEED="$DOTFILES/claude/settings.json"
  [[ "$PROFILE" == "work" ]] && SETTINGS_SEED="$DOTFILES/claude/settings.work.json"
  if [[ ! -f ~/.claude/settings.json ]]; then
    if (( DRY_RUN )); then
      echo "  [dry-run] would copy ~/.claude/settings.json (from ${SETTINGS_SEED:t})"
    else
      mkdir -p ~/.claude
      if cp "$SETTINGS_SEED" ~/.claude/settings.json; then
        record file-copied ~/.claude/settings.json
        echo "  copied ~/.claude/settings.json (from ${SETTINGS_SEED:t})"
      else
        note_skip "settings.json seed" "copy failed"
      fi
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
fi

# macOS-only (.zprofile has Homebrew shellenv; not needed on Linux)
if is_mac; then
  symlink zsh/.zprofile             ~/.zprofile
  symlink karabiner/karabiner.json  ~/.config/karabiner/karabiner.json
  if [[ "$PROFILE" != "work" ]]; then
    symlink launchd/com.user.watchcommit.plist ~/Library/LaunchAgents/com.user.watchcommit.plist
  fi
fi

# VS Code settings/keybindings — not mac-only, VS Code runs on Linux and WSL
# too, just at a different config path per OS.
if is_mac; then
  symlink vscode/settings.json    "$HOME/Library/Application Support/Code/User/settings.json"
  symlink vscode/keybindings.json "$HOME/Library/Application Support/Code/User/keybindings.json"
elif is_wsl; then
  # Under WSL, VS Code is normally driven from the Windows-side GUI via the
  # Remote-WSL extension, so the real user settings.json lives in the Windows
  # user profile, not in the WSL filesystem. Derive that profile dir from the
  # Windows-side `code` shim's own path (inherited onto PATH via WSL interop)
  # rather than hardcoding a username.
  CODE_BIN="$(command -v code 2>/dev/null)"
  if [[ "$CODE_BIN" == /mnt/*/Users/*/AppData/* ]]; then
    WIN_USER_DIR="${CODE_BIN%%/AppData/*}"
    symlink vscode/settings.json    "$WIN_USER_DIR/AppData/Roaming/Code/User/settings.json"
    symlink vscode/keybindings.json "$WIN_USER_DIR/AppData/Roaming/Code/User/keybindings.json"
  else
    note_skip "VS Code settings" "WSL detected but no Windows-side 'code' CLI found on PATH"
  fi
else
  symlink vscode/settings.json    ~/.config/Code/User/settings.json
  symlink vscode/keybindings.json ~/.config/Code/User/keybindings.json
fi

# ============================================================================
# macOS: Rectangle prefs, Caps Lock → Escape, launchd agent
# ============================================================================

if is_mac; then
  if (( DRY_RUN )); then
    echo "  [dry-run] would import Rectangle preferences from $DOTFILES/rectangle/com.knollsoft.Rectangle.plist"
  else
    echo "==> Importing Rectangle preferences..."
    defaults import com.knollsoft.Rectangle "$DOTFILES/rectangle/com.knollsoft.Rectangle.plist" \
      || note_skip "Rectangle preferences" "defaults import failed"
  fi

  if (( DRY_RUN )); then
    echo "  [dry-run] would set Caps Lock → Escape (rewrite ~/Library/Preferences/ByHost/.GlobalPreferences.*.plist)"
  else
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
  fi

  if [[ "$PROFILE" != "work" ]]; then
    if (( DRY_RUN )); then
      echo "  [dry-run] would (re)load watchcommit launchd agent"
    else
      echo "==> Loading watchcommit launchd agent..."
      launchctl unload ~/Library/LaunchAgents/com.user.watchcommit.plist 2>/dev/null || true
      launchctl load ~/Library/LaunchAgents/com.user.watchcommit.plist \
        || note_skip "watchcommit agent" "launchctl load failed"
    fi
  fi
fi

# ============================================================================
# Linux/WSL: watchcommit systemd --user service
# ============================================================================

if is_linux && [[ "$PROFILE" != "work" ]]; then
  if command -v systemctl &>/dev/null && systemctl --user show-environment &>/dev/null; then
    if (( DRY_RUN )); then
      echo "  [dry-run] would enable+start watchcommit systemd user service, enable-linger for $USER"
    else
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
    fi
  else
    note_skip "watchcommit service" "systemd --user unavailable (enable systemd in /etc/wsl.conf?)"
  fi
fi

# ============================================================================
# vim-plug (both platforms)
# ============================================================================

if [[ ! -f "$HOME/.vim/autoload/plug.vim" ]]; then
  if (( DRY_RUN )); then
    echo "  [dry-run] would install vim-plug to ~/.vim/autoload/plug.vim"
  else
    echo "==> Installing vim-plug..."
    if curl -fLo ~/.vim/autoload/plug.vim --create-dirs \
        https://raw.githubusercontent.com/junegunn/vim-plug/master/plug.vim; then
      record file-copied ~/.vim/autoload/plug.vim
      echo "  Run :PlugInstall inside vim to install plugins"
    else
      note_skip "vim-plug" "download failed (network blocked?)"
    fi
  fi
fi

# ============================================================================
# Neovim: lazy.nvim plugin bootstrap (both platforms)
# ============================================================================

# The vendored config (nvim/, from theonepichael/nvim-config) targets
# Neovim 0.11+. Older distro repos (e.g. Ubuntu 22.04's apt package) can
# ship older builds — same class of version gap as the eza/lsd note above —
# so this degrades to a skip rather than the upstream config's own
# hard `exit 1`, consistent with this script never aborting the run.
if ! command -v nvim &>/dev/null; then
  note_skip "Neovim plugin bootstrap" "nvim not installed"
else
  NVIM_VERSION="$(nvim --version | head -n1 | sed 's/^NVIM v//')"
  NVIM_MAJOR="${NVIM_VERSION%%.*}"
  NVIM_MINOR="$(echo "$NVIM_VERSION" | cut -d. -f2)"
  if (( NVIM_MAJOR == 0 && NVIM_MINOR < 11 )); then
    note_skip "Neovim plugin bootstrap" "nvim $NVIM_VERSION found, config needs >=0.11"
  elif (( DRY_RUN )); then
    echo "  [dry-run] would run: nvim --headless \"+Lazy! sync\" +qa (nvim $NVIM_VERSION)"
  else
    echo "==> Bootstrapping Neovim plugins (lazy.nvim sync, nvim $NVIM_VERSION)..."
    if nvim --headless "+Lazy! sync" +qa; then
      echo "  plugins synced"
    else
      note_skip "Neovim plugin sync" "'Lazy! sync' reported errors — run :Lazy sync manually inside nvim"
    fi
  fi
fi

# ============================================================================
# Profile marker (guards later no-flag runs on a work machine)
# ============================================================================

if [[ "$PROFILE" == "work" && ! -f "$PROFILE_MARKER" ]]; then
  if (( DRY_RUN )); then
    echo "  [dry-run] would write profile marker: $PROFILE_MARKER"
  else
    echo "work" > "$PROFILE_MARKER"
    record file-copied "$PROFILE_MARKER"
  fi
fi

# ============================================================================
# Summary
# ============================================================================

echo ""
if (( DRY_RUN )); then
  echo "════════ Dry run summary — profile: $PROFILE ════════"
else
  echo "════════ Install summary — profile: $PROFILE ════════"
fi
if (( ${#SKIPPED} )); then
  echo "⚠ ${#SKIPPED} step(s) DID NOT run:"
  for s in "${SKIPPED[@]}"; do
    echo "  ✗ $s"
  done
elif (( DRY_RUN )); then
  echo "✓ all steps previewed cleanly (no real detection failures)"
else
  echo "✓ all steps completed"
fi
if [[ -n "$SETTINGS_DRIFT" ]]; then
  echo "⚠ ~/.claude/settings.json drifted from ${SETTINGS_SEED:t}: $SETTINGS_DRIFT"
  echo "  (copy-once by design — port changes manually if wanted)"
fi
if [[ -n "$OPENCODE_JSONC_DRIFT" ]]; then
  echo "⚠ ~/.config/opencode/opencode.jsonc drifted from ${OPENCODE_SEED:t}: $OPENCODE_JSONC_DRIFT"
  echo "  (copy-once by design — port changes manually if wanted)"
fi
if (( DRY_RUN )); then
  echo "  dry run — nothing was changed; re-run without --dry-run to apply"
else
  echo "  rollback available: ./install.sh --rollback (manifest: $MANIFEST)"
fi

if (( ! DRY_RUN )); then
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
  elif has_harness claude; then
    echo "  - Run 'claude login' if you haven't, so watchcommit can generate commit messages"
  fi
  is_linux && echo "  - Restart your shell to pick up the new config"
fi

exit $(( ${#SKIPPED} > 0 ))
