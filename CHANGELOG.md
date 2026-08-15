# Changelog

This repo's internal tooling changes are logged here. Breaking changes to
harness CLIs, flags, or behavior get an entry going forward.

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
