#!/usr/bin/env python3
"""Tests for gen_core_instructions.py.

The shareable core lives in agent-toolkit, not here, so composition is
tested against a fixture git checkout and fixture overlay, and the committed
output is checked only for what this repo alone can vouch for: its overlay
suffix and the absence of pre-migration paths in its core portion. No test
needs a real agent-toolkit checkout -- CI has none.
"""

import contextlib
import fcntl
import io
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
import gen_core_instructions as gci

pytestmark = pytest.mark.allow_real_subprocess

REPO_ROOT = Path(__file__).resolve().parents[2]
COMMITTED_OUTPUT = REPO_ROOT / "claude" / "global-instructions.md"
COMMITTED_OVERLAY = REPO_ROOT / "claude" / "personal-overlay.md"
CORE_REL = "claude/CORE_INSTRUCTIONS.md"

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

PROVENANCE = re.compile(r"\n<!-- agent-toolkit source: ([0-9a-f]{40}|unknown) -->\n")


def _split_committed() -> tuple[str, str, str]:
    """Return the committed output's (core portion, source, overlay suffix)."""
    text = COMMITTED_OUTPUT.read_text(encoding="utf-8")
    core, marker, rest = text.partition(gci.DO_NOT_EDIT_MARKER)
    if not marker:
        raise AssertionError("committed global-instructions.md has no marker")
    match = PROVENANCE.match(rest)
    if match is None:
        raise AssertionError("no provenance line right after the marker")
    return core, match.group(1), rest[match.end() :]


def git(cwd: Path, *args: str) -> str:
    """Run git in ``cwd`` and return stdout; fail the test on error."""
    result = subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, check=True
    )
    return result.stdout.strip()


class _FixtureTestCase(unittest.TestCase):
    """A fixture agent-toolkit git checkout plus a fixture dotfiles tree."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="gen-core-test-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.checkout = self.tmp / "agent-toolkit"
        (self.checkout / "agent-scripts").mkdir(parents=True)
        (self.checkout / "install.py").write_text("")
        (self.checkout / "links.toml").write_text("")
        self.core = self.checkout / CORE_REL
        self.core.parent.mkdir()
        git(self.checkout, "init", "-q", "-b", "main")
        git(self.checkout, "config", "user.email", "test@test.com")
        git(self.checkout, "config", "user.name", "test")
        self.sha_a = self._commit_core("# CLAUDE.md\n\n## Planning Gate\n\nA.\n")

        self.repo = self.tmp / "dotfiles"
        self.overlay = self.repo / "claude" / "personal-overlay.md"
        self.overlay.parent.mkdir(parents=True)
        self.overlay.write_text("## Personal Policy\n\nuse iron-lb-\n")
        self.output = self.repo / "claude" / "global-instructions.md"

    def _commit_core(self, text: str) -> str:
        self.core.write_text(text)
        git(self.checkout, "add", "-A")
        git(self.checkout, "commit", "-q", "-m", "core")
        return git(self.checkout, "rev-parse", "HEAD")

    def _commit_other(self) -> str:
        (self.checkout / "other.txt").write_text(str(time.time_ns()))
        git(self.checkout, "add", "-A")
        git(self.checkout, "commit", "-q", "-m", "other")
        return git(self.checkout, "rev-parse", "HEAD")

    def _main(self, *args: str) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            status = gci.main(
                [
                    "--repo-root",
                    str(self.repo),
                    "--agent-toolkit-path",
                    str(self.checkout),
                    *args,
                ]
            )
        return status, out.getvalue(), err.getvalue()

    def _recorded(self) -> str:
        match = PROVENANCE.search(self.output.read_text())
        assert match is not None
        return match.group(1)


class ComposeTests(_FixtureTestCase):
    def test_core_then_marker_then_provenance_then_overlay(self) -> None:
        composed = gci.compose_text("core\n\n", "overlay\n", "abc")
        self.assertEqual(
            composed,
            f"core\n\n{gci.DO_NOT_EDIT_MARKER}\n"
            "<!-- agent-toolkit source: abc -->\n\noverlay\n",
        )

    def test_marker_names_the_real_sources(self) -> None:
        self.assertIn("agent-toolkit", gci.DO_NOT_EDIT_MARKER)
        self.assertIn("claude/personal-overlay.md", gci.DO_NOT_EDIT_MARKER)


class ProvenanceTests(_FixtureTestCase):
    def test_records_the_last_core_commit_not_head(self) -> None:
        sha_b = self._commit_core("B\n")
        self._commit_other()
        status, _, err = self._main()
        self.assertEqual(status, 0, err)
        self.assertEqual(self._recorded(), sha_b)

    def test_composes_the_committed_core_not_the_working_tree(self) -> None:
        self.core.write_text("UNCOMMITTED\n")
        self._main()
        text = self.output.read_text()
        self.assertTrue(text.startswith("# CLAUDE.md\n\n## Planning Gate\n\nA."))
        self.assertNotIn("UNCOMMITTED", text)

    def test_core_path_records_unknown(self) -> None:
        loose = self.tmp / "loose.md"
        loose.write_text("loose\n")
        status, _, _ = self._main("--core-path", str(loose))
        self.assertEqual(status, 0)
        self.assertEqual(self._recorded(), "unknown")


class ForwardOnlyTests(_FixtureTestCase):
    def test_moves_forward(self) -> None:
        self._main()
        sha_b = self._commit_core("B\n")
        status, _, err = self._main()
        self.assertEqual(status, 0, err)
        self.assertEqual(self._recorded(), sha_b)

    def test_skips_when_the_checkout_is_behind(self) -> None:
        sha_b = self._commit_core("B\n")
        self._main()
        git(self.checkout, "reset", "-q", "--hard", self.sha_a)
        status, _, err = self._main()
        self.assertEqual(status, 3)
        self.assertIn("skipped", err)
        self.assertEqual(self._recorded(), sha_b)

    def test_skips_an_unknown_recorded_commit(self) -> None:
        self._main()
        text = self.output.read_text().replace(self.sha_a, "f" * 40)
        self.output.write_text(text)
        status, _, _ = self._main()
        self.assertEqual(status, 3)
        self.assertEqual(self._recorded(), "f" * 40)

    def test_a_merge_that_kept_the_old_core_is_not_forward(self) -> None:
        git(self.checkout, "checkout", "-q", "-b", "side")
        sha_b = self._commit_core("B\n")
        git(self.checkout, "checkout", "-q", "main")
        self._commit_other()
        git(self.checkout, "merge", "-q", "-s", "ours", "side", "-m", "ours")
        # HEAD contains B, but the core's last change on HEAD's path is A.
        text = gci.compose_text("x\n", "y\n", sha_b)
        self.output.write_text(text)
        status, _, _ = self._main()
        self.assertEqual(status, 3)
        self.assertEqual(self._recorded(), sha_b)

    def test_skip_is_silent_under_quiet(self) -> None:
        self._commit_core("B\n")
        self._main()
        git(self.checkout, "reset", "-q", "--hard", self.sha_a)
        status, out, err = self._main("-q")
        self.assertEqual((status, out, err), (3, "", ""))

    def test_force_overrides_a_skip(self) -> None:
        self._commit_core("B\n")
        self._main()
        git(self.checkout, "reset", "-q", "--hard", self.sha_a)
        status, _, _ = self._main("--force")
        self.assertEqual(status, 0)
        self.assertEqual(self._recorded(), self.sha_a)

    def test_a_file_without_provenance_is_overwritten(self) -> None:
        self.output.write_text("old composed file, no provenance\n")
        status, _, _ = self._main()
        self.assertEqual(status, 0)
        self.assertEqual(self._recorded(), self.sha_a)

    def test_identical_compose_leaves_the_file_untouched(self) -> None:
        self._main()
        os.utime(self.output, (1, 1))
        status, _, _ = self._main("-q")
        self.assertEqual(status, 0)
        self.assertEqual(self.output.stat().st_mtime, 1)


class ErrorTests(_FixtureTestCase):
    def test_non_git_checkout_exits_2(self) -> None:
        shutil.rmtree(self.checkout / ".git")
        status, _, err = self._main("-q")
        self.assertEqual(status, 2)
        self.assertIn(str(self.checkout), err)

    def test_checkout_without_core_history_exits_2(self) -> None:
        shutil.rmtree(self.checkout / ".git")
        self.core.unlink()
        git(self.checkout, "init", "-q", "-b", "main")
        git(self.checkout, "config", "user.email", "test@test.com")
        git(self.checkout, "config", "user.name", "test")
        self._commit_other()
        self.core.write_text("untracked\n")
        status, _, _ = self._main("-q")
        self.assertEqual(status, 2)

    def test_recorded_object_that_is_not_a_commit_exits_2_even_quiet(self) -> None:
        self._main()
        tree = git(self.checkout, "rev-parse", "HEAD^{tree}")
        self.output.write_text(gci.compose_text("x\n", "y\n", tree))
        status, _, err = self._main("-q")
        self.assertEqual(status, 2)
        self.assertIn(tree, err)

    def test_unresolvable_checkout_exits_2_with_the_reason(self) -> None:
        missing = self.tmp / "nowhere"
        for extra in ((), ("--check",), ("--print-agent-toolkit-path",)):
            out, err = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                status = gci.main(
                    [
                        "--repo-root",
                        str(self.repo),
                        "--agent-toolkit-path",
                        str(missing),
                        *extra,
                    ]
                )
            self.assertEqual(status, 2, extra)
            self.assertEqual(out.getvalue(), "", extra)
            self.assertIn(str(missing), err.getvalue(), extra)
        self.assertFalse(self.output.exists())

    def test_unreadable_overlay_exits_2(self) -> None:
        self.overlay.chmod(0)
        self.addCleanup(self.overlay.chmod, 0o644)
        for extra in ((), ("--check",)):
            status, _, err = self._main(*extra)
            self.assertEqual(status, 2, extra)
            self.assertIn(str(self.overlay), err, extra)


class CliTests(_FixtureTestCase):
    def test_check_reports_stale_without_writing(self) -> None:
        self.output.write_text("stale\n")
        status, _, err = self._main("--check")
        self.assertEqual(status, 1)
        self.assertIn("stale", err)
        self.assertEqual(self.output.read_text(), "stale\n")

    def test_check_is_fresh_after_a_write(self) -> None:
        self._main()
        status, _, _ = self._main("--check")
        self.assertEqual(status, 0)

    def test_stdout_writes_nothing(self) -> None:
        status, out, _ = self._main("--stdout")
        self.assertEqual(status, 0)
        self.assertIn(self.sha_a, out)
        self.assertFalse(self.output.exists())

    def test_write_leaves_no_stray_file_behind(self) -> None:
        self._main()
        names = {p.name for p in self.output.parent.iterdir()}
        self.assertLessEqual(
            names - {"global-instructions.md", "personal-overlay.md"},
            {".global-instructions.md.lock"},
        )

    def test_print_agent_toolkit_path(self) -> None:
        status, out, _ = self._main("--print-agent-toolkit-path")
        self.assertEqual(status, 0)
        self.assertEqual(out, f"{self.checkout.resolve()}\n")


class LockTests(_FixtureTestCase):
    def test_a_write_waits_for_the_lock(self) -> None:
        lock_path = self.output.with_name(".global-instructions.md.lock")
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        done = threading.Event()
        with open(lock_path, "w") as held:
            fcntl.flock(held, fcntl.LOCK_EX)
            worker = threading.Thread(target=lambda: (self._main("-q"), done.set()))
            worker.start()
            time.sleep(0.3)
            self.assertFalse(done.is_set(), "composer wrote while the lock was held")
            self.assertFalse(self.output.exists())
        worker.join(timeout=10)
        self.assertTrue(done.is_set())
        self.assertEqual(self._recorded(), self.sha_a)


class CommittedOutputTests(unittest.TestCase):
    def test_dotfiles_keeps_no_copy_of_the_core(self) -> None:
        self.assertFalse((REPO_ROOT / "claude" / "CORE_INSTRUCTIONS.md").exists())

    def test_committed_output_records_a_real_source_commit(self) -> None:
        _, source, _ = _split_committed()
        self.assertRegex(source, r"^[0-9a-f]{40}$")

    def test_core_portion_has_no_pre_migration_path(self) -> None:
        core, _, _ = _split_committed()
        for path in PRE_MIGRATION_PATHS:
            self.assertNotIn(path, core, f"core portion still references {path}")

    def test_core_portion_has_no_personal_only_string(self) -> None:
        core, _, _ = _split_committed()
        for needle in PERSONAL_ONLY_STRINGS:
            self.assertNotIn(needle, core, f"core leaks personal-only {needle!r}")

    def test_overlay_suffix_matches_committed_overlay(self) -> None:
        """Catches an overlay edit committed without regenerating."""
        _, _, suffix = _split_committed()
        overlay = COMMITTED_OVERLAY.read_text(encoding="utf-8").rstrip("\n")
        self.assertEqual(suffix, f"\n{overlay}\n")


if __name__ == "__main__":
    unittest.main()
