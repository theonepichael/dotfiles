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
MANIFEST="$STATE_DIR/history.jsonl"
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

manifest_has() {  # manifest_has <kind> <field=value> [<field=value> ...]
  python3 - "$MANIFEST" "$@" <<'PY'
import json, sys
path, kind, *pairs = sys.argv[1:]
wanted = dict(p.split("=", 1) for p in pairs)
try:
    lines = open(path, encoding="utf-8").read().splitlines()
except FileNotFoundError:
    sys.exit(1)
for line in lines:
    line = line.strip()
    if not line:
        continue
    try:
        entry = json.loads(line)
    except json.JSONDecodeError:
        continue
    if entry.get("kind") != kind:
        continue
    if all(str(entry.get(k)) == v for k, v in wanted.items()):
        sys.exit(0)
sys.exit(1)
PY
}

manifest_run_count() {  # manifest_run_count <N>
  python3 - "$MANIFEST" "$1" <<'PY'
import json, sys
path, want = sys.argv[1], int(sys.argv[2])
try:
    lines = open(path, encoding="utf-8").read().splitlines()
except FileNotFoundError:
    sys.exit(0 if want == 0 else 1)
count = 0
for line in lines:
    line = line.strip()
    if not line:
        continue
    try:
        entry = json.loads(line)
    except json.JSONDecodeError:
        continue
    if entry.get("kind") == "run":
        count += 1
sys.exit(0 if count == want else 1)
PY
}

echo "=== 1. Fresh personal install (--harness=claude) ==="
./install.sh --harness=claude >/tmp/install.out 2>&1
code=$?
cat /tmp/install.out
check "exit code 0 or 1 (0/1 = ok-with-skips, not a hard error)" \
  bash -c "[[ $code -eq 0 || $code -eq 1 ]]"
check "manifest recorded profile=personal" manifest_has run profile=personal
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
# history.jsonl is append-only (manifest_init appends a new run marker rather
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
check "history.jsonl now holds 2 run markers (run 1 + this rerun, nothing erased)" \
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
check "manifest recorded file-backed-up for ~/.vimrc" manifest_has file-backed-up "dest=$HOME/.vimrc"
check "manifest ALSO recorded symlink-created for ~/.vimrc" manifest_has symlink-created "dest=$HOME/.vimrc"

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
check "manifest untouched by blocked run (still shows work)" manifest_has run profile=work

echo ""
echo "=== 6. --force overrides the guard ==="
./install.sh --force --harness=claude >/tmp/force.out 2>&1
force_code=$?
cat /tmp/force.out
check "--force run does not get blocked" bash -c "[[ $force_code -eq 0 || $force_code -eq 1 ]]"
check "--force run records profile=personal" manifest_has run profile=personal
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
check "--help documents --wipe" grep -q -- "--wipe" /tmp/help.out

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

./install.sh --wipe >/tmp/wipealone.out 2>&1
wipealone_code=$?
check "--wipe without --rollback exits 2" bash -c "[[ $wipealone_code -eq 2 ]]"
check "--wipe-without-rollback message shown" grep -q -- "--wipe can only be used with --rollback" /tmp/wipealone.out

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
check "copilot backlog-item skill symlinked" bash -c \
  '[[ "$(readlink -f ~/.copilot/skills/backlog-item/SKILL.md)" == "'"$DOTFILES"'/copilot/skills/backlog-item/SKILL.md" ]]'

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
echo "=== 10. opencode.jsonc: personal-only permission seeding ==="
./install.sh --harness=opencode >/tmp/oc-personal.out 2>&1
cat /tmp/oc-personal.out
check "opencode.jsonc seeded from personal file" diff -q ~/.config/opencode/opencode.jsonc "$DOTFILES/opencode/opencode.jsonc"
check "personal opencode.jsonc has no xargs (allowlist bypass removed everywhere)" \
  bash -c '! grep -q "xargs" ~/.config/opencode/opencode.jsonc'
check "personal opencode.jsonc has no awk (allowlist bypass removed everywhere)" \
  bash -c '! grep -q "\"awk \*\"" ~/.config/opencode/opencode.jsonc'
check "personal opencode.jsonc keeps curl (personal convenience)" \
  bash -c 'grep -q "curl \*" ~/.config/opencode/opencode.jsonc'
# backlog-item port wiring. Explicit checks matter here: install.sh exits 0
# OR 1 (ok-with-skips) on success, so a typo'd src in links.toml would
# otherwise surface only as a silent SKIPPED line, not a failed scenario.
check "opencode backlog-item command symlinked" bash -c \
  '[[ "$(readlink -f ~/.config/opencode/commands/backlog-item.md)" == "'"$DOTFILES"'/opencode/command/backlog-item.md" ]]'
check "opencode grill-me skill symlinked (backlog-item delegates via skill tool)" bash -c \
  '[[ "$(readlink -f ~/.config/opencode/skills/grill-me/SKILL.md)" == "'"$DOTFILES"'/opencode/skills/grill-me/SKILL.md" ]]'
check "opencode second-opinion skill symlinked (backlog-item delegates via skill tool)" bash -c \
  '[[ "$(readlink -f ~/.config/opencode/skills/second-opinion/SKILL.md)" == "'"$DOTFILES"'/opencode/skills/second-opinion/SKILL.md" ]]'
./install.sh --rollback >/tmp/rb-oc1.out 2>&1

rm -f "$MARKER"
echo ""
echo "=== 10b. opencode is rejected outright on --profile=work, not tightened ==="
./install.sh --profile=work --harness=opencode >/tmp/oc-work.out 2>&1
ocwork_code=$?
cat /tmp/oc-work.out
check "--profile=work --harness=opencode exits 2" bash -c "[[ $ocwork_code -eq 2 ]]"
check "rejection names the flag combination" \
  grep -q "harness=opencode is not allowed with --profile=work" /tmp/oc-work.out
check "no opencode.jsonc written on a rejected work+opencode run" \
  bash -c '! [[ -e ~/.config/opencode/opencode.jsonc ]]'

./install.sh --profile=work --harness=copilot,opencode >/tmp/oc-work2.out 2>&1
ocwork2_code=$?
check "--profile=work --harness=copilot,opencode is rejected the same way (opencode anywhere in the list is enough)" \
  bash -c "[[ $ocwork2_code -eq 2 ]]"

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
check "history.jsonl recorded both runs (2 run markers, not overwritten by run B)" \
  manifest_run_count 2
check "history.jsonl still holds run A's claude symlink record after run B" \
  manifest_has symlink-created "dest=$HOME/.claude/CLAUDE.md"

./install.sh --rollback >/tmp/rollback-multi.out 2>&1
cat /tmp/rollback-multi.out
check "single rollback removes run A's files too (Claude), not just run B's" \
  bash -c '[[ ! -e ~/.claude/CLAUDE.md ]]'
check "single rollback removes run B's files (opencode)" \
  bash -c '[[ ! -e ~/.config/opencode/opencode.jsonc ]]'
check "history.jsonl cleared after a full rollback" bash -c '[[ ! -f "'"$MANIFEST"'" ]]'

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
echo "=== 13. --rollback --wipe: blank-slate rollback ==="
# This container has no init system, so systemd --user is never reachable
# here (either systemctl itself is absent, or the probe call fails with no
# session bus to talk to) — deterministic in a plain `docker run` container,
# not env flakiness. That means _wipe_watchcommit's "systemd --user is
# unavailable" anomaly always fires once the watchcommit unit symlink
# exists, which is exactly the real-world case this branch exists to cover
# (a machine that provisioned watchcommit and no longer has systemd --user,
# e.g. a fresh WSL distro without `enable-systemd` in /etc/wsl.conf yet).
echo "sentinel-content" > ~/.vimrc
./install.sh --harness=claude >/tmp/pre-wipe.out 2>&1
cat /tmp/pre-wipe.out
check "~/.vimrc backed up before the wipe scenario" bash -c '[[ -f ~/.vimrc.bak ]]'
check "watchcommit unit symlinked before the wipe scenario" bash -c '[[ -L ~/.config/systemd/user/watchcommit.service ]]'

mkdir -p ~/.local/share/nvim ~/.local/state/nvim ~/.cache/nvim
touch ~/.local/share/nvim/sentinel ~/.local/state/nvim/sentinel ~/.cache/nvim/sentinel

./install.sh --rollback --wipe --dry-run >/tmp/wipe-dry.out 2>&1
wipedry_code=$?
cat /tmp/wipe-dry.out
check "--rollback --wipe --dry-run exits 1 (systemd-unavailable skip is reported even in preview)" \
  bash -c "[[ $wipedry_code -eq 1 ]]"
check "dry-run wipe leaves the backup in place" bash -c '[[ -f ~/.vimrc.bak ]]'
check "dry-run wipe leaves ~/.vimrc symlinked, not deleted" bash -c '[[ -L ~/.vimrc ]]'
check "dry-run wipe leaves nvim dirs in place" bash -c '[[ -f ~/.local/share/nvim/sentinel ]]'
check "dry-run wipe previews backup deletion, not restoration" grep -q "would delete backup" /tmp/wipe-dry.out
check "dry-run wipe previews nvim runtime dir removal" grep -q "would remove.*nvim (wipe)" /tmp/wipe-dry.out
check "dry-run wipe surfaces the watchcommit systemd-unavailable skip" \
  grep -q "systemd --user is unavailable" /tmp/wipe-dry.out

./install.sh --rollback --wipe >/tmp/wipe-real.out 2>&1
wipereal_code=$?
cat /tmp/wipe-real.out
check "--rollback --wipe exits 1 (systemd-unavailable skip pushes the tally to 1)" \
  bash -c "[[ $wipereal_code -eq 1 ]]"
check "wipe deletes the backup outright" bash -c '[[ ! -e ~/.vimrc.bak ]]'
check "wipe removes ~/.vimrc entirely (not restored to sentinel content)" bash -c '[[ ! -e ~/.vimrc ]]'
check "wipe reports deleting the backup, not restoring it" grep -q "deleted backup" /tmp/wipe-real.out
check "wipe sweeps nvim share dir" bash -c '[[ ! -e ~/.local/share/nvim ]]'
check "wipe sweeps nvim state dir" bash -c '[[ ! -e ~/.local/state/nvim ]]'
check "wipe sweeps nvim cache dir" bash -c '[[ ! -e ~/.cache/nvim ]]'
check "wipe reports the watchcommit systemd-unavailable skip" \
  grep -q "systemd --user is unavailable" /tmp/wipe-real.out
check "watchcommit unit symlink still removed by the normal manifest walk despite the skip" \
  bash -c '[[ ! -e ~/.config/systemd/user/watchcommit.service ]]'
check "history.jsonl removed after wipe" bash -c '[[ ! -f "'"$MANIFEST"'" ]]'
check "state dir removed once empty after wipe" bash -c '[[ ! -d "'"$STATE_DIR"'" ]]'
check "wipe's final message distinguishes it from plain rollback" grep -q "wiped to a blank slate" /tmp/wipe-real.out

echo ""
echo "=== 13b. --wipe with untracked state but no manifest (already-consumed history) ==="
# Simulates the case the "no-manifest-wipe-behavior" decision exists for: a
# second --wipe (or --wipe after an earlier plain --rollback already deleted
# the manifest) still needs to sweep leftover untracked state instead of
# hard-failing with "nothing to roll back". The watchcommit unit is already
# gone at this point (removed above), so this run has nothing anomalous to
# report and should exit cleanly.
mkdir -p ~/.local/share/nvim
touch ~/.local/share/nvim/sentinel
./install.sh --rollback --wipe >/tmp/wipe-no-manifest.out 2>&1
wipenomanifest_code=$?
cat /tmp/wipe-no-manifest.out
check "wipe with swept state but no manifest exits 0 (nothing recorded to skip)" \
  bash -c "[[ $wipenomanifest_code -eq 0 ]]"
check "wipe-no-manifest header explains the swept-but-no-history case" \
  grep -q "Wipe swept untracked state" /tmp/wipe-no-manifest.out
check "wipe-no-manifest actually removed the leftover nvim dir" bash -c '[[ ! -e ~/.local/share/nvim ]]'
check "wipe-no-manifest did NOT print the generic nothing-to-roll-back error" \
  bash -c '! grep -q "nothing to roll back" /tmp/wipe-no-manifest.out'

echo ""
echo "=== 13c. A third --wipe run with truly nothing left reports the plain error ==="
./install.sh --rollback --wipe >/tmp/wipe-truly-empty.out 2>&1
wipeempty_code=$?
cat /tmp/wipe-truly-empty.out
check "wipe with nothing left at all exits 1" bash -c "[[ $wipeempty_code -eq 1 ]]"
check "wipe with nothing left reports the plain nothing-to-roll-back error" \
  grep -q "nothing to roll back" /tmp/wipe-truly-empty.out

echo ""
echo "════════ Scenario summary: $PASS passed, $FAIL failed ════════"
[[ $FAIL -eq 0 ]]
