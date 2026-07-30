# Rollback feature — clarified requirements

Requirements for evolving `install.sh --rollback` from "undo the last run's
file mutations" into a complete teardown. Captured here before
implementation so the scope is explicit and reviewable on its own, separate
from the `--rollback` code today (`install.sh` lines ~130–196, documented in
`README.md` under "Failures, skips, and rollback").

## Baseline: what `--rollback` does today

- Single manifest at `~/.local/state/dotfiles/last-run.tsv`, truncated and
  rewritten at the start of every `install.sh` run (`manifest_init`).
- Records four action types: `symlink-created`, `file-copied`,
  `file-backed-up`, `package-installed`.
- `--rollback` replays the current manifest in reverse: removes created
  symlinks/copies, restores backed-up files from their `.bak` path, deletes
  the manifest, exits.
- Packages are reported only, never uninstalled ("Packages are never
  uninstalled by rollback — they're identical across profiles").
- Because the manifest is overwritten each run, only the **most recent**
  run's mutations are ever recoverable this way — anything from an earlier
  run is permanently outside `--rollback`'s reach.

## Clarified requirements

### 1. Scope: full history, not just the last run

`--rollback` must be able to undo **every** mutation `install.sh` has ever
made on the machine, back to a state as if it had never been run — not just
the most recent invocation.

Implication for the manifest: the current "one file, truncated per run"
model can't satisfy this. It needs to become either an append-only log
across all runs (with per-run boundary markers, as the `run` action already
provides) or a retained set of per-run manifest files. Either way, rollback
must walk *all* recorded runs, newest-to-oldest, and reverse each in turn.

Open implementation question: when the same path is mutated across
multiple runs (e.g. a file is symlinked in run 1, backed up and relinked in
run 2), the replay needs to collapse to the correct final undo (restore the
*original* pre-dotfiles file, not an intermediate state) rather than
naively replaying every historical entry.

### 2. Packages: actually uninstall them

Rollback must run the real removal command for everything `install.sh`
installed, not just report it:

- macOS: `brew uninstall` for formulae, `brew uninstall --cask` for casks.
- Linux: `apt-get remove` / `dnf remove` per package, matching whichever
  branch installed it.
- `uv tool uninstall ruff`, and removal of the `uv`/`oh-my-posh` installers'
  own binaries where they were installed by this script.
- `npm uninstall -g @anthropic-ai/claude-code` / `@github/copilot`.
- The pinned JetBrains Nerd Font directory.

This is a change from today's explicit design choice ("packages are
profile-independent, so leave them"). Flagging the risk directly: package
managers can refuse or cascade-remove when something else on the machine
now depends on a package `install.sh` installed. Rollback's failure handling
(see #4) is what keeps that from turning into a hard stop.

### 3. Out of scope: system-level side effects

Explicitly **not** part of this rollback feature — stays file/package-level
only:

- watchcommit services: systemd `--user` unit (enable/start,
  `loginctl enable-linger`) and the macOS launchd agent are not
  stopped/disabled/unloaded by rollback.
- Caps Lock → Escape plist rewrite is not reverted (no backup is taken of
  the prior keyboard mapping either, so there's nothing to restore from).
- Rectangle preferences import (`defaults import`) is not reverted.

Anyone revisiting this later: these were deliberately descoped, not
overlooked — treat adding them as a separate feature request, not a bug in
this one.

### 4. Failure handling: skip-and-report

Consistent with `install.sh`'s existing philosophy (see the file's own
opening comment and the `note_skip`/`SKIPPED` pattern): rollback must never
abort partway through. Every step that can't be reversed as expected (file
already gone, symlink retargeted since, package removal refused/cascades,
uninstall command fails) gets logged via the same skip-and-report mechanism
and rollback continues with everything else. End with a loud summary of
what didn't reverse cleanly, non-zero exit if anything was skipped — same
contract the rest of the script already has.

## Acceptance criteria

- Running `install.sh --rollback` after N historical runs (spanning
  multiple `--harness`/`--profile` combinations over time) removes every
  symlink, restores every backed-up file, deletes every copied file, and
  uninstalls every package this script ever installed, across all N runs —
  not just the latest.
- No watchcommit service state, macOS keyboard remap, or Rectangle prefs
  are touched by rollback.
- A rollback that hits an unreversible step anywhere logs it and keeps
  going; it never stops with earlier steps reversed and later ones
  untouched because of one failure in the middle.
- `--dry-run --rollback` continues to preview the full plan (now including
  package uninstalls) without changing anything, per existing behavior.

## Not yet decided (flag for implementation-time follow-up)

- Whether multi-run replay needs a confirmation prompt before removing
  packages, given the cascade-removal risk noted in #2 — not asked about
  yet, worth a explicit decision before landing the change.
- Exact on-disk format for the append-only/multi-run manifest (single
  growing file with `run` delimiters vs. one file per run under a
  timestamped directory).
