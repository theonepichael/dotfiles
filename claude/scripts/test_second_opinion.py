#!/usr/bin/env python3
"""Tests for second_opinion.py. Run with: python3 test_second_opinion.py

The generic backend-invocation engine (`_run_command`, `_kill_active_process`,
`run_backend_command`, `run_agy`/`run_copilot`/`run_opencode` argv-building,
the opencode JSON-event parsing helpers) lives in llm_backends.py now and is
covered by test_llm_backends.py. This file covers what's still
second_opinion-specific: prompt building, the adversary-agent `run_opencode`,
the CLI-level `cmd_review` fallback loop, and the thin wrappers that supply
second_opinion's own model/timeout/env-var choices to the shared engine.
"""

import argparse
import io
import json
import os
import sys
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


class RunAgyWrapperTests(unittest.TestCase):
    """second_opinion.run_agy supplies a model (default or env-overridden) +
    BACKEND_TIMEOUT_SECONDS; llm_backends.run_agy's own argv-building is
    covered in test_llm_backends.py."""

    def test_03_supplies_default_model_and_timeout(self) -> None:
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("SECOND_OPINION_AGY_MODEL", None)
            with (
                patch.object(
                    second_opinion.llm_backends, "run_agy", return_value="critique"
                ) as mock_run,
                patch.object(second_opinion, "BACKEND_TIMEOUT_SECONDS", 300),
            ):
                result = second_opinion.run_agy("my prompt")
        self.assertEqual(result, "critique")
        mock_run.assert_called_once_with(
            "my prompt", model=second_opinion.DEFAULT_AGY_MODEL, timeout=300
        )

    def test_03b_forwards_env_var_model(self) -> None:
        with (
            patch.dict(
                os.environ, {"SECOND_OPINION_AGY_MODEL": "Gemini 3.5 Flash (Medium)"}
            ),
            patch.object(
                second_opinion.llm_backends, "run_agy", return_value="critique"
            ) as mock_run,
            patch.object(second_opinion, "BACKEND_TIMEOUT_SECONDS", 300),
        ):
            result = second_opinion.run_agy("my prompt")
        self.assertEqual(result, "critique")
        mock_run.assert_called_once_with(
            "my prompt", model="Gemini 3.5 Flash (Medium)", timeout=300
        )


class RunCopilotWrapperTests(unittest.TestCase):
    def test_04_forwards_env_var_model(self) -> None:
        with (
            patch.dict(
                os.environ, {"SECOND_OPINION_COPILOT_MODEL": "claude-sonnet-4.6"}
            ),
            patch.object(
                second_opinion.llm_backends, "run_copilot", return_value="critique"
            ) as mock_run,
            patch.object(second_opinion, "BACKEND_TIMEOUT_SECONDS", 300),
        ):
            result = second_opinion.run_copilot("my prompt")
        self.assertEqual(result, "critique")
        mock_run.assert_called_once_with(
            "my prompt", model="claude-sonnet-4.6", timeout=300
        )

    def test_05_no_env_var_passes_none(self) -> None:
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("SECOND_OPINION_COPILOT_MODEL", None)
            with (
                patch.object(
                    second_opinion.llm_backends, "run_copilot", return_value="critique"
                ) as mock_run,
                patch.object(second_opinion, "BACKEND_TIMEOUT_SECONDS", 300),
            ):
                second_opinion.run_copilot("my prompt")
        mock_run.assert_called_once_with("my prompt", model=None, timeout=300)


class BackendLabelTests(unittest.TestCase):
    def test_51_non_copilot_labels_unaffected_by_env_var(self) -> None:
        with patch.dict(
            os.environ, {"SECOND_OPINION_COPILOT_MODEL": "claude-sonnet-4.6"}
        ):
            os.environ.pop("SECOND_OPINION_OPENCODE_MODEL", None)
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

    def test_54_agy_label_unchanged_when_env_var_unset(self) -> None:
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("SECOND_OPINION_AGY_MODEL", None)
            self.assertEqual(
                second_opinion.backend_label("agy"),
                second_opinion.BACKEND_LABELS["agy"],
            )

    def test_55_agy_label_reflects_overridden_model_when_env_var_set(self) -> None:
        with patch.dict(
            os.environ, {"SECOND_OPINION_AGY_MODEL": "Gemini 3.5 Flash (Medium)"}
        ):
            self.assertEqual(
                second_opinion.backend_label("agy"),
                "agy (Gemini 3.5 Flash (Medium))",
            )

    def test_56_agy_label_unchanged_when_env_var_set_to_default(self) -> None:
        with patch.dict(
            os.environ, {"SECOND_OPINION_AGY_MODEL": second_opinion.DEFAULT_AGY_MODEL}
        ):
            self.assertEqual(
                second_opinion.backend_label("agy"),
                second_opinion.BACKEND_LABELS["agy"],
            )

    def test_57_opencode_label_includes_model_when_env_var_set(self) -> None:
        with patch.dict(
            os.environ, {"SECOND_OPINION_OPENCODE_MODEL": "claude-sonnet-4.6"}
        ):
            self.assertEqual(
                second_opinion.backend_label("opencode"),
                f"{second_opinion.BACKEND_LABELS['opencode']} (claude-sonnet-4.6)",
            )

    def test_58_opencode_label_unchanged_when_env_var_unset(self) -> None:
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("SECOND_OPINION_OPENCODE_MODEL", None)
            self.assertEqual(
                second_opinion.backend_label("opencode"),
                second_opinion.BACKEND_LABELS["opencode"],
            )

    def test_59_opencode_label_unchanged_when_env_var_empty(self) -> None:
        with patch.dict(os.environ, {"SECOND_OPINION_OPENCODE_MODEL": ""}):
            self.assertEqual(
                second_opinion.backend_label("opencode"),
                second_opinion.BACKEND_LABELS["opencode"],
            )

    def test_60_opencode_var_does_not_affect_agy_or_copilot_labels(self) -> None:
        with patch.dict(
            os.environ, {"SECOND_OPINION_OPENCODE_MODEL": "claude-sonnet-4.6"}
        ):
            os.environ.pop("SECOND_OPINION_AGY_MODEL", None)
            os.environ.pop("SECOND_OPINION_COPILOT_MODEL", None)
            self.assertEqual(
                second_opinion.backend_label("agy"),
                second_opinion.BACKEND_LABELS["agy"],
            )
            self.assertEqual(
                second_opinion.backend_label("copilot"),
                second_opinion.BACKEND_LABELS["copilot"],
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
        with (
            self._run_command_returning(stdout),
            self.assertRaises(second_opinion.BackendError) as cm,
        ):
            second_opinion.run_opencode("prompt")
        self.assertIn("agent crashed", str(cm.exception))

    def test_38_error_field_as_plain_string_does_not_crash(self) -> None:
        # Regression: `e.get("error", {}).get("data", {})` used to crash
        # with AttributeError when "error" was a plain string rather than
        # a nested object.
        stdout = json.dumps({"type": "error", "error": "flat string error"}) + "\n"
        with (
            self._run_command_returning(stdout),
            self.assertRaises(second_opinion.BackendError) as cm,
        ):
            second_opinion.run_opencode("prompt")
        self.assertIn("flat string error", str(cm.exception))

    def test_39_no_text_no_error_falls_back_to_raw_output(self) -> None:
        stdout = json.dumps({"type": "other"}) + "\n"
        with (
            self._run_command_returning(stdout, stderr="some stderr detail"),
            self.assertRaises(second_opinion.BackendError) as cm,
        ):
            second_opinion.run_opencode("prompt")
        self.assertIn("some stderr detail", str(cm.exception))

    def test_40_completely_unparseable_output_falls_back_to_stdout_snippet(
        self,
    ) -> None:
        with (
            self._run_command_returning("not json at all", stderr=""),
            self.assertRaises(second_opinion.BackendError) as cm,
        ):
            second_opinion.run_opencode("prompt")
        self.assertIn("not json at all", str(cm.exception))

    def _capture_cmd(self) -> tuple[list[str], dict[str, list[str]]]:
        """Return a `_run_command` side-effect capturing its argv, plus the box."""
        box: dict[str, list[str]] = {}

        def capture(cmd: list[str]) -> tuple[int, str, str]:
            box["cmd"] = cmd
            return (
                0,
                json.dumps({"type": "text", "part": {"text": "critique"}}) + "\n",
                "",
            )

        return capture, box

    def test_40b_model_flag_added_when_env_var_set(self) -> None:
        capture, box = self._capture_cmd()
        with (
            patch.dict(
                os.environ, {"SECOND_OPINION_OPENCODE_MODEL": "claude-sonnet-4.6"}
            ),
            patch.object(second_opinion, "_run_command", side_effect=capture),
        ):
            result = second_opinion.run_opencode("my prompt")
        self.assertEqual(result, "critique")
        self.assertEqual(box["cmd"][-3:-1], ["-m", "claude-sonnet-4.6"])
        self.assertEqual(box["cmd"].count("-m"), 1)

    def test_40c_no_model_flag_when_env_var_unset(self) -> None:
        capture, box = self._capture_cmd()
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("SECOND_OPINION_OPENCODE_MODEL", None)
            with patch.object(second_opinion, "_run_command", side_effect=capture):
                second_opinion.run_opencode("my prompt")
        self.assertNotIn("-m", box["cmd"])

    def test_40d_no_model_flag_when_env_var_empty(self) -> None:
        capture, box = self._capture_cmd()
        with (
            patch.dict(os.environ, {"SECOND_OPINION_OPENCODE_MODEL": ""}),
            patch.object(second_opinion, "_run_command", side_effect=capture),
        ):
            second_opinion.run_opencode("my prompt")
        self.assertNotIn("-m", box["cmd"])

    def test_60_tool_use_event_raises_backend_error(self) -> None:
        # Regression: the adversary agent must be stateless and text-only; a
        # tool_use event means a swapped-in model took a real shell/file
        # action instead of critiquing, so fail loud rather than swallow it.
        stdout = (
            json.dumps(
                {
                    "type": "tool_use",
                    "part": {"tool": "bash", "state": {"status": "completed"}},
                }
            )
            + "\n"
        )
        with (
            self._run_command_returning(stdout),
            self.assertRaises(second_opinion.BackendError) as cm,
        ):
            second_opinion.run_opencode("prompt")
        self.assertIn("bash", str(cm.exception))
        self.assertIn("instead of returning", str(cm.exception))

    def test_61_tool_use_raises_even_with_text_chunks(self) -> None:
        stdout = (
            json.dumps({"type": "text", "part": {"text": "the plan looks fine"}})
            + "\n"
            + json.dumps(
                {
                    "type": "tool_use",
                    "part": {"tool": "read", "state": {"status": "completed"}},
                }
            )
            + "\n"
        )
        with (
            self._run_command_returning(stdout),
            self.assertRaises(second_opinion.BackendError) as cm,
        ):
            second_opinion.run_opencode("prompt")
        self.assertIn("read", str(cm.exception))

    def test_62_emitted_tool_call_markup_text_raises(self) -> None:
        # Regression: a tool-hungry model denied every tool can emit its
        # attempted tool calls as literal <tool_calls> XML in a text event
        # (the captured post-fix failure), which would otherwise be returned
        # verbatim as the adversary critique.
        text = (
            "\n\n<tool_calls>\n"
            '<invoke name="bash">\n'
            '<parameter name="command" string="true">ls -la</parameter>\n'
            "</invoke>\n"
            "</tool_calls>"
        )
        stdout = json.dumps({"type": "text", "part": {"text": text}}) + "\n"
        with (
            self._run_command_returning(stdout),
            self.assertRaises(second_opinion.BackendError) as cm,
        ):
            second_opinion.run_opencode("prompt")
        self.assertIn("tool-call markup", str(cm.exception))

    def test_63_prose_merely_mentioning_tool_calls_passes(self) -> None:
        text = (
            "The plan's agent config will never emit <tool_calls> wrappers or "
            '<invoke name="bash"> markup since every tool is denied, so this '
            "risk is moot."
        )
        stdout = json.dumps({"type": "text", "part": {"text": text}}) + "\n"
        with self._run_command_returning(stdout):
            self.assertEqual(second_opinion.run_opencode("prompt"), text)

    def test_64_reordered_invoke_attributes_raises(self) -> None:
        # Regression: attribute order isn't guaranteed on a leaked <invoke>.
        text = '<invoke id="1" name="bash"><parameter name="command">ls</parameter></invoke>'
        stdout = json.dumps({"type": "text", "part": {"text": text}}) + "\n"
        with (
            self._run_command_returning(stdout),
            self.assertRaises(second_opinion.BackendError) as cm,
        ):
            second_opinion.run_opencode("prompt")
        self.assertIn("tool-call markup", str(cm.exception))

    def test_65_quoted_example_in_larger_critique_passes(self) -> None:
        # Regression: a real critique that quotes a fully-closed example of
        # this exact leak shape (plausible when reviewing this very feature)
        # must not itself be rejected as a leaked call.
        critique = (
            "This diff's leak detection is a good idea but underspecified. "
            "For instance, a leak shaped like "
            '<tool_calls><invoke name="bash"><parameter name="command">ls'
            "</parameter></invoke></tool_calls> would need every attribute "
            "in exactly that order to be caught, which is fragile. I'd "
            "recommend a broader set of shape patterns plus a check that "
            "the matched markup actually dominates the response, rather "
            "than merely appearing somewhere within it, so a critique that "
            "quotes an example like the one above during review doesn't "
            "get rejected as if it were the leak itself."
        )
        stdout = json.dumps({"type": "text", "part": {"text": critique}}) + "\n"
        with self._run_command_returning(stdout):
            self.assertEqual(second_opinion.run_opencode("prompt"), critique)


class CmdDetectTests(unittest.TestCase):
    def test_41_detect_prints_availability_json(self) -> None:
        out = io.StringIO()
        with (
            patch(
                "shutil.which",
                side_effect=lambda b: "/usr/bin/agy" if b == "agy" else None,
            ),
            patch("sys.stdout", out),
        ):
            second_opinion.cmd_detect(ns())
        self.assertEqual(
            json.loads(out.getvalue()),
            {"agy": True, "opencode": False, "copilot": False},
        )


class CmdReviewTests(unittest.TestCase):
    def test_42_forced_backend_not_on_path_dies(self) -> None:
        err = io.StringIO()
        with (
            patch("shutil.which", return_value=None),
            self.assertRaises(SystemExit) as cm,
            patch("sys.stderr", err),
        ):
            second_opinion.cmd_review(ns(plan="text", backend="agy"))
        self.assertEqual(cm.exception.code, 1)
        self.assertIn("agy not found on PATH", err.getvalue())

    def test_43_no_backend_available_dies(self) -> None:
        err = io.StringIO()
        with (
            patch("shutil.which", return_value=None),
            self.assertRaises(SystemExit) as cm,
            patch("sys.stderr", err),
        ):
            second_opinion.cmd_review(ns(plan="text"))
        self.assertEqual(cm.exception.code, 1)
        self.assertIn("no backend available", err.getvalue())

    def test_44_first_backend_fails_second_succeeds(self) -> None:
        out, err = io.StringIO(), io.StringIO()
        with (
            patch("shutil.which", side_effect=lambda b: f"/usr/bin/{b}"),
            patch.object(
                second_opinion,
                "BACKEND_RUNNERS",
                {
                    "agy": lambda p: (_ for _ in ()).throw(
                        second_opinion.BackendError("agy broke")
                    ),
                    "opencode": lambda p: "opencode's critique",
                },
            ),
            patch("sys.stdout", out),
            patch("sys.stderr", err),
        ):
            second_opinion.cmd_review(ns(plan="my plan", verbose=True))
        self.assertIn("opencode's critique", out.getvalue())
        self.assertIn("agy broke", err.getvalue())

    def test_45_all_backends_fail_dies_with_combined_message(self) -> None:
        err = io.StringIO()
        with (
            patch("shutil.which", side_effect=lambda b: f"/usr/bin/{b}"),
            patch.object(
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
            ),
            self.assertRaises(SystemExit) as cm,
            patch("sys.stderr", err),
        ):
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
            with (
                patch("shutil.which", side_effect=lambda b: f"/usr/bin/{b}"),
                patch.object(
                    second_opinion,
                    "BACKEND_RUNNERS",
                    {"agy": fake_runner, "opencode": fake_runner},
                ),
                patch("sys.stdout", out),
            ):
                second_opinion.cmd_review(ns(plan=str(plan_path)))
        self.assertIn("# The actual plan", captured_prompt["value"])
        self.assertIn("details here", captured_prompt["value"])


class DieTests(unittest.TestCase):
    def test_47_die_prints_prefixed_message_and_exits_1(self) -> None:
        err = io.StringIO()
        with self.assertRaises(SystemExit) as cm, patch("sys.stderr", err):
            second_opinion.die("something went wrong")
        self.assertEqual(cm.exception.code, 1)
        self.assertEqual(
            err.getvalue().strip(), "[second_opinion] something went wrong"
        )


class HandleTerminationTests(unittest.TestCase):
    def test_48_kills_active_process_and_exits_with_128_plus_signum(self) -> None:
        with (
            patch.object(second_opinion, "_kill_active_process") as mock_kill,
            self.assertRaises(SystemExit) as cm,
        ):
            second_opinion._handle_termination(15, None)
        mock_kill.assert_called_once()
        self.assertEqual(cm.exception.code, 128 + 15)


class ParserVerbosityTests(unittest.TestCase):
    def test_flags_parse_after_every_leaf_subcommand(self) -> None:
        # A leaf added later without an entry here silently loses coverage.
        cases = {
            "detect": ("cmd_detect", []),
            "review": ("cmd_review", ["dummy-plan"]),
        }
        for cmd, (target, extra) in cases.items():
            argv = ["second_opinion.py", cmd, *extra, "-q"]
            with (
                patch.object(second_opinion, target) as mock_cmd,
                patch.object(sys, "argv", argv),
            ):
                second_opinion.main()
            self.assertTrue(mock_cmd.call_args.args[0].quiet)


if __name__ == "__main__":
    unittest.main(verbosity=1)
