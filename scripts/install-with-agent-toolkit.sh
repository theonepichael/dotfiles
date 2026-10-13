#!/usr/bin/env sh
# Runs agent-toolkit's installer, then dotfiles' own, in that fixed order,
# for any machine that has both checked out (this one).
#
# Why this has to exist: agent-toolkit's own links.toml deliberately
# symlinks the bare claude/CORE_INSTRUCTIONS.md to ~/.claude/CLAUDE.md (and
# the copilot/gemini/pi equivalents) -- correct for a coworker machine with
# no personal overlay. On THIS machine, dotfiles instead symlinks its own
# composed claude/global-instructions.md (CORE_INSTRUCTIONS.md +
# personal-overlay.md) to those same 4 destinations. Running agent-toolkit
# first and dotfiles second means dotfiles always reasserts the composed,
# personal version -- every time, not just during the one-time cutover --
# so a later agent-toolkit-only update (a new skill, a bugfix) can never
# silently drop the personal overlay from every harness on this machine
# again.
#
# 2026-09-07 incident: a swarm worker ran agent-toolkit/install.py directly
# (bypassing this wrapper) to repoint its own renamed scripts, and silently
# won all 4 personal-overlay destinations -- nothing was stopping a direct,
# unwrapped call. agent-toolkit/install.py now refuses those 4 destinations
# on its own when it detects ~/dotfiles present and AGENT_TOOLKIT_INSTALL_WRAPPER
# is unset, so this wrapper exports the marker before invoking it below.
# That backstop protects against future direct calls; it doesn't replace
# this script -- dotfiles' own reassert step (below) is still what actually
# recomposes the personal overlay content on every run.
#
# After both installers, it recomposes claude/global-instructions.md from
# agent-toolkit's live claude/CORE_INSTRUCTIONS.md (this repo keeps no copy
# of the core), so every install leaves the harnesses loading the current
# core. It composes only when the dotfiles installer actually installed
# (exit 0, or 1 for an ordinary skip), previews with --check under
# --dry-run, and never composes for --help or a usage error.
#
# The agent-toolkit checkout is resolved by gen_core_instructions.py
# --print-agent-toolkit-path ($AGENT_TOOLKIT_PATH, then
# ~/Workspace/agent-toolkit/agent-toolkit, then ~/Workspace/agent-toolkit)
# -- one Python implementation rather than a shell copy that could drift --
# and exported so the dotfiles installer sees the same checkout.
#
# Only for a plain install run. --rollback/--check-links/--depart/--wipe
# each mean something different per-repo (rolling back BOTH repos' entire
# histories together, auditing two different link tables at once) that this
# script does not attempt to reconcile -- run install.py directly in the
# repo you mean for those.
set -eu

# shellcheck disable=SC1007 # CDPATH= clears CDPATH for this one command so cd ignores it.
DOTFILES_DIR="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"

for flag in "$@"; do
  case "$flag" in
    --rollback|--check-links|--depart|--wipe)
      echo "install-with-agent-toolkit.sh: --rollback/--check-links/--depart/--wipe" >&2
      echo "  each mean something different per repo -- run install.py directly in" >&2
      echo "  the repo you mean (agent-toolkit or dotfiles), not through this wrapper." >&2
      exit 2
      ;;
  esac
done

for candidate in python3.14 python3 python3.13 python3.12 python; do
  bin="$(command -v "$candidate" 2>/dev/null)" || continue
  if "$bin" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 12) else 1)' 2>/dev/null; then
    PYTHON="$bin"
    break
  fi
done
if [ -z "${PYTHON:-}" ]; then
  echo "install-with-agent-toolkit.sh: no Python 3.12+ found on PATH." >&2
  exit 1
fi

COMPOSER="$DOTFILES_DIR/claude/scripts/gen_core_instructions.py"
# An explicit branch, not a bare assignment: under `set -e` a failing
# command substitution would exit 1 before this message could explain it.
if ! AGENT_TOOLKIT_DIR="$("$PYTHON" "$COMPOSER" --print-agent-toolkit-path)"; then
  echo "install-with-agent-toolkit.sh: cannot resolve the agent-toolkit checkout (see above);" >&2
  echo "  set AGENT_TOOLKIT_PATH to your checkout." >&2
  exit 2
fi
export AGENT_TOOLKIT_PATH="$AGENT_TOOLKIT_DIR"

echo "==> agent-toolkit installer ($AGENT_TOOLKIT_DIR)"
agent_toolkit_status=0
AGENT_TOOLKIT_INSTALL_WRAPPER=1 "$PYTHON" "$AGENT_TOOLKIT_DIR/install.py" "$@" || agent_toolkit_status=$?

# The dotfiles reassert step below MUST still run even if agent-toolkit's
# install reported a skip (exit 1 is install.py's normal "something was
# skipped" signal, e.g. an unmet Neovim version floor -- not a reason to
# abort before the personal overlay is ever reasserted). `|| agent_toolkit_status=$?`
# above (rather than a bare command checked via `$?` on the next line) is
# load-bearing under `set -e`: a bare failing command aborts the script
# immediately, before a following `status=$?` line ever runs.

echo ""
echo "==> dotfiles installer ($DOTFILES_DIR) -- reasserting the personal overlay"
dotfiles_status=0
"$PYTHON" "$DOTFILES_DIR/install.py" "$@" || dotfiles_status=$?

compose_mode=write
for flag in "$@"; do
  case "$flag" in
    # A short-flag cluster such as -qh asks for help too.
    -h|--help|-[!-]*h*) compose_mode=skip ;;
    --dry-run) [ "$compose_mode" = skip ] || compose_mode=check ;;
  esac
done
if [ "$compose_mode" = write ] && [ "$dotfiles_status" -gt 1 ]; then
  compose_mode=skip
  echo "" >&2
  echo "install-with-agent-toolkit.sh: dotfiles installer exited $dotfiles_status -- not recomposing global-instructions.md" >&2
fi

compose_status=0
case "$compose_mode" in
  write)
    echo ""
    echo "==> composing claude/global-instructions.md from $AGENT_TOOLKIT_DIR"
    "$PYTHON" "$COMPOSER" --agent-toolkit-path "$AGENT_TOOLKIT_DIR" || compose_status=$?
    ;;
  check)
    # Report-only under --dry-run: exit 1 (stale) is what the real run
    # would fix, not a failure of this one -- but exit 2 means the real run
    # could not compose at all, so that one still counts.
    echo ""
    echo "==> [dry-run] would recompose claude/global-instructions.md from $AGENT_TOOLKIT_DIR"
    "$PYTHON" "$COMPOSER" --check --agent-toolkit-path "$AGENT_TOOLKIT_DIR" || compose_status=$?
    [ "$compose_status" -ne 1 ] || compose_status=0
    ;;
esac

if [ "$agent_toolkit_status" -ne 0 ] || [ "$dotfiles_status" -ne 0 ] || [ "$compose_status" -ne 0 ]; then
  echo "" >&2
  echo "install-with-agent-toolkit.sh: agent-toolkit exit=$agent_toolkit_status dotfiles exit=$dotfiles_status compose exit=$compose_status" >&2
  exit 1
fi
