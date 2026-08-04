#!/usr/bin/env python3
"""Tests for settings_seed_drift_check.py. Run with: python3 test_settings_seed_drift_check.py"""

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
            patch.object(ssdc, "REPO", self.dotfiles),
            patch.object(ssdc, "PROFILE_MARKER", self.state_dir / "profile"),
        ]
        # sys.path must already have the repo inserted by the module itself;
        # patching REPO after import won't re-run the sys.path.insert in the
        # module body, so manually keep install importable for the helper
        # imports that already happened at module load.
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

    def write_opencode_seed(self, body: dict[str, object]) -> None:
        (self.opencode_dir / "opencode.jsonc").write_text(
            json.dumps(body, indent=2) + "\n"
        )

    def write_live_opencode(self, body: dict[str, object]) -> None:
        oc_dir = self.home / ".config" / "opencode"
        oc_dir.mkdir(parents=True, exist_ok=True)
        (oc_dir / "opencode.jsonc").write_text(json.dumps(body, indent=2) + "\n")

    def mark_work(self) -> None:
        (self.state_dir / "profile").write_text("work\n")

    def run_check(self) -> str:
        out = io.StringIO()
        with patch("sys.stdout", out):
            ssdc.cmd_check()
        return out.getvalue().strip()

    def run_fix(self) -> str:
        out = io.StringIO()
        with patch("sys.stdout", out), patch("sys.stderr", io.StringIO()):
            ssdc.cmd_fix()
        return out.getvalue().strip()

    # ── critical_key_drift unit ─────────────────────────────────────────

    def test_critical_key_drift_only_criticals_count(self) -> None:
        seed = {"permissions": {"allow": []}, "hooks": {}, "theme": "light"}
        live = {"permissions": {"allow": ["Bash(x)"]}, "hooks": {}, "theme": "dark"}
        result = ssdc.critical_key_drift(seed, live, ssdc.SETTINGS_CRITICAL_KEYS)
        self.assertEqual(result, ["permissions"])

    def test_critical_key_drift_silent_on_cosmetic_only(self) -> None:
        seed = {"theme": "light", "model": "x", "voiceEnabled": False}
        live = {"theme": "dark", "model": "y", "voiceEnabled": True}
        result = ssdc.critical_key_drift(seed, live, ssdc.SETTINGS_CRITICAL_KEYS)
        self.assertEqual(result, [])

    def test_critical_key_drift_silent_when_keys_match(self) -> None:
        seed = {"permissions": {"allow": []}, "hooks": {}}
        live = {"permissions": {"allow": []}, "hooks": {}, "theme": "dark"}
        result = ssdc.critical_key_drift(seed, live, ssdc.SETTINGS_CRITICAL_KEYS)
        self.assertEqual(result, [])

    def test_critical_key_drift_detects_present_in_live_missing_in_seed(self) -> None:
        # Seed lacks `permissions`; live has it. Only `permissions` should
        # drift — `hooks` is absent on both sides, so None == None, no drift.
        seed = {"theme": "light"}
        live = {"permissions": {"allow": ["Bash(x)"]}, "theme": "light"}
        result = ssdc.critical_key_drift(seed, live, ssdc.SETTINGS_CRITICAL_KEYS)
        self.assertEqual(sorted(result), ["permissions"])

    def test_critical_key_drift_detects_present_in_seed_missing_in_live(self) -> None:
        seed = {"permissions": {"allow": []}, "hooks": {}}
        live = {"theme": "light"}
        result = ssdc.critical_key_drift(seed, live, ssdc.SETTINGS_CRITICAL_KEYS)
        self.assertEqual(result, ["hooks", "permissions"])

    def test_critical_key_drift_handles_missing_critical_key_when_both_absent(
        self,
    ) -> None:
        # critical key 'hooks' is in neither seed nor live -> no drift on it
        seed = {"theme": "light"}
        live = {"theme": "dark"}
        result = ssdc.critical_key_drift(seed, live, ssdc.SETTINGS_CRITICAL_KEYS)
        # Both critical keys absent on both sides -> seed.get == live.get (None==None)
        self.assertEqual(result, [])

    # ── profile resolution ─────────────────────────────────────────────

    def test_resolve_profile_defaults_personal_no_marker(self) -> None:
        # setUp creates state_dir but no profile marker file
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

    # ── cmd_check end-to-end ────────────────────────────────────────────

    def test_check_silent_when_no_live_settings(self) -> None:
        self.write_settings_seed({"permissions": {"allow": []}})
        # No live file -> _load_json_pair returns None -> no drift reported
        self.assertEqual(self.run_check(), "")

    def test_check_silent_when_critical_keys_match(self) -> None:
        self.write_settings_seed(
            {"permissions": {"allow": ["Bash(x)"]}, "hooks": {}, "theme": "light"}
        )
        self.write_live_settings(
            {"permissions": {"allow": ["Bash(x)"]}, "hooks": {}, "theme": "dark"}
        )
        self.assertEqual(self.run_check(), "")

    def test_check_reports_settings_critical_drift(self) -> None:
        self.write_settings_seed({"permissions": {"allow": []}, "hooks": {}})
        self.write_live_settings(
            {"permissions": {"allow": ["Bash(BAD)"]}, "hooks": {}, "theme": "light"}
        )
        out = self.run_check()
        self.assertIn("settings.json drifted", out)
        self.assertIn("permissions", out)
        self.assertIn("fix`", out)
        # Cosmetic key `theme` should NOT be mentioned as drift
        self.assertNotIn("theme", out)

    def test_check_reports_hooks_drift(self) -> None:
        self.write_settings_seed(
            {"permissions": {"allow": []}, "hooks": {"SessionStart": []}}
        )
        self.write_live_settings({"permissions": {"allow": []}, "hooks": {}})
        out = self.run_check()
        self.assertIn("hooks", out)

    def test_check_opencode_silent_when_no_live_opencode(self) -> None:
        self.write_settings_seed({"permissions": {"allow": []}})
        self.write_opencode_seed({"permission": {"bash": {"git status*": "allow"}}})
        # No live opencode file -> opencode check silently skips (file missing)
        # Critical settings drift absent too -> completely silent
        self.assertEqual(self.run_check(), "")

    def test_check_opencode_reports_permission_drift(self) -> None:
        self.write_settings_seed({"permissions": {"allow": []}})
        self.write_opencode_seed({"permission": {"bash": {"git status*": "allow"}}})
        self.write_live_opencode({"permission": {"bash": {"rm -f *": "allow"}}})
        out = self.run_check()
        self.assertIn("permission", out)

    def test_check_opencode_reports_security_bypass(self) -> None:
        self.write_settings_seed({"permissions": {"allow": []}})
        seed_oc = {
            "permission": {"bash": {"git status*": "allow"}},
        }
        self.write_opencode_seed(seed_oc)
        # Live has xargs bypass that the seed doesn't allow -> SECURITY line
        live_oc = {
            "permission": {"bash": {"git status*": "allow", "xargs *": "allow"}},
        }
        self.write_live_opencode(live_oc)
        out = self.run_check()
        self.assertIn("SECURITY:", out)
        self.assertIn("xargs *", out)
        self.assertIn("fix`", out)

    def test_check_work_profile_skips_opencode(self) -> None:
        self.mark_work()
        self.write_settings_seed(
            {"permissions": {"allow": []}, "hooks": {}}, name="settings.work.json"
        )
        # Even if an opencode live file exists under personal paths, work
        # profile must not check it.
        self.write_live_opencode({"permission": {"bash": {"rm -f *": "allow"}}})
        # No live settings.json -> silent
        self.assertEqual(self.run_check(), "")

    # ── cmd_fix ─────────────────────────────────────────────────────────

    def test_fix_repairs_permissions_drift_preserving_cosmetic_keys(self) -> None:
        seed = {"permissions": {"allow": []}, "hooks": {}, "theme": "light"}
        self.write_settings_seed(seed)
        live = {
            "permissions": {"allow": ["Bash(BAD)"]},
            "hooks": {},
            "theme": "dark",  # personal pref — must survive the fix
            "model": "y",  # personal pref — must survive
        }
        self.write_live_settings(live)
        self.run_fix()

        repaired = json.loads((self.home / ".claude" / "settings.json").read_text())
        # Critical key overwritten from seed
        self.assertEqual(repaired["permissions"], {"allow": []})
        # Cosmetic keys preserved
        self.assertEqual(repaired["theme"], "dark")
        self.assertEqual(repaired["model"], "y")

    def test_fix_creates_backup(self) -> None:
        self.write_settings_seed({"permissions": {"allow": []}, "hooks": {}})
        live = {"permissions": {"allow": ["Bash(BAD)"]}, "hooks": {}}
        self.write_live_settings(live)
        self.run_fix()

        backups = list((self.home / ".claude").glob("settings.json.bak.*"))
        self.assertEqual(len(backups), 1)
        # Backup holds the pre-fix (drifted) permissions
        backup_content = json.loads(backups[0].read_text())
        self.assertEqual(backup_content["permissions"], {"allow": ["Bash(BAD)"]})

    def test_fix_silent_when_no_critical_drift(self) -> None:
        self.write_settings_seed(
            {"permissions": {"allow": []}, "hooks": {}, "theme": "light"}
        )
        # live diverges ONLY on cosmetic `theme`
        self.write_live_settings(
            {"permissions": {"allow": []}, "hooks": {}, "theme": "dark"}
        )
        out = self.run_fix()
        self.assertIn("no critical drift", out)
        # Live file untouched
        live = json.loads((self.home / ".claude" / "settings.json").read_text())
        self.assertEqual(live["theme"], "dark")

    def test_fix_drops_critical_key_present_in_live_missing_in_seed(self) -> None:
        # Storage seed has no `hooks` key. Live has one. Fix should drop it.
        self.write_settings_seed({"permissions": {"allow": []}})
        self.write_live_settings(
            {"permissions": {"allow": []}, "hooks": {"SessionStart": []}}
        )
        self.run_fix()

        repaired = json.loads((self.home / ".claude" / "settings.json").read_text())
        self.assertNotIn("hooks", repaired)

    def test_fix_opencode_security_bypass_repaired_by_permission_overwrite(
        self,
    ) -> None:
        self.write_settings_seed({"permissions": {"allow": []}})
        seed_oc = {"permission": {"bash": {"git status*": "allow"}}}
        self.write_opencode_seed(seed_oc)
        # Live allows a bypass
        self.write_live_opencode(
            {"permission": {"bash": {"git status*": "allow", "xargs *": "allow"}}}
        )
        out = self.run_fix()
        # After fix, the SECURITY bypass should be gone (we overwrote the
        # entire `permission` key from the seed, dropping xargs along with it)
        repaired = json.loads(
            (self.home / ".config" / "opencode" / "opencode.jsonc").read_text()
        )
        self.assertEqual(repaired["permission"], {"bash": {"git status*": "allow"}})
        self.assertNotIn("xargs *", repaired["permission"]["bash"])
        # No SECURITY warning on stderr/stdout after the repair
        self.assertNotIn("SECURITY", out)

    def test_fix_is_no_op_when_live_settings_missing(self) -> None:
        self.write_settings_seed({"permissions": {"allow": []}, "hooks": {}})
        # No live settings.json
        out = self.run_fix()
        # Should not crash, should not claim a repair
        self.assertNotIn("repaired", out)


if __name__ == "__main__":
    unittest.main()
