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


class _WrapperTreeTestCase(unittest.TestCase):
    """A fake dotfiles + agent-toolkit tree holding a copy of the wrapper."""

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


class InstallWithAgentToolkitTestCase(_WrapperTreeTestCase):
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


FAKE_INTERPRETER = """#!/bin/sh
if [ "$1" = "-c" ]; then
  exit {guard_status}
fi
echo "CHOSEN {name}"
"""


class InterpreterChoiceTestCase(_WrapperTreeTestCase):
    """Which interpreter the wrapper hands both installers to.

    PATH holds only fake interpreters (plus dirname), so the machine's real
    python3 can't leak into the choice. Each fake answers the wrapper's
    ``-c`` version guard as a given version and, when run with install.py,
    prints its own name instead of installing anything.
    """

    def _fake_bin(self, versions: dict[str, tuple[int, int]]) -> Path:
        bin_dir = self.tmp / "bin"
        bin_dir.mkdir()
        dirname = Path("/usr/bin/dirname")
        if not dirname.exists():
            dirname = Path("/bin/dirname")
        (bin_dir / "dirname").symlink_to(dirname)
        for name, version in versions.items():
            fake = bin_dir / name
            guard_status = 0 if version >= (3, 12) else 1
            fake.write_text(
                FAKE_INTERPRETER.format(guard_status=guard_status, name=name)
            )
            fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
        return bin_dir

    def _run_with_path(self, bin_dir: Path) -> subprocess.CompletedProcess[str]:
        self._write_fake_install(self.agent_toolkit_dir, marker="UNUSED", exit_code=0)
        self._write_fake_install(self.dotfiles_dir, marker="UNUSED", exit_code=0)
        return subprocess.run(
            ["/bin/sh", str(self.wrapper_copy), "--harness=claude"],
            env={
                "PATH": str(bin_dir),
                "AGENT_TOOLKIT_PATH": str(self.agent_toolkit_dir),
            },
            capture_output=True,
            text=True,
        )

    def _chosen(self, stdout: str) -> list[str]:
        return [line for line in stdout.splitlines() if line.startswith("CHOSEN ")]

    def test_prefers_python314_for_both_installers(self) -> None:
        bin_dir = self._fake_bin(
            {"python3.14": (3, 14), "python3.12": (3, 12), "python3": (3, 12)}
        )

        result = self._run_with_path(bin_dir)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            self._chosen(result.stdout), ["CHOSEN python3.14", "CHOSEN python3.14"]
        )

    def test_falls_back_to_python312_when_no_314(self) -> None:
        bin_dir = self._fake_bin({"python3": (3, 10), "python3.12": (3, 12)})

        result = self._run_with_path(bin_dir)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            self._chosen(result.stdout), ["CHOSEN python3.12", "CHOSEN python3.12"]
        )

    def test_refuses_when_nothing_is_312_or_newer(self) -> None:
        bin_dir = self._fake_bin({"python3": (3, 11), "python": (3, 9)})

        result = self._run_with_path(bin_dir)

        self.assertEqual(result.returncode, 1)
        self.assertEqual(self._chosen(result.stdout), [])
        self.assertIn("no Python 3.12+ found", result.stderr)


if __name__ == "__main__":
    unittest.main()
