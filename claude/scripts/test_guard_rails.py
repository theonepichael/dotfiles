#!/usr/bin/env python3
"""Unit tests for guard_rails.py -- payload shaping, verdict rendering, and
the failure posture. Git is mocked here; the topology behaviour that depends
on real git output is covered by test/test_guard_rails_topology.py against
actual repositories, because mocked git output would encode the very
assumptions under test."""

import io
import json
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

import guard_rails  # noqa: E402


class NormalizeToolTests(unittest.TestCase):
    def test_write_family_covers_every_harness_spelling(self) -> None:
        for name in (
            "Write",
            "Edit",
            "write",
            "edit",
            "write_to_file",
            "replace_file_content",
            "MultiEdit",
        ):
            self.assertEqual(guard_rails.tool_family(name), "write", name)

    def test_bash_and_unknown_tools_are_not_write_family(self) -> None:
        for name in ("Bash", "bash", "run_command", "Read", "", "Glob"):
            self.assertNotEqual(guard_rails.tool_family(name), "write", name)


class PayloadParsingTests(unittest.TestCase):
    def test_claude_payload(self) -> None:
        req = guard_rails.parse_payload(
            "claude",
            {
                "cwd": "/repo",
                "tool_name": "Edit",
                "tool_input": {"file_path": "/repo/a.py"},
            },
        )
        self.assertEqual(req.tool, "write")
        self.assertEqual(req.path, "/repo/a.py")
        self.assertEqual(req.cwd, "/repo")

    def test_agy_payload_is_not_double_encoded(self) -> None:
        req = guard_rails.parse_payload(
            "agy",
            {"toolCall": {"name": "write_to_file", "args": {"TargetFile": "/repo/a.py"}}},
        )
        self.assertEqual(req.tool, "write")
        self.assertEqual(req.path, "/repo/a.py")

    def test_copilot_toolargs_is_a_json_string_needing_a_second_parse(self) -> None:
        req = guard_rails.parse_payload(
            "copilot",
            {"toolName": "edit", "toolArgs": json.dumps({"path": "/repo/a.py"})},
        )
        self.assertEqual(req.tool, "write")
        self.assertEqual(req.path, "/repo/a.py")

    def test_malformed_payload_yields_no_request_rather_than_raising(self) -> None:
        self.assertIsNone(guard_rails.parse_payload("claude", {"nonsense": True}))
        self.assertIsNone(guard_rails.parse_payload("agy", []))


class VerdictRenderingTests(unittest.TestCase):
    def test_claude_deny_uses_permission_decision(self) -> None:
        out, code = guard_rails.render("claude", guard_rails.Verdict("deny", "nope"))
        self.assertEqual(code, 0)
        body = json.loads(out)["hookSpecificOutput"]
        self.assertEqual(body["hookEventName"], "PreToolUse")
        self.assertEqual(body["permissionDecision"], "deny")
        self.assertEqual(body["permissionDecisionReason"], "nope")

    def test_claude_warn_uses_additional_context_not_a_denial(self) -> None:
        out, _ = guard_rails.render("claude", guard_rails.Verdict("warn", "behind"))
        body = json.loads(out)["hookSpecificOutput"]
        self.assertNotEqual(body.get("permissionDecision"), "deny")
        self.assertIn("behind", body["additionalContext"])

    def test_agy_deny_and_warn_both_use_decision_field(self) -> None:
        deny = json.loads(guard_rails.render("agy", guard_rails.Verdict("deny", "x"))[0])
        warn = json.loads(guard_rails.render("agy", guard_rails.Verdict("warn", "y"))[0])
        self.assertEqual(deny["decision"], "deny")
        self.assertEqual(warn["decision"], "allow")
        self.assertIn("y", warn["reason"])

    def test_copilot_deny_uses_the_exit_code_since_json_is_ignored(self) -> None:
        """Probed live 2026-09-01: Copilot honours exit 2 only. A JSON verdict
        on exit 0 was ignored and the write proceeded."""
        _, code = guard_rails.render("copilot", guard_rails.Verdict("deny", "x"))
        self.assertEqual(code, 2)
        _, allow_code = guard_rails.render("copilot", guard_rails.Verdict("allow"))
        self.assertEqual(allow_code, 0)
        _, warn_code = guard_rails.render("copilot", guard_rails.Verdict("warn", "y"))
        self.assertEqual(warn_code, 0, "a warn must never block")

    def test_neutral_form_always_exits_zero(self) -> None:
        for decision in ("allow", "deny", "warn"):
            out, code = guard_rails.render(None, guard_rails.Verdict(decision, "r"))
            self.assertEqual(code, 0)
            self.assertEqual(json.loads(out)["decision"], decision)


class EscapeHatchTests(unittest.TestCase):
    def test_guard_rails_off_short_circuits_every_rule(self) -> None:
        with mock.patch.dict("os.environ", {"GUARD_RAILS_OFF": "1"}):
            with mock.patch.object(guard_rails, "git") as git:
                verdict = guard_rails.evaluate(
                    guard_rails.Request("write", "/repo", "/repo/a.py")
                )
        self.assertEqual(verdict.decision, "allow")
        git.assert_not_called()


class FailurePostureTests(unittest.TestCase):
    def test_not_a_repo_allows_without_reading_the_backlog(self) -> None:
        with mock.patch.object(guard_rails, "repo_info", return_value=None):
            with mock.patch.object(guard_rails, "load_in_progress") as load:
                verdict = guard_rails.evaluate(
                    guard_rails.Request("write", "/tmp", "/tmp/a.py")
                )
        self.assertEqual(verdict.decision, "allow")
        load.assert_not_called()

    def test_worktree_short_circuits_before_the_backlog_read(self) -> None:
        info = guard_rails.RepoInfo(
            toplevel="/wt", common_dir="/repo/.git", is_worktree=True,
            is_bare=False, branch="feature",
        )
        with mock.patch.object(guard_rails, "repo_info", return_value=info):
            with mock.patch.object(guard_rails, "load_in_progress") as load:
                # A worktree still gets the R3 base check; stub it so this
                # test stays about the backlog short-circuit.
                with mock.patch.object(
                    guard_rails, "_behind_origin_main", return_value=False
                ):
                    verdict = guard_rails.evaluate(
                        guard_rails.Request("write", "/wt", "/wt/a.py")
                    )
        self.assertEqual(verdict.decision, "allow")
        load.assert_not_called()

    def test_worktree_behind_origin_main_warns_without_reading_the_backlog(
        self,
    ) -> None:
        info = guard_rails.RepoInfo(
            toplevel="/wt", common_dir="/repo/.git", is_worktree=True,
            is_bare=False, branch="feature",
        )
        with mock.patch.object(guard_rails, "repo_info", return_value=info):
            with mock.patch.object(guard_rails, "load_in_progress") as load:
                with mock.patch.object(
                    guard_rails, "_behind_origin_main", return_value=True
                ):
                    verdict = guard_rails.evaluate(
                        guard_rails.Request("write", "/wt", "/wt/a.py")
                    )
        self.assertEqual(verdict.decision, "warn")
        self.assertIn("origin/main", verdict.reason)
        load.assert_not_called()

    def test_bare_repo_allows(self) -> None:
        info = guard_rails.RepoInfo(
            toplevel="", common_dir="/repo.git", is_worktree=False,
            is_bare=True, branch="main",
        )
        with mock.patch.object(guard_rails, "repo_info", return_value=info):
            with mock.patch.object(guard_rails, "load_in_progress") as load:
                verdict = guard_rails.evaluate(
                    guard_rails.Request("write", "/repo.git", "/repo.git/x")
                )
        self.assertEqual(verdict.decision, "allow")
        load.assert_not_called()

    def test_feature_branch_in_a_main_checkout_allows(self) -> None:
        info = guard_rails.RepoInfo(
            toplevel="/repo", common_dir="/repo/.git", is_worktree=False,
            is_bare=False, branch="feature",
        )
        with mock.patch.object(guard_rails, "repo_info", return_value=info):
            with mock.patch.object(guard_rails, "load_in_progress") as load:
                verdict = guard_rails.evaluate(
                    guard_rails.Request("write", "/repo", "/repo/a.py")
                )
        self.assertEqual(verdict.decision, "allow")
        load.assert_not_called()

    def test_unreadable_backlog_store_allows(self) -> None:
        info = guard_rails.RepoInfo(
            toplevel="/repo", common_dir="/repo/.git", is_worktree=False,
            is_bare=False, branch="main",
        )
        with mock.patch.object(guard_rails, "repo_info", return_value=info):
            with mock.patch.object(guard_rails, "load_in_progress", return_value=None):
                verdict = guard_rails.evaluate(
                    guard_rails.Request("write", "/repo", "/repo/a.py")
                )
        self.assertEqual(verdict.decision, "allow")


class BusyMainCheckoutTests(unittest.TestCase):
    MAIN = guard_rails.RepoInfo(
        toplevel="/repo", common_dir="/repo/.git", is_worktree=False,
        is_bare=False, branch="main",
    )

    def _evaluate(self, items, common_dirs):
        def fake_common_dir(directory: str) -> str | None:
            return common_dirs.get(directory)

        with mock.patch.object(guard_rails, "repo_info", return_value=self.MAIN):
            with mock.patch.object(guard_rails, "load_in_progress", return_value=items):
                with mock.patch.object(
                    guard_rails, "common_dir_of", side_effect=fake_common_dir
                ) as resolver:
                    verdict = guard_rails.evaluate(
                        guard_rails.Request("write", "/repo", "/repo/a.py")
                    )
        return verdict, resolver

    def test_item_whose_worktree_shares_a_common_dir_denies(self) -> None:
        """The item's related_files point at the main checkout while the work
        happens in a linked worktree. Their common dirs match; their toplevels
        would not."""
        items = [{"id": "slug", "related_files": [{"path": "/repo/x.py"}]}]
        verdict, _ = self._evaluate(items, {"/repo": "/repo/.git"})
        self.assertEqual(verdict.decision, "deny")
        self.assertIn("slug", verdict.reason)

    def test_item_in_a_different_repo_allows(self) -> None:
        items = [{"id": "other", "related_files": [{"path": "/elsewhere/x.py"}]}]
        verdict, _ = self._evaluate(items, {"/elsewhere": "/elsewhere/.git"})
        self.assertEqual(verdict.decision, "allow")

    def test_directories_are_deduplicated_before_any_git_call(self) -> None:
        items = [
            {
                "id": "slug",
                "related_files": [
                    {"path": "/other/a.py"},
                    {"path": "/other/b.py"},
                    {"path": "/other/c.py"},
                    {"path": "/other/nested/d.py"},
                ],
            }
        ]
        _, resolver = self._evaluate(
            items, {"/other": "/other/.git", "/other/nested": "/other/.git"}
        )
        seen = [call.args[0] for call in resolver.call_args_list]
        self.assertEqual(len(seen), len(set(seen)), f"duplicate git calls: {seen}")
        self.assertLessEqual(len(seen), 2)

    def test_stale_related_file_is_skipped_not_matched(self) -> None:
        items = [{"id": "slug", "related_files": [{"path": "/gone/x.py"}]}]
        verdict, _ = self._evaluate(items, {"/gone": None})
        self.assertEqual(verdict.decision, "allow")

    def test_item_with_no_related_files_does_not_match_everything(self) -> None:
        items = [{"id": "slug", "related_files": []}]
        verdict, _ = self._evaluate(items, {})
        self.assertEqual(verdict.decision, "allow")


class MainTests(unittest.TestCase):
    def _run(self, argv, stdin_text=""):
        out = io.StringIO()
        with mock.patch.object(sys, "stdin", io.StringIO(stdin_text)):
            with mock.patch.object(sys, "stdout", out):
                code = guard_rails.main(argv)
        return out.getvalue(), code

    def test_bash_calls_are_ignored_entirely(self) -> None:
        payload = json.dumps(
            {"cwd": "/repo", "tool_name": "Bash", "tool_input": {"command": "git commit"}}
        )
        with mock.patch.object(guard_rails, "evaluate") as ev:
            _, code = self._run(["--harness", "claude"], payload)
        self.assertEqual(code, 0)
        ev.assert_not_called()

    def test_unparseable_stdin_allows_and_does_not_raise(self) -> None:
        _, code = self._run(["--harness", "claude"], "not json at all")
        self.assertEqual(code, 0)


if __name__ == "__main__":
    unittest.main()
