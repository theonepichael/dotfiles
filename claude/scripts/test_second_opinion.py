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
import subprocess
import sys
import unittest
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).parent))
import second_opinion


def ns(**kwargs: object) -> argparse.Namespace:
    kwargs.setdefault("backend", None)
    kwargs.setdefault("focus_file", None)
    kwargs.setdefault("model_index", None)
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
            os.environ.pop("SECOND_OPINION_AGY_TIMEOUT_SECONDS", None)
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
            os.environ.pop("SECOND_OPINION_AGY_TIMEOUT_SECONDS", None)
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
            os.environ.pop("SECOND_OPINION_COPILOT_TIMEOUT_SECONDS", None)
            result = second_opinion.run_copilot("my prompt")
        self.assertEqual(result, "critique")
        mock_run.assert_called_once_with(
            "my prompt", model="claude-sonnet-4.6", timeout=300
        )

    def test_05_no_env_var_passes_none(self) -> None:
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("SECOND_OPINION_COPILOT_MODEL", None)
            os.environ.pop("SECOND_OPINION_COPILOT_MODEL_POOL", None)
            os.environ.pop("SECOND_OPINION_COPILOT_TIMEOUT_SECONDS", None)
            with (
                patch.object(
                    second_opinion.llm_backends, "run_copilot", return_value="critique"
                ) as mock_run,
                patch.object(second_opinion, "BACKEND_TIMEOUT_SECONDS", 300),
            ):
                second_opinion.run_copilot("my prompt")
        mock_run.assert_called_once_with("my prompt", model=None, timeout=300)

    def test_05b_model_from_pool_at_index(self) -> None:
        with (
            patch.dict(
                os.environ,
                {
                    "SECOND_OPINION_COPILOT_MODEL_POOL": "gpt-4.1,gpt-5-mini",
                    "SECOND_OPINION_COPILOT_MODEL": "",
                },
            ),
            patch.object(
                second_opinion.llm_backends, "run_copilot", return_value="critique"
            ) as mock_run,
            patch.object(second_opinion, "BACKEND_TIMEOUT_SECONDS", 300),
        ):
            os.environ.pop("SECOND_OPINION_COPILOT_TIMEOUT_SECONDS", None)
            second_opinion.run_copilot("my prompt", model_index=1)
        mock_run.assert_called_once_with("my prompt", model="gpt-5-mini", timeout=300)


class ResolvePooledModelTests(unittest.TestCase):
    """Direct tests of _resolve_pooled_model -- the pure precedence/rotation
    logic shared by the runners and backend_label."""

    def _clear(self) -> None:
        for var in (
            "SECOND_OPINION_OPENCODE_MODEL",
            "SECOND_OPINION_OPENCODE_MODEL_POOL",
        ):
            os.environ.pop(var, None)

    def test_06a_single_override_wins_regardless_of_pool(self) -> None:
        with patch.dict(
            os.environ,
            {
                "SECOND_OPINION_OPENCODE_MODEL": "pinned",
                "SECOND_OPINION_OPENCODE_MODEL_POOL": "a,b,c",
            },
        ):
            self.assertEqual(
                second_opinion._resolve_pooled_model(
                    "SECOND_OPINION_OPENCODE_MODEL_POOL",
                    "SECOND_OPINION_OPENCODE_MODEL",
                    2,
                ),
                "pinned",
            )

    def test_06b_whitespace_only_single_override_treated_as_unset(self) -> None:
        with patch.dict(
            os.environ,
            {
                "SECOND_OPINION_OPENCODE_MODEL": "   ",
                "SECOND_OPINION_OPENCODE_MODEL_POOL": "a,b,c",
            },
        ):
            self.assertEqual(
                second_opinion._resolve_pooled_model(
                    "SECOND_OPINION_OPENCODE_MODEL_POOL",
                    "SECOND_OPINION_OPENCODE_MODEL",
                    1,
                ),
                "b",
            )

    def test_06c_empty_pool_and_no_override_returns_none(self) -> None:
        with patch.dict(os.environ, {}, clear=False):
            self._clear()
            self.assertIsNone(
                second_opinion._resolve_pooled_model(
                    "SECOND_OPINION_OPENCODE_MODEL_POOL",
                    "SECOND_OPINION_OPENCODE_MODEL",
                    None,
                )
            )

    def test_06d_pool_of_commas_and_whitespace_treated_as_empty(self) -> None:
        with patch.dict(os.environ, {"SECOND_OPINION_OPENCODE_MODEL_POOL": " , , "}):
            self._clear_single_only()
            self.assertIsNone(
                second_opinion._resolve_pooled_model(
                    "SECOND_OPINION_OPENCODE_MODEL_POOL",
                    "SECOND_OPINION_OPENCODE_MODEL",
                    None,
                )
            )

    def _clear_single_only(self) -> None:
        os.environ.pop("SECOND_OPINION_OPENCODE_MODEL", None)

    def test_06e_no_index_defaults_to_zero(self) -> None:
        with patch.dict(os.environ, {"SECOND_OPINION_OPENCODE_MODEL_POOL": "a,b,c"}):
            self._clear_single_only()
            self.assertEqual(
                second_opinion._resolve_pooled_model(
                    "SECOND_OPINION_OPENCODE_MODEL_POOL",
                    "SECOND_OPINION_OPENCODE_MODEL",
                    None,
                ),
                "a",
            )

    def test_06f_explicit_index_selects_entry(self) -> None:
        with patch.dict(os.environ, {"SECOND_OPINION_OPENCODE_MODEL_POOL": "a,b,c"}):
            self._clear_single_only()
            self.assertEqual(
                second_opinion._resolve_pooled_model(
                    "SECOND_OPINION_OPENCODE_MODEL_POOL",
                    "SECOND_OPINION_OPENCODE_MODEL",
                    2,
                ),
                "c",
            )

    def test_06g_index_wraps_via_modulo(self) -> None:
        with patch.dict(os.environ, {"SECOND_OPINION_OPENCODE_MODEL_POOL": "a,b,c"}):
            self._clear_single_only()
            self.assertEqual(
                second_opinion._resolve_pooled_model(
                    "SECOND_OPINION_OPENCODE_MODEL_POOL",
                    "SECOND_OPINION_OPENCODE_MODEL",
                    5,
                ),
                "c",
            )

    def test_06h_pool_entries_stripped_of_whitespace(self) -> None:
        with patch.dict(
            os.environ, {"SECOND_OPINION_OPENCODE_MODEL_POOL": " a , b ,c "}
        ):
            self._clear_single_only()
            self.assertEqual(
                second_opinion._resolve_pooled_model(
                    "SECOND_OPINION_OPENCODE_MODEL_POOL",
                    "SECOND_OPINION_OPENCODE_MODEL",
                    1,
                ),
                "b",
            )


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

    def test_61_opencode_label_reflects_pool_resolved_model(self) -> None:
        # Regression: backend_label used to read SECOND_OPINION_OPENCODE_MODEL
        # directly, independent of the runner's resolution -- once the runner
        # started resolving via the pool, the label would silently stop
        # showing a model at all unless it shares the same resolution.
        with patch.dict(
            os.environ, {"SECOND_OPINION_OPENCODE_MODEL_POOL": "model-a,model-b"}
        ):
            os.environ.pop("SECOND_OPINION_OPENCODE_MODEL", None)
            self.assertEqual(
                second_opinion.backend_label("opencode", model_index=1),
                f"{second_opinion.BACKEND_LABELS['opencode']} (model-b)",
            )

    def test_62_copilot_label_reflects_pool_resolved_model(self) -> None:
        with patch.dict(
            os.environ, {"SECOND_OPINION_COPILOT_MODEL_POOL": "gpt-4.1,gpt-5-mini"}
        ):
            os.environ.pop("SECOND_OPINION_COPILOT_MODEL", None)
            self.assertEqual(
                second_opinion.backend_label("copilot", model_index=0),
                f"{second_opinion.BACKEND_LABELS['copilot']} (gpt-4.1)",
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

    def _capture_cmd(
        self,
    ) -> tuple[Callable[..., tuple[int, str, str]], dict[str, list[str] | int]]:
        """Return a `_run_command` side-effect capturing its argv and timeout, plus the box."""
        box: dict[str, list[str] | int] = {}

        def capture(cmd: list[str], timeout: int | None = None) -> tuple[int, str, str]:
            box["cmd"] = cmd
            if timeout is not None:
                box["timeout"] = timeout
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

    def test_40e_model_flag_from_pool_at_index(self) -> None:
        capture, box = self._capture_cmd()
        with (
            patch.dict(
                os.environ,
                {
                    "SECOND_OPINION_OPENCODE_MODEL_POOL": "model-a,model-b,model-c",
                    "SECOND_OPINION_OPENCODE_MODEL": "",
                },
            ),
            patch.object(second_opinion, "_run_command", side_effect=capture),
        ):
            second_opinion.run_opencode("my prompt", model_index=1)
        self.assertEqual(box["cmd"][-3:-1], ["-m", "model-b"])

    def test_40f_pool_index_wraps_via_modulo(self) -> None:
        capture, box = self._capture_cmd()
        with (
            patch.dict(
                os.environ,
                {
                    "SECOND_OPINION_OPENCODE_MODEL_POOL": "model-a,model-b,model-c",
                    "SECOND_OPINION_OPENCODE_MODEL": "",
                },
            ),
            patch.object(second_opinion, "_run_command", side_effect=capture),
        ):
            second_opinion.run_opencode("my prompt", model_index=4)
        self.assertEqual(box["cmd"][-3:-1], ["-m", "model-b"])  # 4 % 3 == 1

    def test_40g_single_override_wins_over_pool(self) -> None:
        capture, box = self._capture_cmd()
        with (
            patch.dict(
                os.environ,
                {
                    "SECOND_OPINION_OPENCODE_MODEL_POOL": "model-a,model-b",
                    "SECOND_OPINION_OPENCODE_MODEL": "pinned-model",
                },
            ),
            patch.object(second_opinion, "_run_command", side_effect=capture),
        ):
            second_opinion.run_opencode("my prompt", model_index=1)
        self.assertEqual(box["cmd"][-3:-1], ["-m", "pinned-model"])

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
                    "agy": lambda p, model_index=None: (_ for _ in ()).throw(
                        second_opinion.BackendError("agy broke")
                    ),
                    "opencode": lambda p, model_index=None: "opencode's critique",
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
                    "agy": lambda p, model_index=None: (_ for _ in ()).throw(
                        second_opinion.BackendError("agy broke")
                    ),
                    "opencode": lambda p, model_index=None: (_ for _ in ()).throw(
                        second_opinion.BackendError("opencode broke")
                    ),
                    "copilot": lambda p, model_index=None: (_ for _ in ()).throw(
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

            def fake_runner(prompt: str, model_index: int | None = None) -> str:
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

    def test_63_model_index_threaded_through_to_runner(self) -> None:
        # Not just that the runner tolerates model_index (the fakes updated
        # above for that) -- that cmd_review actually passes args.model_index
        # through, end to end, rather than e.g. a stale default.
        captured = {}

        def spy_runner(prompt: str, model_index: int | None = None) -> str:
            captured["model_index"] = model_index
            return "critique"

        out = io.StringIO()
        with (
            patch("shutil.which", side_effect=lambda b: f"/usr/bin/{b}"),
            patch.object(second_opinion, "BACKEND_RUNNERS", {"agy": spy_runner}),
            patch("sys.stdout", out),
        ):
            second_opinion.cmd_review(ns(plan="text", backend="agy", model_index=2))
        self.assertEqual(captured["model_index"], 2)

    def test_64_agy_backend_with_explicit_model_index_succeeds(self) -> None:
        # agy accepts and ignores model_index (signature symmetry only) --
        # must not crash.
        out = io.StringIO()
        with (
            patch("shutil.which", side_effect=lambda b: f"/usr/bin/{b}"),
            patch.object(
                second_opinion,
                "BACKEND_RUNNERS",
                {"agy": lambda p, model_index=None: "agy critique"},
            ),
            patch("sys.stdout", out),
        ):
            second_opinion.cmd_review(ns(plan="text", backend="agy", model_index=3))
        self.assertIn("agy critique", out.getvalue())


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


class ModelIndexParsingTests(unittest.TestCase):
    """--model-index validation, tested directly against the parser
    (build_parser) rather than only live -- see Verification steps."""

    def test_65_negative_index_rejected(self) -> None:
        parser = second_opinion.build_parser()
        err = io.StringIO()
        with patch("sys.stderr", err), self.assertRaises(SystemExit) as cm:
            parser.parse_args(["review", "plan.md", "--model-index", "-1"])
        self.assertEqual(cm.exception.code, 2)
        self.assertIn("--model-index", err.getvalue())

    def test_66_non_integer_index_rejected(self) -> None:
        parser = second_opinion.build_parser()
        err = io.StringIO()
        with patch("sys.stderr", err), self.assertRaises(SystemExit) as cm:
            parser.parse_args(["review", "plan.md", "--model-index", "abc"])
        self.assertEqual(cm.exception.code, 2)
        self.assertIn("--model-index", err.getvalue())

    def test_67_non_negative_index_accepted(self) -> None:
        parser = second_opinion.build_parser()
        args = parser.parse_args(["review", "plan.md", "--model-index", "0"])
        self.assertEqual(args.model_index, 0)

    def test_68_index_omitted_defaults_to_none(self) -> None:
        parser = second_opinion.build_parser()
        args = parser.parse_args(["review", "plan.md"])
        self.assertIsNone(args.model_index)


class ParsePositiveIntTests(unittest.TestCase):
    """Direct tests of _parse_positive_int -- the pure hardening/clamping
    logic shared by the global default and every per-backend override."""

    def test_70_blank_falls_back_to_default(self) -> None:
        self.assertEqual(second_opinion._parse_positive_int("", 120), 120)

    def test_71_whitespace_only_falls_back_to_default(self) -> None:
        self.assertEqual(second_opinion._parse_positive_int("   ", 120), 120)

    def test_72_non_integer_falls_back_to_default(self) -> None:
        self.assertEqual(second_opinion._parse_positive_int("abc", 120), 120)

    def test_73_zero_falls_back_to_default(self) -> None:
        self.assertEqual(second_opinion._parse_positive_int("0", 120), 120)

    def test_74_negative_falls_back_to_default(self) -> None:
        self.assertEqual(second_opinion._parse_positive_int("-5", 120), 120)

    def test_75_valid_value_below_cap_returned_as_is(self) -> None:
        self.assertEqual(second_opinion._parse_positive_int("90", 120), 90)

    def test_76_value_above_cap_clamped(self) -> None:
        self.assertEqual(
            second_opinion._parse_positive_int("999999", 120),
            second_opinion._MAX_BACKEND_TIMEOUT_SECONDS,
        )

    def test_77_surrounding_whitespace_stripped_then_parsed(self) -> None:
        self.assertEqual(second_opinion._parse_positive_int(" 200 ", 120), 200)

    def test_78_leading_plus_accepted(self) -> None:
        self.assertEqual(second_opinion._parse_positive_int("+120", 90), 120)

    def test_79_default_itself_clamped_to_max(self) -> None:
        self.assertEqual(
            second_opinion._parse_positive_int("", 999999),
            second_opinion._MAX_BACKEND_TIMEOUT_SECONDS,
        )


class ResolveTimeoutTests(unittest.TestCase):
    """Direct tests of _resolve_timeout -- reads its env var fresh per call,
    unlike the module-latched global BACKEND_TIMEOUT_SECONDS."""

    def test_80_unset_falls_back_to_global(self) -> None:
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("SECOND_OPINION_AGY_TIMEOUT_SECONDS", None)
            with patch.object(second_opinion, "BACKEND_TIMEOUT_SECONDS", 120):
                self.assertEqual(
                    second_opinion._resolve_timeout(
                        "SECOND_OPINION_AGY_TIMEOUT_SECONDS"
                    ),
                    120,
                )

    def test_81_valid_value_overrides_global(self) -> None:
        with (
            patch.dict(os.environ, {"SECOND_OPINION_AGY_TIMEOUT_SECONDS": "45"}),
            patch.object(second_opinion, "BACKEND_TIMEOUT_SECONDS", 120),
        ):
            self.assertEqual(
                second_opinion._resolve_timeout("SECOND_OPINION_AGY_TIMEOUT_SECONDS"),
                45,
            )

    def test_82_invalid_value_falls_back_to_global(self) -> None:
        with (
            patch.dict(os.environ, {"SECOND_OPINION_AGY_TIMEOUT_SECONDS": "-5"}),
            patch.object(second_opinion, "BACKEND_TIMEOUT_SECONDS", 120),
        ):
            self.assertEqual(
                second_opinion._resolve_timeout("SECOND_OPINION_AGY_TIMEOUT_SECONDS"),
                120,
            )


class PerBackendTimeoutIsolationTests(unittest.TestCase):
    """Cross-contamination checks run through the real run_agy/run_opencode/
    run_copilot call sites -- the only place a wrong literal env-var-name
    string typo'd into one call site would actually be caught."""

    def _clear_all_timeout_vars(self) -> None:
        for var in (
            "SECOND_OPINION_TIMEOUT_SECONDS",
            "SECOND_OPINION_AGY_TIMEOUT_SECONDS",
            "SECOND_OPINION_OPENCODE_TIMEOUT_SECONDS",
            "SECOND_OPINION_COPILOT_TIMEOUT_SECONDS",
        ):
            os.environ.pop(var, None)

    def test_83_agy_override_does_not_leak_into_copilot_or_opencode(self) -> None:
        with patch.dict(os.environ, {}, clear=False):
            self._clear_all_timeout_vars()
            os.environ["SECOND_OPINION_AGY_TIMEOUT_SECONDS"] = "45"
            with patch.object(second_opinion, "BACKEND_TIMEOUT_SECONDS", 120):
                with patch.object(
                    second_opinion.llm_backends, "run_agy", return_value="c"
                ) as mock_agy:
                    second_opinion.run_agy("prompt")
                mock_agy.assert_called_once_with(
                    "prompt", model=second_opinion.DEFAULT_AGY_MODEL, timeout=45
                )

                with patch.object(
                    second_opinion.llm_backends, "run_copilot", return_value="c"
                ) as mock_copilot:
                    second_opinion.run_copilot("prompt")
                mock_copilot.assert_called_once_with("prompt", model=None, timeout=120)

                capture, box = RunOpencodeTests()._capture_cmd()
                with patch.object(second_opinion, "_run_command", side_effect=capture):
                    second_opinion.run_opencode("prompt")
                self.assertEqual(box["timeout"], 120)

    def test_84_opencode_override_does_not_leak_into_agy_or_copilot(self) -> None:
        with patch.dict(os.environ, {}, clear=False):
            self._clear_all_timeout_vars()
            os.environ["SECOND_OPINION_OPENCODE_TIMEOUT_SECONDS"] = "60"
            with patch.object(second_opinion, "BACKEND_TIMEOUT_SECONDS", 120):
                capture, box = RunOpencodeTests()._capture_cmd()
                with patch.object(second_opinion, "_run_command", side_effect=capture):
                    second_opinion.run_opencode("prompt")
                self.assertEqual(box["timeout"], 60)

                with patch.object(
                    second_opinion.llm_backends, "run_agy", return_value="c"
                ) as mock_agy:
                    second_opinion.run_agy("prompt")
                mock_agy.assert_called_once_with(
                    "prompt", model=second_opinion.DEFAULT_AGY_MODEL, timeout=120
                )

                with patch.object(
                    second_opinion.llm_backends, "run_copilot", return_value="c"
                ) as mock_copilot:
                    second_opinion.run_copilot("prompt")
                mock_copilot.assert_called_once_with("prompt", model=None, timeout=120)


class BackendTimeoutDefaultSubprocessTest(unittest.TestCase):
    """Regression-tests the literal default-argument value against a fresh
    interpreter, since BACKEND_TIMEOUT_SECONDS is latched at import time and
    pytest imports test modules at collection time -- before any fixture
    runs -- so in-process env patching can't cover this. Spawns only the
    Python interpreter itself; no backend CLI or production path involved."""

    @pytest.mark.allow_real_subprocess
    def test_85_default_is_120_with_no_env_override(self) -> None:
        env = dict(os.environ)
        env.pop("SECOND_OPINION_TIMEOUT_SECONDS", None)
        script_dir = str(Path(__file__).parent)
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                "import second_opinion; print(second_opinion.BACKEND_TIMEOUT_SECONDS)",
            ],
            env={**env, "PYTHONPATH": script_dir},
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "120")


if __name__ == "__main__":
    unittest.main(verbosity=1)
