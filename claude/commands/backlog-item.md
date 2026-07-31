---
name: backlog-item
description: "Runs a dev_status.py backlog item end-to-end: resolve, worktree, grill-me plan, second-opinion critique, TDD implement, verify, commit/merge/push gates, prune, review+approve. Use when the user says 'work on backlog item 4', 'pick up <slug>', 'let's do the next backlog item', or otherwise names a specific item to work end-to-end."
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
grill plan (`~/.claude/data/grill/<slug>-plan.md`)? That's a resume after a
plan/execute handoff (step 5) — skip straight to step 6 with that plan.

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
executor-readiness, clear-and-go — don't hand-roll `grill.py` calls here.
Once it records a plan path, add that path to the item's related_files if
missing (CLAUDE.md's "Plans and deliverables get a path on record") — this
is what a resumed invocation checks in step 1. Take the executor-readiness
offer seriously and lean toward accepting clear-and-go over continuing
in-session: this user's established pattern is a strong-model plan handed
to a cheaper model for execution. If clear-and-go is taken, tell the user to
resume with `/backlog-item <slug|N>` after `/clear` — step 1 will find the
recorded plan and pick up at step 6.

## 6. Critique
Offer the `second-opinion` skill against the resulting plan file.
Recommended: yes.

## 7. Red, green
TDD in the worktree: a failing test that proves the gap the plan names,
then the minimal implementation.

## 8. Verify
Run the full suite (and lint, if present) in the worktree and show the
output — "should work" is not verification (CLAUDE.md).

## 9. Gate: commit
Show the full diff. Stop — AskUserQuestion for explicit commit approval. No
exceptions for being mid-pipeline (CLAUDE.md).

## 10. Gate: merge, Gate: push
On approval, commit (conventional format). Ask again, separately, before
merging to main. Ask again, separately, before pushing. Three distinct
approvals, never bundled.

## 11. Prune
After a successful merge: `git worktree remove`, then `git branch -d` the
merged branch.

## 12. Close
`dev_status.py review <slug|N>` then `approve <slug|N>` — never a bare
`done` on an in-review item. Display the full dashboard stdout these print;
don't just narrate a one-line confirmation.
