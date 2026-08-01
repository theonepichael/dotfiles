---
name: backlog-item
description: "Runs a dev_status.py backlog item end-to-end: resolve, worktree, grill-me plan, second-opinion critique, execution handoff, TDD implement, verify, commit/merge/push gates, prune, review+approve. Use when the user says 'work on backlog item 4', 'pick up <slug>', 'let's do the next backlog item', or otherwise names a specific item to work end-to-end."
argument-hint: [slug|N]
---

Work the named item to done, one step at a time. Every gate below stops and
waits for the user — never collapse two gates into one approval.

## 1. Resolve
`python3 ~/.claude/scripts/dev_status.py show <slug|N>`. Read the full
record — never start from the dashboard's one-line summary (CLAUDE.md).
Empty context/next_steps/related_files: stop and ask the user to fill them
in; don't fabricate a plan from the title. Numeric id: note the rendered rev
for `--if-rev` on the next mutating call. related_files already names a
grill plan (`~/.claude/data/grill/<slug>-plan.md`)? Planning and critique
(steps 5–6) are already done — skip to step 8. Worktree already has
implemented, uncommitted changes (e.g. handed back from an external
executor)? Skip straight to step 9.

## 2. Start
If not already in-progress: `dev_status.py start <slug|N>` (`--if-rev <N>`
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
Delegate to the `grill-me` skill (Skill tool) with the item's context/
next_steps as topic. Let it own its full protocol — Q&A, --verify,
executor-readiness — don't hand-roll `grill.py` calls here. Once it records
a plan path, add that path to the item's related_files if missing
(CLAUDE.md's "Plans and deliverables get a path on record"). Take the
executor-readiness offer seriously: this user's default is a hardened plan
handed to a cheaper executor, not continuing in-session. Decline grill-me's
own clear-and-go offer here — step 7 below owns the handoff decision, since
this user's real executors include targets grill-me's clear-and-go doesn't
reach.

## 6. Critique
Offer the `second-opinion` skill against the resulting plan file.
Recommended: yes — critique the plan before committing to an executor.

## 7. Handoff
Decide who implements the plan — ask if it isn't already obvious from the
conversation:
- **Same session, now.** Trivial/small item → go to step 8 immediately.
- **Cheaper Claude session.** Now run grill-me's `mark-pending-execution`
  and tell the user to resume with `/backlog-item <slug|N>` after `/clear`
  — step 1 finds the recorded plan and resumes at step 8.
- **opencode/GLM-5.2 — personal projects only, never at work; this user
  does not use opencode in a work context under any circumstances.**
  Confirm the model actually exists in opencode's catalog (`opencode
  models`) before invoking — don't assume the version number is right,
  dictation has flubbed it before. From the worktree, hand off
  non-interactively: `opencode run -m opencode-go/glm-5.2 "Implement <plan
  path> exactly as written — TDD, run the full suite, then STOP without
  committing and report the diff."`. GLM never gets the commit gate. Once
  it reports back, tell the user to point Claude at the worktree to
  review — that resumes at step 9.

For a work-related item, only the first two options are on the table —
don't offer the opencode/GLM route at all.

## 8. Red, green
TDD in the worktree: a failing test that proves the gap the plan names,
then the minimal implementation.

## 9. Verify
Run the full suite (and lint, if present) in the worktree and show the
output — "should work" is not verification (CLAUDE.md).

## 10. Gate: commit
Show the full diff. Stop — AskUserQuestion for explicit commit approval. No
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
`dev_status.py review <slug|N>` then `approve <slug|N>` — never a bare
`done` on an in-review item. Display the full dashboard stdout these print;
don't just narrate a one-line confirmation.
