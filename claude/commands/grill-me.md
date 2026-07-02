---
name: grill-me
description: Interview the user relentlessly about a plan or design until reaching shared understanding, resolving each branch of the decision tree. Use when user wants to stress-test a plan, get grilled on their design, or mentions "grill me".
argument-hint: [--verify] [topic or plan to grill on]
allowed-tools: [Read, Glob, Grep]
---

## Default mode

If $ARGUMENTS is empty (and does not contain `--verify`), ask the user what plan or design to grill before proceeding.

**Pre-step: orient before asking.**
Check what codebase context is already in scope from prior exploration. Do a targeted read/grep only for files directly relevant to the topic that haven't been read yet. Don't re-crawl what's already known. Never ask a question the codebase already answers.

**Q&A loop:**

1. Identify the top-level decisions and unknowns. Order them by dependency — resolve blockers before dependent decisions.

2. Ask one question at a time. For each:
   - State the question directly.
   - Give your recommended answer with brief reasoning.
   - Wait for the user's response.

3. When the user answers:
   - If the answer is consistent and resolves the question, accept it and move to the next.
   - If the answer is vague, push back and ask them to be specific.
   - If the answer introduces inconsistencies or new risks, name them and keep drilling.
   - If the user defers ("whatever you think", "you decide"), record your recommendation as the decision and move on.

4. Keep drilling until every branch is resolved.

**End of session:**

Output the plan, then a decision table:

| Decision | What we decided | Verified |
|----------|-----------------|----------|

Every decision made during the session gets a row. `Verified` is empty — it's filled by a subsequent `--verify` pass.

---

## `--verify` mode

Run this after a default-mode session has produced a decision table. It does not ask new questions — it tests each decision against reality.

**`--verify` requires Bash in addition to Read, Glob, Grep.**

1. Read the decision table from the conversation context. Do not ask the user to re-state decisions.

2. For each row, design and run a non-destructive experiment to test whether the decision holds — run relevant tests, write and execute a scoped throwaway script, invoke existing CLI/dev tools. Observe actual behavior.

3. Assign a verdict:
   - **VERIFIED** — experiment confirms the decision holds.
   - **DISPUTED** — experiment contradicts it. State exactly what was found and why it conflicts.
   - **UNVERIFIABLE** — no experiment can test this yet (e.g. code doesn't exist). State why.

4. For any DISPUTED row: re-grill the user to revise the decision, then re-run the experiment. Do not mark VERIFIED until an experiment confirms the revised claim. Repeat until resolved.

5. Output the updated decision table with the `Verified` column filled.
