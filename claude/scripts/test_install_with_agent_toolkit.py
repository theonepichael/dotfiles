#!/usr/bin/env python3
"""Subprocess-level tests for ../../scripts/install-with-agent-toolkit.sh.

Exercises the wrapper as a real subprocess against fake install.py
stand-ins (not the real, slow installers) so these tests stay fast and
fully isolated -- no real HOME, no real package/symlink side effects.
"""

import shutil
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

import pytest

pytestmark = pytest.mark.allow_real_subprocess

WRAPPER = (
    Path(__file__).parent.parent.parent / "scripts" / "install-with-agent-toolkit.sh"
)

FAKE_INSTALL_PY = """#!/usr/bin/env python3
import sys
print("{marker}", *sys.argv[1:])
sys.exit({exit_code})
"""


class InstallWithAgentToolkitTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="install-with-agent-toolkit-test-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

        self.dotfiles_dir = self.tmp / "dotfiles"
        self.agent_toolkit_dir = self.tmp / "agent-toolkit"
        (self.dotfiles_dir / "scripts").mkdir(parents=True)
        self.agent_toolkit_dir.mkdir()

        # The wrapper lives at <dotfiles>/scripts/ and resolves DOTFILES_DIR
        # as its own parent's parent -- copy it into the fake tree so that
        # resolution lands on the fake dotfiles root, not the real one.
        wrapper_copy = self.dotfiles_dir / "scripts" / "install-with-agent-toolkit.sh"
        wrapper_copy.write_text(WRAPPER.read_text())
        wrapper_copy.chmod(wrapper_copy.stat().st_mode | stat.S_IEXEC)
        self.wrapper_copy = wrapper_copy

    def _write_fake_install(
        self, repo_dir: Path, *, marker: str, exit_code: int
    ) -> None:
        path = repo_dir / "install.py"
        path.write_text(FAKE_INSTALL_PY.format(marker=marker, exit_code=exit_code))
        path.chmod(path.stat().st_mode | stat.S_IEXEC)

    def _run(self, args: list[str], *, agent_toolkit_path: Path | None = None):
        env = {"PATH": "/usr/bin:/bin:/usr/local/bin"}
        candidate = (
            agent_toolkit_path
            if agent_toolkit_path is not None
            else self.agent_toolkit_dir
        )
        env["AGENT_TOOLKIT_PATH"] = str(candidate)
        return subprocess.run(
            [str(self.wrapper_copy), *args],
            env=env,
            capture_output=True,
            text=True,
        )

    def test_runs_agent_toolkit_then_dotfiles_in_order(self) -> None:
        self._write_fake_install(
            self.agent_toolkit_dir, marker="AGENT_TOOLKIT_RAN", exit_code=0
        )
        self._write_fake_install(self.dotfiles_dir, marker="DOTFILES_RAN", exit_code=0)

        result = self._run(["--harness=claude"])

        self.assertEqual(result.returncode, 0, result.stderr)
        agent_pos = result.stdout.index("AGENT_TOOLKIT_RAN")
        dotfiles_pos = result.stdout.index("DOTFILES_RAN")
        self.assertLess(agent_pos, dotfiles_pos, result.stdout)
        self.assertIn("AGENT_TOOLKIT_RAN --harness=claude", result.stdout)
        self.assertIn("DOTFILES_RAN --harness=claude", result.stdout)

    def test_dotfiles_still_runs_when_agent_toolkit_reports_a_skip(self) -> None:
        """Regression: install.py exits 1 for an ordinary skip (e.g. an unmet
        Neovim version floor), not just for a real failure. Under `set -e`,
        a bare `cmd; status=$?` line aborts the whole script the instant the
        first command exits non-zero -- silently skipping the dotfiles
        reassert step entirely, which is the one thing this wrapper exists
        to guarantee always runs."""
        self._write_fake_install(
            self.agent_toolkit_dir, marker="AGENT_TOOLKIT_RAN", exit_code=1
        )
        self._write_fake_install(self.dotfiles_dir, marker="DOTFILES_RAN", exit_code=0)

        result = self._run(["--harness=claude"])

        self.assertIn("DOTFILES_RAN", result.stdout, result.stdout)
        self.assertEqual(result.returncode, 1, result.stderr)

    def test_refuses_rollback(self) -> None:
        self._write_fake_install(
            self.agent_toolkit_dir, marker="AGENT_TOOLKIT_RAN", exit_code=0
        )
        self._write_fake_install(self.dotfiles_dir, marker="DOTFILES_RAN", exit_code=0)

        result = self._run(["--rollback"])

        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertNotIn("AGENT_TOOLKIT_RAN", result.stdout)
        self.assertNotIn("DOTFILES_RAN", result.stdout)
        self.assertIn("run install.py directly", result.stderr)

    def test_refuses_check_links(self) -> None:
        self._write_fake_install(
            self.agent_toolkit_dir, marker="AGENT_TOOLKIT_RAN", exit_code=0
        )
        self._write_fake_install(self.dotfiles_dir, marker="DOTFILES_RAN", exit_code=0)

        result = self._run(["--check-links"])

        self.assertEqual(result.returncode, 2, result.stderr)

    def test_missing_agent_toolkit_checkout_errors_clearly(self) -> None:
        self._write_fake_install(self.dotfiles_dir, marker="DOTFILES_RAN", exit_code=0)
        missing = self.tmp / "nowhere"

        result = self._run(["--harness=claude"], agent_toolkit_path=missing)

        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn("AGENT_TOOLKIT_PATH", result.stderr)
        self.assertNotIn("DOTFILES_RAN", result.stdout)

    def test_both_nonzero_exits_propagate_as_failure(self) -> None:
        self._write_fake_install(
            self.agent_toolkit_dir, marker="AGENT_TOOLKIT_RAN", exit_code=1
        )
        self._write_fake_install(self.dotfiles_dir, marker="DOTFILES_RAN", exit_code=1)

        result = self._run(["--harness=claude"])

        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("agent-toolkit exit=1 dotfiles exit=1", result.stderr)


if __name__ == "__main__":
    unittest.main()
