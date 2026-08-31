# Changelog

This repo's internal tooling changes are logged here. Breaking changes to
harness CLIs, flags, or behavior get an entry going forward.

## 2026-08-30

### Added

- Pi (`@earendil-works/pi-coding-agent`) added as a 5th harness alongside
  Claude Code, Copilot, opencode, and agy — purely additive, nothing removed
  or restructured for the existing four. `claude/CLAUDE.md` is symlinked to
  `~/.pi/agent/AGENTS.md` (Pi has no fallback to `~/.claude/CLAUDE.md` like
  opencode does, so it needs a real link of its own, same as agy's
  `GEMINI.md`). `pi/prompts/*.md` (8 files, Pi's
  command layer) symlink into `~/.pi/agent/prompts/`; `pi/extensions/
  permission-gate.ts`, `pi/extensions/ruff-format-on-edit.ts`, and
  `pi/extensions/guard-rails.ts` (rm -rf/sudo confirmation gates, blocks
  `git commit` on `main`/`master`, protects `.env`/`.git`/`node_modules`
  from writes — all three live-tested against a real Pi session) symlink
  into `~/.pi/agent/extensions/`; `pi/settings.json` points Pi's `skills`
  setting straight at
  `agy/skills/` instead of duplicating a skills tree, and is copy-once-
  seeded to `~/.pi/agent/settings.json` the same drift-reporting way as
  Claude Code's `settings.json`. `install.py` gains `"pi"` in
  `VALID_HARNESSES` and a `seed_pi_settings()` function; `--harness=pi` is
  fully available on both `--profile=personal` and `--profile=work` — no
  work-profile exclusion, unlike opencode's. `gen_shell_completion.py`
  gains a `pi` entry (reusing the `commander` adapter, since Pi has no
  native completion subcommand). `llm_backends.py`'s `BACKEND_PRIORITY`
  gains `"pi"`, placed right after `"agy"`. New env vars
  `SECOND_OPINION_PI_MODEL`, `SECOND_OPINION_PI_MODEL_POOL`, and
  `SECOND_OPINION_PI_TIMEOUT_SECONDS` follow the same per-backend contract
  as the other three backends. See `pi/CLAUDE_CODE_PARITY.md` for the full
  verification notes.

### Fixed

- **opencode was loading no instructions at all.** `links.toml` and
  `opencode/CLAUDE_CODE_PARITY.md` §1 both stated that opencode reads
  `~/.claude/CLAUDE.md` as a legacy fallback when no `AGENTS.md` exists, and
  used that as the reason no instruction-file link was managed for it. That
  was compiled from opencode's docs on 2026-07-24 and does not hold against
  opencode 1.18.25: probed live, it loads `AGENTS.md` only — from the
  current directory up to the project root, plus one in its own config
  directory — and never `CLAUDE.md`. The binary's instruction loader
  confirms it, targeting `AGENTS.md` alone. Since nothing existed at
  `~/.config/opencode/AGENTS.md` and this repo's root instructions were
  named `CLAUDE.md`, opencode had neither global nor per-repo context.
  `links.toml` now links `claude/CLAUDE.md` to
  `~/.config/opencode/AGENTS.md` (gated `harness = "opencode"`; a work
  profile rejects that harness at argument-parsing time, so no
  `profile_exclude` is needed), and both prose claims are corrected. Takes
  effect on the next `install.sh --harness=opencode` run.

### Changed

- **Agent instruction files are now `AGENTS.md` with a `CLAUDE.md` symlink
  beside them.** No single filename reaches all five harnesses — Claude Code
  2.1.251 reads `CLAUDE.md` and ignores `AGENTS.md`; opencode 1.18.25 reads
  `AGENTS.md` and never `CLAUDE.md`; Pi 0.84.4 prefers `AGENTS.md` with a
  `CLAUDE.md` fallback; Copilot 1.0.80 reads either; agy 1.1.22 reads no
  project-level file at all. All five were probed live on 2026-08-30 with a
  fixture repo; the full table is in `README.md`. The repo-root `CLAUDE.md`
  is now `AGENTS.md` with `CLAUDE.md` symlinked to it, and `pi/`, `test/`
  and `claude/scripts/` each gained the same pair, carrying the local
  conventions an agent would otherwise get wrong. Only Claude Code attaches
  a nested file automatically, so the root file also carries a one-line
  index of each directory's main hazard.
  `test/test_agents_md_links.py` enforces the pairing — it discovers
  directories rather than listing them, and asserts each `CLAUDE.md` is
  committed as a symlink (mode `120000`) pointing at its own sibling.

## 2026-08-29

### Fixed

- `agy/hooks/agy-elapsed.js` (agy status line): the elapsed-time timer never
  reset after the first turn, every tool call inside a turn was
  misclassified as idle (producing a premature "done" line mid-turn), and
  all concurrent agy processes on a machine shared one state file. Now uses
  a deny-list of idle `agent_state` values, resets the timer only when a new
  turn starts after a genuine idle transition, scopes the state file per
  `session_id`, writes state atomically (temp file + rename), and adds a 6h
  staleness heartbeat to recover from a crashed/killed turn. Also fixed the
  non-Gemini quota label to read "3rd-party" instead of the current model's
  name, since the `3p-*` quota buckets are shared across all non-Gemini
  models. Covered by a new `agy/hooks/agy-elapsed.test.js` (Node's built-in
  test runner), wired into `uv run pytest` via `test/test_agy_elapsed.py`.

## 2026-08-27

### Fixed

- `llm_backends.py`'s `_run_command` (shared subprocess plumbing for
  agy/opencode/copilot, used by `second_opinion.py` and `dev_status.py`'s
  recap generation) now spawns every backend with `stdin=subprocess.DEVNULL`
  instead of inheriting this process's stdin. Root-caused against a
  currently-open upstream bug, `anomalyco/opencode#38723`: `opencode run`
  unconditionally reads stdin at startup and blocks forever if that
  descriptor never reaches EOF (e.g. an open pipe with no writer) — exactly
  what a long-lived parent process (this one) hands its children by default.
  This reproduced live in this environment and is the dominant cause behind
  `second_opinion.py`'s opencode backend "intermittently hangs" reports,
  distinct from the already-mitigated event-stream-stall issue the existing
  single retry targets.

## 2026-08-26

### Added

- `links.toml` gains a `dir = true` row type: `src` is globbed recursively
  instead of linked as one file, giving each file under it (skipping
  dotfiles and editor swap/backup junk) its own symlink under `dest`. Built
  for the Fidelity local-skill-fork mechanism (permanent, one-way,
  never-git-tracked skill additions under a gitignored `local/` directory)
  but usable by any future row needing the same shape. A `dir=true` row and
  another entry that expand to the same destination abort the run with a
  collision error instead of one silently winning.

### Changed (breaking)

- A plain `install.py` run (not just `--check-links`) now automatically
  removes any manifest-recorded symlink that no current `links.toml` entry
  produces — its manifest entry and now-possibly-empty parent directory too
  — and prints each removal. Previously an orphaned symlink was only ever
  reported via `--check-links`, never removed automatically. Scoped
  strictly to that case: a broken-source, wrong-target, or not-a-symlink
  destination is unaffected and stays human-reviewed via `--check-links`.

## 2026-08-22

### Added

- `scripts/opencode_skills_sync.py` — a standalone, commit-only daemon that
  mirrors `~/.config/opencode/skills` (which is not itself a git repo) into
  a second dotfiles worktree on its own branch (`opencode-skills-live`), so
  a skill authored directly there is captured automatically instead of
  being lost if it's deleted before ever being promoted into
  `dotfiles/opencode/skills`. Skips skills already curated via the existing
  symlink pattern. Never calls `git push`/`fetch`/`pull` — no code path in
  the module reaches a remote. Ships with
  `systemd/opencode-skills-sync.service` and `links.toml` wiring
  (`~/.local/bin/opencode-skills-sync`), Linux personal machines only.

## 2026-08-19

### Added

- `second_opinion.py` now supports model-pool rotation for **agy** via the
  new `SECOND_OPINION_AGY_MODEL_POOL` env var, matching the existing
  opencode/copilot pools. All three backends share one indexed-pool contract.
- `claude/scripts/gen_second_opinion.py` — the five hand-maintained
  `second-opinion` skill/command copies are now generated from one canonical
  body template (`templates/second_opinion.md.tmpl`) plus a per-harness
  parameter table, mirroring `gen_interfaces.py`'s generator/`--check`/
  `--stdout` shape. `--check` also runs a contract-shape check (every
  `review`/`detect` flag `INTERFACES.md` lists for `second_opinion.py` is
  named in the template) and a guard-phrase check (a hand-maintained list of
  exact substrings that must agree between `INTERFACES.md`'s help text and
  the template, for flags the template makes a specific behavioral claim
  about). Do not hand-edit the five copies — edit the template or the
  parameter table and regenerate.

### Changed (breaking)

- `second_opinion.py review --model-index N` is now a **hard error** when the
  selected backend has no configured pool (`SECOND_OPINION_<BACKEND>_MODEL_POOL`
  unset/empty) — previously it silently proceeded with the default model (a
  no-op for agy, a fallback for opencode/copilot). The error names the exact
  pool variable and the `--backend` recovery path.
- `--model-index N` is now **rejected** when `N` is outside the pool's range
  (`0..len(pool)-1`) instead of wrapping modulo pool length. Previously an
  out-of-range index silently selected the wrong model.
- An explicit `--model-index` now selects the pool entry **even when a
  single-model override** (`SECOND_OPINION_<BACKEND>_MODEL`) is set; without
  `--model-index` the single override (or backend default) still wins.
- Automatic backend selection now **stops on the first priority candidate
  that fails pool validation** (`[agy, opencode, copilot]` order) rather than
  silently falling through to a later backend — a configuration error is not
  a fallback trigger. Use `--backend <configured-backend>` to target a
  working one. Execution failures (a backend runs but errors) still fall
  through as before.
- Updated all six `second-opinion` guidance copies, `README.md`,
  `INTERFACES.md`, and this changelog; `agy` is no longer documented as
  non-pool-capable.

## 2026-08-14

### Changed

- `dev_status.py recap --force` renamed to `--refresh`
  ([`d35d82a`](https://github.com/theonepichael/dotfiles/commit/d35d82a6624328b7e63f3c768dc915260e007129)).
  `--force` previously bypassed the recap freshness cache with no destructive
  semantics; the new flag name matches that intent. `prune --force` is
  unchanged.
- `second_opinion.py`'s opencode backend now fails hard if the adversary
  agent emits a `tool_use` event, instead of silently returning whatever text
  also appeared; the `adversary` agent is now text-only by construction
  (`"permission": "deny"`). Closes the gap where a swapped-in model could
  take real shell/file actions rather than returning a stateless critique.
- `second_opinion.py` now also rejects critiques that are a denied tool call
  leaked through as prose: a tool-starved model can emit its attempted call
  as text (XML like `<tool_calls>`/`<invoke>`, or a JSON `tool_calls`/
  `tool_use` block) in a text event instead of a real `tool_use` event, which
  was previously returned verbatim. Both opencode `run_opencode` paths now
  raise when the returned text is dominated by one of those shapes — bounded
  to a scan window so it can't block on a large repetitive response, and
  requiring the matched markup to dominate the response (not merely appear
  in it) so a critique that quotes an example of this failure mode isn't
  itself rejected. The `adversary` agent gained an explicit text-only
  `prompt`, and the critique prompt now forbids tool-call markup. Note:
  gpt-5.6-luna specifically fails outright (not a text leak) under this
  agent's restricted permission, reproduced regardless of permission shape —
  pin a different model via `SECOND_OPINION_OPENCODE_MODEL` if you hit it.

## 2026-08-15

### Added

- `second_opinion.py review` gained per-machine model-pool rotation for the
  opencode and copilot backends: `SECOND_OPINION_OPENCODE_MODEL_POOL` /
  `SECOND_OPINION_COPILOT_MODEL_POOL` (comma-separated model IDs) plus a new
  `--model-index N` flag (0-based, non-negative) pick
  `pool[N % len(pool)]` for that call. The existing
  `SECOND_OPINION_OPENCODE_MODEL`/`_COPILOT_MODEL` single-model overrides
  still work and win outright over a configured pool. Both env vars are
  unset by default, so behavior is unchanged unless you opt in. `agy` is
  unaffected — no pool/rotation for it. The `/second-opinion` skill's
  iteration loop now passes `--model-index` on every round. grill-me's
  `--auto` mode has no rotation available through either of its documented
  paths: its Primary path is a native opencode Task-tool spawn (no
  per-spawn model override exists in opencode's Task tool), and its
  Alternative path is `agy`-only (no pool support) by design — see
  `opencode/skills/grill-me/SKILL.md` for the corrected explanation.

### Changed

- `dev_status.py render` now shows at most the five most recently completed
  backlog items in `DONE`, replacing the previous 48-hour recency window.
  Recap generation uses the same selected completions while retaining the
  broader 48-hour journal activity window.
- `second_opinion.py`'s default per-backend subprocess timeout dropped from
  300s to 120s, so a hung backend blocks a single review attempt for at
  most ~2 minutes before `cmd_review`'s existing cross-backend fallback
  moves on, instead of a full 5 minutes. Each backend also gained an
  optional override — `SECOND_OPINION_AGY_TIMEOUT_SECONDS`,
  `SECOND_OPINION_OPENCODE_TIMEOUT_SECONDS`,
  `SECOND_OPINION_COPILOT_TIMEOUT_SECONDS` — for a backend known to run
  slower or faster than the default. `SECOND_OPINION_TIMEOUT_SECONDS`
  (unset, blank, non-integer, zero, or negative all fall back to 120) is
  now hardened the same way it always should have been: previously a
  non-integer value crashed the module at import, and zero/negative values
  silently parsed and propagated as an unusable timeout. A hard ceiling of
  300s — the previous global default — now applies to every timeout,
  default or overridden, so a large per-backend override can no longer
  recreate the old unbounded-per-backend wait.

## 2026-08-18

### Added

- `install.py`/`install.sh --adopt` copies intentional drift from selected
  live copy-once seeds back into the repository for Claude, WSL VS Code, and
  opencode. Adoption requires tracked, clean repo seeds, creates no backup or
  history entry, normalizes CRLF to LF, and refuses empty, unreadable, dirty,
  untracked, malformed opencode, or live allowlist-bypass inputs.
