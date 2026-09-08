# Work machine cutover: zip-only, `--profile=work`

A runbook for provisioning `agent-toolkit` on a machine where dotfiles is
already installed from a downloaded ZIP — no git checkout, no `git pull`,
no SSH key or PAT. This is the **inverse** case of README.md's "Dual-repo
machines: installation ordering" section: that section's wrapper
(`scripts/install-with-agent-toolkit.sh`) exists so dotfiles' composed
personal overlay always wins the five shared destinations, which is correct
for a personal machine. A work machine wants the opposite — agent-toolkit's
bare content on those five destinations, no personal overlay — so the
wrapper is *not* what to run here. This file is the alternative procedure
for that case.

## The situation

Two repos manage global harness config. `agent-toolkit` owns the shared
skills, scripts, and per-harness plumbing. `dotfiles` owns everything
genuinely personal — nvim, shell, Alacritty — and, on top of that, still
composes a personal-policy overlay into five destinations both repos know
how to write:

| Harness | Destination |
| --- | --- |
| Claude Code | `~/.claude/CLAUDE.md` |
| Copilot | `~/.copilot/copilot-instructions.md` |
| agy | `~/.gemini/GEMINI.md` |
| Pi | `~/.pi/agent/AGENTS.md` |
| Codex | `~/.codex/AGENTS.md` |

On a work machine those five should carry agent-toolkit's bare
`CORE_INSTRUCTIONS.md` — no repo-prefix table, no cross-machine sync, no
personal-account content on work hardware.

## Before you start

- **Decide the harness list for this machine.** Any of `claude, copilot,
  agy, pi, codex`. `opencode` is refused outright under `--profile=work` —
  don't include it. codex/copilot/agy are best-effort tier: the five base
  skills work, but `standup`/`to-tickets` genuinely don't exist there yet
  (not broken, just absent).
- **Check `~/dotfiles` for anything that isn't part of the repo.** A fresh
  ZIP only adds/overwrites the files it contains — it won't touch anything
  dropped in there by hand. You're about to replace the directory wholesale
  (see step 1), so pull out anything local-only first.
- **Confirm browser access to both GitHub repos.** A ZIP download is
  authenticated by your logged-in browser session, not an SSH key or
  PAT — this works the same for the private `agent-toolkit` repo as it does
  for dotfiles; you own it, no collaborator step needed.

## The procedure

### 1. Replace `~/dotfiles` wholesale (dotfiles)

Download the current dotfiles ZIP the way you always do, then remove the
old directory entirely and extract fresh under the same path. A zip overlay
wouldn't delete `pi/`, `copilot/`, or `agy/` on its own — those trees were
dropped from dotfiles upstream (2026-09-04), and a clean re-extract is what
actually removes them locally too.

### 2. Download & extract agent-toolkit (agent-toolkit)

From `github.com/theonepichael/agent-toolkit` → Code → Download ZIP.
Extract wherever you'd like it to live, e.g. `~/Workspace/agent-toolkit`.

### 3. Run dotfiles' installer first (dotfiles — first)

This is the one place the order reverses from README.md's dual-repo
section. Running dotfiles first claims nvim/shell/Alacritty — unconditional,
no harness or profile scoping touches those entries — and, temporarily, the
five shared destinations too. That's expected; step 4 overwrites those five
on purpose.

```sh
cd ~/dotfiles
python3 install.py --profile=work --harness=claude,copilot,pi,codex
```

Swap in your actual harness list. `watchcommit` and `dev_status_sync.py`
are excluded automatically under `--profile=work` (`profile_exclude =
["work"]` in `links.toml`) — nothing to do there.

### 4. Run agent-toolkit's installer second, with the override (agent-toolkit — second)

`agent-toolkit/install.py` detects `~/dotfiles` and silently skips the five
shared destinations unless told otherwise — that guard exists to stop a
direct run from accidentally dropping a *personal* machine's overlay. Here
you want the opposite outcome on purpose, so pass the override its own skip
message names:

```sh
cd ~/Workspace/agent-toolkit
# dotfiles is present, so the override is required to win these 5 paths:
AGENT_TOOLKIT_INSTALL_WRAPPER=1 python3 install.py --profile=work --harness=claude,copilot,pi,codex
```

Same harness list as step 3. This run wins the five shared destinations
back with agent-toolkit's bare content, and claims every harness-specific
skill/script agent-toolkit manages. It never touches nvim/shell/Alacritty —
those stay exactly as step 3 left them.

### 5. Wire the shell integration (shell)

Add to `~/.zshrc` (or `~/.bashrc`) if it isn't already there from a
previous provisioning pass:

```sh
[[ -f "$HOME/.agent-tools.zsh" ]] && source "$HOME/.agent-tools.zsh"
```

### 6. Audit both link tables (verify — both repos)

Read-only, mutates nothing:

```sh
python3 install.py --check-links --harness=claude,copilot,pi,codex --profile=work   # in agent-toolkit
python3 install.py --check-links --harness=claude,copilot,pi,codex --profile=work   # in ~/dotfiles
```

Both should report every entry present, correct, and backed by a file that
exists — zero broken-source, wrong-target, or orphaned.

### 7. Spot-check the five destinations (verify — spot check)

The check above proves the manifests agree with the filesystem — it won't
tell you *which* repo won each destination. Confirm directly:

```sh
readlink ~/.claude/CLAUDE.md   # should end in agent-toolkit/claude/CORE_INSTRUCTIONS.md
readlink ~/.codex/AGENTS.md
```

If either still points into `~/dotfiles/claude/global-instructions.md`,
step 4 didn't run with the override, or ran before step 3 rather than
after — re-run step 4.

## Worth knowing going in

- Only Claude Code has an actual tightened `settings.work.json` today — the
  other harnesses just get their normal seed under `--profile=work`.
- `/second-opinion`'s non-Claude model keys come from your own
  `~/.secrets` (`SECOND_OPINION_*`) — nothing here creates that file. Skip
  it, or supply work-appropriate keys, before relying on that skill.
- Neither checkout is a git repo on this machine, so there's no
  `/backlog-item` worktree workflow for contributing fixes back to either
  repo from here — fine if you're only using the tools, not developing
  them, on this machine.
- This exact shape — dotfiles-then-agent-toolkit, zip-sourced,
  `--profile=work`, no git — hasn't been run live before. Treat steps 6/7
  as the real test, not a formality.

## If something's wrong

Both repos support `--rollback` (reverses every mutation either installer
has ever recorded) and `--depart` (removes everything a specific run owns,
with a preflight report). Run either directly in the repo you mean — there
is no wrapper in play here to route it for you.
