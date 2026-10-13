"""Tests for dotfiles_cli_common.py."""

import argparse
import io
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

import dotfiles_cli_common as cli_common


class AddVerbosityArgsTests(unittest.TestCase):
    def test_adds_quiet_and_verbose_flags(self) -> None:
        parser = argparse.ArgumentParser()
        cli_common.add_verbosity_args(parser)
        args = parser.parse_args([])
        self.assertFalse(args.quiet)
        self.assertFalse(args.verbose)

    def test_short_flags_are_accepted(self) -> None:
        parser = argparse.ArgumentParser()
        cli_common.add_verbosity_args(parser)
        quiet_args = parser.parse_args(["-q"])
        self.assertTrue(quiet_args.quiet)
        verbose_args = parser.parse_args(["-v"])
        self.assertTrue(verbose_args.verbose)

    def test_quiet_and_verbose_are_mutually_exclusive(self) -> None:
        parser = argparse.ArgumentParser()
        cli_common.add_verbosity_args(parser)
        with self.assertRaises(SystemExit):
            parser.parse_args(["--quiet", "--verbose"])


class VPrintTests(unittest.TestCase):
    def test_prints_when_verbose(self) -> None:
        out = io.StringIO()
        cli_common.vprint("hello", verbose=True, file=out)
        self.assertEqual(out.getvalue(), "hello\n")

    def test_is_silent_when_not_verbose(self) -> None:
        out = io.StringIO()
        cli_common.vprint("hello", verbose=False, file=out)
        self.assertEqual(out.getvalue(), "")

    def test_defaults_to_stderr(self) -> None:
        captured = io.StringIO()
        original_stderr = sys.stderr
        sys.stderr = captured
        try:
            cli_common.vprint("stderr msg", verbose=True)
        finally:
            sys.stderr = original_stderr
        self.assertEqual(captured.getvalue(), "stderr msg\n")


class QPrintTests(unittest.TestCase):
    def test_prints_when_not_quiet(self) -> None:
        out = io.StringIO()
        cli_common.qprint("hello", quiet=False, file=out)
        self.assertEqual(out.getvalue(), "hello\n")

    def test_is_silent_when_quiet(self) -> None:
        out = io.StringIO()
        cli_common.qprint("hello", quiet=True, file=out)
        self.assertEqual(out.getvalue(), "")

    def test_defaults_to_stdout(self) -> None:
        captured = io.StringIO()
        original_stdout = sys.stdout
        sys.stdout = captured
        try:
            cli_common.qprint("stdout msg", quiet=False)
        finally:
            sys.stdout = original_stdout
        self.assertEqual(captured.getvalue(), "stdout msg\n")


def _make_checkout(path: Path) -> Path:
    """Create the marker files that make ``path`` count as a toolkit checkout."""
    (path / "agent-scripts").mkdir(parents=True)
    (path / "install.py").write_text("")
    (path / "links.toml").write_text("")
    return path


class AgentToolkitCheckoutTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="cli-common-checkout-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.home = self.tmp / "home"
        self.flat = self.home / "Workspace" / "agent-toolkit"
        self.nested = self.flat / "agent-toolkit"

    def _resolve(
        self, env: dict[str, str] | None = None, explicit: Path | None = None
    ) -> Path:
        return cli_common.agent_toolkit_checkout(
            explicit=explicit, env=env or {}, home=self.home
        )

    def test_nested_layout_beats_flat_container(self) -> None:
        _make_checkout(self.nested)
        self.assertEqual(self._resolve(), self.nested.resolve())

    def test_flat_layout_is_used_when_it_is_the_checkout(self) -> None:
        _make_checkout(self.flat)
        self.assertEqual(self._resolve(), self.flat.resolve())

    def test_flat_container_without_markers_is_rejected(self) -> None:
        self.flat.mkdir(parents=True)
        with self.assertRaises(cli_common.AgentToolkitNotFoundError) as caught:
            self._resolve()
        self.assertIn(str(self.nested), str(caught.exception))
        self.assertIn(str(self.flat), str(caught.exception))

    def test_env_wins_over_layout_defaults(self) -> None:
        _make_checkout(self.nested)
        elsewhere = _make_checkout(self.tmp / "elsewhere")
        self.assertEqual(
            self._resolve({"AGENT_TOOLKIT_PATH": str(elsewhere)}), elsewhere.resolve()
        )

    def test_set_but_invalid_env_raises_instead_of_falling_through(self) -> None:
        _make_checkout(self.nested)
        with self.assertRaises(cli_common.AgentToolkitNotFoundError) as caught:
            self._resolve({"AGENT_TOOLKIT_PATH": str(self.tmp / "nowhere")})
        self.assertIn("AGENT_TOOLKIT_PATH", str(caught.exception))

    def test_empty_env_counts_as_unset(self) -> None:
        _make_checkout(self.nested)
        self.assertEqual(
            self._resolve({"AGENT_TOOLKIT_PATH": ""}), self.nested.resolve()
        )

    def test_env_tilde_expands_against_home(self) -> None:
        _make_checkout(self.home / "tk")
        self.assertEqual(
            self._resolve({"AGENT_TOOLKIT_PATH": "~/tk", "HOME": str(self.home)}),
            (self.home / "tk").resolve(),
        )

    def test_explicit_wins_and_never_falls_through(self) -> None:
        _make_checkout(self.nested)
        explicit = _make_checkout(self.tmp / "explicit")
        self.assertEqual(self._resolve(explicit=explicit), explicit.resolve())
        with self.assertRaises(cli_common.AgentToolkitNotFoundError):
            self._resolve(explicit=self.tmp / "missing")


if __name__ == "__main__":
    unittest.main(verbosity=1)
