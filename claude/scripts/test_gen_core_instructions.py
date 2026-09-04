#!/usr/bin/env python3
"""Tests for gen_core_instructions.py.

Composition-structure tests, not just a `--check` pass/fail: assert the
generated `claude/global-instructions.md` actually contains every
`CORE_INSTRUCTIONS.md` section header and the personal-overlay content
under its own heading, and that no personal-only string leaks into what
`CORE_INSTRUCTIONS.md` itself renders without the overlay -- a `--check`
pass alone wouldn't catch a generator that silently drops or duplicates
content while still producing *some* stable, checkable output.
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import gen_core_instructions as gci

REPO_ROOT = Path(__file__).resolve().parents[2]

# Section headers CORE_INSTRUCTIONS.md must carry into the composed output.
CORE_SECTION_HEADERS = (
    "## Planning Gate",
    "### Judgment calls",
    "### Root-causing recurring problems",
    "### Backlog",
    "## Git",
    "## Test Hygiene",
    "## Shell Command Safety",
    "## Scripts",
    "## Python",
)

# Personal-only strings that must never appear in CORE_INSTRUCTIONS.md on
# its own (only in the composed output, via the overlay).
PERSONAL_ONLY_STRINGS = (
    "iron-lb-",
    "ajhp-",
    "watchcommit",
    "dev_status_sync.py",
)


class CompositionTests(unittest.TestCase):
    def test_composed_output_contains_every_core_section(self) -> None:
        composed = gci.compose(REPO_ROOT)
        for header in CORE_SECTION_HEADERS:
            self.assertIn(header, composed, f"missing core section: {header}")

    def test_composed_output_contains_the_overlay_under_its_own_heading(self) -> None:
        composed = gci.compose(REPO_ROOT)
        self.assertIn("## Personal Policy", composed)
        self.assertIn("iron-lb-", composed)
        self.assertIn("watchcommit", composed)

    def test_core_instructions_alone_has_no_personal_only_string(self) -> None:
        core_text = (REPO_ROOT / "claude" / "CORE_INSTRUCTIONS.md").read_text(
            encoding="utf-8"
        )
        for needle in PERSONAL_ONLY_STRINGS:
            self.assertNotIn(
                needle,
                core_text,
                f"CORE_INSTRUCTIONS.md leaks personal-only content: {needle!r}",
            )

    def test_composed_output_matches_committed_global_instructions(self) -> None:
        composed = gci.compose(REPO_ROOT)
        on_disk = (REPO_ROOT / "claude" / "global-instructions.md").read_text(
            encoding="utf-8"
        )
        self.assertEqual(composed, on_disk)


if __name__ == "__main__":
    unittest.main()
