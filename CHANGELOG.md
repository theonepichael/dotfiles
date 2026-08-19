# Changelog

This repo's internal tooling changes are logged here. Breaking changes to
harness CLIs, flags, or behavior get an entry going forward.

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
