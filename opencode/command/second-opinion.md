---
description: "Send a plan to a non-Claude model for adversarial critique, then iterate — revise, re-send, repeat — until the critique stops surfacing anything new or a round cap is hit. Use when the user wants a second opinion, an outside critique, or to stress-test a plan against a different model."
---

All backend I/O goes through `python3 ~/.claude/scripts/second_opinion.py` —
never shell out to `agy`/`opencode`/`copilot` directly. It is single-round: one
call, one critique. The multi-round loop and plan revision are your job, not
the script's.

```
second_opinion.py detect                        # which backends are present (JSON)
second_opinion.py review <plan-file-or-text>     # one critique from the
                                                  # priority-selected backend
```

`--model-index N` (optional) is a 0-based index into a per-machine model
pool (`SECOND_OPINION_AGY_MODEL_POOL` / `_OPENCODE_MODEL_POOL` /
`_COPILOT_MODEL_POOL`, set by the user). All three backends share the same
indexed-pool contract. An explicit index selects the pool entry for that
call even when a single-model override is also set; without `--model-index`
the single override (or backend default) applies. An explicit index is a
hard error if the selected backend's pool is unset/empty or the index is out
of range — it no longer silently falls back. Automatic selection tries
backends in fixed priority `[agy, opencode, copilot]` and stops on the first
candidate with a pool config error, so name a pool-configured backend
explicitly with `--backend <backend>` when the first priority candidate
lacks a pool.

## Resolving the target plan

If `$ARGUMENTS` is non-empty, treat it as an explicit file path or inline plan
text. Otherwise infer it: prefer `grill.py show`'s most recent session
`plan_path` if one exists, otherwise the plan or proposal visible in the
current conversation. If neither exists, ask the user what to review.

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

Round-by-round narration inline in the plan while the loop is running —
"Rejected feedback" notes, "changed in round N" framing, per-round
"critique point X" cross-references — is legitimate working scratch: the
reviewer is called statelessly each round, so without some record it will
just repeat what you already addressed. But before the plan is shown as
final or saved to disk (converged or capped), do one cleanup pass: move all
of it out of the plan into a separate critique-notes file, written to
`<current_plan without its extension>-critique-notes.md` (e.g.
`~/.claude/data/grill/<topic-slug>-plan-critique-notes.md`) — a
round-by-round record of what was raised each round, what changed in
response, and the rejected-feedback rationale. Then rewrite each affected
step in the plan itself to state the final decision plainly, as if it had
been correct from the start, and drop any trailing per-round changelog
section. Downstream tooling (`dev_status.py` `related_files`, a future
executor session, `grill.py plan_path`) reads the plan file as *the* plan —
the critique history is a companion artifact, not inline noise in it.

## On convergence

Show the final revised plan and the summary, and save both files: the
cleaned-up plan and the critique-notes file described above. If the plan
came from an existing file (e.g. a `grill.py` `plan_path`), confirm before
overwriting it — `question` tool: `Yes, overwrite (recommended)` / `No,
leave as-is`. Never silently rewrite a file. The critique-notes file is new
each run, so it doesn't need the same overwrite confirmation.

## On cap-out without convergence

State plainly, distinct from a converged finish:

```
Stopped after 3 rounds — not converged.
Unresolved: <specific remaining disagreement>.
```

Then `question` tool: `Keep the current approach (recommended)` /
`Use the reviewer's suggestion` / `Let me decide manually`. Never silently
pick a side when the round cap is hit mid-disagreement.

## No backend available

`second_opinion.py review` exits nonzero with a clear message when none of
`agy`, `opencode`, or `copilot` is on `PATH`, or when the available
backend(s) fail (e.g. the `adversary` agent errors out — check with
`opencode run --agent adversary --auto --format json <text> 2>&1` if that
happens). Relay that message and stop — don't retry or fall back to
critiquing the plan yourself.
