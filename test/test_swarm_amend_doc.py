"""Guards the amend contract in `pi/prompts/backlog-item.md`.

On 2026-09-03 a worker was several minutes into an item whose stored premise
was wrong -- it was reasoning toward a fix that would have broken the user's
work machine. The correction went through three raw `herdr agent prompt`
calls. It worked, but only because a human was watching: swarm-tool never saw
it, the run state had no record an item had been amended, the orchestrator
went on polling a worker whose instructions had been rewritten underneath it,
and nothing acknowledged that the worker had read any of it.

`swarm_amend` replaces that path. These assertions hold the doc to the two
properties that make it safe rather than merely convenient -- that the
correction lives in the store and not in the message, and that the
turn-boundary race is stated as unsolved rather than papered over.
"""

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def _amend_block() -> str:
    text = (REPO_ROOT / "pi" / "prompts" / "backlog-item.md").read_text(encoding="utf-8")
    match = re.search(
        r"^### [^\n]*[Cc]orrecting a running worker[^\n]*\n.*?(?=\n^### |\n^## |\Z)",
        text,
        re.DOTALL | re.MULTILINE,
    )
    assert match is not None, "backlog-item.md has no amend subsection"
    return match.group(0)


def test_the_item_is_edited_before_the_amend_is_sent() -> None:
    block = _amend_block().lower()
    assert "edit the item first" in block or "edit the item" in block, (
        "the doc does not say to correct the stored item before amending, so "
        "a caller could send an amend with nothing changed to re-read"
    )


def test_the_channel_carries_no_correction_text() -> None:
    block = _amend_block().lower()
    assert "no correction text" in block or "carries no correction" in block, (
        "the doc does not state that the amend carries no correction text -- "
        "the property that keeps the backlog store the single source of truth"
    )


def test_raw_herdr_prompt_is_ruled_out() -> None:
    block = _amend_block().lower()
    assert "herdr agent prompt" in block, (
        "the doc never mentions the raw herdr prompt path this replaces, so "
        "nothing tells a reader not to fall back to it"
    )


def test_the_parked_worker_limit_is_stated() -> None:
    block = _amend_block()
    assert "amend_refused" in block and "swarm_resolve_blocked" in block, (
        "the doc does not say an amend cannot reach a worker parked at a "
        "gate, nor what to do instead"
    )


def test_the_turn_boundary_race_is_stated_as_unsolved() -> None:
    block = _amend_block().lower()
    assert "unsolved" in block, (
        "the turn-boundary race is not stated as unsolved. It is not solved: "
        "pi exposes no turn checkpoint over herdr, so an amend can land as a "
        "rewrite of finished work. Papering over that is how a caller comes "
        "to trust it further than it deserves"
    )


def test_the_digest_is_told_to_report_the_amendment() -> None:
    block = _amend_block().lower()
    assert "digest" in block, (
        "nothing tells the orchestrator to report a mid-flight amendment in "
        "the end-of-run digest -- the trace the raw-prompt path never left"
    )
