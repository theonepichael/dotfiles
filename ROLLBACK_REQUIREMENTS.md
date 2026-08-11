# Rollback feature — clarified requirements

Requirements for evolving `install.sh --rollback` from "undo the last run's
file mutations" into a complete teardown. Captured here before
implementation so the scope is explicit and reviewable on its own, separate
from the `--rollback` code today (`install.sh` lines ~130–196, documented in
`README.md` under "Failures, skips, and rollback").

**Status: implemented**, including the `--wipe` modifier added afterward (see
requirement #5, added once the base rollback landed). The "Baseline" section
below describes the pre-implementation behavior for context; see
requirements #1–#5 for what changed, `README.md`'s "Failures, skips, and
rollback" section for the current user-facing behavior, and `install.sh
--help`/`README.md`'s migration note for the `history.tsv` → `history.jsonl`
manifest-format change this feature also depended on.

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

### 2. Packages: leave them installed (unchanged from today)

Rollback does **not** uninstall packages — this matches the existing
design and was reconsidered after clarifying the actual motivating use
case (undo a bad local config change, then cleanly redo setup via a fresh
`install.sh` run). Uninstalling/reinstalling packages does nothing for that
goal and adds real risk: package managers can refuse or cascade-remove
when something else on the machine has since come to depend on a package
`install.sh` installed. `--rollback` continues to only report packages
("package left installed"), same as today.

### 3. Out of scope: system-level side effects

Explicitly **not** part of a plain `--rollback` — stays file-level only
(packages, as covered in #2, are also left untouched):

- watchcommit services: systemd `--user` unit (enable/start,
  `loginctl enable-linger`) and the macOS launchd agent are not
  stopped/disabled/unloaded by rollback.
- Caps Lock → Escape plist rewrite is not reverted (no backup is taken of
  the prior keyboard mapping either, so there's nothing to restore from).
- Rectangle preferences import (`defaults import`) is not reverted.

Anyone revisiting this later: these were deliberately descoped, not
overlooked — treat adding them as a separate feature request, not a bug in
this one. **Exception carved out by `--wipe` (requirement #5):** with that
modifier, the Linux watchcommit systemd `--user` unit *is* disabled and
stopped. The macOS launchd agent, Caps Lock→Escape remap, and Rectangle
preferences remain untouched even under `--wipe` — none of them have a clean
filesystem-delete equivalent.

### 4. Failure handling: skip-and-report

Consistent with `install.sh`'s existing philosophy (see the file's own
opening comment and the `note_skip`/`SKIPPED` pattern): rollback must never
abort partway through. Every step that can't be reversed as expected (file
already gone, symlink retargeted since, backup destination missing) gets
logged via the same skip-and-report mechanism and rollback continues with
everything else. End with a loud summary of
what didn't reverse cleanly, non-zero exit if anything was skipped — same
contract the rest of the script already has.

### 5. `--wipe`: optional full teardown, not just original-state restore

Added after the base rollback feature above shipped, as a modifier on
`--rollback` (requires it; rejected standalone). Where plain `--rollback`
restores the machine to its state just before `install.sh` first ran
(originals restored from `.bak`), `--wipe` goes further — a true blank-slate
undo with nothing dotfiles-related left behind, at the cost of the original
pre-dotfiles files:

- Deletes `.bak` backups outright instead of restoring them.
- Sweeps state the installer creates but never records in its manifest:
  nvim's runtime directories (`~/.local/share/nvim`, `~/.local/state/nvim`,
  `~/.cache/nvim`).
- On Linux, disables and stops the watchcommit systemd `--user` service —
  see the exception carved out of requirement #3 above.
- Packages are still never touched, same as plain rollback.
- Still excluded, same as plain rollback and for the same reason (no clean
  filesystem-delete equivalent): the macOS watchcommit launchd agent,
  Rectangle preferences, and the Caps Lock→Escape remap.

## Acceptance criteria

- Running `install.sh --rollback` after N historical runs (spanning
  multiple `--harness`/`--profile` combinations over time) removes every
  symlink, restores every backed-up file, and deletes every copied file
  this script ever created, across all N runs — not just the latest.
  Packages are left installed and merely reported, as today.
- No watchcommit service state, macOS keyboard remap, or Rectangle prefs
  are touched by rollback.
- A rollback that hits an unreversible step anywhere logs it and keeps
  going; it never stops with earlier steps reversed and later ones
  untouched because of one failure in the middle.
- `--dry-run --rollback` continues to preview the full plan without
  changing anything, per existing behavior.

## Not yet decided (flag for implementation-time follow-up)

- Exact on-disk format for the append-only/multi-run manifest (single
  growing file with `run` delimiters vs. one file per run under a
  timestamped directory).
