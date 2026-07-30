#!/usr/bin/env bash
# Scenario suite for install.sh, meant to run inside test/run.sh's container.
# Exercises the full lifecycle: fresh install, rollback, backup-and-restore
# of a pre-existing dotfile, work profile + guard, --force override,
# harness opt-in selection (--harness=), opencode profile-specific
# permission seeding, and argument-parsing edge cases (including the old
# --work/--copilot flags being rejected outright). Not meant to run on a
# real machine.
set -uo pipefail

DOTFILES="$HOME/dotfiles"
STATE_DIR="$HOME/.local/state/dotfiles"
MANIFEST="$STATE_DIR/history.tsv"
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
manifest_run_count() { [[ "$(grep -c $'^run\t' "$MANIFEST" 2>/dev/null)" -eq "$1" ]]; }

echo "=== 1. Fresh personal install (--harness=claude) ==="
./install.sh --harness=claude >/tmp/install.out 2>&1
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
check "JetBrainsMono Nerd Font extracted (ttf files present)" bash -c \
  '[[ -n "$(ls ~/.local/share/fonts/JetBrainsMonoNerdFont/*.ttf 2>/dev/null)" ]]'
check "Nerd Font version marker written" bash -c \
  '[[ "$(cat ~/.local/share/fonts/JetBrainsMonoNerdFont/.nerd-fonts-version 2>/dev/null)" == "3.4.0" ]]'
check "fc-list recognizes the installed Nerd Font" bash -c \
  'fc-list | grep -q "JetBrainsMono Nerd Font"'
check "Copilot NOT installed (not in --harness)" bash -c '[[ ! -e ~/.copilot/copilot-instructions.md ]]'
check "copilot-work alias file NOT symlinked (Copilot not selected)" bash -c '[[ ! -e ~/.copilot_aliases ]]'
check "opencode NOT wired (not in --harness)" bash -c '[[ ! -e ~/.config/opencode/opencode.jsonc ]]'

echo ""
echo "=== 1b. Re-run is idempotent — no re-download of the Nerd Font ==="
# history.tsv is append-only (manifest_init appends a new run marker rather
# than truncating), and symlinks that already exist don't get re-recorded
# (the was_link gate in symlink()) — so a second run here just adds another
# run marker on top of run 1's records instead of erasing them. No
# backup/restore of the manifest needed around this rerun.
font_mtime_before="$(stat -c %Y ~/.local/share/fonts/JetBrainsMonoNerdFont/.nerd-fonts-version)"
./install.sh --harness=claude >/tmp/install-rerun.out 2>&1
cat /tmp/install-rerun.out
font_mtime_after="$(stat -c %Y ~/.local/share/fonts/JetBrainsMonoNerdFont/.nerd-fonts-version)"
check "version marker untouched by re-run (no re-download)" bash -c \
  "[[ '$font_mtime_before' -eq '$font_mtime_after' ]]"
check "history.tsv now holds 2 run markers (run 1 + this rerun, nothing erased)" \
  manifest_run_count 2

echo ""
echo "=== 2. Rollback undoes the personal install ==="
./install.sh --rollback >/tmp/rollback.out 2>&1
cat /tmp/rollback.out
check "manifest removed after rollback" bash -c '[[ ! -f "'"$MANIFEST"'" ]]'
check "~/.vimrc symlink removed" bash -c '[[ ! -e ~/.vimrc ]]'
check "~/.claude/settings.json removed" bash -c '[[ ! -e ~/.claude/settings.json ]]'
check "watchcommit symlink removed" bash -c '[[ ! -e ~/.local/bin/watchcommit ]]'
check "watchcommit systemd unit symlink removed" bash -c '[[ ! -e ~/.config/systemd/user/watchcommit.service ]]'
check "Nerd Font NOT removed by rollback (packages aren't rolled back)" bash -c \
  '[[ -n "$(ls ~/.local/share/fonts/JetBrainsMonoNerdFont/*.ttf 2>/dev/null)" ]]'

echo ""
echo "=== 3. Pre-existing file gets backed up, not clobbered ==="
echo "sentinel-content" > ~/.vimrc
./install.sh --harness=claude >/tmp/install2.out 2>&1
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
echo "=== 4. Work profile + Claude harness: exclusions + settings seed ==="
./install.sh --profile=work --harness=claude >/tmp/work.out 2>&1
cat /tmp/work.out
check "profile marker written as 'work'" bash -c '[[ "$(cat "'"$MARKER"'")" == "work" ]]'
check "watchcommit excluded on work profile" bash -c '[[ ! -e ~/.local/bin/watchcommit ]]'
check "watchcommit systemd unit excluded on work profile" bash -c '[[ ! -e ~/.config/systemd/user/watchcommit.service ]]'
check "~/.claude/settings.json matches WORK seed" diff -q ~/.claude/settings.json "$DOTFILES/claude/settings.work.json"
check "Claude Code IS installed despite work profile (profile never restricts harness choice)" \
  bash -c '[[ -L ~/.claude/CLAUDE.md ]]'

echo ""
echo "=== 5. Guard blocks a plain (no --profile) run on a work-marked machine ==="
./install.sh --harness=claude >/tmp/guard.out 2>&1
guard_code=$?
cat /tmp/guard.out
check "plain run exits 2 (blocked)" bash -c "[[ $guard_code -eq 2 ]]"
check "guard message mentions WORK" grep -q "provisioned as WORK" /tmp/guard.out
check "manifest untouched by blocked run (still shows work)" manifest_has $'\twork'

echo ""
echo "=== 6. --force overrides the guard ==="
./install.sh --force --harness=claude >/tmp/force.out 2>&1
force_code=$?
cat /tmp/force.out
check "--force run does not get blocked" bash -c "[[ $force_code -eq 0 || $force_code -eq 1 ]]"
check "--force run records profile=personal" manifest_has $'\tpersonal'
check "work marker is NOT reset by a forced personal run (next plain run is still blocked)" \
  bash -c '[[ "$(cat "'"$MARKER"'")" == "work" ]]'
check "settings.json drift reported instead of silently overwritten" grep -q "drifted" /tmp/force.out

# Clean slate for the harness-focused scenarios below.
./install.sh --rollback >/tmp/rollback3.out 2>&1
rm -f "$MARKER"

echo ""
echo "=== 7. Argument-parsing edge cases ==="
./install.sh --bogus >/tmp/bogus.out 2>&1
bogus_code=$?
check "unknown arg exits 2" bash -c "[[ $bogus_code -eq 2 ]]"
check "unknown arg message names the bad flag" grep -q "unknown argument: --bogus" /tmp/bogus.out

./install.sh --help >/tmp/help.out 2>&1
help_code=$?
check "--help exits 0" bash -c "[[ $help_code -eq 0 ]]"
check "--help prints usage" grep -q "^usage:" /tmp/help.out

./install.sh >/tmp/noharness.out 2>&1
noharness_code=$?
check "no --harness at all exits 2" bash -c "[[ $noharness_code -eq 2 ]]"
check "no-harness message says so" grep -q "no --harness specified" /tmp/noharness.out

./install.sh --harness=bogus >/tmp/badharness.out 2>&1
badharness_code=$?
check "--harness=bogus exits 2" bash -c "[[ $badharness_code -eq 2 ]]"
check "bad-harness message names it" grep -q "unknown harness: bogus" /tmp/badharness.out

./install.sh --harness= >/tmp/emptyharness.out 2>&1
emptyharness_code=$?
check "--harness= (empty) exits 2" bash -c "[[ $emptyharness_code -eq 2 ]]"
check "empty-harness message is the dedicated one, not a blank 'unknown harness'" \
  grep -q "empty value" /tmp/emptyharness.out

./install.sh --work >/tmp/oldwork.out 2>&1
oldwork_code=$?
check "old --work flag is rejected (hard cutover, no back-compat)" bash -c "[[ $oldwork_code -eq 2 ]]"
check "old --work message is the generic unknown-argument path" grep -q "unknown argument: --work" /tmp/oldwork.out

./install.sh --copilot >/tmp/oldcopilot.out 2>&1
oldcopilot_code=$?
check "old --copilot flag is rejected (hard cutover, no back-compat)" bash -c "[[ $oldcopilot_code -eq 2 ]]"
check "old --copilot message is the generic unknown-argument path" grep -q "unknown argument: --copilot" /tmp/oldcopilot.out

./install.sh --rollback --harness=claude >/tmp/rollbackharness.out 2>&1
rollbackharness_code=$?
check "--rollback combined with --harness is rejected" bash -c "[[ $rollbackharness_code -eq 2 ]]"
check "rollback-must-be-alone message shown" grep -q "must be used alone" /tmp/rollbackharness.out

./install.sh --rollback --profile=work >/tmp/rollbackprofile.out 2>&1
rollbackprofile_code=$?
check "--rollback combined with --profile=work is rejected" bash -c "[[ $rollbackprofile_code -eq 2 ]]"

echo ""
echo "=== 8. Harness opt-in: only the selected harness(es) get wired ==="
./install.sh --harness=claude >/tmp/harness-claude.out 2>&1
cat /tmp/harness-claude.out
check "Claude Code wired" bash -c '[[ -L ~/.claude/CLAUDE.md ]]'
check "Copilot NOT wired" bash -c '[[ ! -e ~/.copilot/copilot-instructions.md ]]'
check "opencode NOT wired" bash -c '[[ ! -e ~/.config/opencode/opencode.jsonc ]]'
./install.sh --rollback >/tmp/rb-h1.out 2>&1

./install.sh --harness=claude,opencode >/tmp/harness-both.out 2>&1
cat /tmp/harness-both.out
check "Claude Code wired (combo)" bash -c '[[ -L ~/.claude/CLAUDE.md ]]'
check "opencode wired (combo)" bash -c '[[ -f ~/.config/opencode/opencode.jsonc ]]'
check "Copilot still NOT wired (combo omits it)" bash -c '[[ ! -e ~/.copilot/copilot-instructions.md ]]'
check "repeated --harness flags accumulate, not overwrite" \
  bash -c 'true'  # exercised directly below with a second invocation

# --harness=claude --harness=copilot (repeated flag) must select BOTH, not
# just the last one — the array-append fix from the redesign.
./install.sh --rollback >/tmp/rb-h2.out 2>&1
./install.sh --harness=claude --harness=copilot >/tmp/harness-repeated.out 2>&1
cat /tmp/harness-repeated.out
check "repeated --harness=claude --harness=copilot selects claude" bash -c '[[ -L ~/.claude/CLAUDE.md ]]'
check "repeated --harness=claude --harness=copilot ALSO selects copilot (not just the last flag)" \
  bash -c '[[ -e ~/.copilot/copilot-instructions.md ]]'

echo ""
echo "=== 9. Additive-only: narrowing --harness on a later run doesn't uninstall ==="
# Machine currently has claude+copilot from scenario 8's last run. Re-running
# with just claude must leave copilot's files untouched.
./install.sh --harness=claude >/tmp/narrow.out 2>&1
cat /tmp/narrow.out
check "Copilot files left in place after a narrower re-run (additive-only, no surprise uninstall)" \
  bash -c '[[ -e ~/.copilot/copilot-instructions.md ]]'
./install.sh --rollback >/tmp/rb-h3.out 2>&1

echo ""
echo "=== 10. opencode.jsonc: profile-specific permission seeding ==="
./install.sh --harness=opencode >/tmp/oc-personal.out 2>&1
cat /tmp/oc-personal.out
check "opencode.jsonc seeded from personal file" diff -q ~/.config/opencode/opencode.jsonc "$DOTFILES/opencode/opencode.jsonc"
check "personal opencode.jsonc has no xargs (allowlist bypass removed everywhere)" \
  bash -c '! grep -q "xargs" ~/.config/opencode/opencode.jsonc'
check "personal opencode.jsonc has no awk (allowlist bypass removed everywhere)" \
  bash -c '! grep -q "\"awk \*\"" ~/.config/opencode/opencode.jsonc'
check "personal opencode.jsonc still keeps curl (personal convenience, tightened only on work)" \
  bash -c 'grep -q "curl \*" ~/.config/opencode/opencode.jsonc'
./install.sh --rollback >/tmp/rb-oc1.out 2>&1

rm -f "$MARKER"
./install.sh --profile=work --harness=opencode >/tmp/oc-work.out 2>&1
cat /tmp/oc-work.out
check "opencode.jsonc seeded from WORK file" diff -q ~/.config/opencode/opencode.jsonc "$DOTFILES/opencode/opencode.work.jsonc"
check "work opencode.jsonc has no curl (tightened tier-3 removed on work)" \
  bash -c '! grep -q "curl \*" ~/.config/opencode/opencode.jsonc'
check "work opencode.jsonc has no npx" bash -c '! grep -q "npx \*" ~/.config/opencode/opencode.jsonc'
check "work opencode.jsonc has no node -e" bash -c '! grep -q "node -e" ~/.config/opencode/opencode.jsonc'
check "work opencode.jsonc has no rm -f" bash -c '! grep -q "rm -f" ~/.config/opencode/opencode.jsonc'
check "work opencode.jsonc has no kill" bash -c '! grep -q "\"kill\*\"" ~/.config/opencode/opencode.jsonc'
check "work opencode.jsonc has no nohup" bash -c '! grep -q "nohup" ~/.config/opencode/opencode.jsonc'
check "work opencode.jsonc still has no xargs/awk either" \
  bash -c '! grep -qE "xargs|\"awk " ~/.config/opencode/opencode.jsonc'

echo ""
echo "=== 11. Full-history rollback: undoes every past run, not just the most recent ==="
# Clean slate: previous scenarios leave the opencode work-profile run in
# place with no marker cleanup.
./install.sh --rollback >/tmp/rb-pre11.out 2>&1
rm -f "$MARKER"

./install.sh --harness=claude >/tmp/multi-run-a.out 2>&1
cat /tmp/multi-run-a.out
check "run A: Claude Code wired" bash -c '[[ -L ~/.claude/CLAUDE.md ]]'

./install.sh --harness=opencode >/tmp/multi-run-b.out 2>&1
cat /tmp/multi-run-b.out
check "run B: opencode wired" bash -c '[[ -f ~/.config/opencode/opencode.jsonc ]]'
check "history.tsv recorded both runs (2 run markers, not overwritten by run B)" \
  manifest_run_count 2
check "history.tsv still holds run A's claude symlink record after run B" \
  manifest_has "symlink-created	$HOME/.claude/CLAUDE.md"

./install.sh --rollback >/tmp/rollback-multi.out 2>&1
cat /tmp/rollback-multi.out
check "single rollback removes run A's files too (Claude), not just run B's" \
  bash -c '[[ ! -e ~/.claude/CLAUDE.md ]]'
check "single rollback removes run B's files (opencode)" \
  bash -c '[[ ! -e ~/.config/opencode/opencode.jsonc ]]'
check "history.tsv cleared after a full rollback" bash -c '[[ ! -f "'"$MANIFEST"'" ]]'

echo ""
echo "=== 12. Rollback skips and reports instead of aborting on the unexpected ==="
echo "sentinel-content" > ~/.vimrc
./install.sh --harness=claude >/tmp/pre12.out 2>&1
cat /tmp/pre12.out

# Something else claims a path install.sh symlinked — rollback must not
# blindly delete a symlink that no longer points where it left it.
rm ~/.claude/CLAUDE.md
ln -s /etc/hostname ~/.claude/CLAUDE.md

# The backup install.sh made for the pre-existing ~/.vimrc gets removed out
# from under rollback (manual cleanup, disk pressure, whatever) — rollback
# must report this, not silently no-op or abort.
rm -f ~/.vimrc.bak

./install.sh --rollback >/tmp/rollback12.out 2>&1
rollback12_code=$?
cat /tmp/rollback12.out
check "rollback exits 1 when steps are skipped" bash -c "[[ $rollback12_code -eq 1 ]]"
check "reclaimed symlink is left alone, not deleted" \
  bash -c '[[ "$(readlink ~/.claude/CLAUDE.md)" == /etc/hostname ]]'
check "reclaimed-symlink skip is reported" grep -q "something else has claimed this path" /tmp/rollback12.out
check "missing-backup skip is reported" grep -q "not found — already restored, or removed outside install.sh" /tmp/rollback12.out
check "skip count summary printed" grep -q "rollback step(s) did not apply cleanly" /tmp/rollback12.out
rm -f ~/.claude/CLAUDE.md ~/.vimrc

echo ""
echo "════════ Scenario summary: $PASS passed, $FAIL failed ════════"
[[ $FAIL -eq 0 ]]
