#!/usr/bin/env python3
"""Tests for llm_backends.py. Run with: python3 test_llm_backends.py

Uses real subprocesses (via `sys.executable -c ...`) for `_run_command`/
`_kill_active_process` rather than mocking `subprocess.Popen` — those two
functions' entire job is process lifecycle management (timeouts, process
groups, kill signals), which a mock would just assert away instead of
exercising. Higher-level logic (argv-building, opencode's event parsing)
mocks `_run_command`/`run_backend_command` instead, since real
`agy`/`opencode`/`copilot` binaries aren't available in CI.
"""

import json
import subprocess
import sys
import time
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent))
import llm_backends


def py(code: str) -> list[str]:
    """Build a command that runs `code` as a Python one-liner."""
    return [sys.executable, "-c", code]


class SafeGetTests(unittest.TestCase):
    def test_01_nested_success(self) -> None:
        obj = {"a": {"b": {"c": "value"}}}
        self.assertEqual(llm_backends._safe_get(obj, "a", "b", "c"), "value")

    def test_02_missing_key_returns_none(self) -> None:
        self.assertIsNone(llm_backends._safe_get({"a": {}}, "a", "b", "c"))

    def test_03_non_dict_intermediate_returns_none_not_crash(self) -> None:
        obj = {"a": {"b": "just a string"}}
        self.assertIsNone(llm_backends._safe_get(obj, "a", "b", "c"))

    def test_04_non_dict_root_returns_none(self) -> None:
        self.assertIsNone(llm_backends._safe_get("not a dict", "a"))

    def test_05_empty_keys_returns_obj_itself(self) -> None:
        obj = {"a": 1}
        self.assertEqual(llm_backends._safe_get(obj), obj)


class OpencodeJsonEventsTests(unittest.TestCase):
    def test_06_valid_lines_parsed(self) -> None:
        raw = '{"type": "text", "part": {"text": "hi"}}\n{"type": "done"}\n'
        events = llm_backends._opencode_json_events(raw)
        self.assertEqual(len(events), 2)
        self.assertEqual(events[0]["type"], "text")

    def test_07_invalid_json_lines_skipped(self) -> None:
        raw = 'not json\n{"type": "text", "part": {"text": "hi"}}\ngarbage{{{\n'
        events = llm_backends._opencode_json_events(raw)
        self.assertEqual(len(events), 1)

    def test_08_blank_lines_skipped(self) -> None:
        raw = '\n  \n{"type": "done"}\n\n'
        events = llm_backends._opencode_json_events(raw)
        self.assertEqual(len(events), 1)

    def test_09_non_dict_json_values_skipped_not_crash(self) -> None:
        raw = '[1, 2, 3]\n"just a string"\n42\n{"type": "done"}\n'
        events = llm_backends._opencode_json_events(raw)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["type"], "done")


class OpencodeTextChunksTests(unittest.TestCase):
    def test_10_normal_chunks_extracted(self) -> None:
        events: list[dict[str, object]] = [
            {"type": "text", "part": {"text": "Hello "}},
            {"type": "text", "part": {"text": "world"}},
            {"type": "done"},
        ]
        self.assertEqual(
            llm_backends._opencode_text_chunks(events), ["Hello ", "world"]
        )

    def test_11_missing_part_skipped_not_crash(self) -> None:
        events: list[dict[str, object]] = [{"type": "text"}]
        self.assertEqual(llm_backends._opencode_text_chunks(events), [])

    def test_12_part_not_a_dict_skipped_not_crash(self) -> None:
        events: list[dict[str, object]] = [{"type": "text", "part": "not a dict"}]
        self.assertEqual(llm_backends._opencode_text_chunks(events), [])

    def test_13_non_text_type_ignored(self) -> None:
        events: list[dict[str, object]] = [
            {"type": "other", "part": {"text": "should not appear"}}
        ]
        self.assertEqual(llm_backends._opencode_text_chunks(events), [])

    def test_14_empty_text_not_included(self) -> None:
        events: list[dict[str, object]] = [{"type": "text", "part": {"text": ""}}]
        self.assertEqual(llm_backends._opencode_text_chunks(events), [])


class AvailableBackendsTests(unittest.TestCase):
    def test_15_priority_order_preserved(self) -> None:
        with patch("shutil.which", side_effect=lambda b: f"/usr/bin/{b}"):
            self.assertEqual(
                llm_backends.available_backends(), ["agy", "opencode", "copilot"]
            )

    def test_16_only_installed_backends_returned(self) -> None:
        with patch(
            "shutil.which",
            side_effect=lambda b: "/usr/bin/opencode" if b == "opencode" else None,
        ):
            self.assertEqual(llm_backends.available_backends(), ["opencode"])

    def test_17_none_installed_returns_empty(self) -> None:
        with patch("shutil.which", return_value=None):
            self.assertEqual(llm_backends.available_backends(), [])

    def test_18_resolve_backend_picks_first_priority(self) -> None:
        with patch("shutil.which", side_effect=lambda b: f"/usr/bin/{b}"):
            self.assertEqual(llm_backends.resolve_backend(), "agy")

    def test_19_resolve_backend_none_when_unavailable(self) -> None:
        with patch("shutil.which", return_value=None):
            self.assertIsNone(llm_backends.resolve_backend())


class RunCommandRealSubprocessTests(unittest.TestCase):
    """Exercises `_run_command`/`_kill_active_process` with real child processes."""

    def test_20_successful_command(self) -> None:
        returncode, stdout, _stderr = llm_backends._run_command(
            py("print('hi')"), timeout=30
        )
        self.assertEqual(returncode, 0)
        self.assertEqual(stdout.strip(), "hi")

    def test_21_nonzero_exit_captured(self) -> None:
        returncode, _stdout, _stderr = llm_backends._run_command(
            py("import sys; sys.exit(3)"), timeout=30
        )
        self.assertEqual(returncode, 3)

    def test_22_stderr_captured(self) -> None:
        _returncode, _stdout, stderr = llm_backends._run_command(
            py("import sys; sys.stderr.write('oops')"), timeout=30
        )
        self.assertIn("oops", stderr)

    def test_23_missing_executable_wrapped_as_backend_error(self) -> None:
        # Regression: this used to propagate a raw OSError/FileNotFoundError
        # instead of BackendError, crashing the caller's fallback loop.
        with self.assertRaises(llm_backends.BackendError) as cm:
            llm_backends._run_command(["/no/such/executable-xyz"], timeout=30)
        self.assertIn("failed to start", str(cm.exception))

    def test_24_missing_executable_does_not_leave_active_process_set(self) -> None:
        with self.assertRaises(llm_backends.BackendError):
            llm_backends._run_command(["/no/such/executable-xyz"], timeout=30)
        self.assertIsNone(llm_backends._active_process)

    def test_25_active_process_cleared_after_success(self) -> None:
        llm_backends._run_command(py("print('hi')"), timeout=30)
        self.assertIsNone(llm_backends._active_process)

    def test_26_timeout_kills_process_and_raises(self) -> None:
        start = time.monotonic()
        with self.assertRaises(llm_backends.BackendError) as cm:
            llm_backends._run_command(py("import time; time.sleep(30)"), timeout=0.3)
        elapsed = time.monotonic() - start
        self.assertIn("timed out after 0.3s", str(cm.exception))
        # killed promptly, not left running the full 30s sleep
        self.assertLess(elapsed, 5)
        self.assertIsNone(llm_backends._active_process)

    def test_27_kill_active_process_terminates_real_child(self) -> None:
        proc = subprocess.Popen(
            py("import time; time.sleep(30)"),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
        )
        llm_backends._active_process = proc
        try:
            llm_backends._kill_active_process()
            proc.wait(timeout=5)
            self.assertIsNotNone(proc.poll())
        finally:
            llm_backends._active_process = None
            if proc.poll() is None:
                proc.kill()
                proc.wait()
            if proc.stdout:
                proc.stdout.close()
            if proc.stderr:
                proc.stderr.close()

    def test_28_kill_active_process_noop_when_none(self) -> None:
        llm_backends._active_process = None
        llm_backends._kill_active_process()  # must not raise

    def test_29_kill_active_process_noop_when_already_exited(self) -> None:
        proc = subprocess.Popen(py("pass"), text=True)
        proc.wait()
        llm_backends._active_process = proc
        try:
            llm_backends._kill_active_process()  # must not raise
        finally:
            llm_backends._active_process = None


class RunBackendCommandTests(unittest.TestCase):
    def test_30_success_returns_stripped_stdout(self) -> None:
        result = llm_backends.run_backend_command(
            py("print('  output text  ')"), timeout=30
        )
        self.assertEqual(result, "output text")

    def test_31_nonzero_exit_raises_with_stderr(self) -> None:
        with self.assertRaises(llm_backends.BackendError) as cm:
            llm_backends.run_backend_command(
                py("import sys; sys.stderr.write('boom'); sys.exit(1)"), timeout=30
            )
        self.assertIn("exited 1", str(cm.exception))
        self.assertIn("boom", str(cm.exception))

    def test_32_exit_zero_empty_stdout_is_still_a_failure(self) -> None:
        with self.assertRaises(llm_backends.BackendError) as cm:
            llm_backends.run_backend_command(py("pass"), timeout=30)
        self.assertIn("produced no output", str(cm.exception))


class RunAgyTests(unittest.TestCase):
    def test_33_builds_expected_argv(self) -> None:
        with patch.object(
            llm_backends, "run_backend_command", return_value="text"
        ) as mock_run:
            result = llm_backends.run_agy(
                "my prompt", model="Gemini 3.6 Flash (High)", timeout=60
            )
        self.assertEqual(result, "text")
        mock_run.assert_called_once_with(
            ["agy", "-p", "my prompt", "--model", "Gemini 3.6 Flash (High)"], 60
        )


class RunCopilotTests(unittest.TestCase):
    def test_34_no_model_omits_flag(self) -> None:
        with patch.object(
            llm_backends, "run_backend_command", return_value="text"
        ) as mock_run:
            result = llm_backends.run_copilot("my prompt", model=None, timeout=60)
        self.assertEqual(result, "text")
        mock_run.assert_called_once_with(["copilot", "-p", "my prompt", "--silent"], 60)

    def test_35_empty_string_model_omits_flag(self) -> None:
        with patch.object(
            llm_backends, "run_backend_command", return_value="text"
        ) as mock_run:
            llm_backends.run_copilot("my prompt", model="", timeout=60)
        mock_run.assert_called_once_with(["copilot", "-p", "my prompt", "--silent"], 60)

    def test_36_model_appends_flag(self) -> None:
        with patch.object(
            llm_backends, "run_backend_command", return_value="text"
        ) as mock_run:
            llm_backends.run_copilot("my prompt", model="claude-sonnet-4.6", timeout=60)
        mock_run.assert_called_once_with(
            ["copilot", "-p", "my prompt", "--silent", "--model", "claude-sonnet-4.6"],
            60,
        )


class RunOpencodeTests(unittest.TestCase):
    def _run_command_returning(
        self, stdout: str, stderr: str = "", returncode: int = 0
    ):
        return patch.object(
            llm_backends, "_run_command", return_value=(returncode, stdout, stderr)
        )

    def test_37_text_chunks_concatenated(self) -> None:
        stdout = (
            json.dumps({"type": "text", "part": {"text": "Hello "}})
            + "\n"
            + json.dumps({"type": "text", "part": {"text": "world"}})
            + "\n"
        )
        with self._run_command_returning(stdout):
            self.assertEqual(
                llm_backends.run_opencode("prompt", model=None, timeout=60),
                "Hello world",
            )

    def test_38_model_flag_passed_through(self) -> None:
        with patch.object(
            llm_backends,
            "_run_command",
            return_value=(0, '{"type": "text", "part": {"text": "hi"}}\n', ""),
        ) as mock_run:
            llm_backends.run_opencode("prompt", model="claude-sonnet-4.6", timeout=60)
        mock_run.assert_called_once_with(
            [
                "opencode",
                "run",
                "--auto",
                "--format",
                "json",
                "-m",
                "claude-sonnet-4.6",
                "prompt",
            ],
            60,
        )

    def test_39_no_model_argv(self) -> None:
        with patch.object(
            llm_backends,
            "_run_command",
            return_value=(0, '{"type": "text", "part": {"text": "hi"}}\n', ""),
        ) as mock_run:
            llm_backends.run_opencode("prompt", model=None, timeout=60)
        mock_run.assert_called_once_with(
            ["opencode", "run", "--auto", "--format", "json", "prompt"], 60
        )

    def test_40_structured_error_event_raises_with_message(self) -> None:
        stdout = (
            json.dumps(
                {"type": "error", "error": {"data": {"message": "backend crashed"}}}
            )
            + "\n"
        )
        with self._run_command_returning(stdout):
            with self.assertRaises(llm_backends.BackendError) as cm:
                llm_backends.run_opencode("prompt", model=None, timeout=60)
        self.assertIn("backend crashed", str(cm.exception))

    def test_41_error_field_as_plain_string_does_not_crash(self) -> None:
        stdout = json.dumps({"type": "error", "error": "flat string error"}) + "\n"
        with self._run_command_returning(stdout):
            with self.assertRaises(llm_backends.BackendError) as cm:
                llm_backends.run_opencode("prompt", model=None, timeout=60)
        self.assertIn("flat string error", str(cm.exception))

    def test_42_no_text_no_error_falls_back_to_stderr(self) -> None:
        stdout = json.dumps({"type": "other"}) + "\n"
        with self._run_command_returning(stdout, stderr="some stderr detail"):
            with self.assertRaises(llm_backends.BackendError) as cm:
                llm_backends.run_opencode("prompt", model=None, timeout=60)
        self.assertIn("some stderr detail", str(cm.exception))

    def test_43_completely_unparseable_output_falls_back_to_stdout_snippet(
        self,
    ) -> None:
        with self._run_command_returning("not json at all", stderr=""):
            with self.assertRaises(llm_backends.BackendError) as cm:
                llm_backends.run_opencode("prompt", model=None, timeout=60)
        self.assertIn("not json at all", str(cm.exception))

    def test_44_tool_use_event_raises_backend_error(self) -> None:
        stdout = (
            json.dumps(
                {
                    "type": "tool_use",
                    "part": {"tool": "bash", "state": {"status": "completed"}},
                }
            )
            + "\n"
        )
        with self._run_command_returning(stdout):
            with self.assertRaises(llm_backends.BackendError) as cm:
                llm_backends.run_opencode("prompt", model=None, timeout=60)
        self.assertIn("bash", str(cm.exception))
        self.assertIn("tools instead of returning text", str(cm.exception))

    def test_45_tool_use_raises_even_with_text_chunks(self) -> None:
        # Regression: a text-only caller (a recap) must fail loud if the run
        # also performed real tool actions, not silently return the prose.
        stdout = (
            json.dumps({"type": "text", "part": {"text": "a recap"}})
            + "\n"
            + json.dumps(
                {
                    "type": "tool_use",
                    "part": {"tool": "read", "state": {"status": "completed"}},
                }
            )
            + "\n"
        )
        with self._run_command_returning(stdout):
            with self.assertRaises(llm_backends.BackendError) as cm:
                llm_backends.run_opencode("prompt", model=None, timeout=60)
        self.assertIn("read", str(cm.exception))


class OpencodeToolUseEventsTests(unittest.TestCase):
    def test_46_filters_only_tool_use_events(self) -> None:
        events: list[dict[str, object]] = [
            {"type": "text", "part": {"text": "hi"}},
            {"type": "tool_use", "part": {"tool": "bash"}},
            {"type": "step_start"},
            {"type": "tool_use", "part": {"tool": "read"}},
        ]
        self.assertEqual(
            [
                e["part"]["tool"]  # type: ignore[index]
                for e in llm_backends._opencode_tool_use_events(events)
            ],
            ["bash", "read"],
        )

    def test_47_no_tool_use_returns_empty(self) -> None:
        events: list[dict[str, object]] = [
            {"type": "text", "part": {"text": "hi"}},
            {"type": "step_finish"},
        ]
        self.assertEqual(llm_backends._opencode_tool_use_events(events), [])


if __name__ == "__main__":
    unittest.main(verbosity=1)
