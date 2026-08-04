---
name: backlog-item
description: "Runs a dev_status.py backlog item end-to-end: resolve, worktree, grill-me plan, second-opinion critique, execution handoff, TDD implement, verify, commit/merge/push gates, review+approve. Use when the user says 'work on backlog item 4', 'pick up <slug>', 'let's do the next backlog item', or otherwise names a specific item to work end-to-end."
allowed-tools: shell
---

The target item is whatever slug or integer N the user named in their
prompt (e.g. `/backlog-item 4`, "work on backlog item 4"). If none is
named, ask which item — never guess.

Work the named item to done, one step at a time. Every gate below stops and
waits for the user — never collapse two gates into one approval.

## 1. Resolve
`python3 ~/.claude/scripts/dev_status.py show <slug|N>`. Read the full
record — never start from the dashboard's one-line summary (the shared
instructions file). Empty context/next_steps/related_files: stop and ask
the user to fill them in; don't fabricate a plan from the title. Numeric
id: note the rendered rev for `--if-rev` on the next mutating call.
related_files already names a grill plan (`~/.claude/data/grill/<slug>-plan.md`)?
Planning and critique (steps 5–6) are already done — skip to step 8.
Worktree already has implemented, uncommitted changes (e.g. handed back
from an external executor)? Skip straight to step 9. Either skip: the
worktree lives at `$(dirname <repo>)/<repo-name>-<slug>`, where `<repo>` is
the absolute path from `related_files` — resolve that path explicitly and
work there, not the root checkout. Do not assume `cd ../<repo-name>-<slug>`
resolves correctly; a fresh Copilot session's ambient cwd is not guaranteed
to be the repo root.

## 2. Start
If not already in-progress: `dev_status.py start <slug|N>` (`--if-rev <N>`
for numeric ids).

## 3. Branch
related_files names exactly one project repo → worktree it per the shared
instructions file's Git section: `git -C <repo> worktree add
../<repo-name>-<slug> -b <slug>`. Reuse a worktree this session already
made for this item instead of a second one. Multiple repos, or none: ask
which repo — never guess.

## 4. Baseline
Run that repo's test suite (or the most relevant targeted subset) in the
fresh worktree before touching anything (the shared instructions file's
"Baseline tests before starting code work").

## 5. Plan
Now use the `grill-me` skill with the item's context/next_steps as topic.
Let grill-me run its full protocol — Q&A, plan recording, showing the
render output, and the `--verify` offer if any decision was
defaulted/assumed. Only when it reaches its own end-of-session
clear-and-go offer: do not ask the user that question and do not run
`mark-pending-execution` yet — move immediately to step 6 here instead.
Once grill-me has recorded a plan path, update the backlog item's
`related_files` to include it if not already present (per the shared
instructions file's "Plans and deliverables get a path on record" rule) —
grill-me has no knowledge of `dev_status.py`, so nothing else performs this
update, and step 1's resume branch depends on it.

## 6. Critique
Offer to now use the `second-opinion` skill against the resulting plan
file. Recommended: yes — critique the plan before committing to an
executor.

## 7. Handoff
Decide who implements the plan — ask if it isn't already obvious from the
conversation:
- **Same session, now.** Trivial/small item → go to step 8 immediately.
- **Cheaper Copilot session.** Now run `grill.py mark-pending-execution
  --backlog-slug <slug>`, using the resolved string slug from step 1,
  never the raw numeric `N` — `--backlog-slug` validates
  lowercase-kebab-case and aborts otherwise. Tell the user to start a
  fresh Copilot session — the SessionStart hook auto-prints `Resume via:
  /backlog-item <slug>`, no manual `/clear` needed (unlike Claude, where
  the user types `/clear` themselves).
- **opencode/GLM-5.2 — personal projects only, never at work; this user
  does not use opencode in a work context under any circumstances.**
  Confirm the model actually exists in opencode's catalog (`opencode
  models`) before invoking — don't assume the version number is right,
  dictation has flubbed it before. From the worktree, hand off
  non-interactively: `cd $(dirname <repo>)/<repo-name>-<slug> && opencode
  run -m opencode-go/glm-5.2 "Implement <plan path> exactly as written —
  TDD, run the full suite, then STOP without committing and report the
  diff."`. GLM never gets the commit gate. Once it reports back, review
  the diff directly in the current Copilot session — that resumes at step
  9 here, no second harness to hand off to.

For a work-related item, only the first two options are on the table —
don't offer the opencode/GLM route at all.

## 8. Red, green
Work inside the worktree resolved in step 1 — `cd` there (or use `git -C`)
for every shell command in this step and the next three; do not run
TDD/test/diff/merge commands against the root checkout. TDD in the
worktree: a failing test that proves the gap the plan names, then the
minimal implementation.

## 9. Verify
Run the full suite (and lint, if present) in the worktree and show the
output — "should work" is not verification (the shared instructions
file).

## 10. Gate: commit
Show the full diff (read from the worktree, not root). Stop — ask in
plain text for explicit commit approval, stating your recommendation
first. No exceptions for being mid-pipeline, and no exception for code an
external executor wrote (the shared instructions file).

## 11. Gate: commit-then-land
On approval, commit (conventional format) — this gate is never bundled
with what follows. Personal project (this repo, a personal side project —
never a `work-`-prefixed item or a work repo): offer the follow-on
sequence as one bundled plain-text question (the shared instructions
file's Git section) — "merge to main, push, and clean up the worktree?" —
then merge locally, push, `git worktree remove`, `git branch -d` on that
single approval. Work-related or ambiguous: ask separately for merge and
for push — never bundle. Run merge, push, `git worktree remove`, and `git
branch -d` from the main checkout (`git -C <repo> merge <slug>` etc.), not
from inside the worktree being removed — a branch cannot merge into
itself.

## 12. Close
`dev_status.py review <slug|N>` then `approve <slug|N>` — never a bare
`done` on an in-review item. Display the full dashboard stdout these
print; don't just narrate a one-line confirmation.
