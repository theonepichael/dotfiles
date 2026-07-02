---
name: grill-me
description: Interview the user relentlessly about a plan or design until reaching shared understanding, resolving each branch of the decision tree. Use when user wants to stress-test a plan, get grilled on their design, or mentions "grill me".
argument-hint: [--verify] [topic or plan to grill on]
allowed-tools: [Read, Glob, Grep, Write, AskUserQuestion, "Bash(python3 ~/.claude/scripts/grill.py:*)"]
---

All session state lives in JSON under `~/.claude/data/grill/`, mutated only through
`python3 ~/.claude/scripts/grill.py` — never write or edit session files by hand.
The JSON is the capture mechanism: every decision point is recorded the moment it's
identified, so an unfinished session resumes cleanly. The plan document is a separate
markdown artifact **you author yourself**, informed by the recorded decision points.

```
grill.py new '{"topic": "..."}'                          # start session, prints slug
grill.py ask '{"id", "question", ["reasoning"]}'         # register an open decision point
grill.py decide '{"id", "decision", ["question"], ["source"]}'  # resolve one
grill.py revise <id> '{"decision": "..."}'               # amend (resets its verdict)
grill.py verdict <id> '{"result", "evidence"}'           # record verification result
grill.py plan <path>                                     # record plan artifact location
grill.py next / render / show / list                     # resume point / status / raw JSON
```

All commands default to the most recent session; pass `--session <slug>` otherwise.
Mutations echo what they did on stderr — check it matches your intent.

## Default mode

If $ARGUMENTS is empty (and does not contain `--verify`), grill the plan under discussion when the conversation makes it obvious; otherwise ask the user what to grill before proceeding.

**Pre-step: orient before asking.**
Check what codebase context is already in scope from prior exploration. Do a targeted read/grep only for files directly relevant to the topic that haven't been read yet. Don't re-crawl what's already known. Never ask a question the codebase already answers.

Then check `grill.py list` for an existing session matching the topic. If one matches, resume it — `grill.py next` picks up at the first open question; tell the user you're resuming. Only `grill.py new` when nothing matches or the user says "start over".

**Q&A loop:**

1. Identify the top-level decisions and unknowns. Register each one immediately with `ask` (id + question) so nothing is lost if the session is cut short. Order by dependency — resolve blockers before dependent decisions. New decision points surfaced by later answers get `ask`ed as they appear.

2. Ask one question at a time. When the plausible answers are enumerable (2–4 real options), use AskUserQuestion with your recommendation as the first option, labeled "(Recommended)". When the question is genuinely open-ended, ask in plain text:
   - State the question directly.
   - Give your recommended answer with brief reasoning.
   - Wait for the user's response.

3. When the user answers:
   - If the answer is consistent and resolves the question, record it — `decide` with source `user` — and move to the next.
   - If the answer is vague, push back and ask them to be specific.
   - If the answer introduces inconsistencies or new risks, name them and keep drilling.
   - If the user defers ("whatever you think", "you decide"), record your recommendation via `decide` with source `defaulted`.

4. A branch is resolved when the answer generates no new questions. Keep drilling until every open question is decided. Two distinct early exits — the user's words pick which:
   - **Pause** ("let's stop here", "I need to step away", "we'll come back to this") — stop without deciding anything. Open questions stay open for a later resume; no plan is written. Offer (never auto-add) a backlog item via `dev_status.py add`: session slug in `context`, `next_steps` pointing at `grill.py next`.
   - **Wrap up** ("wrap it up", "just finish it", "that's enough") — `decide` each remaining open question with your best-guess answer and source `assumed`, then conclude normally.

**End of session** (fully decided or wrapped up — not on pause):

1. Author the plan as a markdown document — a real plan someone could execute, not a decision log. The recorded decision points (`grill.py show`) inform it. Plans always live centrally at `~/.claude/data/grill/<slug>-plan.md` — never in project repos; this is personal tooling, not team-facing docs.
2. Record it: `grill.py plan <path>`.
3. Show the user the plan and the `grill.py render` output (decision table, any open questions, verification state).

---

## `--verify` mode

Run this after a default-mode session. It does not ask new questions — it tests each decision against reality.

1. Load state with `grill.py render` (most recent session by default, `--session` otherwise). Do not ask the user to re-state decisions. Decisions still open must be decided in a default-mode pass first.

2. For each decided row — `defaulted` and `assumed` first, since no human confirmed those — design and run a non-destructive experiment to test whether the decision holds: run relevant tests, write and execute a scoped throwaway script, invoke existing CLI/dev tools. Observe actual behavior. (Bash beyond grill.py isn't pre-approved here; permission prompts for experiments are expected.)

3. Record each result with `grill.py verdict <id> '{"result": ..., "evidence": ...}'`:
   - **VERIFIED** — experiment confirms the decision holds. Evidence: what was run, what was observed.
   - **DISPUTED** — experiment contradicts it. Evidence: exactly what was found and why it conflicts.
   - **UNVERIFIABLE** — no experiment can test this yet (e.g. code doesn't exist). State why.

4. For any DISPUTED decision: re-grill the user, record the new answer with `grill.py revise <id>` (this resets the verdict), then re-run the experiment and record a fresh verdict. Repeat until nothing is DISPUTED.

5. If any decisions changed, update the plan artifact to match, then show the final `grill.py render`.
