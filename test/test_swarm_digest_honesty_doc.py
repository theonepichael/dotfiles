"""Guards the swarm digest's two-voice rule in `pi/prompts/backlog-item.md`.

The orchestrator has no independent view of what a worker did. It sees
spawn/blocked/settle events and the worker's own narration, so a worker that
misdescribes its diff propagates that description straight into the
end-of-run digest -- the ONLY account most items get, since nobody reads two
workers' transcripts.

Twice on 2026-09-03. First: the digest said a worker had reverted a stray
one-line change before commit. It had not; the hunk is in 897f00b and on
main. Second, and worse: the orchestrator relayed "the 3 full-suite failures
are in toggle-check.test.ts and reproduce on untouched main (verified
myself)". There were no such failures -- toggle-check gives 7 pass 0 fail,
the full suite 432 pass 0 fail on main. The phrase "verified myself" is what
makes the second worse: repeating a worker's claim is a flaw a careful
reader discounts, but vouching for it removes the suspicion that would catch
it.

That near-miss had a cost beyond its own run. `meta-pi-trust-session-noop`'s
premise is that toggle-check.test.ts passes GREEN while the feature it
covers is a proven no-op -- the false green IS the bug. Had "toggle-check is
failing on main" reached the digest, the next person to pick that item up
would have concluded its premise was stale when it is exactly right.

What the orchestrator did RIGHT in the same breath must not be discouraged:
it went looking for the toggle-check result unprompted, because it
recognised the claim mattered to a later item in its queue. The instinct was
correct; asserting a result it had not obtained is the defect. So these
assertions target the register a claim is stated in, never the presence of
claims -- the goal is not a quieter digest.

The checks key on the rule being stated, not on exact prose, so rewording
does not false-positive.
"""

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def _swarm_section() -> str:
    text = (REPO_ROOT / "pi" / "prompts" / "backlog-item.md").read_text(encoding="utf-8")
    match = re.search(
        r"^## `--swarm\[=N\]` mode.*?(?=\n^## |\Z)", text, re.DOTALL | re.MULTILINE
    )
    assert match is not None, "backlog-item.md lost its `--swarm[=N]` mode section"
    return match.group(0)


def _digest_block() -> str:
    section = _swarm_section()
    match = re.search(
        r"^\d+\. \*\*End of run\*\*.*?(?=\n^\d+\. |\n^## |\Z)",
        section,
        re.DOTALL | re.MULTILINE,
    )
    assert match is not None, "the --swarm section lost its End of run step"
    return match.group(0)


def test_digest_separates_observed_from_claimed() -> None:
    block = _digest_block().lower()
    assert "observed" in block, (
        "the digest step does not name a tool-observed register, so a reader "
        "cannot tell what the tools saw from what a worker said about itself"
    )
    assert "claim" in block or "reported" in block, (
        "the digest step does not name the worker-claim register"
    )


def test_digest_forbids_vouching_for_an_unrun_check() -> None:
    block = _digest_block().lower()
    assert "verified" in block, (
        'the digest step never addresses the word "verified" -- the failure '
        "was an orchestrator attaching \"verified myself\" to a claim it had "
        "not checked, which removes the suspicion that would catch it"
    )


def test_digest_prefers_naming_the_sha_over_paraphrasing_a_diff() -> None:
    block = _digest_block().lower()
    # \b-anchored: a bare "sha" substring also matches "shape", which the
    # step already contained ("same shape as --auto's") and which would have
    # passed this against the pre-fix document.
    assert re.search(r"\bsha\b", block), (
        "the digest step does not tell the orchestrator to name the commit "
        "sha rather than paraphrase a worker's account of its own diff -- "
        "git show against a sha is ground truth and is cheap"
    )


def test_the_rule_stays_cheap() -> None:
    # The orchestrator runs on a small model with a filling context by end of
    # run. A rule costing a full diff read per item would not survive a
    # ten-item run, so the doc must say the check is bounded.
    block = _digest_block().lower()
    assert "cheap" in block or "do not read" in block or "without reading" in block, (
        "nothing bounds the cost of the verification rule, so it will be "
        "dropped on a long run"
    )


def test_the_digest_is_not_told_to_go_quiet() -> None:
    block = _digest_block().lower()
    assert "anomal" in block or "surface" in block, (
        "the rule reads as suppression rather than as a register change; the "
        "same digest that stated a falsehood also surfaced a real anomaly "
        "nobody else had, and that must not be discouraged"
    )
