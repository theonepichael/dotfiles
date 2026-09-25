"""dev_status_sync.py across the toolkit-home cutover: refuse to sync while
either machine is mid-migration or on a different layout, find the toolkit
modules wherever they are installed, and move grill artifacts between each
side's own grill root."""

import fcntl
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent))
import test_dev_status_sync as base  # noqa: E402  (sets up the toolkit import path)

sync = base.sync
dev_status = base.dev_status
make_item = base.make_item

BEGIN = {"phase": "run", "event": "begin"}
COMMITTED = {"phase": "run", "event": "end", "detail": {"outcome": "committed"}}
RESTORED = {"phase": "run", "event": "end", "detail": {"outcome": "restored"}}
ABANDONED = {"phase": "run", "event": "abandoned"}
FINALIZE_BEGIN = {"phase": "finalize", "event": "begin"}
FINALIZED = {"phase": "finalize", "event": "end", "detail": {"outcome": "finalized"}}
ROLLBACK_BEGIN = {"phase": "rollback", "event": "begin"}
ROLLED_BACK = {
    "phase": "rollback",
    "event": "end",
    "detail": {"outcome": "rolled-back"},
}


def _lines(*records):
    return b"".join(json.dumps(r).encode() + b"\n" for r in records)


class MigrationStatusTests(unittest.TestCase):
    def setUp(self):
        self.state_dir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.state_dir, ignore_errors=True)

    def _run(self, name="mig-1", **files):
        d = self.state_dir / "migrations" / name
        d.mkdir(parents=True, exist_ok=True)
        for filename, raw in files.items():
            (d / filename.replace("_", ".")).write_bytes(raw)
        return d

    def _status(self):
        return sync.migration_status(self.state_dir)[0]

    def test_no_migrations_is_idle(self):
        self.assertEqual(self._status(), "idle")

    def test_committed_awaiting_finalize_is_allowed(self):
        self._run(journal_jsonl=_lines(BEGIN, COMMITTED))
        self.assertEqual(self._status(), "committed-unfinalized")

    def test_finalized_is_idle(self):
        self._run(
            journal_jsonl=_lines(BEGIN, COMMITTED),
            finalize_jsonl=_lines(FINALIZE_BEGIN, FINALIZED),
        )
        self.assertEqual(self._status(), "idle")

    def test_rolled_back_is_idle(self):
        self._run(
            journal_jsonl=_lines(BEGIN, COMMITTED),
            rollback_jsonl=_lines(ROLLBACK_BEGIN, ROLLED_BACK),
        )
        self.assertEqual(self._status(), "idle")

    def test_restored_and_abandoned_runs_are_idle(self):
        self._run("a", journal_jsonl=_lines(BEGIN, RESTORED))
        self._run("b", journal_jsonl=_lines(BEGIN, ABANDONED))
        self.assertEqual(self._status(), "idle")

    def test_unfinished_run_is_in_flight(self):
        self._run(journal_jsonl=_lines(BEGIN))
        self.assertEqual(self._status(), "in-flight")

    def test_missing_main_journal_is_in_flight(self):
        (self.state_dir / "migrations" / "mig-1").mkdir(parents=True)
        self.assertEqual(self._status(), "in-flight")

    def test_empty_main_journal_is_in_flight(self):
        self._run(journal_jsonl=b"")
        self.assertEqual(self._status(), "in-flight")

    def test_crashed_rollback_is_in_flight(self):
        self._run(
            journal_jsonl=_lines(BEGIN, COMMITTED),
            rollback_jsonl=_lines(ROLLBACK_BEGIN),
        )
        self.assertEqual(self._status(), "in-flight")

    def test_crashed_finalize_is_in_flight(self):
        self._run(
            journal_jsonl=_lines(BEGIN, COMMITTED),
            finalize_jsonl=_lines(FINALIZE_BEGIN),
        )
        self.assertEqual(self._status(), "in-flight")

    def test_torn_final_line_is_skipped(self):
        self._run(journal_jsonl=_lines(BEGIN, COMMITTED) + b'{"event": "be')
        self.assertEqual(self._status(), "committed-unfinalized")

    def test_corrupt_earlier_line_is_unknown(self):
        self._run(journal_jsonl=b"not json\n" + _lines(COMMITTED))
        self.assertEqual(self._status(), "unknown")


class LockProbeTests(unittest.TestCase):
    def setUp(self):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        self.lock = tmp / "migration.lock"
        p = patch.object(sync.migration_lock, "lock_path", lambda: self.lock)
        p.start()
        self.addCleanup(p.stop)

    def _hold(self, mode):
        self.lock.touch()
        fd = os.open(self.lock, os.O_RDWR)
        self.addCleanup(os.close, fd)
        fcntl.flock(fd, mode)

    def test_missing_lock_file_is_not_held(self):
        self.assertIs(sync.migration_lock_held_exclusively(), False)

    def test_exclusive_holder_is_detected(self):
        self._hold(fcntl.LOCK_EX)
        self.assertIs(sync.migration_lock_held_exclusively(), True)

    def test_a_shared_holder_such_as_this_sync_is_not_reported(self):
        self._hold(fcntl.LOCK_SH)
        self.assertIs(sync.migration_lock_held_exclusively(), False)


def _state(layout="legacy", migration="idle", root="/home/x/.claude/data/grill"):
    return {
        "layout": layout,
        "migration": migration,
        "decisions_root": root,
        "detail": "d",
    }


class RefusalTests(unittest.TestCase):
    def test_both_idle_on_the_same_layout_is_allowed(self):
        sync.refuse_unsafe_states(_state(), _state(root="/home/y/g"), "fedora")

    def test_committed_awaiting_finalize_is_allowed(self):
        sync.refuse_unsafe_states(
            _state(migration="committed-unfinalized"), _state(), "fedora"
        )

    def test_in_flight_on_either_side_refuses(self):
        for local, remote in (
            (_state(migration="in-flight"), _state()),
            (_state(), _state(migration="in-flight")),
        ):
            with self.assertRaisesRegex(sync.SyncFatalError, "mid-migration"):
                sync.refuse_unsafe_states(local, remote, "fedora")

    def test_unknown_migration_state_refuses(self):
        with self.assertRaisesRegex(sync.SyncFatalError, "mid-migration"):
            sync.refuse_unsafe_states(_state(), _state(migration="unknown"), "fedora")

    def test_unknown_layout_refuses(self):
        with self.assertRaisesRegex(sync.SyncFatalError, "unknown toolkit layout"):
            sync.refuse_unsafe_states(_state(layout="unknown"), _state(), "fedora")

    def test_layout_mismatch_refuses(self):
        with self.assertRaisesRegex(sync.SyncFatalError, "layout mismatch"):
            sync.refuse_unsafe_states(_state(), _state(layout="toolkit-home"), "fedora")

    def test_a_peer_sending_no_state_is_told_to_redeploy(self):
        with self.assertRaisesRegex(sync.SyncFatalError, "redeploy"):
            sync.refuse_unsafe_states(_state(), None, "fedora")

    def test_a_protocol_2_peer_is_refused(self):
        with self.assertRaisesRegex(sync.SyncFatalError, "redeploy"):
            sync._check_protocol_version({"protocol_version": 2})


class MigrationGuardTests(base.SyncTestCase):
    def test_a_running_migration_refuses(self):
        def busy(*a, **k):
            raise sync.migration_lock.MigrationLockBusy("held")

        with (
            patch.object(sync.migration_lock, "shared", busy),
            self.assertRaisesRegex(sync.SyncFatalError, "migration is running"),
            sync.migration_guard(),
        ):
            pass

    def test_a_layout_change_since_start_refuses(self):
        with (
            patch.object(sync, "_LAYOUT_AT_IMPORT", "toolkit-home"),
            self.assertRaisesRegex(sync.SyncFatalError, "since this process started"),
            sync.migration_guard(),
        ):
            pass


class ExportImportGuardTests(base.SyncTestCase):
    def _export(self):
        self.data_dir.mkdir(parents=True, exist_ok=True)
        dev_status.save_items([make_item("foo-bar")])
        out = io.StringIO()
        with patch("sys.stdout", out):
            sync.cmd_export(base.argparse.Namespace(lock_timeout=5.0))
        return sync._extract_framed_json(out.getvalue())

    def test_export_payload_keys_are_pinned(self):
        payload = self._export()
        self.assertEqual(
            set(payload),
            {
                "protocol_version",
                "machine_state",
                "rev",
                "schema_version",
                "items",
                "pending_items",
                "runs",
            },
        )
        self.assertEqual(
            set(payload["machine_state"]),
            {"layout", "migration", "decisions_root", "detail"},
        )

    def _import(self, payload):
        raw = f"===DEVSTATUS_SYNC_JSON_START:ab===\n{json.dumps(payload)}\n===DEVSTATUS_SYNC_JSON_END:ab===\n"
        with patch("sys.stdin", io.StringIO(raw)):
            sync.cmd_import(base.argparse.Namespace(lock_timeout=5.0, if_rev=0))

    def test_import_refuses_when_the_state_changed_since_export(self):
        payload = self._export()
        payload["items"][0]["status"] = "done"
        payload["expected_remote_state"] = {
            "layout": "toolkit-home",
            "decisions_root": payload["machine_state"]["decisions_root"],
        }
        with self.assertRaisesRegex(sync.SyncFatalError, "changed since export"):
            self._import(payload)
        self.assertEqual(dev_status.load_items()[0]["status"], "open")

    def test_import_refuses_without_an_expected_state(self):
        payload = self._export()
        with self.assertRaisesRegex(sync.SyncFatalError, "changed since export"):
            self._import(payload)

    def test_import_with_a_matching_state_writes(self):
        payload = self._export()
        payload["items"][0]["status"] = "done"
        payload["expected_remote_state"] = {
            k: payload["machine_state"][k] for k in ("layout", "decisions_root")
        }
        self._import(payload)
        self.assertEqual(dev_status.load_items()[0]["status"], "done")

    def test_state_subcommand_prints_this_machines_state(self):
        out = io.StringIO()
        with patch("sys.stdout", out):
            sync.cmd_state(base.argparse.Namespace())
        payload = sync._extract_framed_json(out.getvalue())
        self.assertEqual(payload["protocol_version"], sync.PROTOCOL_VERSION)
        self.assertEqual(payload["machine_state"]["migration"], "idle")


class ToolkitScriptsDirTests(unittest.TestCase):
    MODULES = (
        "dev_status",
        "dev_status_mutation",
        "agent_toolkit_paths",
        "migration_lock",
    )

    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.checkout = self.root / "checkout"
        self.checkout.mkdir()
        for m in self.MODULES:
            (self.checkout / f"{m}.py").write_text("")

    def _install(self, name, modules=MODULES, target=None):
        d = self.root / name
        d.mkdir()
        for m in modules:
            (d / f"{m}.py").symlink_to((target or self.checkout) / f"{m}.py")
        return d

    def test_picks_the_first_complete_install_and_resolves_to_the_checkout(self):
        partial = self._install("new", modules=self.MODULES[:2])
        full = self._install("old")
        self.assertEqual(sync.toolkit_scripts_dir([partial, full]), self.checkout)

    def test_prefers_the_toolkit_home_install_when_complete(self):
        other = self.root / "other-checkout"
        other.mkdir()
        for m in self.MODULES:
            (other / f"{m}.py").write_text("")
        new = self._install("new", target=other)
        old = self._install("old")
        self.assertEqual(sync.toolkit_scripts_dir([new, old]), other)

    def test_an_install_split_across_checkouts_is_skipped(self):
        other = self.root / "other-checkout"
        other.mkdir()
        (other / "migration_lock.py").write_text("")
        split = self.root / "split"
        split.mkdir()
        for m in self.MODULES:
            src = other if m == "migration_lock" else self.checkout
            (split / f"{m}.py").symlink_to(src / f"{m}.py")
        self.assertIsNone(sync.toolkit_scripts_dir([split]))

    def test_no_complete_install_returns_none(self):
        self.assertIsNone(sync.toolkit_scripts_dir([self.root / "missing"]))


class ArtifactRootTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.local_root = self.tmp / "L" / ".agent-toolkit" / "data" / "grill"
        self.local_root.mkdir(parents=True)
        (self.local_root / "spec.md").write_text("spec")
        self.remote_root = "/home/theon/custom/toolkit/data/grill"
        self.item = make_item(
            "foo", related_files=[{"path": str(self.local_root / "spec.md")}]
        )

    def _argv(self, fn):
        with patch("subprocess.run") as run:
            run.return_value = subprocess.CompletedProcess(None, 0, b"", b"")
            fn(
                "fedora",
                [self.item],
                str(self.tmp / "L"),
                "/home/theon",
                20.0,
                20.0,
                quiet=True,
                dry_run=False,
                local_root=str(self.local_root),
                remote_root=self.remote_root,
            )
        return run.call_args[0][0]

    def test_push_anchors_at_each_sides_grill_root(self):
        argv = self._argv(sync.push_artifacts)
        self.assertIn(f"{self.local_root}/./spec.md", argv)
        self.assertEqual(argv[-1], f"fedora:{self.remote_root}/")
        self.assertIn("--mkpath", argv)

    def test_pull_anchors_at_each_sides_grill_root(self):
        argv = self._argv(sync.pull_artifacts)
        self.assertIn(f"fedora:{self.remote_root}/./spec.md", argv)
        self.assertEqual(argv[-1], f"{self.local_root}/")

    def test_grill_root_rewrite_runs_before_the_home_rewrite(self):
        item = make_item(
            "foo",
            related_files=[
                {"path": "/home/theon/custom/toolkit/data/grill/spec.md"},
                {"path": "/home/theon/Workspace/repo/file.py"},
            ],
        )
        out = sync.rewrite_related_files_paths(
            item,
            "/home/theon",
            "/home/yanil",
            (
                "/home/theon/custom/toolkit/data/grill",
                "/home/yanil/.agent-toolkit/data/grill",
            ),
        )
        self.assertEqual(
            [e["path"] for e in out["related_files"]],
            [
                "/home/yanil/.agent-toolkit/data/grill/spec.md",
                "/home/yanil/Workspace/repo/file.py",
            ],
        )


class FinalRemoteStateTests(base.SyncTestCase):
    def _args(self):
        return base.argparse.Namespace(
            lock_timeout=5.0,
            host="fedora",
            remote_script="x",
            ssh_timeout=20.0,
            max_retries=1,
            user_map={"yanil": "/L", "theon": "/R"},
            local_user="yanil",
            remote_user="theon",
            dry_run=False,
            quiet=True,
            verbose=False,
            no_artifacts=True,
            rsync_io_timeout=None,
        )

    def _run(self, final_state):
        self.data_dir.mkdir(parents=True, exist_ok=True)
        dev_status.save_items([make_item("foo", updated="2026-03-01")])
        exported = sync.machine_state()
        payload = {
            "protocol_version": sync.PROTOCOL_VERSION,
            "machine_state": exported,
            "schema_version": {"items": 2, "pending_items": 1},
            "items": [make_item("foo", updated="2026-02-01")],
            "pending_items": [],
            "rev": 0,
        }
        committed = []
        with (
            patch.object(sync, "ssh_export", lambda *a, **k: payload),
            patch.object(sync, "ssh_import", lambda *a, **k: None),
            patch.object(sync, "ssh_state", lambda *a, **k: final_state(exported)),
            patch.object(sync, "local_commit", lambda *a, **k: committed.append(1)),
        ):
            sync.cmd_sync(self._args())
        return committed

    def test_an_unchanged_remote_state_commits(self):
        self.assertEqual(self._run(lambda s: dict(s)), [1])

    def test_a_remote_that_migrated_during_the_sync_is_not_committed_over(self):
        with self.assertRaisesRegex(sync.SyncFatalError, "changed during the sync"):
            self._run(lambda s: {**s, "layout": "toolkit-home"})

    def test_a_remote_now_mid_migration_is_not_committed_over(self):
        with self.assertRaisesRegex(sync.SyncFatalError, "changed during the sync"):
            self._run(lambda s: {**s, "migration": "in-flight"})

    def test_a_remote_mid_migration_at_export_is_refused_before_any_write(self):
        self.data_dir.mkdir(parents=True, exist_ok=True)
        dev_status.save_items([make_item("foo")])
        payload = {
            "protocol_version": sync.PROTOCOL_VERSION,
            "machine_state": {**sync.machine_state(), "migration": "in-flight"},
            "schema_version": {"items": 2, "pending_items": 1},
            "items": [],
            "pending_items": [],
            "rev": 0,
        }
        imported = []
        with (
            patch.object(sync, "ssh_export", lambda *a, **k: payload),
            patch.object(sync, "ssh_import", lambda *a, **k: imported.append(1)),
            self.assertRaisesRegex(sync.SyncFatalError, "mid-migration"),
        ):
            sync.cmd_sync(self._args())
        self.assertEqual(imported, [])


class StatusPreviewTests(base.SyncTestCase):
    def _args(self):
        return base.argparse.Namespace(
            lock_timeout=5.0,
            host="fedora",
            remote_script="x",
            ssh_timeout=20.0,
            max_retries=1,
            user_map={"yanil": str(self.local_home), "theon": "/R"},
            local_user="yanil",
            remote_user="theon",
            quiet=False,
            verbose=False,
        )

    def setUp(self):
        super().setUp()
        self.local_home = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.local_home, ignore_errors=True)
        self.local_grill = self.local_home / ".agent-toolkit" / "data" / "grill"
        self.local_grill.mkdir(parents=True)
        self.spec = self.local_grill / "foo-plan.md"
        self.spec.write_text("spec")

    def test_status_previews_artifacts_under_toolkit_home_decisions_root(self):
        local_grill = str(self.local_grill)
        remote_grill = "/R/.agent-toolkit/data/grill"
        self.data_dir.mkdir(parents=True, exist_ok=True)
        dev_status.save_items(
            [make_item("foo", related_files=[{"path": str(self.spec)}])]
        )
        local_state = {
            "layout": "toolkit-home",
            "migration": "idle",
            "detail": "idle",
            "decisions_root": local_grill,
        }
        remote_state = {
            "layout": "toolkit-home",
            "migration": "idle",
            "detail": "idle",
            "decisions_root": remote_grill,
        }
        payload = {
            "protocol_version": sync.PROTOCOL_VERSION,
            "machine_state": remote_state,
            "schema_version": {"items": 2, "pending_items": 1},
            "items": [],
            "pending_items": [],
            "rev": 0,
        }
        buf = io.StringIO()
        with (
            patch.object(sync, "machine_state", lambda: local_state),
            patch.object(sync, "_LAYOUT_AT_IMPORT", "toolkit-home"),
            patch.object(sync, "ssh_export", lambda *a, **k: payload),
            patch("sys.stdout", buf),
        ):
            sync.cmd_status(self._args())
        out = buf.getvalue()
        self.assertIn(f"artifacts (1 file(s) under {local_grill}/):", out)
        self.assertIn(
            f"push  {local_grill}/./foo-plan.md -> fedora:{remote_grill}/", out
        )
        self.assertNotIn("artifacts: none", out)

    def test_status_refuses_when_remote_mid_migration(self):
        self.data_dir.mkdir(parents=True, exist_ok=True)
        dev_status.save_items([make_item("foo")])
        payload = {
            "protocol_version": sync.PROTOCOL_VERSION,
            "machine_state": {**sync.machine_state(), "migration": "in-flight"},
            "schema_version": {"items": 2, "pending_items": 1},
            "items": [],
            "pending_items": [],
            "rev": 0,
        }
        with (
            patch.object(sync, "ssh_export", lambda *a, **k: payload),
            self.assertRaisesRegex(sync.SyncFatalError, "mid-migration"),
        ):
            sync.cmd_status(self._args())


if __name__ == "__main__":
    unittest.main()
