#!/usr/bin/env python3
"""Subprocess-level tests for ../../scripts/install-with-agent-toolkit.sh.

Exercises the wrapper as a real subprocess against fake install.py
stand-ins (not the real, slow installers) so these tests stay fast and
fully isolated -- no real HOME, no real package/symlink side effects. The
checkout resolver and composer are the real ones, copied into the fake
dotfiles tree, so resolution and composition are exercised end to end.
"""

import shutil
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

import pytest

pytestmark = pytest.mark.allow_real_subprocess

SCRIPTS_DIR = Path(__file__).parent
WRAPPER = SCRIPTS_DIR.parent.parent / "scripts" / "install-with-agent-toolkit.sh"
# The real files the wrapper calls into, copied into the fake dotfiles tree.
COMPOSER_FILES = ("gen_core_instructions.py", "dotfiles_cli_common.py")

FAKE_INSTALL_PY = """#!/usr/bin/env python3
import os
import sys
print("{marker}", *sys.argv[1:])
print("{marker}_SAW_AGENT_TOOLKIT_PATH=" + os.environ.get("AGENT_TOOLKIT_PATH", ""))
sys.exit({exit_code})
"""

CORE_TEXT = "# CLAUDE.md\n\n## Planning Gate\n"
OVERLAY_TEXT = "## Personal Policy\n"


class _WrapperTreeTestCase(unittest.TestCase):
    """A fake dotfiles + agent-toolkit tree holding a copy of the wrapper."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="install-with-agent-toolkit-test-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

        self.home = self.tmp / "home"
        self.home.mkdir()
        self.dotfiles_dir = self.tmp / "dotfiles"
        self.agent_toolkit_dir = self.tmp / "agent-toolkit"
        (self.dotfiles_dir / "scripts").mkdir(parents=True)
        self._make_checkout(self.agent_toolkit_dir)

        scripts = self.dotfiles_dir / "claude" / "scripts"
        scripts.mkdir(parents=True)
        for name in COMPOSER_FILES:
            shutil.copy(SCRIPTS_DIR / name, scripts / name)
        self.overlay = self.dotfiles_dir / "claude" / "personal-overlay.md"
        self.overlay.write_text(OVERLAY_TEXT)
        self.composed = self.dotfiles_dir / "claude" / "global-instructions.md"

        # The wrapper lives at <dotfiles>/scripts/ and resolves DOTFILES_DIR
        # as its own parent's parent -- copy it into the fake tree so that
        # resolution lands on the fake dotfiles root, not the real one.
        wrapper_copy = self.dotfiles_dir / "scripts" / "install-with-agent-toolkit.sh"
        wrapper_copy.write_text(WRAPPER.read_text())
        wrapper_copy.chmod(wrapper_copy.stat().st_mode | stat.S_IEXEC)
        self.wrapper_copy = wrapper_copy

    def _make_checkout(self, path: Path) -> None:
        """Give ``path`` the markers and core file of an agent-toolkit checkout."""
        (path / "agent-scripts").mkdir(parents=True)
        (path / "links.toml").write_text("")
        (path / "install.py").write_text("")
        (path / "claude").mkdir()
        (path / "claude" / "CORE_INSTRUCTIONS.md").write_text(CORE_TEXT)

    def _write_fake_install(
        self, repo_dir: Path, *, marker: str, exit_code: int
    ) -> None:
        path = repo_dir / "install.py"
        path.write_text(FAKE_INSTALL_PY.format(marker=marker, exit_code=exit_code))
        path.chmod(path.stat().st_mode | stat.S_IEXEC)


class InstallWithAgentToolkitTestCase(_WrapperTreeTestCase):
    def _run(
        self,
        args: list[str],
        *,
        agent_toolkit_path: Path | None = None,
        unset_agent_toolkit_path: bool = False,
    ):
        env = {"PATH": "/usr/bin:/bin:/usr/local/bin", "HOME": str(self.home)}
        if not unset_agent_toolkit_path:
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

    def _write_both(self, *, dotfiles_exit: int = 0) -> None:
        self._write_fake_install(
            self.agent_toolkit_dir, marker="AGENT_TOOLKIT_RAN", exit_code=0
        )
        self._write_fake_install(
            self.dotfiles_dir, marker="DOTFILES_RAN", exit_code=dotfiles_exit
        )

    def test_composes_from_the_live_core_after_both_installers(self) -> None:
        self._write_both()

        result = self._run(["--harness=claude"])

        self.assertEqual(result.returncode, 0, result.stderr)
        composed = self.composed.read_text()
        self.assertTrue(composed.startswith(CORE_TEXT), composed)
        self.assertTrue(composed.endswith(OVERLAY_TEXT), composed)
        self.assertLess(
            result.stdout.index("DOTFILES_RAN"),
            result.stdout.index("wrote claude/global-instructions.md"),
            result.stdout,
        )

    def test_resolves_the_nested_checkout_with_the_env_var_unset(self) -> None:
        nested = self.home / "Workspace" / "agent-toolkit" / "agent-toolkit"
        self._make_checkout(nested)
        self._write_fake_install(nested, marker="NESTED_RAN", exit_code=0)
        self._write_fake_install(self.dotfiles_dir, marker="DOTFILES_RAN", exit_code=0)

        result = self._run(["--harness=claude"], unset_agent_toolkit_path=True)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("NESTED_RAN --harness=claude", result.stdout)
        self.assertIn(
            f"DOTFILES_RAN_SAW_AGENT_TOOLKIT_PATH={nested.resolve()}", result.stdout
        )
        self.assertTrue(self.composed.read_text().startswith(CORE_TEXT))

    def test_dry_run_previews_without_writing(self) -> None:
        self._write_both()

        result = self._run(["--dry-run", "--harness=claude"])

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(self.composed.exists())
        self.assertIn("stale", result.stderr)

    def test_dry_run_still_fails_when_composition_cannot_run(self) -> None:
        """Stale output is what a dry run expects; a composer error is not."""
        self._write_both()
        (self.agent_toolkit_dir / "claude" / "CORE_INSTRUCTIONS.md").unlink()

        result = self._run(["--dry-run", "--harness=claude"])

        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn("compose exit=2", result.stderr)
        self.assertFalse(self.composed.exists())

    def test_help_and_usage_errors_never_compose(self) -> None:
        self._write_both()
        for help_args in (["--help"], ["-h"], ["-qh"]):
            result = self._run(help_args)
            self.assertFalse(self.composed.exists(), (help_args, result.stdout))

        self._write_both(dotfiles_exit=2)
        result = self._run(["--bogus"])
        self.assertFalse(self.composed.exists(), result.stdout)
        self.assertNotEqual(result.returncode, 0)

    def test_composer_failure_fails_the_wrapper(self) -> None:
        self._write_both()
        self.overlay.unlink()

        result = self._run(["--harness=claude"])

        self.assertIn("DOTFILES_RAN --harness=claude", result.stdout)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("compose exit=2", result.stderr)


FAKE_INTERPRETER = """#!/bin/sh
if [ "$1" = "-c" ]; then
  exit {guard_status}
fi
case "$1" in
  */gen_core_instructions.py)
    case " $* " in
      *" --print-agent-toolkit-path "*) echo "$AGENT_TOOLKIT_PATH" ;;
      *) echo "COMPOSED {name}" ;;
    esac
    exit 0
    ;;
esac
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
                "HOME": str(self.home),
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
        self.assertIn("COMPOSED python3.14", result.stdout)

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
