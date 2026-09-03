"""Guards the one-ask rule for a worker's end-of-run capture digest.

On 2026-09-03 a swarm worker finished its item -- committed, merged, pushed,
cleaned up -- and then began walking its proactive-capture digest one
question at a time, announcing "question 1/3" with two more to come.

In a normal `--auto` session that is cheap: the worker asks the human
directly. In swarm mode every question is a five-hop round trip -- the
worker raises it, `swarm_poll` reports it blocked, the orchestrator relays
it, a human answers, `swarm_resolve_blocked` drives the picker back. Three
housekeeping offers became three of those, after the item's actual work was
already finished and landed, while the worker held a concurrency slot open
throughout.

Two separable defects, and this module guards both.

The first is not swarm-specific. `--auto`'s own End of run step said to walk
the digest "in one pass" and then "confirming or declining each in turn" --
which reads as serial, so the worker's behaviour was arguably compliant with
the second half of a sentence that contradicts its first half. The
ambiguity is the bug.

The second is the swarm section never saying whether a worker should walk
its digest at all. Decided 2026-09-03: it should, but in exactly ONE ask
covering every queued offer. That cuts the storm from N round trips to one
per worker without needing a payload channel -- a finished event carries no
payload today, and the worker's record is deleted the moment it finishes, so
carrying offers to the orchestrator would need somewhere durable to put
them. Folding every worker's offers into the orchestrator's single
end-of-run walk remains the fuller fix and is deliberately not done here.
"""

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def _doc() -> str:
    return (REPO_ROOT / "pi" / "prompts" / "backlog-item.md").read_text(encoding="utf-8")


def _auto_end_of_run() -> str:
    match = re.search(r"\*\*End of run\.\*\*.*?(?=\n---|\n^## )", _doc(), re.DOTALL | re.MULTILINE)
    assert match is not None, "--auto lost its End of run step"
    return match.group(0)


def _swarm_section() -> str:
    match = re.search(
        r"^## `--swarm\[=N\]` mode.*?(?=\n^## |\Z)", _doc(), re.DOTALL | re.MULTILINE
    )
    assert match is not None, "backlog-item.md lost its `--swarm[=N]` mode section"
    return match.group(0)


def _worker_digest_block() -> str:
    """The --swarm subsection covering a worker's own capture digest.

    Scoped to its own subsection rather than the whole --swarm section: that
    section already contains "capture", "digest", "slot" and "round trip" for
    unrelated reasons (the orchestrator's own digest step, the pane-slot
    accounting, the relay loop), so unscoped searches passed against the
    pre-fix document and proved nothing.
    """
    section = _swarm_section()
    match = re.search(
        r"^### [^\n]*(capture digest|worker.s own digest)[^\n]*\n.*?(?=\n^### |\n^## |\Z)",
        section,
        re.DOTALL | re.MULTILINE | re.IGNORECASE,
    )
    assert match is not None, (
        "the --swarm section has no subsection covering a worker's own "
        "capture digest -- the gap that let one worker cost three relay "
        "round trips after its work was already landed"
    )
    return match.group(0)


def test_auto_digest_walk_is_not_self_contradictory() -> None:
    block = _auto_end_of_run()
    assert "one pass" in block, "--auto's End of run lost its one-pass instruction"
    # "each in turn" is what made a serial walk defensible against a sentence
    # whose first half says one pass. It has to go, or be qualified.
    assert "each in turn" not in block, (
        '--auto still says "each in turn" alongside "in one pass" -- the two '
        "read as opposite instructions, and a worker followed the serial half"
    )


def test_auto_digest_walk_says_one_ask_not_one_per_offer() -> None:
    block = _auto_end_of_run().lower()
    # A bare "single" also matches "the single item completes", already in
    # this step -- so the phrase is required, not the word.
    assert "single ask" in block or "one ask" in block, (
        "--auto's End of run does not say the walk is a single ask, so a "
        "serial reading survives"
    )


def test_swarm_section_states_the_worker_digest_rule() -> None:
    block = _worker_digest_block().lower()
    assert "PI_SWARM_CAPTURE_FILE" in _worker_digest_block(), (
        "the worker-digest subsection does not name the env var a worker "
        "writes its offers to, so the contract is unstated"
    )
    assert "does not ask" in block or "not ask" in block, (
        "the subsection does not say a worker refrains from asking at all"
    )


def test_swarm_section_names_the_relay_cost_of_asking_twice() -> None:
    block = _worker_digest_block().lower()
    assert "round trip" in block or "round-trip" in block, (
        "the worker-digest subsection does not say what an extra worker "
        "question actually costs, so the one-ask rule reads as arbitrary style"
    )


def test_swarm_section_notes_the_slot_is_held_while_blocked() -> None:
    block = _worker_digest_block().lower()
    assert "slot" in block, (
        "nothing in the worker-digest subsection says a worker blocked on "
        "housekeeping still holds its concurrency slot -- the reason this is "
        "a throughput bug and not merely chatter"
    )
