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
# Only for a plain install run. --rollback/--check-links/--depart/--wipe
# each mean something different per-repo (rolling back BOTH repos' entire
# histories together, auditing two different link tables at once) that this
# script does not attempt to reconcile -- run install.py directly in the
# repo you mean for those.
set -eu

# shellcheck disable=SC1007 # CDPATH= clears CDPATH for this one command so cd ignores it.
DOTFILES_DIR="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
AGENT_TOOLKIT_DIR="${AGENT_TOOLKIT_PATH:-$HOME/Workspace/agent-toolkit}"

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

if [ ! -f "$AGENT_TOOLKIT_DIR/install.py" ]; then
  echo "install-with-agent-toolkit.sh: no install.py at $AGENT_TOOLKIT_DIR" >&2
  echo "  set AGENT_TOOLKIT_PATH to override the default (~/Workspace/agent-toolkit)." >&2
  exit 2
fi

for candidate in python3.13 python3.12 python3 python; do
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

if [ "$agent_toolkit_status" -ne 0 ] || [ "$dotfiles_status" -ne 0 ]; then
  echo "" >&2
  echo "install-with-agent-toolkit.sh: agent-toolkit exit=$agent_toolkit_status dotfiles exit=$dotfiles_status" >&2
  exit 1
fi
