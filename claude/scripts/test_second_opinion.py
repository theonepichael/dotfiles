#!/usr/bin/env python3
"""Tests for second_opinion.py. Run with: python3 test_second_opinion.py

Uses real subprocesses (via `sys.executable -c ...`) for `_run_command` and
`_kill_active_process` rather than mocking `subprocess.Popen` — those two
functions' entire job is process lifecycle management (timeouts, process
groups, kill signals), which a mock would just assert away instead of
exercising. Higher-level backend logic (`run_opencode`'s event parsing,
`cmd_review`'s fallback loop) mocks `_run_command`/the individual runners
instead, since real `agy`/`opencode` binaries aren't available in CI.
"""

import argparse
import io
import json
import os
import subprocess
import sys
import time
import unittest
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent))
import second_opinion


def ns(**kwargs: object) -> argparse.Namespace:
    kwargs.setdefault("backend", None)
    kwargs.setdefault("focus_file", None)
    return argparse.Namespace(**kwargs)


def py(code: str) -> list[str]:
    """Build a command that runs `code` as a Python one-liner."""
    return [sys.executable, "-c", code]


class ResolvePlanTextTests(unittest.TestCase):
    def test_01_file_path_reads_contents(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            plan_path = Path(tmp) / "plan.md"
            plan_path.write_text("# Plan\n\nSome content.")
            self.assertEqual(
                second_opinion.resolve_plan_text(str(plan_path)),
                "# Plan\n\nSome content.",
            )

    def test_02_inline_text_passed_through(self) -> None:
        self.assertEqual(
            second_opinion.resolve_plan_text("not a real path, just plan text"),
            "not a real path, just plan text",
        )


class SafeGetTests(unittest.TestCase):
    def test_03_nested_success(self) -> None:
        obj = {"a": {"b": {"c": "value"}}}
        self.assertEqual(second_opinion._safe_get(obj, "a", "b", "c"), "value")

    def test_04_missing_key_returns_none(self) -> None:
        self.assertIsNone(second_opinion._safe_get({"a": {}}, "a", "b", "c"))

    def test_05_non_dict_intermediate_returns_none_not_crash(self) -> None:
        # `a.b` is a plain string, not a nested dict — must not raise.
        obj = {"a": {"b": "just a string"}}
        self.assertIsNone(second_opinion._safe_get(obj, "a", "b", "c"))

    def test_06_non_dict_root_returns_none(self) -> None:
        self.assertIsNone(second_opinion._safe_get("not a dict", "a"))

    def test_07_empty_keys_returns_obj_itself(self) -> None:
        obj = {"a": 1}
        self.assertEqual(second_opinion._safe_get(obj), obj)


class OpencodeJsonEventsTests(unittest.TestCase):
    def test_08_valid_lines_parsed(self) -> None:
        raw = '{"type": "text", "part": {"text": "hi"}}\n{"type": "done"}\n'
        events = second_opinion._opencode_json_events(raw)
        self.assertEqual(len(events), 2)
        self.assertEqual(events[0]["type"], "text")

    def test_09_invalid_json_lines_skipped(self) -> None:
        raw = 'not json\n{"type": "text", "part": {"text": "hi"}}\ngarbage{{{\n'
        events = second_opinion._opencode_json_events(raw)
        self.assertEqual(len(events), 1)

    def test_10_blank_lines_skipped(self) -> None:
        raw = '\n  \n{"type": "done"}\n\n'
        events = second_opinion._opencode_json_events(raw)
        self.assertEqual(len(events), 1)

    def test_11_non_dict_json_values_skipped_not_crash(self) -> None:
        # A bare JSON array, number, or string is valid JSON but not an
        # event object — must be filtered, not passed through as `object`.
        raw = '[1, 2, 3]\n"just a string"\n42\n{"type": "done"}\n'
        events = second_opinion._opencode_json_events(raw)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["type"], "done")


class OpencodeTextChunksTests(unittest.TestCase):
    def test_12_normal_chunks_extracted(self) -> None:
        events: list[dict[str, object]] = [
            {"type": "text", "part": {"text": "Hello "}},
            {"type": "text", "part": {"text": "world"}},
            {"type": "done"},
        ]
        self.assertEqual(
            second_opinion._opencode_text_chunks(events), ["Hello ", "world"]
        )

    def test_13_missing_part_skipped_not_crash(self) -> None:
        events: list[dict[str, object]] = [{"type": "text"}]
        self.assertEqual(second_opinion._opencode_text_chunks(events), [])

    def test_14_part_not_a_dict_skipped_not_crash(self) -> None:
        # Regression: `e.get("part", {}).get("text")` used to crash with
        # AttributeError when "part" was present but not a dict.
        events: list[dict[str, object]] = [{"type": "text", "part": "not a dict"}]
        self.assertEqual(second_opinion._opencode_text_chunks(events), [])

    def test_15_non_text_type_ignored(self) -> None:
        events: list[dict[str, object]] = [
            {"type": "other", "part": {"text": "should not appear"}}
        ]
        self.assertEqual(second_opinion._opencode_text_chunks(events), [])

    def test_16_empty_text_not_included(self) -> None:
        events: list[dict[str, object]] = [{"type": "text", "part": {"text": ""}}]
        self.assertEqual(second_opinion._opencode_text_chunks(events), [])


class AvailableBackendsTests(unittest.TestCase):
    def test_17_priority_order_preserved(self) -> None:
        with patch("shutil.which", side_effect=lambda b: f"/usr/bin/{b}"):
            self.assertEqual(
                second_opinion.available_backends(), ["agy", "opencode", "copilot"]
            )

    def test_18_only_installed_backends_returned(self) -> None:
        with patch(
            "shutil.which",
            side_effect=lambda b: "/usr/bin/opencode" if b == "opencode" else None,
        ):
            self.assertEqual(second_opinion.available_backends(), ["opencode"])

    def test_19_none_installed_returns_empty(self) -> None:
        with patch("shutil.which", return_value=None):
            self.assertEqual(second_opinion.available_backends(), [])

    def test_20_resolve_backend_picks_first_priority(self) -> None:
        with patch("shutil.which", side_effect=lambda b: f"/usr/bin/{b}"):
            self.assertEqual(second_opinion.resolve_backend(), "agy")

    def test_21_resolve_backend_none_when_unavailable(self) -> None:
        with patch("shutil.which", return_value=None):
            self.assertIsNone(second_opinion.resolve_backend())


class RunCommandRealSubprocessTests(unittest.TestCase):
    """Exercises `_run_command`/`_kill_active_process` with real child processes."""

    def test_22_successful_command(self) -> None:
        returncode, stdout, stderr = second_opinion._run_command(py("print('hi')"))
        self.assertEqual(returncode, 0)
        self.assertEqual(stdout.strip(), "hi")

    def test_23_nonzero_exit_captured(self) -> None:
        returncode, stdout, stderr = second_opinion._run_command(
            py("import sys; sys.exit(3)")
        )
        self.assertEqual(returncode, 3)

    def test_24_stderr_captured(self) -> None:
        returncode, stdout, stderr = second_opinion._run_command(
            py("import sys; sys.stderr.write('oops')")
        )
        self.assertIn("oops", stderr)

    def test_25_missing_executable_wrapped_as_backend_error(self) -> None:
        # Regression: this used to propagate a raw OSError/FileNotFoundError
        # instead of BackendError, crashing cmd_review's fallback loop.
        with self.assertRaises(second_opinion.BackendError) as cm:
            second_opinion._run_command(["/no/such/executable-xyz"])
        self.assertIn("failed to start", str(cm.exception))

    def test_26_missing_executable_does_not_leave_active_process_set(self) -> None:
        with self.assertRaises(second_opinion.BackendError):
            second_opinion._run_command(["/no/such/executable-xyz"])
        self.assertIsNone(second_opinion._active_process)

    def test_27_active_process_cleared_after_success(self) -> None:
        second_opinion._run_command(py("print('hi')"))
        self.assertIsNone(second_opinion._active_process)

    def test_28_timeout_kills_process_and_raises(self) -> None:
        with patch.object(second_opinion, "BACKEND_TIMEOUT_SECONDS", 0.3):
            start = time.monotonic()
            with self.assertRaises(second_opinion.BackendError) as cm:
                second_opinion._run_command(py("import time; time.sleep(30)"))
            elapsed = time.monotonic() - start
        self.assertIn("timed out after 0.3s", str(cm.exception))
        # killed promptly, not left running the full 30s sleep
        self.assertLess(elapsed, 5)
        self.assertIsNone(second_opinion._active_process)

    def test_29_kill_active_process_terminates_real_child(self) -> None:
        proc = subprocess.Popen(
            py("import time; time.sleep(30)"),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
        )
        second_opinion._active_process = proc
        try:
            second_opinion._kill_active_process()
            proc.wait(timeout=5)
            self.assertIsNotNone(proc.poll())
        finally:
            second_opinion._active_process = None
            if proc.poll() is None:
                proc.kill()
                proc.wait()
            if proc.stdout:
                proc.stdout.close()
            if proc.stderr:
                proc.stderr.close()

    def test_30_kill_active_process_noop_when_none(self) -> None:
        second_opinion._active_process = None
        second_opinion._kill_active_process()  # must not raise

    def test_31_kill_active_process_noop_when_already_exited(self) -> None:
        proc = subprocess.Popen(py("pass"), text=True)
        proc.wait()
        second_opinion._active_process = proc
        try:
            second_opinion._kill_active_process()  # must not raise
        finally:
            second_opinion._active_process = None


class RunBackendCommandTests(unittest.TestCase):
    def test_32_success_returns_stripped_stdout(self) -> None:
        result = second_opinion.run_backend_command(py("print('  critique text  ')"))
        self.assertEqual(result, "critique text")

    def test_33_nonzero_exit_raises_with_stderr(self) -> None:
        with self.assertRaises(second_opinion.BackendError) as cm:
            second_opinion.run_backend_command(
                py("import sys; sys.stderr.write('boom'); sys.exit(1)")
            )
        self.assertIn("exited 1", str(cm.exception))
        self.assertIn("boom", str(cm.exception))

    def test_34_exit_zero_empty_stdout_is_still_a_failure(self) -> None:
        with self.assertRaises(second_opinion.BackendError) as cm:
            second_opinion.run_backend_command(py("pass"))
        self.assertIn("produced no output", str(cm.exception))

    def test_35_run_agy_builds_expected_argv(self) -> None:
        with patch.object(
            second_opinion, "run_backend_command", return_value="critique"
        ) as mock_run:
            result = second_opinion.run_agy("my prompt")
        self.assertEqual(result, "critique")
        mock_run.assert_called_once_with(
            ["agy", "-p", "my prompt", "--model", "Gemini 3.1 Pro (High)"]
        )

    def test_36_run_copilot_builds_expected_argv(self) -> None:
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("SECOND_OPINION_COPILOT_MODEL", None)
            with patch.object(
                second_opinion, "run_backend_command", return_value="critique"
            ) as mock_run:
                result = second_opinion.run_copilot("my prompt")
        self.assertEqual(result, "critique")
        mock_run.assert_called_once_with(["copilot", "-p", "my prompt", "--silent"])

    def test_49_run_copilot_appends_model_flag_when_env_var_set(self) -> None:
        with patch.dict(
            os.environ, {"SECOND_OPINION_COPILOT_MODEL": "claude-sonnet-4.6"}
        ):
            with patch.object(
                second_opinion, "run_backend_command", return_value="critique"
            ) as mock_run:
                result = second_opinion.run_copilot("my prompt")
        self.assertEqual(result, "critique")
        mock_run.assert_called_once_with(
            ["copilot", "-p", "my prompt", "--silent", "--model", "claude-sonnet-4.6"]
        )

    def test_50_run_copilot_ignores_empty_string_env_var(self) -> None:
        with patch.dict(os.environ, {"SECOND_OPINION_COPILOT_MODEL": ""}):
            with patch.object(
                second_opinion, "run_backend_command", return_value="critique"
            ) as mock_run:
                second_opinion.run_copilot("my prompt")
        mock_run.assert_called_once_with(["copilot", "-p", "my prompt", "--silent"])


class BackendLabelTests(unittest.TestCase):
    def test_51_non_copilot_labels_unaffected_by_env_var(self) -> None:
        with patch.dict(
            os.environ, {"SECOND_OPINION_COPILOT_MODEL": "claude-sonnet-4.6"}
        ):
            self.assertEqual(
                second_opinion.backend_label("agy"),
                second_opinion.BACKEND_LABELS["agy"],
            )
            self.assertEqual(
                second_opinion.backend_label("opencode"),
                second_opinion.BACKEND_LABELS["opencode"],
            )

    def test_52_copilot_label_unchanged_when_env_var_unset(self) -> None:
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("SECOND_OPINION_COPILOT_MODEL", None)
            self.assertEqual(
                second_opinion.backend_label("copilot"),
                second_opinion.BACKEND_LABELS["copilot"],
            )

    def test_53_copilot_label_includes_model_when_env_var_set(self) -> None:
        with patch.dict(
            os.environ, {"SECOND_OPINION_COPILOT_MODEL": "claude-sonnet-4.6"}
        ):
            self.assertEqual(
                second_opinion.backend_label("copilot"),
                f"{second_opinion.BACKEND_LABELS['copilot']} (claude-sonnet-4.6)",
            )


class RunOpencodeTests(unittest.TestCase):
    @contextmanager
    def _run_command_returning(
        self, stdout: str, stderr: str = "", returncode: int = 0
    ) -> Iterator[None]:
        with patch.object(
            second_opinion, "_run_command", return_value=(returncode, stdout, stderr)
        ):
            yield

    def test_36_text_chunks_concatenated(self) -> None:
        stdout = (
            json.dumps({"type": "text", "part": {"text": "Hello "}})
            + "\n"
            + json.dumps({"type": "text", "part": {"text": "world"}})
            + "\n"
        )
        with self._run_command_returning(stdout):
            self.assertEqual(second_opinion.run_opencode("prompt"), "Hello world")

    def test_37_structured_error_event_raises_with_message(self) -> None:
        stdout = (
            json.dumps(
                {"type": "error", "error": {"data": {"message": "agent crashed"}}}
            )
            + "\n"
        )
        with self._run_command_returning(stdout):
            with self.assertRaises(second_opinion.BackendError) as cm:
                second_opinion.run_opencode("prompt")
        self.assertIn("agent crashed", str(cm.exception))

    def test_38_error_field_as_plain_string_does_not_crash(self) -> None:
        # Regression: `e.get("error", {}).get("data", {})` used to crash
        # with AttributeError when "error" was a plain string rather than
        # a nested object.
        stdout = json.dumps({"type": "error", "error": "flat string error"}) + "\n"
        with self._run_command_returning(stdout):
            with self.assertRaises(second_opinion.BackendError) as cm:
                second_opinion.run_opencode("prompt")
        self.assertIn("flat string error", str(cm.exception))

    def test_39_no_text_no_error_falls_back_to_raw_output(self) -> None:
        stdout = json.dumps({"type": "other"}) + "\n"
        with self._run_command_returning(stdout, stderr="some stderr detail"):
            with self.assertRaises(second_opinion.BackendError) as cm:
                second_opinion.run_opencode("prompt")
        self.assertIn("some stderr detail", str(cm.exception))

    def test_40_completely_unparseable_output_falls_back_to_stdout_snippet(
        self,
    ) -> None:
        with self._run_command_returning("not json at all", stderr=""):
            with self.assertRaises(second_opinion.BackendError) as cm:
                second_opinion.run_opencode("prompt")
        self.assertIn("not json at all", str(cm.exception))


class CmdDetectTests(unittest.TestCase):
    def test_41_detect_prints_availability_json(self) -> None:
        out = io.StringIO()
        with patch(
            "shutil.which", side_effect=lambda b: "/usr/bin/agy" if b == "agy" else None
        ):
            with patch("sys.stdout", out):
                second_opinion.cmd_detect(ns())
        self.assertEqual(
            json.loads(out.getvalue()),
            {"agy": True, "opencode": False, "copilot": False},
        )


class CmdReviewTests(unittest.TestCase):
    def test_42_forced_backend_not_on_path_dies(self) -> None:
        err = io.StringIO()
        with patch("shutil.which", return_value=None):
            with self.assertRaises(SystemExit) as cm:
                with patch("sys.stderr", err):
                    second_opinion.cmd_review(ns(plan="text", backend="agy"))
        self.assertEqual(cm.exception.code, 1)
        self.assertIn("agy not found on PATH", err.getvalue())

    def test_43_no_backend_available_dies(self) -> None:
        err = io.StringIO()
        with patch("shutil.which", return_value=None):
            with self.assertRaises(SystemExit) as cm:
                with patch("sys.stderr", err):
                    second_opinion.cmd_review(ns(plan="text"))
        self.assertEqual(cm.exception.code, 1)
        self.assertIn("no backend available", err.getvalue())

    def test_44_first_backend_fails_second_succeeds(self) -> None:
        out, err = io.StringIO(), io.StringIO()
        with patch("shutil.which", side_effect=lambda b: f"/usr/bin/{b}"):
            with patch.object(
                second_opinion,
                "BACKEND_RUNNERS",
                {
                    "agy": lambda p: (_ for _ in ()).throw(
                        second_opinion.BackendError("agy broke")
                    ),
                    "opencode": lambda p: "opencode's critique",
                },
            ):
                with patch("sys.stdout", out), patch("sys.stderr", err):
                    second_opinion.cmd_review(ns(plan="my plan"))
        self.assertIn("opencode's critique", out.getvalue())
        self.assertIn("agy broke", err.getvalue())

    def test_45_all_backends_fail_dies_with_combined_message(self) -> None:
        err = io.StringIO()
        with patch("shutil.which", side_effect=lambda b: f"/usr/bin/{b}"):
            with patch.object(
                second_opinion,
                "BACKEND_RUNNERS",
                {
                    "agy": lambda p: (_ for _ in ()).throw(
                        second_opinion.BackendError("agy broke")
                    ),
                    "opencode": lambda p: (_ for _ in ()).throw(
                        second_opinion.BackendError("opencode broke")
                    ),
                    "copilot": lambda p: (_ for _ in ()).throw(
                        second_opinion.BackendError("copilot broke")
                    ),
                },
            ):
                with self.assertRaises(SystemExit) as cm:
                    with patch("sys.stderr", err):
                        second_opinion.cmd_review(ns(plan="my plan"))
        self.assertEqual(cm.exception.code, 1)
        self.assertIn("agy broke", err.getvalue())
        self.assertIn("opencode broke", err.getvalue())
        self.assertIn("copilot broke", err.getvalue())

    def test_46_plan_file_contents_used_not_the_path(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            plan_path = Path(tmp) / "plan.md"
            plan_path.write_text("# The actual plan\ndetails here")

            captured_prompt = {}

            def fake_runner(prompt: str) -> str:
                captured_prompt["value"] = prompt
                return "critique"

            out = io.StringIO()
            with patch("shutil.which", side_effect=lambda b: f"/usr/bin/{b}"):
                with patch.object(
                    second_opinion,
                    "BACKEND_RUNNERS",
                    {"agy": fake_runner, "opencode": fake_runner},
                ):
                    with patch("sys.stdout", out):
                        second_opinion.cmd_review(ns(plan=str(plan_path)))
        self.assertIn("# The actual plan", captured_prompt["value"])
        self.assertIn("details here", captured_prompt["value"])


class DieTests(unittest.TestCase):
    def test_47_die_prints_prefixed_message_and_exits_1(self) -> None:
        err = io.StringIO()
        with self.assertRaises(SystemExit) as cm:
            with patch("sys.stderr", err):
                second_opinion.die("something went wrong")
        self.assertEqual(cm.exception.code, 1)
        self.assertEqual(
            err.getvalue().strip(), "[second_opinion] something went wrong"
        )


class HandleTerminationTests(unittest.TestCase):
    def test_48_kills_active_process_and_exits_with_128_plus_signum(self) -> None:
        with patch.object(second_opinion, "_kill_active_process") as mock_kill:
            with self.assertRaises(SystemExit) as cm:
                second_opinion._handle_termination(15, None)
        mock_kill.assert_called_once()
        self.assertEqual(cm.exception.code, 128 + 15)


if __name__ == "__main__":
    unittest.main(verbosity=1)
