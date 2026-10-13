#!/usr/bin/env python3
"""Tests for gen_core_instructions.py.

The shareable core lives in agent-toolkit, not here, so composition is
tested against fixture core/overlay files and the committed output is
checked only for what this repo alone can vouch for: its overlay suffix and
the absence of pre-migration paths in its core portion. No test needs an
agent-toolkit checkout -- CI has none.
"""

import contextlib
import io
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import gen_core_instructions as gci

REPO_ROOT = Path(__file__).resolve().parents[2]
COMMITTED_OUTPUT = REPO_ROOT / "claude" / "global-instructions.md"
COMMITTED_OVERLAY = REPO_ROOT / "claude" / "personal-overlay.md"

# Personal-only strings that must never appear in the shareable core (only
# in the composed output, via the overlay).
PERSONAL_ONLY_STRINGS = (
    "iron-lb-",
    "ajhp-",
    "watchcommit",
    "dev_status_sync.py",
)

# Paths that stopped existing when the toolkit moved to ~/.agent-toolkit/.
# The overlay may still name them deliberately (dotfiles' own sync script,
# the legacy data layout), so only the core portion is checked.
PRE_MIGRATION_PATHS = ("~/.claude/scripts/", "~/.claude/data/")


def _split_committed() -> tuple[str, str]:
    """Return the committed output's (core portion, overlay suffix)."""
    text = COMMITTED_OUTPUT.read_text(encoding="utf-8")
    core, marker, suffix = text.partition(gci.DO_NOT_EDIT_MARKER)
    if not marker:
        raise AssertionError("committed global-instructions.md has no marker")
    return core, suffix


class _FixtureTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="gen-core-test-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.checkout = self.tmp / "agent-toolkit"
        (self.checkout / "agent-scripts").mkdir(parents=True)
        (self.checkout / "install.py").write_text("")
        (self.checkout / "links.toml").write_text("")
        self.core = self.checkout / "claude" / "CORE_INSTRUCTIONS.md"
        self.core.parent.mkdir()
        self.core.write_text("# CLAUDE.md\n\n## Planning Gate\n\nPlan first.\n\n")
        self.repo = self.tmp / "dotfiles"
        self.overlay = self.repo / "claude" / "personal-overlay.md"
        self.overlay.parent.mkdir(parents=True)
        self.overlay.write_text("## Personal Policy\n\nuse iron-lb-\n")
        self.output = self.repo / "claude" / "global-instructions.md"

    def _main(self, *args: str) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            status = gci.main(["--repo-root", str(self.repo), *args])
        return status, out.getvalue(), err.getvalue()


class ComposeTests(_FixtureTestCase):
    def test_core_comes_first_then_marker_then_overlay(self) -> None:
        composed = gci.compose(self.core, self.overlay)
        self.assertEqual(
            composed,
            "# CLAUDE.md\n\n## Planning Gate\n\nPlan first.\n\n"
            f"{gci.DO_NOT_EDIT_MARKER}\n\n## Personal Policy\n\nuse iron-lb-\n",
        )

    def test_core_text_passes_through_untransformed(self) -> None:
        self.core.write_text("check `agent-scripts/` and ~/.agent-toolkit/scripts/\n")
        composed = gci.compose(self.core, self.overlay)
        self.assertTrue(
            composed.startswith(
                "check `agent-scripts/` and ~/.agent-toolkit/scripts/\n"
            )
        )

    def test_marker_names_the_real_sources(self) -> None:
        self.assertIn("agent-toolkit", gci.DO_NOT_EDIT_MARKER)
        self.assertIn("claude/personal-overlay.md", gci.DO_NOT_EDIT_MARKER)


class CliTests(_FixtureTestCase):
    def test_write_then_check_is_fresh(self) -> None:
        status, _, _ = self._main("--agent-toolkit-path", str(self.checkout))
        self.assertEqual(status, 0)
        self.assertEqual(self.output.read_text(), gci.compose(self.core, self.overlay))
        status, _, _ = self._main("--check", "--agent-toolkit-path", str(self.checkout))
        self.assertEqual(status, 0)

    def test_check_reports_stale_without_writing(self) -> None:
        self.output.write_text("stale\n")
        status, _, err = self._main(
            "--check", "--agent-toolkit-path", str(self.checkout)
        )
        self.assertEqual(status, 1)
        self.assertIn("stale", err)
        self.assertEqual(self.output.read_text(), "stale\n")

    def test_core_path_bypasses_the_resolver(self) -> None:
        status, out, _ = self._main("--stdout", "--core-path", str(self.core))
        self.assertEqual(status, 0)
        self.assertEqual(out, gci.compose(self.core, self.overlay))
        self.assertFalse(self.output.exists())

    def test_write_leaves_no_temp_file_behind(self) -> None:
        self._main("--agent-toolkit-path", str(self.checkout))
        leftovers = [
            p.name
            for p in self.output.parent.iterdir()
            if p.name not in ("global-instructions.md", "personal-overlay.md")
        ]
        self.assertEqual(leftovers, [])

    def test_print_agent_toolkit_path(self) -> None:
        status, out, _ = self._main(
            "--print-agent-toolkit-path", "--agent-toolkit-path", str(self.checkout)
        )
        self.assertEqual(status, 0)
        self.assertEqual(out, f"{self.checkout.resolve()}\n")

    def test_unresolvable_checkout_exits_2_with_the_reason(self) -> None:
        missing = self.tmp / "nowhere"
        for extra in ((), ("--check",), ("--print-agent-toolkit-path",)):
            status, out, err = self._main(*extra, "--agent-toolkit-path", str(missing))
            self.assertEqual(status, 2, extra)
            self.assertEqual(out, "", extra)
            self.assertIn(str(missing), err, extra)
        self.assertFalse(self.output.exists())


class UnreadableInputTests(_FixtureTestCase):
    def test_unreadable_core_exits_2_not_a_crash(self) -> None:
        """A crash would exit 1, which reads as merely "stale" to --check callers."""
        self.core.chmod(0)
        self.addCleanup(self.core.chmod, 0o644)
        for extra in ((), ("--check",)):
            status, _, err = self._main(*extra, "--core-path", str(self.core))
            self.assertEqual(status, 2, extra)
            self.assertIn(str(self.core), err, extra)


class CommittedOutputTests(unittest.TestCase):
    def test_dotfiles_keeps_no_copy_of_the_core(self) -> None:
        self.assertFalse((REPO_ROOT / "claude" / "CORE_INSTRUCTIONS.md").exists())

    def test_core_portion_has_no_pre_migration_path(self) -> None:
        core, _ = _split_committed()
        for path in PRE_MIGRATION_PATHS:
            self.assertNotIn(path, core, f"core portion still references {path}")

    def test_core_portion_has_no_personal_only_string(self) -> None:
        core, _ = _split_committed()
        for needle in PERSONAL_ONLY_STRINGS:
            self.assertNotIn(needle, core, f"core leaks personal-only {needle!r}")

    def test_overlay_suffix_matches_committed_overlay(self) -> None:
        """Catches an overlay edit committed without regenerating."""
        _, suffix = _split_committed()
        overlay = COMMITTED_OVERLAY.read_text(encoding="utf-8").rstrip("\n")
        self.assertEqual(suffix, f"\n\n{overlay}\n")


if __name__ == "__main__":
    unittest.main()
