## Personal Policy

<!-- This user's own configuration, layered on top of CORE_INSTRUCTIONS.md.
     Composed by claude/scripts/gen_core_instructions.py into the final
     claude/global-instructions.md that's actually symlinked to every
     harness. Never synced to agent-toolkit -- see
     scripts/sync_from_dotfiles.py's EXCLUDE tuple. -->

### MCP configuration note

Gmail/Calendar/Drive MCP servers are deliberately not configured under
Copilot CLI on this machine, per the `--work` profile's
no-personal-data-on-work-hardware rule — intentional, not a gap.

### Backlog: this machine's repo-prefix table

| prefix | repo | safe for a swarm worker |
|---|---|---|
| `iron-lb-` | iron-logbook | yes |
| `ajhp-` | ai-job-hunter-pro | yes |
| `atk-` | agent-toolkit | yes |
| `meta-` | dotfiles — the harness itself | **never** |
| `work-` | day-job work | separate policy |

`meta-` is the established name dotfiles goes by rather than a literal
directory name. `meta-` also covers any item touching two repos at once —
not made safe by any single prefix, so it stays under `meta-`. A `meta-`
item edits the harness a worker is itself running, which is why it never
goes to a worker — hand those to a normal session.

`work-` is also what `/standup`'s `work_backlog_prefixes` config filters
on; keep the two in sync if that prefix ever changes.

### Backlog: cross-machine sync

The backlog/pending store is per-machine by default. If the user wants it
reconciled with another machine's store, use
`python3 ~/.claude/scripts/dev_status_sync.py sync` (add `--dry-run` to
preview, or `status` to check divergence without merging) — a desktop-
initiated bidirectional merge over SSH. This is a manual, occasional
operation, not part of the normal add/update/done loop above. `sync` also
transfers the `~/.claude/data/grill/` artifact files referenced by items'
`related_files` (specs, grill plans, critique notes) via `rsync`, so those
references don't dangle on the other machine; pass `--no-artifacts` to skip,
and `--rsync-io-timeout` to bound each rsync call.

### Git: personal-project bundling preference

For the user's own personal projects only (this dotfiles repo, personal
side projects under their own accounts — never a day-job/work repo, a
`work-`-prefixed backlog item, a work-profile machine, or anything
ambiguous): once a commit is in and the work is tested/verified, the
follow-on sequence — merge to main locally, push to the remote, clean up
(remove the worktree, delete the merged branch) — is what the user almost
always wants next, so offer it as one bundled question ("merge to main,
push, and clean up the worktree?") instead of asking separately at each
step. For anything work-related, or when it's unclear which category a
repo falls into, default to the safer path: keep merge and push as
separate, individually-confirmed asks — never bundle.

### Shell Command Safety: watchcommit auto-commit guard

On a machine running watchcommit (personal, non-work), wrap any command
you run yourself that deliberately leaves a watched repo (`~/dotfiles`)
in a broken or temporary state on purpose — a test/demo script proving a
staleness check works, a deliberate mid-refactor pause, anything the user
hasn't reviewed yet — in `wc-guard <command>` (`scripts/wc-guard`,
installed to `~/.local/bin/wc-guard`). Without it, watchcommit's 90s poll
can auto-commit and auto-push the broken state to `main` before anyone
reviews it, with an LLM-written commit message that makes the breakage
look intentional — this already happened once (commit `cd0bad8`, fixed
forward in `60401d2`). A plain edit you intend to keep doesn't need the
wrapper; this is specifically for state that is deliberately, temporarily
wrong.
