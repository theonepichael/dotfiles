---
name: second-opinion
description: Send a plan to a non-Claude model for adversarial critique, then iterate — revise, re-send, repeat — until the critique stops surfacing anything new or a round cap is hit. Use when the user wants a second opinion, an outside critique, or to stress-test a plan against a different model.
argument-hint: [plan file or text]
allowed-tools: [Read, Write, AskUserQuestion, "Bash(python3 ~/.claude/scripts/second_opinion.py:*)", "Bash(python3 ~/.claude/scripts/grill.py:*)"]
---

All backend I/O goes through `python3 ~/.claude/scripts/second_opinion.py` —
never shell out to `agy`/`opencode` directly. It is single-round: one call, one
critique. The multi-round loop and plan revision are your job, not the script's.

```
second_opinion.py detect                        # which backends are present (JSON)
second_opinion.py review <plan-file-or-text>     # one critique from the
                                                  # priority-selected backend
```

## Resolving the target plan

If `$ARGUMENTS` is non-empty, treat it as an explicit file path or inline plan
text. Otherwise infer it: prefer `grill.py show`'s most recent session
`plan_path` if one exists, otherwise the plan or proposal visible in the
current conversation. If neither exists, ask the user what to review.

Whenever the resolved plan has no backing file yet — inline `$ARGUMENTS` text,
or the "visible in the current conversation" fallback — write it to
`~/.claude/data/grill/<topic-slug>-plan.md` first, the same central location
`grill.py` plans use (never the per-session scratchpad dir — it can be gone
by the time anything references this path later, e.g. a `dev_status.py`
`related_files` entry read back in a future session). Use that path as
`current_plan` for the rest of this skill. Never pass inline plan text to
`second_opinion.py review`, and never embed full plan text into a prose field
meant for short descriptions (a `context`/`next_steps`/note field on a
`dev_status.py` item, etc.) — reference the file path there instead.

## Iteration loop

```
round = 1
current_plan = <resolved input>
prior_critique = None

loop:
    critique = second_opinion.py review <current_plan>   # one call
    show "Round N critique" + critique in chat

    if round > 1 and critique raises nothing substantively
       new vs prior_critique:
        stop — CONVERGED

    if round == 3:
        stop — CAPPED, converged_or_not = (no new issues this round)

    current_plan = revise current_plan yourself, addressing
                   valid points from the critique. For any point you
                   reject, append a brief "Rejected feedback" note to
                   current_plan stating what was suggested and why —
                   the reviewer is called statelessly each round, so
                   without this it will just repeat the same rejected
                   suggestion instead of engaging with your reasoning.
    prior_critique = critique
    round += 1

show the final revised plan + a round-by-round summary of what changed and why
```

"Raises nothing substantively new" is your judgment call, made by reading both
critiques side by side — not delegated to the reviewer model or to
deterministic code. A repeated suggestion you already rejected (and noted as
rejected) does not count as new.

Strip any "Rejected feedback" notes from the plan before showing or saving
the final version — they're working scratch for the reviewer, not part of
the plan itself.

## On convergence

Show the final revised plan and the summary. If the input came from an
existing file (e.g. a `grill.py` `plan_path`), confirm before overwriting it —
`AskUserQuestion`: `Yes, overwrite (recommended)` / `No, leave as-is`. Never
silently rewrite a file.

## On cap-out without convergence

State plainly, distinct from a converged finish:

```
Stopped after 3 rounds — not converged.
Unresolved: <specific remaining disagreement>.
```

Then `AskUserQuestion`: `Keep Claude's approach (recommended)` /
`Use the reviewer's suggestion` / `Let me decide manually`. Never silently
pick a side when the round cap is hit mid-disagreement.

## Recording it in the backlog

Once the final plan is settled (converged or capped out), apply CLAUDE.md's
"Plans and deliverables get a path on record" backlog policy: the plan's
file path ends up in a tracking item's `related_files`, whether that means
creating the item (offer first) or updating an existing one.

## No backend available

`second_opinion.py review` exits nonzero with a clear message when neither
`agy` nor `opencode` is on `PATH`, or when the available backend(s) fail
(e.g. opencode's `adversary` agent errors out — check with
`opencode run --agent adversary --auto --format json <text> 2>&1` if that
happens). Relay that message and stop — don't retry or fall back to
critiquing the plan yourself.
