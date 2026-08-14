#!/usr/bin/env python3
"""Tests for statusline.py. Run with: python3 test_statusline.py

statusline.py is a Claude Code status line script: reads a JSON session
payload on stdin and renders one ANSI-color-coded line. These tests cover
the pure render helper with payload dicts, and one subprocess test for the
full stdin -> stdout contract. main() is intentionally never called
directly — it ends in sys.exit(0) for gen_interfaces' AST extraction, so
calling it in-process would raise SystemExit.
"""

import json
import subprocess
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import statusline  # noqa: E402

GREEN = "\033[32m"
YELLOW = "\033[33m"
RED = "\033[31m"
RESET = "\033[0m"


def payload(pct: float | None = None, model: str | None = "Opus") -> dict:
    return {
        "model": {"display_name": model},
        "context_window": {"used_percentage": pct},
    }


class RenderTestCase(unittest.TestCase):
    """Exact-string rendering, thresholds, and prefix/bar behavior."""

    def test_42_percent_green_four_blocks(self) -> None:
        self.assertEqual(
            statusline._render(payload(42.0)),
            f"[Opus] {GREEN}▓▓▓▓░░░░░░{RESET} 42%",
        )

    def test_85_percent_yellow_eight_blocks(self) -> None:
        self.assertEqual(
            statusline._render(payload(85.0)),
            f"[Opus] {YELLOW}▓▓▓▓▓▓▓▓░░{RESET} 85%",
        )

    def test_95_percent_red_nine_blocks(self) -> None:
        self.assertEqual(
            statusline._render(payload(95.0)),
            f"[Opus] {RED}▓▓▓▓▓▓▓▓▓░{RESET} 95%",
        )

    def test_70_0_is_yellow(self) -> None:
        self.assertIn(YELLOW, statusline._render(payload(70.0)))

    def test_90_0_is_red(self) -> None:
        self.assertIn(RED, statusline._render(payload(90.0)))

    def test_zero_percent_green_empty_bar(self) -> None:
        self.assertEqual(
            statusline._render(payload(0.0)),
            f"[Opus] {GREEN}░░░░░░░░░░{RESET} 0%",
        )

    def test_100_percent_red_full_bar(self) -> None:
        self.assertEqual(
            statusline._render(payload(100.0)),
            f"[Opus] {RED}▓▓▓▓▓▓▓▓▓▓{RESET} 100%",
        )

    def test_truncation_not_rounding(self) -> None:
        self.assertEqual(
            statusline._render(payload(99.0)),
            f"[Opus] {RED}▓▓▓▓▓▓▓▓▓░{RESET} 99%",
        )

    def test_rounding_display(self) -> None:
        self.assertEqual(
            statusline._render(payload(42.3)),
            f"[Opus] {GREEN}▓▓▓▓░░░░░░{RESET} 42%",
        )

    def test_no_model_prefix_when_name_absent(self) -> None:
        self.assertEqual(
            statusline._render(payload(50.0, model=None)),
            f"{GREEN}▓▓▓▓▓░░░░░{RESET} 50%",
        )

    def test_model_blank_string_drops_prefix(self) -> None:
        self.assertEqual(
            statusline._render(payload(50.0, model="  ")),
            f"{GREEN}▓▓▓▓▓░░░░░{RESET} 50%",
        )

    def test_model_non_string_drops_prefix(self) -> None:
        for bad in (42, ["Opus"], {"name": "Opus"}, True):
            self.assertEqual(
                statusline._render(payload(50.0, model=bad)),
                f"{GREEN}▓▓▓▓▓░░░░░{RESET} 50%",
            )


class DegradationTestCase(unittest.TestCase):
    """Defensive input handling: absent/null/wrong-typed fields."""

    def test_missing_context_window_zero_percent(self) -> None:
        self.assertEqual(
            statusline._render({"model": {"display_name": "Opus"}}),
            f"[Opus] {GREEN}░░░░░░░░░░{RESET} 0%",
        )

    def test_null_used_percentage_zero_percent(self) -> None:
        self.assertEqual(
            statusline._render(payload(None)),
            f"[Opus] {GREEN}░░░░░░░░░░{RESET} 0%",
        )

    def test_context_window_non_dict(self) -> None:
        for bad in (None, "ctx", [], 42):
            self.assertEqual(
                statusline._render(
                    {"model": {"display_name": "Opus"}, "context_window": bad}
                ),
                f"[Opus] {GREEN}░░░░░░░░░░{RESET} 0%",
            )

    def test_model_non_dict(self) -> None:
        for bad in (None, "Opus", [], 42):
            self.assertEqual(
                statusline._render(
                    {"model": bad, "context_window": {"used_percentage": 50.0}}
                ),
                f"{GREEN}▓▓▓▓▓░░░░░{RESET} 50%",
            )

    def test_nan_treated_as_zero(self) -> None:
        self.assertEqual(
            statusline._render(payload(float("nan"))),
            f"[Opus] {GREEN}░░░░░░░░░░{RESET} 0%",
        )

    def test_inf_treated_as_zero(self) -> None:
        self.assertEqual(
            statusline._render(payload(float("inf"))),
            f"[Opus] {GREEN}░░░░░░░░░░{RESET} 0%",
        )

    def test_negative_clamped_to_zero(self) -> None:
        self.assertEqual(
            statusline._render(payload(-5.0)),
            f"[Opus] {GREEN}░░░░░░░░░░{RESET} 0%",
        )

    def test_over_100_clamped(self) -> None:
        self.assertEqual(
            statusline._render(payload(250.0)),
            f"[Opus] {RED}▓▓▓▓▓▓▓▓▓▓{RESET} 100%",
        )

    def test_numeric_string_accepted(self) -> None:
        self.assertEqual(
            statusline._render(payload("42")),
            f"[Opus] {GREEN}▓▓▓▓░░░░░░{RESET} 42%",
        )

    def test_bool_rejected_as_zero(self) -> None:
        for bad in (True, False):
            self.assertEqual(
                statusline._render(payload(bad)),
                f"[Opus] {GREEN}░░░░░░░░░░{RESET} 0%",
            )

    def test_non_numeric_string_rejected(self) -> None:
        for bad in ("", "abc"):
            self.assertEqual(
                statusline._render(payload(bad)),
                f"[Opus] {GREEN}░░░░░░░░░░{RESET} 0%",
            )

    def test_non_numeric_other_types_rejected(self) -> None:
        for bad in ([], {}, None):
            self.assertEqual(
                statusline._render(payload(bad)),
                f"[Opus] {GREEN}░░░░░░░░░░{RESET} 0%",
            )


class ProcessTestCase(unittest.TestCase):
    """Full stdin -> stdout contract through a real subprocess."""

    def test_pipe_payload_renders_line(self) -> None:
        proc = subprocess.run(
            [sys.executable, str(Path(__file__).parent / "statusline.py")],
            input=json.dumps(payload(42.0)),
            capture_output=True,
            text=True,
        )
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(proc.stdout, f"[Opus] {GREEN}▓▓▓▓░░░░░░{RESET} 42%\n")
        self.assertEqual(proc.stderr, "")

    def test_non_json_stdin_empty_output_exit_zero(self) -> None:
        for raw in ("", "not json", "42", "null", "[]", "true"):
            proc = subprocess.run(
                [sys.executable, str(Path(__file__).parent / "statusline.py")],
                input=raw,
                capture_output=True,
                text=True,
            )
            self.assertEqual(proc.returncode, 0)
            self.assertEqual(proc.stdout, "")
            self.assertEqual(proc.stderr, "")

    def test_non_dict_payload_empty_output_exit_zero(self) -> None:
        for raw in ("null", "[]", "true", "123"):
            proc = subprocess.run(
                [sys.executable, str(Path(__file__).parent / "statusline.py")],
                input=raw,
                capture_output=True,
                text=True,
            )
            self.assertEqual(proc.returncode, 0)
            self.assertEqual(proc.stdout, "")
            self.assertEqual(proc.stderr, "")


if __name__ == "__main__":
    unittest.main()
