#!/usr/bin/env bash
# Scenario suite for install.sh, meant to run inside test/run.sh's container.
# Exercises the full lifecycle: fresh install, rollback, backup-and-restore
# of a pre-existing dotfile, work profile + guard, --force override, and
# argument-parsing edge cases. Not meant to run on a real machine.
set -uo pipefail

DOTFILES="$HOME/dotfiles"
STATE_DIR="$HOME/.local/state/dotfiles"
MANIFEST="$STATE_DIR/last-run.tsv"
MARKER="$STATE_DIR/profile"

cd "$DOTFILES"

PASS=0
FAIL=0
check() {  # check <description> <command...>
  local desc="$1"; shift
  if "$@" >/tmp/check.out 2>&1; then
    echo "  PASS: $desc"
    PASS=$((PASS + 1))
  else
    echo "  FAIL: $desc"
    sed 's/^/         | /' /tmp/check.out
    FAIL=$((FAIL + 1))
  fi
}

manifest_has() { grep -qF "$1" "$MANIFEST"; }

echo "=== 1. Fresh personal install ==="
./install.sh >/tmp/install.out 2>&1
code=$?
cat /tmp/install.out
check "exit code 0 or 1 (0/1 = ok-with-skips, not a hard error)" \
  bash -c "[[ $code -eq 0 || $code -eq 1 ]]"
check "manifest recorded profile=personal" manifest_has $'\tpersonal'
check "~/.vimrc symlinks into repo" bash -c '[[ "$(readlink -f ~/.vimrc)" == "'"$DOTFILES"'/vim/.vimrc" ]]'
check "~/.zshrc symlinks into repo" bash -c '[[ "$(readlink -f ~/.zshrc)" == "'"$DOTFILES"'/zsh/.zshrc" ]]'
check "~/.claude/CLAUDE.md symlinks into repo" bash -c '[[ "$(readlink -f ~/.claude/CLAUDE.md)" == "'"$DOTFILES"'/claude/CLAUDE.md" ]]'
check "~/.claude/settings.json copied (not symlinked)" bash -c '[[ -f ~/.claude/settings.json && ! -L ~/.claude/settings.json ]]'
check "~/.claude/settings.json matches personal seed" diff -q ~/.claude/settings.json "$DOTFILES/claude/settings.json"
check "watchcommit symlinked on personal profile" bash -c '[[ -L ~/.local/bin/watchcommit ]]'
check "watchcommit systemd unit symlinked on personal profile" bash -c '[[ -L ~/.config/systemd/user/watchcommit.service ]]'
check "no profile marker written on personal run" bash -c '[[ ! -f "'"$MARKER"'" ]]'
check "watchcommit starts and exits cleanly against a non-git dir" bash -c \
  'timeout 10 ~/.local/bin/watchcommit /tmp 2>&1 | grep -q "not a git repo"'

echo ""
echo "=== 2. Rollback undoes the personal install ==="
./install.sh --rollback >/tmp/rollback.out 2>&1
cat /tmp/rollback.out
check "manifest removed after rollback" bash -c '[[ ! -f "'"$MANIFEST"'" ]]'
check "~/.vimrc symlink removed" bash -c '[[ ! -e ~/.vimrc ]]'
check "~/.claude/settings.json removed" bash -c '[[ ! -e ~/.claude/settings.json ]]'
check "watchcommit symlink removed" bash -c '[[ ! -e ~/.local/bin/watchcommit ]]'
check "watchcommit systemd unit symlink removed" bash -c '[[ ! -e ~/.config/systemd/user/watchcommit.service ]]'

echo ""
echo "=== 3. Pre-existing file gets backed up, not clobbered ==="
echo "sentinel-content" > ~/.vimrc
./install.sh >/tmp/install2.out 2>&1
cat /tmp/install2.out
check "original content preserved in .bak" bash -c '[[ "$(cat ~/.vimrc.bak)" == "sentinel-content" ]]'
check "~/.vimrc is now the symlink" bash -c '[[ -L ~/.vimrc ]]'
check "manifest recorded file-backed-up for ~/.vimrc" manifest_has "file-backed-up	$HOME/.vimrc"
check "manifest ALSO recorded symlink-created for ~/.vimrc" manifest_has "symlink-created	$HOME/.vimrc"

./install.sh --rollback >/tmp/rollback2.out 2>&1
cat /tmp/rollback2.out
check "rollback restores original content, not just removes symlink" bash -c '[[ "$(cat ~/.vimrc)" == "sentinel-content" ]]'
check "backup file cleaned up after restore" bash -c '[[ ! -e ~/.vimrc.bak ]]'
rm -f ~/.vimrc

echo ""
echo "=== 4. Work profile: exclusions + settings seed ==="
./install.sh --work >/tmp/work.out 2>&1
cat /tmp/work.out
check "profile marker written as 'work'" bash -c '[[ "$(cat "'"$MARKER"'")" == "work" ]]'
check "watchcommit excluded on work profile" bash -c '[[ ! -e ~/.local/bin/watchcommit ]]'
check "watchcommit systemd unit excluded on work profile" bash -c '[[ ! -e ~/.config/systemd/user/watchcommit.service ]]'
check "~/.claude/settings.json matches WORK seed" diff -q ~/.claude/settings.json "$DOTFILES/claude/settings.work.json"

echo ""
echo "=== 5. Guard blocks a plain run on a work-marked machine ==="
./install.sh >/tmp/guard.out 2>&1
guard_code=$?
cat /tmp/guard.out
check "plain run exits 2 (blocked)" bash -c "[[ $guard_code -eq 2 ]]"
check "guard message mentions WORK" grep -q "provisioned as WORK" /tmp/guard.out
check "manifest untouched by blocked run (still shows work)" manifest_has $'\twork'

echo ""
echo "=== 6. --force overrides the guard ==="
./install.sh --force >/tmp/force.out 2>&1
force_code=$?
cat /tmp/force.out
check "--force run does not get blocked" bash -c "[[ $force_code -eq 0 || $force_code -eq 1 ]]"
check "--force run records profile=personal" manifest_has $'\tpersonal'
check "work marker is NOT reset by a forced personal run (next plain run is still blocked)" \
  bash -c '[[ "$(cat "'"$MARKER"'")" == "work" ]]'
check "settings.json drift reported instead of silently overwritten" grep -q "drifted" /tmp/force.out

echo ""
echo "=== 7. Unknown argument is rejected ==="
./install.sh --bogus >/tmp/bogus.out 2>&1
bogus_code=$?
check "unknown arg exits 2" bash -c "[[ $bogus_code -eq 2 ]]"
check "unknown arg message names the bad flag" grep -q "unknown argument: --bogus" /tmp/bogus.out

echo ""
echo "=== 8. --help exits 0 with usage ==="
./install.sh --help >/tmp/help.out 2>&1
help_code=$?
check "--help exits 0" bash -c "[[ $help_code -eq 0 ]]"
check "--help prints usage" grep -q "^usage:" /tmp/help.out

echo ""
echo "════════ Scenario summary: $PASS passed, $FAIL failed ════════"
[[ $FAIL -eq 0 ]]
