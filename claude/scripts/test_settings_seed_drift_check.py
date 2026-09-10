#!/usr/bin/env python3
"""Tests for settings_seed_drift_check.py. Run with: python3 test_settings_seed_drift_check.py

Covers the VS-Code-only drift check/sync/push surface.
"""

import io
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent))
import settings_seed_drift_check as ssdc

REPO_ROOT = Path(__file__).resolve().parent.parent.parent


class SettingsSeedDriftCheckTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.mkdtemp()
        self.home = Path(self.tmpdir) / "home"
        self.home.mkdir()
        self.dotfiles = Path(self.tmpdir) / "dotfiles"

        self._patches = [
            patch.object(ssdc, "HOME", self.home),
            patch.object(ssdc, "DOTFILES", self.dotfiles),
            # This machine is itself a live instance of the WSL VS Code
            # symlink bug (see install.py's _vscode_wsl_user_dir), so the
            # real function resolves here — default it off so unrelated
            # tests don't pick up real machine state; vscode-specific
            # tests override it explicitly.
            patch.object(ssdc, "_vscode_wsl_user_dir", lambda: None),
        ]
        for p in self._patches:
            p.start()

    def tearDown(self) -> None:
        for p in self._patches:
            p.stop()
        shutil.rmtree(self.tmpdir)

    # ── setup helpers ──────────────────────────────────────────────────

    def vscode_user_dir(self) -> Path:
        d = self.home / "winappdata"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def write_vscode_seed(self, name: str, text: str) -> None:
        d = self.dotfiles / "vscode"
        d.mkdir(parents=True, exist_ok=True)
        (d / name).write_text(text)

    def write_live_vscode(self, name: str, text: str) -> None:
        (self.vscode_user_dir() / name).write_text(text)

    def run_check(self) -> tuple[str, int]:
        out = io.StringIO()
        err = io.StringIO()
        with patch("sys.stdout", out), patch("sys.stderr", err):
            code = ssdc.cmd_check()
        return out.getvalue().strip(), code

    def run_sync(self, root: Path | None = None) -> tuple[str, int]:
        out = io.StringIO()
        err = io.StringIO()
        target_root = root if root is not None else self.dotfiles
        with patch("sys.stdout", out), patch("sys.stderr", err):
            code = ssdc.cmd_sync_to_seed(target_root)
        return out.getvalue().strip(), code

    def run_push_vscode(
        self, root: Path | None = None, *, yes: bool = False
    ) -> tuple[str, int]:
        out = io.StringIO()
        err = io.StringIO()
        target_root = root if root is not None else self.dotfiles
        with patch("sys.stdout", out), patch("sys.stderr", err):
            code = ssdc.cmd_push_vscode(target_root, yes=yes)
        return out.getvalue().strip(), code

    # ── cmd_sync_to_seed: reverse direction (live -> seed) ──────────────

    def test_sync_rejects_missing_dotfiles_root(self) -> None:
        nonexistent_root = Path(self.tmpdir) / "does-not-exist"
        out, code = self.run_sync(root=nonexistent_root)
        self.assertEqual(code, 1)
        self.assertIn("does not exist", out)
        self.assertFalse(nonexistent_root.exists())

    def test_main_dispatches_sync_to_seed_via_argparse(self) -> None:
        custom_root = Path(self.tmpdir) / "argparse-root"
        with patch.object(ssdc, "cmd_sync_to_seed") as mock_cmd:
            mock_cmd.return_value = 0
            ssdc.main(["sync-to-seed", "--dotfiles-root", str(custom_root)])
        mock_cmd.assert_called_once_with(custom_root, quiet=False)

    def test_verbosity_flags_parse_after_every_leaf_subcommand(self) -> None:
        # A leaf added later without an entry here silently loses coverage.
        custom_root = Path(self.tmpdir) / "argparse-root-quiet"
        cases = {
            "check": (ssdc, "cmd_check", []),
            "sync-to-seed": (
                ssdc,
                "cmd_sync_to_seed",
                ["--dotfiles-root", str(custom_root)],
            ),
            "push-vscode": (
                ssdc,
                "cmd_push_vscode",
                ["--dotfiles-root", str(custom_root)],
            ),
        }
        for cmd, (module, target, extra) in cases.items():
            with patch.object(module, target) as mock_cmd:
                mock_cmd.return_value = 0
                ssdc.main([cmd, *extra, "-q"])
            self.assertEqual(mock_cmd.call_args.kwargs.get("quiet"), True)

    # ── _try_parse_json ───────────────────────────────────────────────────

    def test_try_parse_json_returns_parsed_value(self) -> None:
        path = Path(self.tmpdir) / "ok.json"
        path.write_text('{"a": 1}')
        self.assertEqual(ssdc._try_parse_json(path), {"a": 1})

    def test_try_parse_json_returns_none_on_missing_file(self) -> None:
        path = Path(self.tmpdir) / "missing.json"
        self.assertIsNone(ssdc._try_parse_json(path))

    def test_try_parse_json_returns_none_on_parse_failure_never_raises(self) -> None:
        path = Path(self.tmpdir) / "bad.json"
        path.write_text("// a comment\n{not valid json}")
        self.assertIsNone(ssdc._try_parse_json(path))

    # ── vscode_drift ─────────────────────────────────────────────────────

    def test_vscode_drift_identical_content_is_no_drift(self) -> None:
        seed = Path(self.tmpdir) / "seed.json"
        live = Path(self.tmpdir) / "live.json"
        seed.write_text('{"a": 1}')
        live.write_text('{"a": 1}')
        self.assertEqual(ssdc.vscode_drift(seed, live), "")

    def test_vscode_drift_dict_key_diff(self) -> None:
        seed = Path(self.tmpdir) / "seed.json"
        live = Path(self.tmpdir) / "live.json"
        seed.write_text(json.dumps({"a": 1, "b": 2}))
        live.write_text(json.dumps({"a": 1, "b": 3}))
        self.assertEqual(ssdc.vscode_drift(seed, live), "b")

    def test_vscode_drift_list_length_diff(self) -> None:
        seed = Path(self.tmpdir) / "seed.json"
        live = Path(self.tmpdir) / "live.json"
        seed.write_text(json.dumps([{"key": "a"}]))
        live.write_text(json.dumps([{"key": "a"}, {"key": "b"}]))
        self.assertEqual(ssdc.vscode_drift(seed, live), "2 bindings live vs 1 in seed")

    def test_vscode_drift_list_same_length_content_differs(self) -> None:
        seed = Path(self.tmpdir) / "seed.json"
        live = Path(self.tmpdir) / "live.json"
        seed.write_text(json.dumps([{"key": "a"}]))
        live.write_text(json.dumps([{"key": "b"}]))
        self.assertEqual(
            ssdc.vscode_drift(seed, live), "binding definitions differ (1 bindings)"
        )

    def test_vscode_drift_jsonc_comment_still_reports_generic_fallback(self) -> None:
        """A live file with `//` comments fails to parse as JSON, but the
        raw-text compare must still catch the drift rather than silently
        returning "" just because the parse step failed."""
        seed = Path(self.tmpdir) / "seed.json"
        live = Path(self.tmpdir) / "live.json"
        seed.write_text(json.dumps({"a": 1}))
        live.write_text('// a comment\n{"a": 1, "b": 2}')
        self.assertEqual(
            ssdc.vscode_drift(seed, live), "content differs from the repo copy"
        )

    def test_vscode_drift_missing_file_is_nothing_to_compare(self) -> None:
        seed = Path(self.tmpdir) / "seed.json"
        live = Path(self.tmpdir) / "live.json"
        seed.write_text('{"a": 1}')
        self.assertEqual(ssdc.vscode_drift(seed, live), "")

    # ── vscode_seed_path ─────────────────────────────────────────────────

    def test_vscode_seed_path_no_profile_split(self) -> None:
        self.assertEqual(
            ssdc.vscode_seed_path("settings.json"),
            self.dotfiles / "vscode" / "settings.json",
        )

    def test_vscode_seed_path_accepts_root_override(self) -> None:
        custom_root = Path(self.tmpdir) / "alt3"
        self.assertEqual(
            ssdc.vscode_seed_path("keybindings.json", custom_root),
            custom_root / "vscode" / "keybindings.json",
        )

    # ── cmd_check: VS Code block ─────────────────────────────────────────

    def test_check_vscode_silent_when_user_dir_none(self) -> None:
        self.write_vscode_seed("settings.json", '{"a": 1}')
        self.write_live_vscode("settings.json", '{"a": 2}')
        with patch.object(ssdc, "_vscode_wsl_user_dir", lambda: None):
            out, code = self.run_check()
        self.assertEqual(out, "")
        self.assertEqual(code, 0)

    def test_check_vscode_silent_when_no_drift(self) -> None:
        self.write_vscode_seed("settings.json", '{"a": 1}\n')
        self.write_live_vscode("settings.json", '{"a": 1}\n')
        with patch.object(ssdc, "_vscode_wsl_user_dir", lambda: self.vscode_user_dir()):
            out, code = self.run_check()
        self.assertEqual(out, "")
        self.assertEqual(code, 0)

    def test_check_vscode_reports_drift_with_direction_neutral_wording(self) -> None:
        """`check` can't know which side of a whole-file VS Code diff is
        "correct", so it must name both directions (push-vscode and
        sync-to-seed) rather than recommending one by default — and must
        never recommend `fix`, which doesn't cover VS Code at all."""
        self.write_vscode_seed("settings.json", '{"a": 1}')
        self.write_live_vscode("settings.json", '{"a": 2}')
        with patch.object(ssdc, "_vscode_wsl_user_dir", lambda: self.vscode_user_dir()):
            out, code = self.run_check()
        self.assertIn("settings.json", out)
        self.assertIn("push-vscode", out)
        self.assertIn("sync-to-seed", out)
        self.assertNotRegex(out, r"run `[^`]*\bfix`")
        self.assertEqual(code, 0)

    # ── cmd_sync_to_seed: VS Code block ──────────────────────────────────

    def test_sync_vscode_writes_seed_from_live_with_backup(self) -> None:
        self.write_vscode_seed("settings.json", '{"a": 1}\n')
        self.write_live_vscode("settings.json", '{"a": 2}\n')
        with patch.object(ssdc, "_vscode_wsl_user_dir", lambda: self.vscode_user_dir()):
            out, code = self.run_sync()
        self.assertEqual(code, 0)
        self.assertIn("synced", out)
        self.assertEqual(
            (self.dotfiles / "vscode" / "settings.json").read_text(), '{"a": 2}\n'
        )
        backups = list((self.dotfiles / "vscode").glob("settings.json.bak.*"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_text(), '{"a": 1}\n')

    def test_sync_vscode_jsonc_comments_succeed_where_json_parse_would_fail(
        self,
    ) -> None:
        self.write_vscode_seed("settings.json", '{"a": 1}\n')
        live_text = '// user comment\n{"a": 1, "b": 2}\n'
        self.write_live_vscode("settings.json", live_text)
        with patch.object(ssdc, "_vscode_wsl_user_dir", lambda: self.vscode_user_dir()):
            _out, code = self.run_sync()
        self.assertEqual(code, 0)
        self.assertEqual(
            (self.dotfiles / "vscode" / "settings.json").read_text(), live_text
        )

    def test_sync_vscode_noop_when_user_dir_none(self) -> None:
        self.write_vscode_seed("settings.json", '{"a": 1}\n')
        with patch.object(ssdc, "_vscode_wsl_user_dir", lambda: None):
            out, code = self.run_sync()
        self.assertEqual(out, "")
        self.assertEqual(code, 0)
        self.assertEqual(
            (self.dotfiles / "vscode" / "settings.json").read_text(), '{"a": 1}\n'
        )

    def test_sync_vscode_noop_when_live_missing(self) -> None:
        self.write_vscode_seed("settings.json", '{"a": 1}\n')
        with patch.object(ssdc, "_vscode_wsl_user_dir", lambda: self.vscode_user_dir()):
            out, code = self.run_sync()
        self.assertEqual(out, "")
        self.assertEqual(code, 0)
        self.assertEqual(
            (self.dotfiles / "vscode" / "settings.json").read_text(), '{"a": 1}\n'
        )

    def test_sync_vscode_respects_dotfiles_root_override(self) -> None:
        custom_root = Path(self.tmpdir) / "vscode-alt-root"
        (custom_root / "vscode").mkdir(parents=True)
        (custom_root / "vscode" / "settings.json").write_text('{"a": 1}\n')
        self.write_live_vscode("settings.json", '{"a": 2}\n')
        with patch.object(ssdc, "_vscode_wsl_user_dir", lambda: self.vscode_user_dir()):
            _out, code = self.run_sync(root=custom_root)
        self.assertEqual(code, 0)
        self.assertEqual(
            (custom_root / "vscode" / "settings.json").read_text(), '{"a": 2}\n'
        )
        self.assertFalse((self.dotfiles / "vscode" / "settings.json").exists())

    # ── _vscode_process_running ─────────────────────────────────────────

    def test_vscode_process_running_true_on_match(self) -> None:
        # Non-English sample string confirms the regex matches the image
        # name directly, not the (per-locale) "no tasks found" phrasing a
        # substring check would have relied on.
        stdout = (
            "イメージ名                       PID セッション名\n"
            "========================= ======== ================\n"
            "Code.exe                     1234 Console\n"
        )
        with patch(
            "subprocess.run",
            return_value=subprocess.CompletedProcess(
                args=[], returncode=0, stdout=stdout, stderr=""
            ),
        ):
            self.assertTrue(ssdc._vscode_process_running())

    def test_vscode_process_running_false_on_no_match(self) -> None:
        stdout = "INFO: No tasks are running which match the specified criteria.\n"
        with patch(
            "subprocess.run",
            return_value=subprocess.CompletedProcess(
                args=[], returncode=0, stdout=stdout, stderr=""
            ),
        ):
            self.assertFalse(ssdc._vscode_process_running())

    def test_vscode_process_running_none_on_timeout(self) -> None:
        with patch(
            "subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd="tasklist.exe", timeout=5),
        ):
            self.assertIsNone(ssdc._vscode_process_running())

    def test_vscode_process_running_none_when_tasklist_missing(self) -> None:
        with patch("subprocess.run", side_effect=FileNotFoundError()):
            self.assertIsNone(ssdc._vscode_process_running())

    # ── _push_vscode_to_live ────────────────────────────────────────────

    def test_push_vscode_to_live_identical_returns_none(self) -> None:
        seed = Path(self.tmpdir) / "seed.json"
        live = Path(self.tmpdir) / "live.json"
        seed.write_text('{"a": 1}\n')
        live.write_text('{"a": 1}\n')
        self.assertIsNone(ssdc._push_vscode_to_live(seed, live))

    def test_push_vscode_to_live_differing_returns_diff(self) -> None:
        seed = Path(self.tmpdir) / "seed.json"
        live = Path(self.tmpdir) / "live.json"
        seed.write_text('{"a": 2}\n')
        live.write_text('{"a": 1}\n')
        result = ssdc._push_vscode_to_live(seed, live)
        self.assertIsNotNone(result)
        live_exists, diff = result  # type: ignore[misc]
        self.assertTrue(live_exists)
        self.assertIn('"a": 1', diff)
        self.assertIn('"a": 2', diff)

    def test_push_vscode_to_live_missing_live_shows_creation_no_bak_implied(
        self,
    ) -> None:
        seed = Path(self.tmpdir) / "seed.json"
        live = Path(self.tmpdir) / "live-missing.json"
        seed.write_text('{"a": 1}\n')
        result = ssdc._push_vscode_to_live(seed, live)
        self.assertIsNotNone(result)
        live_exists, diff = result  # type: ignore[misc]
        self.assertFalse(live_exists)
        self.assertIn('"a": 1', diff)

    # ── cmd_push_vscode ──────────────────────────────────────────────────

    def test_push_vscode_wsl_dir_none_is_explicit_error_not_silent(self) -> None:
        with patch.object(ssdc, "_vscode_wsl_user_dir", lambda: None):
            out, code = self.run_push_vscode()
        self.assertEqual(code, 1)
        self.assertIn("WSL", out)

    def test_push_vscode_both_identical_is_noop_no_process_check_no_prompt(
        self,
    ) -> None:
        self.write_vscode_seed("settings.json", '{"a": 1}\n')
        self.write_live_vscode("settings.json", '{"a": 1}\n')
        self.write_vscode_seed("keybindings.json", "[]\n")
        self.write_live_vscode("keybindings.json", "[]\n")
        with (
            patch.object(ssdc, "_vscode_wsl_user_dir", lambda: self.vscode_user_dir()),
            patch.object(ssdc, "_vscode_process_running") as mock_proc,
            patch("builtins.input") as mock_input,
        ):
            out, code = self.run_push_vscode()
        self.assertEqual(code, 0)
        self.assertIn("nothing to push", out)
        mock_proc.assert_not_called()
        mock_input.assert_not_called()

    def test_push_vscode_confirmed_and_process_not_running_writes_with_backup(
        self,
    ) -> None:
        self.write_vscode_seed("settings.json", '{"a": 2}\n')
        self.write_live_vscode("settings.json", '{"a": 1}\n')
        self.write_vscode_seed("keybindings.json", "[]\n")
        self.write_live_vscode("keybindings.json", "[]\n")
        with (
            patch.object(ssdc, "_vscode_wsl_user_dir", lambda: self.vscode_user_dir()),
            patch.object(ssdc, "_vscode_process_running", lambda: False),
            patch("builtins.input", return_value="y"),
            patch("sys.stdin.isatty", return_value=True),
        ):
            out, code = self.run_push_vscode()
        self.assertEqual(code, 0)
        live_text = (self.vscode_user_dir() / "settings.json").read_text()
        self.assertEqual(live_text, '{"a": 2}\n')
        backups = list(self.vscode_user_dir().glob("settings.json.bak.*"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_text(), '{"a": 1}\n')

        # second run is a no-op — seed and live now match
        with (
            patch.object(ssdc, "_vscode_wsl_user_dir", lambda: self.vscode_user_dir()),
            patch.object(ssdc, "_vscode_process_running") as mock_proc,
            patch("builtins.input") as mock_input,
        ):
            out2, code2 = self.run_push_vscode()
        self.assertEqual(code2, 0)
        self.assertIn("nothing to push", out2)
        mock_proc.assert_not_called()
        mock_input.assert_not_called()

    def test_push_vscode_process_running_refuses_writes_neither_file(self) -> None:
        self.write_vscode_seed("settings.json", '{"a": 2}\n')
        self.write_live_vscode("settings.json", '{"a": 1}\n')
        self.write_vscode_seed("keybindings.json", '[{"key": "new"}]\n')
        self.write_live_vscode("keybindings.json", '[{"key": "old"}]\n')
        with (
            patch.object(ssdc, "_vscode_wsl_user_dir", lambda: self.vscode_user_dir()),
            patch.object(ssdc, "_vscode_process_running", lambda: True),
            patch("builtins.input", return_value="y"),
            patch("sys.stdin.isatty", return_value=True),
        ):
            out, code = self.run_push_vscode()
        self.assertEqual(code, 1)
        self.assertIn("close it first", out)
        # neither file written — confirms no-partial-write property
        self.assertEqual(
            (self.vscode_user_dir() / "settings.json").read_text(), '{"a": 1}\n'
        )
        self.assertEqual(
            (self.vscode_user_dir() / "keybindings.json").read_text(),
            '[{"key": "old"}]\n',
        )
        self.assertEqual(
            list(self.vscode_user_dir().glob("*.bak.*")),
            [],
        )

    def test_push_vscode_process_running_unknown_refuses_writes_neither_file(
        self,
    ) -> None:
        # Tightened behavior: an undetermined process check (None) now
        # refuses the push too, not just a confirmed-running (True) check —
        # unlike the old collapsed-bool contract, where a timeout/missing
        # tasklist.exe silently permitted the write.
        self.write_vscode_seed("settings.json", '{"a": 2}\n')
        self.write_live_vscode("settings.json", '{"a": 1}\n')
        self.write_vscode_seed("keybindings.json", "[]\n")
        self.write_live_vscode("keybindings.json", "[]\n")
        with (
            patch.object(ssdc, "_vscode_wsl_user_dir", lambda: self.vscode_user_dir()),
            patch.object(ssdc, "_vscode_process_running", lambda: None),
            patch("builtins.input", return_value="y"),
            patch("sys.stdin.isatty", return_value=True),
        ):
            out, code = self.run_push_vscode()
        self.assertEqual(code, 1)
        self.assertIn("close it first", out)
        self.assertEqual(
            (self.vscode_user_dir() / "settings.json").read_text(), '{"a": 1}\n'
        )

    def test_push_vscode_noninteractive_stdin_without_yes_aborts_nothing_written(
        self,
    ) -> None:
        self.write_vscode_seed("settings.json", '{"a": 2}\n')
        self.write_live_vscode("settings.json", '{"a": 1}\n')
        self.write_vscode_seed("keybindings.json", "[]\n")
        self.write_live_vscode("keybindings.json", "[]\n")
        with (
            patch.object(ssdc, "_vscode_wsl_user_dir", lambda: self.vscode_user_dir()),
            patch("sys.stdin.isatty", return_value=False),
        ):
            out, code = self.run_push_vscode()
        self.assertEqual(code, 1)
        self.assertIn("stdin is not interactive", out)
        self.assertEqual(
            (self.vscode_user_dir() / "settings.json").read_text(), '{"a": 1}\n'
        )

    def test_push_vscode_noninteractive_stdin_with_yes_proceeds_without_prompt(
        self,
    ) -> None:
        self.write_vscode_seed("settings.json", '{"a": 2}\n')
        self.write_live_vscode("settings.json", '{"a": 1}\n')
        self.write_vscode_seed("keybindings.json", "[]\n")
        self.write_live_vscode("keybindings.json", "[]\n")
        with (
            patch.object(ssdc, "_vscode_wsl_user_dir", lambda: self.vscode_user_dir()),
            patch.object(ssdc, "_vscode_process_running", lambda: False),
            patch("sys.stdin.isatty", return_value=False),
            patch("builtins.input") as mock_input,
        ):
            out, code = self.run_push_vscode(yes=True)
        self.assertEqual(code, 0)
        mock_input.assert_not_called()
        self.assertEqual(
            (self.vscode_user_dir() / "settings.json").read_text(), '{"a": 2}\n'
        )


class SettingsSeedDriftCheckDefaultRootTestCase(unittest.TestCase):
    def test_dotfiles_default_derives_from_script_location_not_home(self) -> None:
        # Regression: DOTFILES used to be hardcoded to ~/dotfiles, which
        # breaks the moment the checkout has any other name (e.g. a
        # worktree, or the ~/.dotfiles rename a GitHub-blocked work
        # machine's zip-based transfer workflow produces). It must be
        # derived from the script's own location instead, same convention
        # install.py already uses.
        self.assertEqual(ssdc.DOTFILES, REPO_ROOT)


if __name__ == "__main__":
    unittest.main()
