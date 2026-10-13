#!/usr/bin/env python3
"""Subprocess-level tests for ../../scripts/install-with-agent-toolkit.sh.

Exercises the wrapper as a real subprocess against fake install.py
stand-ins (not the real, slow installers) so these tests stay fast and
fully isolated -- no real HOME, no real package/symlink side effects. The
checkout resolver and composer are the real ones, copied into the fake
dotfiles tree, so resolution and composition are exercised end to end.
"""

import ast
import json
import os
import re
import shutil
import stat
import subprocess
import tempfile
import unittest
from dataclasses import dataclass
from pathlib import Path

import dotfiles_cli_common as cli_common
import pytest

import conftest

pytestmark = pytest.mark.allow_real_subprocess

SCRIPTS_DIR = Path(__file__).parent
WRAPPER = SCRIPTS_DIR.parent.parent / "scripts" / "install-with-agent-toolkit.sh"
# The real files the wrapper calls into, copied into the fake dotfiles tree.
COMPOSER_FILES = ("gen_core_instructions.py", "dotfiles_cli_common.py")

FAKE_INSTALL_PY = """#!/usr/bin/env python3
import os
import sys
import json
print("{marker}", *sys.argv[1:])
print("{marker}_SAW_AGENT_TOOLKIT_PATH=" + os.environ.get("AGENT_TOOLKIT_PATH", ""))
print("{marker}_ARGV=" + json.dumps(sys.argv[1:]))
print("{marker}_WRAPPER_MARKER=" + os.environ.get("AGENT_TOOLKIT_INSTALL_WRAPPER", ""))
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
        """Give ``path`` the markers and a committed core, like a real checkout."""
        (path / "agent-scripts").mkdir(parents=True)
        (path / "links.toml").write_text("")
        (path / "install.py").write_text("")
        (path / "claude").mkdir()
        (path / "claude" / "CORE_INSTRUCTIONS.md").write_text(CORE_TEXT)
        for args in (
            ["init", "-q", "-b", "main"],
            ["add", "-A"],
            ["-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "c"],
        ):
            subprocess.run(["git", "-C", str(path), *args], check=True)

    def _write_fake_install(
        self, repo_dir: Path, *, marker: str, exit_code: int
    ) -> None:
        path = repo_dir / "install.py"
        path.write_text(FAKE_INSTALL_PY.format(marker=marker, exit_code=exit_code))
        path.chmod(path.stat().st_mode | stat.S_IEXEC)

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

    def _write_both(self, *, dotfiles_exit: int = 0) -> None:
        self._write_fake_install(
            self.agent_toolkit_dir, marker="AGENT_TOOLKIT_RAN", exit_code=0
        )
        self._write_fake_install(
            self.dotfiles_dir, marker="DOTFILES_RAN", exit_code=dotfiles_exit
        )


class InstallWithAgentToolkitTestCase(_WrapperTreeTestCase):
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
        shutil.rmtree(self.agent_toolkit_dir / ".git")  # no history to compose from

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

    def test_a_forward_only_skip_is_a_notice_not_a_failure(self) -> None:
        self._write_both()
        self.composed.write_text(
            "old\n<!-- agent-toolkit source: " + "f" * 40 + " -->\n"
        )

        result = self._run(["--harness=claude"])

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("skipped", result.stderr)
        self.assertTrue(self.composed.read_text().startswith("old"))

    def test_a_merge_driver_registration_failure_fails_the_wrapper(self) -> None:
        self._write_both()
        subprocess.run(["git", "init", "-q", str(self.dotfiles_dir)], check=True)
        (self.dotfiles_dir / ".git" / "config.lock").write_text("held")

        result = self._run(["--harness=claude"])

        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn("compose exit=2", result.stderr)
        self.assertIn("merge driver", result.stderr)
        self.assertFalse(self.composed.exists())

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


class FlagRoutingTestCase(_WrapperTreeTestCase):
    """Each installer receives only the flags it accepts.

    Regression: the wrapper used to forward "$@" verbatim to both
    installers, so an agent-toolkit-only flag (--force-harness) made the
    dotfiles installer exit 2 with "unknown argument" -- which also skipped
    the global-instructions compose -- and a dotfiles-only flag
    (--no-nvim-pin) broke the agent-toolkit step the same way.
    """

    def _argv(self, stdout: str, marker: str) -> list[str]:
        prefix = f"{marker}_ARGV="
        lines = [line for line in stdout.splitlines() if line.startswith(prefix)]
        self.assertEqual(len(lines), 1, stdout)
        return json.loads(lines[0][len(prefix) :])

    def test_force_harness_reaches_only_agent_toolkit(self) -> None:
        self._write_both()

        result = self._run(["--force-harness", "--harness=claude"])

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            self._argv(result.stdout, "AGENT_TOOLKIT_RAN"),
            ["--force-harness", "--harness=claude"],
        )
        self.assertEqual(
            self._argv(result.stdout, "DOTFILES_RAN"), ["--harness=claude"]
        )

    def test_no_nvim_pin_reaches_only_dotfiles(self) -> None:
        self._write_both()

        result = self._run(["--harness=claude", "--no-nvim-pin"])

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            self._argv(result.stdout, "AGENT_TOOLKIT_RAN"), ["--harness=claude"]
        )
        self.assertEqual(
            self._argv(result.stdout, "DOTFILES_RAN"),
            ["--harness=claude", "--no-nvim-pin"],
        )

    def test_routing_preserves_every_other_token_in_order(self) -> None:
        self._write_both()
        args = [
            "--harness",
            "claude",
            "",
            "a b",
            "*",
            "--force-harness",
            "--no-nvim-pin",
            "--force-harness",
        ]

        result = self._run(args)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            self._argv(result.stdout, "AGENT_TOOLKIT_RAN"),
            [
                "--harness",
                "claude",
                "",
                "a b",
                "*",
                "--force-harness",
                "--force-harness",
            ],
        )
        self.assertEqual(
            self._argv(result.stdout, "DOTFILES_RAN"),
            ["--harness", "claude", "", "a b", "*", "--no-nvim-pin"],
        )

    def test_no_arguments_reach_both_installers_as_none(self) -> None:
        self._write_both()

        result = self._run([])

        self.assertEqual(self._argv(result.stdout, "AGENT_TOOLKIT_RAN"), [])
        self.assertEqual(self._argv(result.stdout, "DOTFILES_RAN"), [])

    def test_wrapper_marker_still_reaches_agent_toolkit_only(self) -> None:
        self._write_both()

        result = self._run(["--force-harness", "--harness=claude"])

        self.assertIn("AGENT_TOOLKIT_RAN_WRAPPER_MARKER=1", result.stdout)
        self.assertIn("DOTFILES_RAN_WRAPPER_MARKER=\n", result.stdout + "\n")

    def test_installer_failure_status_survives_routing(self) -> None:
        self._write_fake_install(
            self.agent_toolkit_dir, marker="AGENT_TOOLKIT_RAN", exit_code=1
        )
        self._write_fake_install(self.dotfiles_dir, marker="DOTFILES_RAN", exit_code=1)

        result = self._run(["--force-harness", "--harness=claude", "--no-nvim-pin"])

        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("agent-toolkit exit=1 dotfiles exit=1", result.stderr)


# --- Flag-table drift guard -------------------------------------------------
#
# The wrapper routes flags by a static table. These helpers read each
# installer's argparse declarations straight from its source (never importing
# it), so a flag added to either installer fails the drift test below until
# someone classifies it in the wrapper's table. Any declaration shape the
# scanner can't prove it understands raises instead of passing silently.

TABLE_NAMES = (
    "SHARED_FLAGS",
    "AGENT_TOOLKIT_ONLY_FLAGS",
    "DOTFILES_ONLY_FLAGS",
    "REFUSED_FLAGS",
)
ZERO_ARG_ACTIONS = {"store_true", "store_false", "store_const", "count"}
PARSER_CONSTRUCTORS = {"_Parser", "ArgumentParser"}
GROUP_FACTORIES = {"add_mutually_exclusive_group", "add_argument_group"}


class UnsupportedDeclaration(Exception):
    """A flag declaration shape the scanner can't vouch for."""


@dataclass(frozen=True)
class FlagRecord:
    """One add_argument call: its long name, other option strings, arity."""

    long: str
    aliases: frozenset[str]
    takes_value: bool


def _callee(call: ast.Call) -> str | None:
    if isinstance(call.func, ast.Name):
        return call.func.id
    if isinstance(call.func, ast.Attribute):
        return call.func.attr
    return None


def _function(tree: ast.Module, name: str) -> ast.FunctionDef:
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise UnsupportedDeclaration(f"no top-level {name}()")


def _group_aliases(fn: ast.FunctionDef, aliases: set[str]) -> set[str]:
    """Grow ``aliases`` with every name assigned from a group factory on one."""
    aliases = set(aliases)
    changed = True
    while changed:
        changed = False
        for node in ast.walk(fn):
            if not (
                isinstance(node, ast.Assign)
                and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name)
                and isinstance(node.value, ast.Call)
                and isinstance(node.value.func, ast.Attribute)
                and node.value.func.attr in GROUP_FACTORIES
                and isinstance(node.value.func.value, ast.Name)
                and node.value.func.value.id in aliases
            ):
                continue
            if node.targets[0].id not in aliases:
                aliases.add(node.targets[0].id)
                changed = True
    return aliases


def _record(call: ast.Call) -> FlagRecord:
    if any(isinstance(arg, ast.Starred) for arg in call.args) or any(
        kw.arg is None for kw in call.keywords
    ):
        raise UnsupportedDeclaration("*args/**kwargs expansion in add_argument")
    names: list[str] = []
    for arg in call.args:
        if not (isinstance(arg, ast.Constant) and isinstance(arg.value, str)):
            raise UnsupportedDeclaration("non-literal add_argument option string")
        names.append(arg.value)
    options = [name for name in names if name.startswith("-")]
    if not options:
        raise UnsupportedDeclaration(f"positional argument {names!r}")
    long = next((name for name in options if name.startswith("--")), options[0])
    keywords = {kw.arg: kw.value for kw in call.keywords}
    action = keywords.get("action")
    if action is not None and not (
        isinstance(action, ast.Constant) and isinstance(action.value, str)
    ):
        raise UnsupportedDeclaration(f"{long}: non-literal action")
    zero_arg = (
        action is not None
        and action.value in ZERO_ARG_ACTIONS
        and "nargs" not in keywords
    )
    return FlagRecord(
        long=long,
        aliases=frozenset(set(options) - {long}),
        takes_value=not zero_arg,
    )


def _scan(
    fn: ast.FunctionDef,
    aliases: set[str],
    helpers: dict[str, ast.FunctionDef],
    records: dict[str, FlagRecord],
) -> None:
    aliases = _group_aliases(fn, aliases)
    for node in ast.walk(fn):
        if not isinstance(node, ast.Call):
            continue
        if isinstance(node.func, ast.Attribute) and node.func.attr == "add_argument":
            receiver = node.func.value
            if not (isinstance(receiver, ast.Name) and receiver.id in aliases):
                raise UnsupportedDeclaration("add_argument on an unknown receiver")
            record = _record(node)
            records[record.long] = record
            continue
        passed = [*node.args, *(kw.value for kw in node.keywords)]
        if not any(isinstance(a, ast.Name) and a.id in aliases for a in passed):
            continue
        name = _callee(node)
        if name not in helpers:
            raise UnsupportedDeclaration(f"parser passed to unknown helper {name}()")
        helper = helpers[name]
        _scan(helper, {helper.args.args[0].arg}, helpers, records)


def installer_flags(install_src: str, cli_common_src: str) -> dict[str, FlagRecord]:
    """Every flag ``install_src``'s parse_args() declares, keyed by long name."""
    parse_args = _function(ast.parse(install_src), "parse_args")
    parsers: list[str] = []
    for node in ast.walk(parse_args):
        if not (isinstance(node, ast.Assign) and isinstance(node.value, ast.Call)):
            continue
        if _callee(node.value) not in PARSER_CONSTRUCTORS:
            continue
        if any(kw.arg in ("parents", None) for kw in node.value.keywords):
            raise UnsupportedDeclaration("parser built with parents= or **kwargs")
        if not (len(node.targets) == 1 and isinstance(node.targets[0], ast.Name)):
            raise UnsupportedDeclaration("parser assigned to a non-name target")
        parsers.append(node.targets[0].id)
    if len(parsers) != 1:
        raise UnsupportedDeclaration(
            f"expected one direct parser constructor in parse_args, found {parsers}"
        )
    helpers = {
        "add_verbosity_args": _function(ast.parse(cli_common_src), "add_verbosity_args")
    }
    records: dict[str, FlagRecord] = {}
    _scan(parse_args, {parsers[0]}, helpers, records)
    return records


def wrapper_flag_table(wrapper_src: str) -> dict[str, set[str]]:
    """The wrapper's four routing lists, by variable name."""
    table: dict[str, set[str]] = {}
    for name in TABLE_NAMES:
        match = re.search(rf'^{name}="([^"]*)"$', wrapper_src, re.MULTILINE)
        if match is None:
            raise AssertionError(f'wrapper has no one-line {name}="..."')
        table[name] = set(match.group(1).split())
    return table


def drift_problems(
    table: dict[str, set[str]],
    dotfiles: dict[str, FlagRecord],
    agent_toolkit: dict[str, FlagRecord] | None,
) -> list[str]:
    """Every way the wrapper's table disagrees with the installers.

    With ``agent_toolkit`` None (no checkout), only the dotfiles-side
    checks run.
    """
    problems: list[str] = []
    names = list(TABLE_NAMES)
    for i, first in enumerate(names):
        for second in names[i + 1 :]:
            for flag in sorted(table[first] & table[second]):
                problems.append(f"{flag} is in both {first} and {second}")

    shared = table["SHARED_FLAGS"]
    at_only = table["AGENT_TOOLKIT_ONLY_FLAGS"]
    df_only = table["DOTFILES_ONLY_FLAGS"]
    refused = table["REFUSED_FLAGS"]

    def check_routed(flag: str, records: dict[str, FlagRecord], side: str) -> None:
        record = records.get(flag)
        if record is None:
            problems.append(f"{flag} is routed to {side} but {side} lacks it")
            return
        if record.takes_value:
            problems.append(f"{flag} takes a value; routing drops one token only")
        if record.aliases:
            problems.append(f"{flag} has aliases {sorted(record.aliases)}")

    for flag in sorted(df_only):
        check_routed(flag, dotfiles, "dotfiles")
    if agent_toolkit is None:
        return problems

    for flag in sorted(at_only):
        check_routed(flag, agent_toolkit, "agent-toolkit")
        if flag in dotfiles:
            problems.append(f"{flag} is agent-toolkit-only but dotfiles has it too")
    for flag in sorted(df_only & set(agent_toolkit)):
        problems.append(f"{flag} is dotfiles-only but agent-toolkit has it too")
    for flag in sorted(shared - (set(dotfiles) & set(agent_toolkit))):
        problems.append(f"{flag} is shared but not in both installers")
    for flag in sorted(shared & set(dotfiles) & set(agent_toolkit)):
        if dotfiles[flag] != agent_toolkit[flag]:
            problems.append(
                f"{flag} is shared but its aliases or arity differ between installers"
            )

    declared = set(dotfiles) | set(agent_toolkit)
    classified = shared | at_only | df_only | refused
    for flag in sorted(declared - classified):
        problems.append(f"{flag} is declared by an installer but unclassified")
    for flag in sorted(classified - declared):
        problems.append(f"{flag} is in the table but no installer declares it")
    for flag in sorted(refused):
        for records in (dotfiles, agent_toolkit):
            if flag in records and records[flag].aliases:
                problems.append(
                    f"{flag} is refused but has aliases {sorted(records[flag].aliases)}"
                )
    return problems


_SYNTH_CLI_COMMON = """
def add_verbosity_args(parser):
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--quiet", "-q", action="store_true")
    group.add_argument("--verbose", "-v", action="store_true")
"""


def _synth_install(body: str, constructor: str = "_Parser(add_help=False)") -> str:
    lines = "\n".join(f"    {line}" for line in body.strip().splitlines())
    return (
        "def parse_args(argv):\n"
        f"    parser = {constructor}\n"
        "    cli_common.add_verbosity_args(parser)\n"
        f"{lines}\n"
        "    return parser.parse_known_args(argv)\n"
    )


class FlagScannerTestCase(unittest.TestCase):
    """The scanner on synthetic sources: what it sees, and what it refuses."""

    def test_sees_literal_flags_with_arity_and_aliases(self) -> None:
        flags = installer_flags(
            _synth_install(
                'parser.add_argument("--profile", default="x")\n'
                'parser.add_argument("--dry-run", action="store_true")\n'
                'parser.add_argument("-h", "--help", action="store_true")'
            ),
            _SYNTH_CLI_COMMON,
        )

        self.assertEqual(
            set(flags), {"--profile", "--dry-run", "--help", "--quiet", "--verbose"}
        )
        self.assertTrue(flags["--profile"].takes_value)
        self.assertFalse(flags["--dry-run"].takes_value)
        self.assertEqual(flags["--help"].aliases, frozenset({"-h"}))
        self.assertEqual(flags["--quiet"].aliases, frozenset({"-q"}))

    def test_sees_a_flag_added_inside_the_verbosity_helper(self) -> None:
        helper = _SYNTH_CLI_COMMON + '    group.add_argument("--zzz-probe")\n'

        flags = installer_flags(_synth_install(""), helper)

        self.assertIn("--zzz-probe", flags)

    def test_nargs_makes_a_store_true_flag_take_a_value(self) -> None:
        flags = installer_flags(
            _synth_install(
                'parser.add_argument("--x", action="store_const", nargs="?")'
            ),
            _SYNTH_CLI_COMMON,
        )

        self.assertTrue(flags["--x"].takes_value)

    def test_refuses_shapes_it_cannot_vouch_for(self) -> None:
        cases = {
            "non-literal": _synth_install("parser.add_argument(FLAG)"),
            "kwargs": _synth_install('parser.add_argument("--x", **opts)'),
            "starred": _synth_install("parser.add_argument(*names)"),
            "unknown helper": _synth_install("add_more_flags(parser)"),
            "unknown helper on a group": _synth_install(
                "g = parser.add_argument_group()\nadd_more_flags(g)"
            ),
            "chained receiver": _synth_install(
                'parser.add_argument_group().add_argument("--x")'
            ),
            "parents": _synth_install("", "_Parser(parents=[base])"),
            "factory": _synth_install("", "make_parser()"),
            "non-literal action": _synth_install(
                'parser.add_argument("--x", action=ACTION)'
            ),
        }
        for label, source in cases.items():
            with self.subTest(label), self.assertRaises(UnsupportedDeclaration):
                installer_flags(source, _SYNTH_CLI_COMMON)


class FlagTableDriftTestCase(unittest.TestCase):
    """The wrapper's routing table against both real installers."""

    REPO_ROOT = Path(__file__).resolve().parent.parent.parent

    def setUp(self) -> None:
        self.table = wrapper_flag_table(WRAPPER.read_text())
        self.dotfiles = installer_flags(
            (self.REPO_ROOT / "install.py").read_text(),
            (SCRIPTS_DIR / "dotfiles_cli_common.py").read_text(),
        )

    def _agent_toolkit_flags(self) -> dict[str, FlagRecord]:
        try:
            checkout = cli_common.agent_toolkit_checkout(home=conftest._REAL_HOME)
        except cli_common.AgentToolkitNotFoundError:
            if os.environ.get("AGENT_TOOLKIT_PATH"):
                raise
            self.skipTest(
                "no agent-toolkit checkout resolves (set AGENT_TOOLKIT_PATH to "
                "check the agent-toolkit half of the table)"
            )
        return installer_flags(
            (checkout / "install.py").read_text(),
            (checkout / "agent-scripts" / "cli_common.py").read_text(),
        )

    def test_dotfiles_side_of_the_table_is_sound(self) -> None:
        self.assertEqual(drift_problems(self.table, self.dotfiles, None), [])

    def test_table_classifies_every_flag_of_both_installers(self) -> None:
        agent_toolkit = self._agent_toolkit_flags()

        self.assertEqual(drift_problems(self.table, self.dotfiles, agent_toolkit), [])

    def test_an_unclassified_new_flag_fails_the_drift_check(self) -> None:
        """Negative control: the check above is able to fail."""
        agent_toolkit = self._agent_toolkit_flags()
        for side in ("dotfiles", "agent-toolkit", "both"):
            with self.subTest(side):
                probe = FlagRecord("--zzz-probe", frozenset(), takes_value=False)
                df = dict(self.dotfiles)
                at = dict(agent_toolkit)
                if side in ("dotfiles", "both"):
                    df["--zzz-probe"] = probe
                if side in ("agent-toolkit", "both"):
                    at["--zzz-probe"] = probe

                problems = drift_problems(self.table, df, at)

                self.assertIn(
                    "--zzz-probe is declared by an installer but unclassified",
                    problems,
                )

    def test_a_shared_flag_whose_installers_disagree_fails(self) -> None:
        """A shared flag is forwarded to both, so both must accept every form."""
        agent_toolkit = self._agent_toolkit_flags()
        base = agent_toolkit["--force"]
        for record in (
            FlagRecord("--force", base.aliases | {"-f"}, base.takes_value),
            FlagRecord("--force", base.aliases, not base.takes_value),
        ):
            with self.subTest(record=record):
                at = {**agent_toolkit, "--force": record}

                self.assertIn(
                    "--force is shared but its aliases or arity differ "
                    "between installers",
                    drift_problems(self.table, self.dotfiles, at),
                )

    def test_a_routed_flag_that_grows_a_value_or_alias_fails(self) -> None:
        agent_toolkit = self._agent_toolkit_flags()
        for record in (
            FlagRecord("--force-harness", frozenset(), takes_value=True),
            FlagRecord("--force-harness", frozenset({"-F"}), takes_value=False),
        ):
            with self.subTest(record=record):
                at = {**agent_toolkit, "--force-harness": record}

                self.assertNotEqual(drift_problems(self.table, self.dotfiles, at), [])


if __name__ == "__main__":
    unittest.main()
