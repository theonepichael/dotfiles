---
description: "Runs a dev_status.py backlog item end-to-end: resolve, worktree, grill-me plan, second-opinion critique, execution handoff, TDD implement, verify, commit/merge/push gates, review+approve. Use when the user says 'work on backlog item 4', 'pick up <slug>', 'let's do the next backlog item', or otherwise names a specific item to work end-to-end."
---

Work the named item to done, one step at a time. The target item is
`$ARGUMENTS` — a slug or an integer N. If it's empty, ask the user which
item — never guess. Every gate below stops and waits for the user — never
collapse two gates into one approval.

## 1. Resolve
`python3 ~/.claude/scripts/dev_status.py show $ARGUMENTS`. Read the full
record — never start from the dashboard's one-line summary (CLAUDE.md).
Empty context/next_steps/related_files: stop and ask the user to fill them
in; don't fabricate a plan from the title. Numeric id: note the rendered rev
for `--if-rev` on the next mutating call. related_files already names a
grill plan (`~/.claude/data/grill/<slug>-plan.md`)? Planning and critique
(steps 5–6) are already done — skip to step 8. Worktree already has
implemented, uncommitted changes (e.g. handed back from an external
executor)? Skip straight to step 9.

## 2. Start
If not already in-progress: `dev_status.py start $ARGUMENTS` (`--if-rev <N>`
for numeric ids).

## 3. Branch
related_files names exactly one project repo → worktree it per CLAUDE.md's
Git section: `git -C <repo> worktree add ../<repo-name>-<slug> -b <slug>`.
Reuse a worktree this session already made for this item instead of a
second one. Multiple repos, or none: ask which repo — never guess.

## 4. Baseline
Run that repo's test suite (or the most relevant targeted subset) in the
fresh worktree before touching anything (CLAUDE.md's "Baseline tests before
starting code work").

## 5. Plan
Load the `grill-me` skill via opencode's native skill tool
(`skill({ name: "grill-me" })`) and follow its protocol with the item's
context/next_steps as topic. Let it own its full protocol — Q&A, --verify,
clear-and-go — don't hand-roll `grill.py` calls here. Once it records a plan
path, add that path to the item's related_files if missing (CLAUDE.md's
"Plans and deliverables get a path on record"). Take the clear-and-go offer
seriously: this user's default is a hardened plan handed to a cheaper
executor, not continuing in-session. Decline grill-me's own clear-and-go
offer here — step 7 below owns the handoff decision, since this user's real
executors include targets grill-me's clear-and-go doesn't reach.

## 6. Critique
Offer the `second-opinion` skill (same skill tool,
`skill({ name: "second-opinion" })`) against the resulting plan file.
Recommended: yes — critique the plan before committing to an executor.

## 7. Handoff
Decide who implements the plan — ask if it isn't already obvious from the
conversation:
- **Same session, now.** Trivial/small item → go to step 8 immediately.
- **Fresh opencode session.** Now run `grill.py mark-pending-execution
  --backlog-slug <slug>` (the plan's session, with this item's slug — not
  its own resolved-topic slug) and tell the user to start a fresh session
  and type `/backlog-item <slug|N>`. opencode has no SessionStart hook to
  auto-surface the marked plan (the hooks→plugin port is still deferred), so
  the typed command IS the resume path — step 1 sees the plan in
  related_files and skips to step 8.
- **Different/cheaper model.** Confirm the model actually exists in
  opencode's catalog (`opencode models`) before invoking — don't assume the
  version number is right, dictation has flubbed it before. From the
  worktree, hand off non-interactively: `opencode run -m <provider/model>
  "Implement <plan path> exactly as written — TDD, run the full suite, then
  STOP without committing and report the diff."` An external executor never
  gets the commit gate. Once it reports back, review — that resumes at
  step 9.

## 8. Red, green
TDD in the worktree: a failing test that proves the gap the plan names,
then the minimal implementation.

## 9. Verify
Run the full suite (and lint, if present) in the worktree and show the
output — "should work" is not verification (CLAUDE.md).

## 10. Gate: commit
Show the full diff. Stop — `question` tool for explicit commit approval. No
exceptions for being mid-pipeline, and no exception for code an external
executor wrote (CLAUDE.md).

## 11. Gate: commit-then-land
On approval, commit (conventional format) — this gate is never bundled with
what follows. Personal project (this repo, a personal side project — never
a `work-`-prefixed item or a work repo): offer the follow-on sequence as one
bundled question (CLAUDE.md's Git section) — "merge to main, push, and
clean up the worktree?" — then merge locally, push, `git worktree remove`,
`git branch -d` on that single approval. Work-related or ambiguous: ask
separately for merge and for push — never bundle.

## 12. Close
`dev_status.py review $ARGUMENTS` then `approve $ARGUMENTS` — never a bare
`done` on an in-review item. Display the full dashboard stdout these print;
don't just narrate a one-line confirmation.
