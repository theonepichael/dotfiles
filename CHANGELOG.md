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
