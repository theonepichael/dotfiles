---
name: backlog-item
description: "Runs a dev_status.py backlog item end-to-end: resolve, worktree, spec/plan, second-opinion critique, execution handoff, TDD implement, verify, commit/merge/push gates, review+approve. Use when the user says 'work on backlog item 4', 'pick up <slug>', 'let's do the next backlog item', or otherwise names a specific item to work end-to-end."
---

Work the named item to done, one step at a time. If the user didn't name a
specific item (slug or N), ask which one — never guess. Every user-approval
gate below (`## 10`, `## 11`) stops and waits for the user — never collapse
two gates into one approval. Distinct from those: the item's own `gate`
field in `dev_status.py` (step 5, step 12) is a judgment-step verification
checkpoint, not a user-approval stop — same word, different mechanism,
don't conflate them.

## 1. Resolve
`python3 ~/.claude/scripts/dev_status.py show <slug|N>`. Read the full
record — never start from the dashboard's one-line summary (the shared
instructions file). Empty context/next_steps/related_files: stop and ask
the user to fill them in; don't fabricate a plan from the title. Numeric
id: note the rendered rev for `--if-rev` on the next mutating call.
related_files already names a grill plan (`~/.claude/data/grill/<slug>-plan.md`)
or a spec (`~/.claude/data/grill/<slug>-spec.md`)? Planning and critique
(steps 5–6) are already done — skip to step 8. Worktree already has
implemented, uncommitted changes (e.g. handed back from an external
executor)? Skip straight to step 9. `next_steps` starts with "Resume
backlog-item at step N" (a return pointer left by an earlier suspend, see
step 5–6)? That step N is where to resume, not step 1's normal dispatch.

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

## 5. Spec or plan
Delegate to the spec skill with the item's context/next_steps as the task.
Let it draft and save the spec end-to-end (its steps 1–4) — including its
own internal escalation to grill-me if a field's design is genuinely open;
spec's step 3 owns that handoff and the resume-after entirely, including
its own suspend-and-return discipline for that inner delegation. There is
nothing to orchestrate here. Decline spec's own step 4 generation offer —
step 7 below owns the handoff decision.

**Delegating into spec is a suspend-and-return, not a fire-and-forget
reference** — agy has no discrete "Skill" tool call; the model activates a
referenced skill by reading and following its SKILL.md body directly using
normal tool access, which means a long sub-conversation inside spec (and,
inside that, potentially grill-me) can push this procedure's own state out
of effective attention. Before delegating:
1. Print a literal checkpoint marker: `[CHECKPOINT: suspending backlog-item
   at step 5 for the spec skill; resume at step 6 when it finishes]`.
2. Persist the same return pointer somewhere that outlives the chat
   transcript — this harness and agy both auto-compress context under
   length pressure, so the marker alone isn't enough: `dev_status.py
   update <slug> '{"next_steps": "Resume backlog-item at step 6 after the
   spec skill finishes - <original next_steps preserved/appended>"}'`.
3. Run spec's protocol to actual completion, including any inner grill-me
   delegation and spec's own end-of-session steps.
4. On return, read `~/.gemini/antigravity-cli/skills/backlog-item/SKILL.md`'s
   own step 6 text by its literal absolute path before acting — don't rely
   on recalling it from earlier in the conversation.

Once spec records its artifact path, add it to the item's related_files if
missing (the shared instructions file's "Plans and deliverables get a path
on record"). If spec delegated into grill-me along the way, that session's
`plan_path` is already cited from the spec's Context field — don't also
record it as a second, competing artifact.

### Architecture capture (new modules only)

If the artifact's related_files point into a module with no
`docs/architecture/{module-slug}.md` yet in the target repo — a genuinely
new module, not routine work in an existing one; judgment call, ask if
unclear — run one more pass before continuing: draft a 300–600 word doc
covering the module's boundary/responsibility (one paragraph), key
interfaces it exposes or consumes, explicit non-goals, and known
unknowns/deferred decisions. Save it to `docs/architecture/{module-slug}.md`
in the target repo (project documentation, not `~/.claude/data/grill/`) and
reference it from the spec/plan. A later item touching the same module cites
the existing doc instead of repeating this pass — check for it first.

### Gate classification

Once the spec/plan is recorded (and after architecture capture, if it ran),
classify its concrete implementation steps: **mechanical** (a rote,
unambiguous transformation — no interpretation needed) or **judgment**
(interprets a requirement, makes a design choice, or has ambiguous
acceptance criteria). If any step is judgment, set the item's gate before
continuing:

```bash
python3 ~/.claude/scripts/dev_status.py gate-set <slug|N> '{"required": true, "criteria": ["<short imperative criterion per judgment step>", "..."]}'
```

If every step is mechanical, leave the gate unset (inert by default) —
don't call `gate-set` for a step breakdown with no judgment calls in it.

## 6. Critique
Offer the second-opinion skill against the resulting plan or spec file,
using the same suspend-and-return framing as step 5 (checkpoint marker,
persisted return pointer, absolute-path re-read on return). Recommended:
yes — critique the plan before committing to an executor.

## 7. Handoff
Decide who implements the plan — ask if it isn't already obvious from the
conversation, in plain conversational text with a stated recommendation (agy
has no structured multi-choice widget — state the options, recommend one,
then stop and wait for an actual reply, don't assume the recommended option
was accepted):

- **Same session, now.** Trivial/small item → go to step 8 immediately.
- **Cheaper agy session.** Run `grill.py mark-pending-execution
  --backlog-slug <slug>` (the plan's session, with this item's slug — not
  its own resolved-topic slug), then tell the user to start a fresh agy
  session and type `/backlog-item <slug|N>` themselves. agy has no
  `SessionStart`-equivalent hook (confirmed: `hooks.md` lists only
  `PreToolUse`/`PostToolUse`/`PreInvocation`/`PostInvocation`/`Stop`) to
  auto-surface the marked plan, so the typed command IS the resume path —
  step 1 sees the plan in related_files and skips to step 8.
- **A cheaper agy model, same machine.** Personal projects only, never at
  work. Ask the user to name the specific model id to run (don't parse `agy
  models` stdout and guess which entry is "the cheap one" — that's brittle
  to catalog/format drift). State explicitly, before offering this option,
  that a flash-tier model doing unsupervised TDD (implement, run tests,
  debug, iterate) is a materially weaker executor than the model running
  this session — step 9's review below is not optional for this branch,
  it's the actual safety net. If chosen: this is a blocking subprocess call
  from this session's own Bash access (the same shape as opencode's `run -m
  <model>` handoff on other harnesses), not an out-of-band handoff like the
  cheaper-session option above — say so, so the user knows this session's
  own context/tokens pay for it. Redirect the child's output to a file
  rather than letting the full streaming transcript land in this session's
  context: `agy -p --model <id> "Implement <plan path> exactly as written —
  TDD, run the full suite, then STOP without committing and report the
  diff." > /tmp/<slug>-handoff.log 2>&1`, then read back only the final
  summary/diff from the log. This branch never gets the commit gate. Once
  it reports back, review the diff yourself — that resumes at step 9.

For a work-related item, only the first two options are on the table —
don't offer the cheaper-model branch at all.

## 8. Red, green
TDD in the worktree: a failing test that proves the gap the plan names,
then the minimal implementation.

## 9. Verify
Run the full suite (and lint, if present) in the worktree and show the
output — "should work" is not verification (the shared instructions file).

## 10. Gate: commit
Show the full diff. Ask for explicit commit approval, then stop and yield
the turn. Do not run `git commit` under any circumstances until the user's
next message contains an explicit yes — stating the question is not the
same as getting an answer. No exceptions for being mid-pipeline, and no
exception for code an external executor wrote (the shared instructions
file).

## 11. Gate: commit-then-land
On approval, commit (conventional format) — this gate is never bundled with
what follows. Personal project (this repo, a personal side project — never
a `work-`-prefixed item or a work repo): offer the follow-on sequence as one
bundled question (the shared instructions file's Git section) — "merge to
main, push, and clean up the worktree?" — then merge locally, push, `git
worktree remove`, `git branch -d` on that single approval. Work-related or
ambiguous: ask separately for merge and for push — never bundle.

## 12. Close
`dev_status.py review <slug|N>` then `approve <slug|N>` — never a bare
`done` on an in-review item. If `approve` refuses citing an unmet gate,
actually check each criterion from `show <slug|N>` against the diff — don't
pass it reflexively — then `dev_status.py gate-pass <slug|N>` and retry
`approve`. Display the full dashboard stdout these print; don't just
narrate a one-line confirmation.
