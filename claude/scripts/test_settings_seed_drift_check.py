#!/usr/bin/env python3
"""Tests for settings_seed_drift_check.py. Run with: python3 test_settings_seed_drift_check.py

Covers the redesign (additive-only ``fix``, denylist cosmetics, loud-fail
on JSONC parse errors, refusal while a Claude Code session is active).
"""

import io
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent))
import settings_seed_drift_check as ssdc


class SettingsSeedDriftCheckTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.mkdtemp()
        self.home = Path(self.tmpdir) / "home"
        self.home.mkdir()
        self.dotfiles = Path(self.tmpdir) / "dotfiles"
        self.claude_dir = self.dotfiles / "claude"
        self.claude_dir.mkdir(parents=True)
        self.opencode_dir = self.dotfiles / "opencode"
        self.opencode_dir.mkdir(parents=True)
        self.state_dir = self.home / ".local" / "state" / "dotfiles"
        self.state_dir.mkdir(parents=True)

        self._patches = [
            patch.object(ssdc, "HOME", self.home),
            patch.object(ssdc, "DOTFILES", self.dotfiles),
            patch.object(ssdc, "PROFILE_MARKER", self.state_dir / "profile"),
            # SessionStart refusal gate must not fire from any test that
            # isn't explicitly exercising it.
            patch.object(ssdc, "_sessions_active", lambda: 0),
        ]
        for p in self._patches:
            p.start()

    def tearDown(self) -> None:
        for p in self._patches:
            p.stop()
        shutil.rmtree(self.tmpdir)

    # ── setup helpers ──────────────────────────────────────────────────

    def write_settings_seed(
        self, body: dict[str, object], *, name: str = "settings.json"
    ) -> None:
        (self.claude_dir / name).write_text(json.dumps(body, indent=2) + "\n")

    def write_live_settings(self, body: dict[str, object]) -> None:
        live_dir = self.home / ".claude"
        live_dir.mkdir(parents=True, exist_ok=True)
        (live_dir / "settings.json").write_text(json.dumps(body, indent=2) + "\n")

    def write_live_settings_raw(self, text: str) -> None:
        live_dir = self.home / ".claude"
        live_dir.mkdir(parents=True, exist_ok=True)
        (live_dir / "settings.json").write_text(text)

    def write_opencode_seed(self, body: dict[str, object]) -> None:
        (self.opencode_dir / "opencode.jsonc").write_text(
            json.dumps(body, indent=2) + "\n"
        )

    def write_live_opencode(self, body: dict[str, object]) -> None:
        oc_dir = self.home / ".config" / "opencode"
        oc_dir.mkdir(parents=True, exist_ok=True)
        (oc_dir / "opencode.jsonc").write_text(json.dumps(body, indent=2) + "\n")

    def write_live_opencode_raw(self, text: str) -> None:
        oc_dir = self.home / ".config" / "opencode"
        oc_dir.mkdir(parents=True, exist_ok=True)
        (oc_dir / "opencode.jsonc").write_text(text)

    def mark_work(self) -> None:
        (self.state_dir / "profile").write_text("work\n")

    def run_check(self) -> tuple[str, int]:
        out = io.StringIO()
        err = io.StringIO()
        with patch("sys.stdout", out), patch("sys.stderr", err):
            code = ssdc.cmd_check()
        return out.getvalue().strip(), code

    def run_fix(self) -> tuple[str, int]:
        out = io.StringIO()
        err = io.StringIO()
        with patch("sys.stdout", out), patch("sys.stderr", err):
            code = ssdc.cmd_fix()
        return out.getvalue().strip(), code

    def load_live_settings(self) -> dict[str, object]:
        return json.loads((self.home / ".claude" / "settings.json").read_text())

    def load_live_opencode(self) -> dict[str, object]:
        return json.loads(
            (self.home / ".config" / "opencode" / "opencode.jsonc").read_text()
        )

    # ── _non_cosmetic_drift unit (denylist, not allowlist) ──────────────

    def test_non_cosmetic_drift_reports_unknown_future_security_key(self) -> None:
        # skipDangerousModePermissionPrompt is shipped today; the old
        # allowlist silently missed it. The denylist must surface it.
        seed = {"skipDangerousModePermissionPrompt": True, "theme": "light"}
        live = {"skipDangerousModePermissionPrompt": False, "theme": "dark"}
        result = ssdc._non_cosmetic_drift(seed, live, ssdc.SETTINGS_COSMETIC_KEYS)
        self.assertIn("skipDangerousModePermissionPrompt", result)
        self.assertNotIn("theme", result)

    def test_non_cosmetic_drift_silent_on_cosmetic_only(self) -> None:
        seed = {"theme": "light", "model": "x", "voiceEnabled": False}
        live = {"theme": "dark", "model": "y", "voiceEnabled": True}
        result = ssdc._non_cosmetic_drift(seed, live, ssdc.SETTINGS_COSMETIC_KEYS)
        self.assertEqual(result, [])

    def test_non_cosmetic_drift_reports_permissions_and_hooks(self) -> None:
        seed = {"permissions": {"allow": []}, "hooks": {}}
        live = {"permissions": {"allow": ["Bash(x)"]}, "hooks": {"X": []}}
        result = ssdc._non_cosmetic_drift(seed, live, ssdc.SETTINGS_COSMETIC_KEYS)
        self.assertEqual(result, ["hooks", "permissions"])

    # ── profile resolution (unchanged behavior) ─────────────────────────

    def test_resolve_profile_defaults_personal_no_marker(self) -> None:
        self.assertEqual(ssdc.resolve_profile(), "personal")

    def test_resolve_profile_work_when_marker_says_work(self) -> None:
        self.mark_work()
        self.assertEqual(ssdc.resolve_profile(), "work")

    def test_settings_seed_path_personal(self) -> None:
        self.assertEqual(ssdc.settings_seed_path(), self.claude_dir / "settings.json")

    def test_settings_seed_path_work(self) -> None:
        self.mark_work()
        self.assertEqual(
            ssdc.settings_seed_path(), self.claude_dir / "settings.work.json"
        )

    def test_opencode_seed_path_personal(self) -> None:
        self.assertEqual(
            ssdc.opencode_seed_path(), self.opencode_dir / "opencode.jsonc"
        )

    def test_opencode_seed_path_work_is_none(self) -> None:
        self.mark_work()
        self.assertIsNone(ssdc.opencode_seed_path())

    # ── cmd_check end-to-end (denylist reporting) ───────────────────────

    def test_check_silent_when_no_live_settings(self) -> None:
        self.write_settings_seed({"permissions": {"allow": []}})
        out, code = self.run_check()
        self.assertEqual(out, "")
        self.assertEqual(code, 0)

    def test_check_silent_when_critical_keys_match(self) -> None:
        self.write_settings_seed(
            {"permissions": {"allow": ["Bash(x)"]}, "hooks": {}, "theme": "light"}
        )
        self.write_live_settings(
            {"permissions": {"allow": ["Bash(x)"]}, "hooks": {}, "theme": "dark"}
        )
        out, code = self.run_check()
        self.assertEqual(out, "")
        self.assertEqual(code, 0)

    def test_check_reports_settings_critical_drift(self) -> None:
        self.write_settings_seed({"permissions": {"allow": []}, "hooks": {}})
        self.write_live_settings(
            {"permissions": {"allow": ["Bash(BAD)"]}, "hooks": {}, "theme": "light"}
        )
        out, _ = self.run_check()
        self.assertIn("settings.json drifted", out)
        self.assertIn("permissions", out)
        self.assertIn("fix`", out)
        self.assertNotIn("theme", out)  # cosmetic — not reported

    def test_check_reports_hooks_drift(self) -> None:
        self.write_settings_seed(
            {"permissions": {"allow": []}, "hooks": {"SessionStart": []}}
        )
        self.write_live_settings({"permissions": {"allow": []}, "hooks": {}})
        out, _ = self.run_check()
        self.assertIn("hooks", out)

    def test_check_reports_unknown_future_security_key_drift(self) -> None:
        # A key that isn't permissions/hooks and isn't in the cosmetic
        # denylist must be reported by default (the plan's whole point).
        self.write_settings_seed({"permissions": {"allow": []}, "autoCompact": True})
        self.write_live_settings({"permissions": {"allow": []}, "autoCompact": False})
        out, _ = self.run_check()
        self.assertIn("autoCompact", out)

    def test_check_opencode_silent_when_no_live_opencode(self) -> None:
        self.write_settings_seed({"permissions": {"allow": []}})
        self.write_opencode_seed({"permission": {"bash": {"git status*": "allow"}}})
        out, code = self.run_check()
        self.assertEqual(out, "")
        self.assertEqual(code, 0)

    def test_check_opencode_reports_permission_drift(self) -> None:
        self.write_settings_seed({"permissions": {"allow": []}})
        self.write_opencode_seed({"permission": {"bash": {"git status*": "allow"}}})
        self.write_live_opencode({"permission": {"bash": {"rm -f *": "allow"}}})
        out, _ = self.run_check()
        self.assertIn("permission", out)

    def test_check_opencode_reports_security_bypass(self) -> None:
        self.write_settings_seed({"permissions": {"allow": []}})
        self.write_opencode_seed({"permission": {"bash": {"git status*": "allow"}}})
        self.write_live_opencode(
            {"permission": {"bash": {"git status*": "allow", "xargs *": "allow"}}}
        )
        out, _ = self.run_check()
        self.assertIn("SECURITY:", out)
        self.assertIn("xargs *", out)
        self.assertIn("fix`", out)

    def test_check_work_profile_skips_opencode(self) -> None:
        self.mark_work()
        self.write_settings_seed(
            {"permissions": {"allow": []}, "hooks": {}}, name="settings.work.json"
        )
        self.write_live_opencode({"permission": {"bash": {"rm -f *": "allow"}}})
        out, _ = self.run_check()
        self.assertEqual(out, "")

    # ── loud-fail on JSONC / parse failure ──────────────────────────────

    def test_check_loudfails_on_unparseable_opencode_jsonc(self) -> None:
        # A // comment makes json.loads choke — the script must say so,
        # not silently report "no drift" (the silent-no-op bug the
        # redesign fixes).
        self.write_settings_seed({"permissions": {"allow": []}})
        self.write_opencode_seed({"permission": {"bash": {"git status*": "allow"}}})
        self.write_live_opencode_raw(
            '{\n  // a comment json.loads cannot parse\n  "permission": {}\n}\n'
        )
        out, code = self.run_check()
        self.assertNotEqual(code, 0)
        self.assertIn("cannot parse", out)
        self.assertIn("opencode", out)

    def test_check_loudfails_on_unparseable_settings_json(self) -> None:
        self.write_settings_seed({"permissions": {"allow": []}})
        self.write_live_settings_raw("{ not valid json ")
        out, code = self.run_check()
        self.assertNotEqual(code, 0)
        self.assertIn("cannot parse", out)

    def test_check_stays_silent_when_live_file_missing(self) -> None:
        # Missing-file is NOT a parse failure — it's "nothing to compare",
        # and must remain silent (no false alarm on a fresh machine).
        self.write_settings_seed({"permissions": {"allow": []}})
        out, code = self.run_check()
        self.assertEqual(out, "")
        self.assertEqual(code, 0)

    # ── cmd_fix: additive union for permissions.allow ──────────────────

    def test_fix_permissions_allow_is_additive_union_preserving_live(self) -> None:
        seed = {"permissions": {"allow": ["Bash(SEED_A)", "Bash(SEED_B)"]}}
        self.write_settings_seed(seed)
        # live already has SEED_A plus a live-only approval Live_USER_X
        live = {
            "permissions": {
                "allow": ["Bash(SEED_A)", "Bash(LIVE_USER_X)"],
                "deny": [],
                "ask": [],
            },
            "theme": "dark",  # cosmetic — must survive
        }
        self.write_live_settings(live)
        self.run_fix()
        repaired = self.load_live_settings()
        allow = repaired["permissions"]["allow"]  # type: ignore[index]
        # Live approval preserved, both seed entries present
        self.assertIn("Bash(LIVE_USER_X)", allow)
        self.assertIn("Bash(SEED_A)", allow)
        self.assertIn("Bash(SEED_B)", allow)
        # cosmetic preserved
        self.assertEqual(repaired["theme"], "dark")

    def test_fix_permissions_deny_restored_when_live_is_weakened_subset(self) -> None:
        # Live deny=[] but seed deny=["Bash(rm *)"] → security regression,
        # wholesale-overwrite from seed.
        seed = {"permissions": {"allow": [], "deny": ["Bash(rm *)"], "ask": []}}
        self.write_settings_seed(seed)
        live = {"permissions": {"allow": [], "deny": [], "ask": []}}
        self.write_live_settings(live)
        self.run_fix()
        repaired = self.load_live_settings()
        self.assertEqual(repaired["permissions"]["deny"], ["Bash(rm *)"])  # type: ignore[index]

    def test_fix_permissions_deny_preserved_when_live_added_entries(self) -> None:
        # Live deny has entries seed lacks → not a weakened subset → preserve.
        seed = {"permissions": {"allow": [], "deny": ["Bash(rm *)"], "ask": []}}
        self.write_settings_seed(seed)
        live = {
            "permissions": {
                "allow": [],
                "deny": ["Bash(rm *)", "Bash(LIVE_EXTRA)"],
                "ask": [],
            }
        }
        self.write_live_settings(live)
        self.run_fix()
        repaired = self.load_live_settings()
        self.assertEqual(
            repaired["permissions"]["deny"],  # type: ignore[index]
            ["Bash(rm *)", "Bash(LIVE_EXTRA)"],
        )

    def test_fix_permissions_ask_flag_and_skip_when_differing(self) -> None:
        # Live ask and seed ask differ but neither is a weakened subset of
        # the other → flag-and-skip (preserve live, report).
        seed = {"permissions": {"allow": [], "ask": ["Bash(git commit)"]}}
        self.write_settings_seed(seed)
        live = {"permissions": {"allow": [], "ask": ["Bash(git push)"]}}
        self.write_live_settings(live)
        out, _ = self.run_fix()
        repaired = self.load_live_settings()
        self.assertEqual(repaired["permissions"]["ask"], ["Bash(git push)"])  # type: ignore[index]
        self.assertIn("permissions.ask", out)
        self.assertIn("leaving live", out)

    def test_fix_hooks_wholesale_overwritten_from_seed(self) -> None:
        # hooks are not live-modified by approvals → any divergence is
        # genuine drift → wholesale-overwrite from seed.
        seed = {
            "permissions": {"allow": []},
            "hooks": {
                "SessionStart": [{"hooks": [{"type": "command", "command": "X"}]}]
            },
        }
        self.write_settings_seed(seed)
        live = {"permissions": {"allow": []}, "hooks": {"StaleDrift": []}}
        self.write_live_settings(live)
        self.run_fix()
        repaired = self.load_live_settings()
        self.assertEqual(repaired["hooks"], seed["hooks"])

    def test_fix_hooks_dropped_when_seed_has_none(self) -> None:
        # seed has no hooks key; live has hooks → drop live's hooks
        # (wholesale: live becomes seed's hooks, which is empty).
        self.write_settings_seed({"permissions": {"allow": []}})
        self.write_live_settings(
            {"permissions": {"allow": []}, "hooks": {"StaleDrift": []}}
        )
        self.run_fix()
        repaired = self.load_live_settings()
        self.assertNotIn("hooks", repaired)

    def test_fix_creates_backup(self) -> None:
        self.write_settings_seed({"permissions": {"allow": ["Bash(SEED)"]}})
        self.write_live_settings({"permissions": {"allow": ["Bash(BAD)"]}})
        self.run_fix()
        backups = list((self.home / ".claude").glob("settings.json.bak.*"))
        self.assertEqual(len(backups), 1)
        backup_content = json.loads(backups[0].read_text())
        self.assertEqual(backup_content["permissions"], {"allow": ["Bash(BAD)"]})

    def test_fix_atomic_write_preserves_other_live_keys(self) -> None:
        # Cosmetic + unknown future keys are preserved by fix.
        self.write_settings_seed(
            {"permissions": {"allow": ["Bash(SEED)"]}, "autoCompact": True}
        )
        self.write_live_settings(
            {
                "permissions": {"allow": ["Bash(BAD)"]},
                "autoCompact": False,
                "theme": "dark",
            }
        )
        self.run_fix()
        repaired = self.load_live_settings()
        # autoCompact and theme untouched (autoCompact isn't permissions/hooks)
        self.assertFalse(repaired["autoCompact"])
        self.assertEqual(repaired["theme"], "dark")
        # permissions.allow additive: BAD retained, SEED appended
        self.assertIn("Bash(BAD)", repaired["permissions"]["allow"])  # type: ignore[index]
        self.assertIn("Bash(SEED)", repaired["permissions"]["allow"])  # type: ignore[index]

    def test_fix_silent_when_no_critical_drift(self) -> None:
        self.write_settings_seed(
            {"permissions": {"allow": []}, "hooks": {}, "theme": "light"}
        )
        self.write_live_settings(
            {"permissions": {"allow": []}, "hooks": {}, "theme": "dark"}
        )
        out, _ = self.run_fix()
        self.assertIn("no auto-repairable drift", out)
        live = self.load_live_settings()
        self.assertEqual(live["theme"], "dark")

    # ── cmd_fix opencode (additive + bypass removal) ────────────────────

    def test_fix_opencode_bash_additive_preserves_live_patterns(self) -> None:
        seed_oc = {
            "permission": {"bash": {"git status*": "allow", "npm run*": "allow"}}
        }
        self.write_settings_seed({"permissions": {"allow": []}})
        self.write_opencode_seed(seed_oc)
        # live has git status* (matches seed) plus a live-only approval
        self.write_live_opencode(
            {"permission": {"bash": {"git status*": "allow", "copilot *": "allow"}}}
        )
        self.run_fix()
        repaired = self.load_live_opencode()
        bash = repaired["permission"]["bash"]  # type: ignore[index]
        self.assertIn("git status*", bash)
        self.assertIn("npm run*", bash)  # appended from seed
        self.assertIn("copilot *", bash)  # live-only preserved

    def test_fix_opencode_strips_bypass_patterns_even_under_additive(self) -> None:
        # xargs * present live but not seed → stripped even though additive
        # policy would otherwise preserve live-only entries.
        self.write_settings_seed({"permissions": {"allow": []}})
        self.write_opencode_seed({"permission": {"bash": {"git status*": "allow"}}})
        self.write_live_opencode(
            {"permission": {"bash": {"git status*": "allow", "xargs *": "allow"}}}
        )
        self.run_fix()
        repaired = self.load_live_opencode()
        bash = repaired["permission"]["bash"]  # type: ignore[index]
        self.assertNotIn("xargs *", bash)
        self.assertIn("git status*", bash)

    def test_fix_opencode_bash_flag_and_skip_differing_verdicts(self) -> None:
        # shared pattern with differing verdict → preserve live, report.
        self.write_settings_seed({"permissions": {"allow": []}})
        self.write_opencode_seed({"permission": {"bash": {"rm -f *": "ask"}}})
        self.write_live_opencode({"permission": {"bash": {"rm -f *": "allow"}}})
        out, _ = self.run_fix()
        repaired = self.load_live_opencode()
        # live verdict preserved
        self.assertEqual(repaired["permission"]["bash"]["rm -f *"], "allow")  # type: ignore[index]
        self.assertIn("verdict differs", out)

    def test_fix_opencode_external_directory_additive_union(self) -> None:
        seed_oc = {
            "permission": {
                "bash": {},
                "external_directory": {"~/Workspace/**": "allow"},
            }
        }
        self.write_settings_seed({"permissions": {"allow": []}})
        self.write_opencode_seed(seed_oc)
        self.write_live_opencode(
            {"permission": {"bash": {}, "external_directory": {"/tmp/**": "allow"}}}
        )
        self.run_fix()
        repaired = self.load_live_opencode()
        ed = repaired["permission"]["external_directory"]  # type: ignore[index]
        self.assertIn("/tmp/**", ed)  # live-only preserved
        self.assertIn("~/Workspace/**", ed)  # seed appended

    # ── cmd_fix refusal while a Claude Code session is active ───────────

    def test_fix_refuses_when_sessions_active(self) -> None:
        self.write_settings_seed({"permissions": {"allow": ["Bash(SEED)"]}})
        self.write_live_settings({"permissions": {"allow": ["Bash(BAD)"]}})
        with patch.object(ssdc, "_sessions_active", lambda: 1):
            out, code = self.run_fix()
        self.assertNotEqual(code, 0)
        self.assertIn("refusing to fix", out)
        # Live file unchanged
        live = self.load_live_settings()
        self.assertEqual(live["permissions"], {"allow": ["Bash(BAD)"]})

    def test_fix_runs_when_no_sessions_active(self) -> None:
        self.write_settings_seed({"permissions": {"allow": ["Bash(SEED)"]}})
        self.write_live_settings({"permissions": {"allow": ["Bash(BAD)"]}})
        with patch.object(ssdc, "_sessions_active", lambda: 0):
            _, code = self.run_fix()
        self.assertEqual(code, 0)
        live = self.load_live_settings()
        self.assertIn("Bash(SEED)", live["permissions"]["allow"])  # type: ignore[index]

    def test_fix_is_no_op_when_live_settings_missing(self) -> None:
        self.write_settings_seed({"permissions": {"allow": []}})
        out, code = self.run_fix()
        self.assertEqual(code, 0)
        self.assertNotIn("repaired", out)

    def test_fix_loudfails_on_unparseable_settings(self) -> None:
        self.write_settings_seed({"permissions": {"allow": []}})
        self.write_live_settings_raw("{ broken json")
        out, code = self.run_fix()
        self.assertNotEqual(code, 0)
        self.assertIn("cannot fix", out)


if __name__ == "__main__":
    unittest.main()
