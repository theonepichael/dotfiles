#!/usr/bin/env python3
"""Keep the second-opinion guidance copies in sync.

The second-opinion contract lives in five hand-maintained copies
(claude/commands/second-opinion.md plus four sibling skill/command forms).
They have drifted before and each drift caused a real bug. This test asserts
every copy still carries the required contract markers, so a future edit to
one copy that forgets the model-pool rotation / no-pool safety net fails
loudly here. The single-source generator (meta-second-opinion-single-source)
is meant to replace these copies eventually; until then this holds the line.
"""

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent

# Every copy must carry all of these markers. Keep in sync with
# claude/commands/second-opinion.md (the canonical copy).
REQUIRED_MARKERS = (
    # usage block exposes the model-index flag
    "[--model-index N]",
    # the loop passes the rotating index every round
    "--model-index <round - 1>",
    # the no-pool safety-net description
    "don't assume a pool is configured",
    # the POOL-config-error retry inside the loop
    "retry, no index",
    # the focus-hints mechanism (callers + risk areas)
    "--focus-file",
    # the caller-check rule for tooling-script changes
    "Caller check for tooling changes",
    # the backlog-recording step
    "Recording it in the backlog",
    # the plan-file path convention callers (backlog-item.md, grill-me.md,
    # spec.md) depend on
    "~/.claude/data/grill/<topic-slug>-plan.md",
    # the critique-notes companion-file path convention those same callers
    # depend on
    "-critique-notes.md",
)

COPIES = (
    "claude/commands/second-opinion.md",
    "opencode/command/second-opinion.md",
    "opencode/skills/second-opinion/SKILL.md",
    "copilot/skills/second-opinion/SKILL.md",
    "agy/skills/second-opinion/SKILL.md",
    "pi/prompts/second-opinion.md",
)


@pytest.mark.parametrize("rel_path", COPIES)
def test_second_opinion_copy_carries_contract(rel_path: str) -> None:
    path = REPO_ROOT / rel_path
    assert path.exists(), f"missing second-opinion copy: {rel_path}"
    text = path.read_text(encoding="utf-8")
    missing = [m for m in REQUIRED_MARKERS if m not in text]
    assert not missing, (
        f"{rel_path} is missing second-opinion contract markers: {missing}"
    )
