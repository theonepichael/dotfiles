---
name: make-skill
description: "Author or revise a Copilot CLI skill using a trigger/structure/steering/pruning rubric. Use when the user wants to create a new skill, improve or simplify an existing one, or complains a skill isn't triggering or isn't being followed."
allowed-tools: shell
---
Work through these steps in order. Don't draft the skill body before step 1 is decided.

## 1. Trigger — decide how it starts

Copilot skills are always description-matched, not typed-slash — there is no
user-invoked/model-invoked split to choose between like Claude Code has. The
frontmatter `description` IS the entire trigger surface. Write it as: what the
skill does + "use when" + the literal phrases the user actually says (steal
them from real transcripts). A vague description means the skill silently
never fires; that failure mode is invisible until you go looking for it, so
err toward over-specifying trigger phrases.

## 2. Structure — steps, then reference

- The body is a **procedure**: numbered/ordered steps in imperative voice, addressed to the agent.
- Supporting material (schemas, long examples, lookup tables) does NOT go in the body. Put it in `~/dotfiles/copilot/skills/<skill>/ref/<topic>.md` and point to it from the step that needs it. Reference files need their own symlink lines (step 5).
- Keep the body under ~50 lines. If it branches into genuinely different workflows, split into separate skills instead of one branching monster — smaller skills also hide the end goal, which stops the agent from rushing past planning/questioning steps. (Splitting isn't the only way: grill-me gets the same effect inside one skill by forbidding plan-writing until every question is decided. Don't split a skill that demonstrably works.)

## 3. Steering — make it stick

- Use dense, industry-standard terms that carry priors ("vertical slice", "copy-once", "idempotent") instead of paragraphs of description. Pick words that *name the behavior you want*: the agent repeats them in its thinking and output, and every repetition re-anchors it on that behavior — that echo loop is what step 4 listens for.
- State the failure mode you're preventing as a direct instruction ("Pass the integer directly — do not look up the slug").
- Copilot has no `AskUserQuestion`-style structured prompt. Any step that needs a multi-choice decision from the user must be written as plain conversational text: state the question, give a recommendation, wait for a plain-text reply. Don't design a skill step around a UI widget Copilot doesn't have.

## 4. Verify in a fresh session

Probe with a headless invocation of the Copilot CLI for a real trigger
phrase — check `copilot --help` on the target machine for the exact
non-interactive/print-mode flag, since it isn't the same binary as Claude
Code and shouldn't be assumed to share flag names. Check the output (and
reasoning, if visible) repeats your leading words back. If the agent skips a
step, that step needs splitting or stronger steering — not more prose.

## 5. Plumbing (house convention)

1. File lives at `~/dotfiles/copilot/skills/<name>/SKILL.md` (same for any ref files, under `~/dotfiles/copilot/skills/<name>/ref/`).
2. Add a `symlink copilot/skills/<name>/SKILL.md ~/.copilot/skills/<name>/SKILL.md` line in install.sh next to the existing ones.
3. Create the live symlink now: `ln -s ~/dotfiles/copilot/skills/<name>/SKILL.md ~/.copilot/skills/<name>/SKILL.md`.
4. Conventional commit, scope `copilot`: `feat` for a new skill, `refactor`/`docs` for revisions.

## 6. Pruning (every revision, not just creation)

- **Single source of truth**: never restate what `copilot-instructions.md` or another skill already says — point to it.
- **Remove sediment**: delete references to scripts, paths, or behaviors that no longer exist.
- **Deletion test**: if a paragraph looks decorative, remove it and rerun the step-4 probe. Identical behavior → it was a no-op; keep it deleted.
