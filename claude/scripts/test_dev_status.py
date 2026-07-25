#!/usr/bin/env python3
"""Tests for dev_status.py v2. Run with: python3 test_dev_status.py"""

import io
import json
import shutil
import sys
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent))
import dev_status


class _TtyStringIO(io.StringIO):
    def isatty(self):
        return True


def make_item(
    slug,
    status="open",
    blocked_by=None,
    updated="2026-01-01",
    summary=None,
    context="",
    next_steps="",
):
    return {
        "id": slug,
        "created": "2026-01-01",
        "updated": updated,
        "status": status,
        "summary": summary or f"Summary of {slug}",
        "category": "feature",
        "blocked_by": blocked_by or [],
        "related_files": [],
        "context": context,
        "next_steps": next_steps,
    }


class BacklogTestCase(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.data_dir = Path(self.tmpdir) / "backlog"
        self.items_file = self.data_dir / "items.json"
        self.pending_file = self.data_dir / "pending_items.json"
        self.meta_file = self.data_dir / "_meta.json"
        self.lock_file = self.data_dir / ".backlog.lock"
        self._patches = [
            patch.object(dev_status, "DATA_DIR", self.data_dir),
            patch.object(dev_status, "ITEMS_FILE", self.items_file),
            patch.object(dev_status, "PENDING_FILE", self.pending_file),
            patch.object(dev_status, "META_FILE", self.meta_file),
            patch.object(dev_status, "LOCK_FILE", self.lock_file),
        ]
        for p in self._patches:
            p.start()

    def tearDown(self):
        for p in self._patches:
            p.stop()
        shutil.rmtree(self.tmpdir)

    def write_items(self, items):
        self.data_dir.mkdir(parents=True, exist_ok=True)
        data = {"schema_version": 2, "items": items}
        self.items_file.write_text(json.dumps(data, indent=2))

    def write_pending(self, pending_items):
        self.data_dir.mkdir(parents=True, exist_ok=True)
        data = {"schema_version": 1, "items": pending_items}
        self.pending_file.write_text(json.dumps(data, indent=2))

    def read_items(self):
        return dev_status.load_items()

    def read_rev(self):
        return dev_status.load_rev()

    # ── 1: add with valid slug succeeds ──────────────────────────────────────

    def test_01_add_valid_slug(self):
        args = _args(json='{"id": "my-feature", "summary": "Test feature"}')
        dev_status.cmd_add(args)
        items = self.read_items()
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["id"], "my-feature")
        self.assertEqual(items[0]["status"], "open")

    # ── 2: add with missing id exits with helpful suggestion ─────────────────

    def test_02_add_missing_id_exits_with_suggestion(self):
        args = _args(json='{"summary": "Fix the broken widget"}')
        err = io.StringIO()
        with self.assertRaises(SystemExit) as cm:
            with patch("sys.stderr", err):
                dev_status.cmd_add(args)
        self.assertEqual(cm.exception.code, 1)
        msg = err.getvalue()
        self.assertIn("'id' is required", msg)
        self.assertIn("fix-the-broken-widget", msg)

    # ── 3: add with invalid slug rejected ────────────────────────────────────

    def test_03_add_uppercase_slug_rejected(self):
        args = _args(json='{"id": "My-Feature", "summary": "x"}')
        with self.assertRaises(SystemExit):
            dev_status.cmd_add(args)

    def test_03b_add_no_hyphen_slug_rejected(self):
        args = _args(json='{"id": "feature", "summary": "x"}')
        with self.assertRaises(SystemExit):
            dev_status.cmd_add(args)

    def test_03c_add_double_hyphen_slug_rejected(self):
        args = _args(json='{"id": "my--feature", "summary": "x"}')
        with self.assertRaises(SystemExit):
            dev_status.cmd_add(args)

    def test_03d_add_reserved_slug_rejected(self):
        args = _args(json='{"id": "render", "summary": "x"}')
        err = io.StringIO()
        with self.assertRaises(SystemExit):
            with patch("sys.stderr", err):
                dev_status.cmd_add(args)
        self.assertIn("reserved", err.getvalue())

    # ── 4: add with duplicate slug rejected ──────────────────────────────────

    def test_04_add_duplicate_slug_rejected(self):
        self.write_items([make_item("my-feature")])
        args = _args(json='{"id": "my-feature", "summary": "Another one"}')
        err = io.StringIO()
        with self.assertRaises(SystemExit):
            with patch("sys.stderr", err):
                dev_status.cmd_add(args)
        self.assertIn("duplicate", err.getvalue())

    # ── 5: add with blocked_by referencing nonexistent slug rejected ──────────

    def test_05_add_blocked_by_nonexistent_rejected(self):
        args = _args(
            json='{"id": "my-feature", "summary": "x", "blocked_by": ["ghost-slug"]}'
        )
        err = io.StringIO()
        with self.assertRaises(SystemExit):
            with patch("sys.stderr", err):
                dev_status.cmd_add(args)
        self.assertIn("ghost-slug", err.getvalue())

    # ── 6: done N resolves via internal re-render ─────────────────────────────

    def test_06_done_by_number_resolves_correctly(self):
        items = [
            make_item("alpha-item", status="in-progress", updated="2026-04-10"),
            make_item("beta-item", status="open", updated="2026-04-09"),
            make_item("gamma-item", status="open", updated="2026-04-08"),
        ]
        self.write_items(items)
        # Render order: IN PROGRESS (alpha=1), READY (beta=2, gamma=3)
        # done 2 should resolve to beta-item
        args = _args(id="2", if_rev=0)
        dev_status.cmd_done(args)
        result = {i["id"]: i["status"] for i in self.read_items()}
        self.assertEqual(result["beta-item"], "done")
        self.assertEqual(result["alpha-item"], "in-progress")
        self.assertEqual(result["gamma-item"], "open")

    # ── 7: done <slug> marks correct item directly ────────────────────────────

    def test_07_done_by_slug(self):
        self.write_items([make_item("my-task"), make_item("other-task")])
        args = _args(id="my-task")
        dev_status.cmd_done(args)
        result = {i["id"]: i["status"] for i in self.read_items()}
        self.assertEqual(result["my-task"], "done")
        self.assertEqual(result["other-task"], "open")

    # ── 8: rename rewrites id, blocked_by, context, next_steps ──────────────

    def test_08_rename_rewrites_all_references(self):
        items = [
            make_item(
                "old-name",
                context="depends on old-name things",
                next_steps="finish old-name first",
            ),
            make_item(
                "other-item",
                blocked_by=["old-name"],
                context="old-name is blocking this",
            ),
        ]
        self.write_items(items)
        args = _args(old_slug="old-name", new_slug="new-name")
        dev_status.cmd_rename(args)
        result = self.read_items()
        index = {i["id"]: i for i in result}
        self.assertIn("new-name", index)
        self.assertNotIn("old-name", index)
        self.assertEqual(index["other-item"]["blocked_by"], ["new-name"])
        self.assertIn("new-name", index["new-name"]["context"])
        self.assertNotIn("old-name", index["new-name"]["context"])
        self.assertIn("new-name", index["new-name"]["next_steps"])
        self.assertIn("new-name", index["other-item"]["context"])

    # ── 9: rename refuses collision ───────────────────────────────────────────

    def test_09_rename_refuses_collision(self):
        self.write_items([make_item("old-name"), make_item("new-name")])
        args = _args(old_slug="old-name", new_slug="new-name")
        err = io.StringIO()
        with self.assertRaises(SystemExit):
            with patch("sys.stderr", err):
                dev_status.cmd_rename(args)
        self.assertIn("collision", err.getvalue())

    # ── 10: rename refuses nonexistent source slug ────────────────────────────

    def test_10_rename_refuses_nonexistent_source(self):
        self.write_items([make_item("existing-item")])
        args = _args(old_slug="ghost-slug", new_slug="new-name")
        err = io.StringIO()
        with self.assertRaises(SystemExit):
            with patch("sys.stderr", err):
                dev_status.cmd_rename(args)
        self.assertIn("not found", err.getvalue())

    # ── 11: block adds dep; cycle rejected ───────────────────────────────────

    def test_11a_block_adds_dep(self):
        self.write_items([make_item("item-a"), make_item("item-b")])
        args = _args(id="item-b", blocker="item-a")
        dev_status.cmd_block(args)
        index = {i["id"]: i for i in self.read_items()}
        self.assertIn("item-a", index["item-b"]["blocked_by"])

    def test_11b_block_cycle_rejected(self):
        items = [
            make_item("item-a", blocked_by=["item-b"]),
            make_item("item-b"),
        ]
        self.write_items(items)
        args = _args(id="item-b", blocker="item-a")
        err = io.StringIO()
        with self.assertRaises(SystemExit):
            with patch("sys.stderr", err):
                dev_status.cmd_block(args)
        self.assertIn("cycle", err.getvalue())

    # ── 12: done blocker promotes BLOCKED → READY ────────────────────────────

    def test_12_done_blocker_promotes_to_ready(self):
        items = [
            make_item("blocker-item", status="done"),
            make_item("blocked-item", blocked_by=["blocker-item"]),
        ]
        self.write_items(items)
        index = dev_status.build_index(items)
        eff = dev_status.effective_blockers(items[1], index)
        self.assertEqual(eff, [])  # done blocker excluded

        in_progress, ready, blocked, done, done_total = dev_status._render_order(items)
        self.assertIn(items[1], ready)
        self.assertEqual(blocked, [])

    # ── 13: numbering is 1..N globally, contiguous ───────────────────────────

    def test_13_numbering_contiguous(self):
        items = [
            make_item("item-a", status="in-progress"),
            make_item("item-b", status="open"),
            make_item("item-c", status="open"),
            make_item("item-d", status="done"),
        ]
        self.write_items(items)
        out = io.StringIO()
        err = io.StringIO()
        dev_status.render(items, out=out, err=err)
        map_line = err.getvalue().strip()
        self.assertTrue(map_line.startswith("item-map:"))
        self.assertIn("rev=0", map_line)
        # format: "item-map: rev=0 1=backlog:..,2=.."; strip prefix + rev token
        pairs = map_line.replace("item-map: ", "").split(" ", 1)[1].split(",")
        numbers = [int(p.split("=")[0]) for p in pairs if p]
        self.assertEqual(numbers, list(range(1, len(numbers) + 1)))

    # ── 14: atomic write — temp cleaned up on failure ────────────────────────

    def test_14_atomic_write_cleans_temp_on_failure(self):
        items = [make_item("my-item")]
        self.write_items(items)
        original_content = self.items_file.read_text()

        with patch("os.replace", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                dev_status.save_items(items)

        self.assertEqual(self.items_file.read_text(), original_content)
        tmp_files = list(self.data_dir.glob(".items_tmp_*"))
        self.assertEqual(tmp_files, [])

    # ── 15: corrupted JSON fails loudly ──────────────────────────────────────

    def test_15_corrupted_json_fails_loudly(self):
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.items_file.write_text("{not valid json")
        err = io.StringIO()
        with self.assertRaises(SystemExit) as cm:
            with patch("sys.stderr", err):
                dev_status.load_items()
        self.assertEqual(cm.exception.code, 1)
        self.assertIn("corrupted", err.getvalue())

    # ── 16: category tag rendered as bracketed prefix ─────────────────────────

    def test_16_category_tag_rendered_on_each_line(self):
        items = [
            make_item("bug-item", status="open", summary="Fix widget"),
        ]
        items[0]["category"] = "bug"
        out = io.StringIO()
        dev_status.render(items, out=out, err=io.StringIO())
        self.assertIn("[bug] Fix widget", out.getvalue())

    def test_16b_unknown_category_truncated_to_five_chars(self):
        items = [make_item("odd-item", status="open")]
        items[0]["category"] = "documentation"
        out = io.StringIO()
        dev_status.render(items, out=out, err=io.StringIO())
        self.assertIn("[docum]", out.getvalue())

    # ── 17: age suffix on IN PROGRESS/BLOCKED, warning past threshold ────────

    def test_17_age_suffix_on_in_progress_only_not_ready(self):
        old = (date.today() - timedelta(days=3)).isoformat()
        items = [
            make_item("active-item", status="in-progress", updated=old),
            make_item("ready-item", status="open", updated=old),
        ]
        out = io.StringIO()
        dev_status.render(items, out=out, err=io.StringIO())
        text = out.getvalue()
        self.assertIn("· 3d", text)
        # only one "· Nd" occurrence — READY must not get an age suffix
        self.assertEqual(text.count("· 3d"), 1)

    def test_17b_warning_glyph_past_stale_threshold(self):
        fresh = (date.today() - timedelta(days=2)).isoformat()
        stale = (date.today() - timedelta(days=8)).isoformat()
        items = [
            make_item("fresh-item", status="in-progress", updated=fresh),
            make_item(
                "stale-item", status="in-progress", updated=stale, summary="Stuck task"
            ),
        ]
        out = io.StringIO()
        dev_status.render(items, out=out, err=io.StringIO())
        lines = out.getvalue().splitlines()
        fresh_line = next(
            ln for ln in lines if "fresh-item" in ln or "Summary of fresh-item" in ln
        )
        stale_line = next(ln for ln in lines if "Stuck task" in ln)
        self.assertNotIn("⚠️", fresh_line)
        self.assertIn("⚠️", stale_line)

    # ── 18: DONE header reports truncation ─────────────────────────────────

    def test_18_done_header_shows_hidden_count_when_truncated(self):
        items = [
            make_item(f"done-item-{i}", status="done", updated="2026-01-01")
            for i in range(7)
        ]
        out = io.StringIO()
        dev_status.render(items, out=out, err=io.StringIO())
        self.assertIn("DONE (showing 5 of 7)", out.getvalue())

    def test_18b_done_header_plain_when_not_truncated(self):
        items = [make_item("only-done-item", status="done")]
        out = io.StringIO()
        dev_status.render(items, out=out, err=io.StringIO())
        text = out.getvalue()
        self.assertIn("DONE", text)
        self.assertNotIn("showing", text)

    # ── 19: color gated on isatty ─────────────────────────────────────────────

    def test_19_no_color_codes_when_not_a_tty(self):
        items = [make_item("plain-item", status="open")]
        out = io.StringIO()
        dev_status.render(items, out=out, err=io.StringIO())
        self.assertNotIn("\x1b[", out.getvalue())

    def test_19b_color_codes_present_when_tty(self):
        items = [make_item("colored-item", status="open")]
        out = _TtyStringIO()
        dev_status.render(items, out=out, err=io.StringIO())
        self.assertIn("\x1b[", out.getvalue())

    # ── 20: rev increments on each mutating op, not on reads ───────────────

    def test_20a_rev_increments_on_add(self):
        self.assertEqual(self.read_rev(), 0)
        args = _args(json='{"id": "my-item", "summary": "x"}')
        dev_status.cmd_add(args)
        self.assertEqual(self.read_rev(), 1)

    def test_20b_rev_increments_on_start(self):
        self.write_items([make_item("my-item")])
        dev_status.cmd_start(_args(id="my-item"))
        self.assertEqual(self.read_rev(), 1)

    def test_20c_rev_increments_on_done(self):
        self.write_items([make_item("my-item", status="in-progress")])
        dev_status.cmd_done(_args(id="my-item"))
        self.assertEqual(self.read_rev(), 1)

    def test_20d_rev_increments_on_update(self):
        self.write_items([make_item("my-item")])
        args = _args(id="my-item", patch='{"context": "ctx"}')
        dev_status.cmd_update(args)
        self.assertEqual(self.read_rev(), 1)

    def test_20e_rev_increments_on_block(self):
        self.write_items([make_item("item-a"), make_item("item-b")])
        dev_status.cmd_block(_args(id="item-b", blocker="item-a"))
        self.assertEqual(self.read_rev(), 1)

    def test_20f_rev_increments_on_unblock(self):
        self.write_items([make_item("item-a"), make_item("item-b", blocked_by=["item-a"])])
        dev_status.cmd_unblock(_args(id="item-b", blocker="item-a"))
        self.assertEqual(self.read_rev(), 1)

    def test_20g_rev_increments_on_rename(self):
        self.write_items([make_item("old-name")])
        dev_status.cmd_rename(_args(old_slug="old-name", new_slug="new-name"))
        self.assertEqual(self.read_rev(), 1)

    def test_20h_rev_increments_on_prune_only_when_removed(self):
        old = (date.today() - timedelta(days=20)).isoformat()
        self.write_items([make_item("done-old", status="done", updated=old)])
        dev_status.cmd_prune(_args(force=True))
        self.assertEqual(self.read_rev(), 1)

    def test_20i_rev_unchanged_on_prune_nothing(self):
        self.write_items([make_item("my-item")])
        dev_status.cmd_prune(_args(force=True))
        self.assertEqual(self.read_rev(), 0)

    def test_20j_rev_increments_on_pending_add(self):
        args = _args(
            json='{"id": "pend-item", "description": "waiting", "kind": "email"}'
        )
        dev_status.cmd_pending_add(args)
        self.assertEqual(self.read_rev(), 1)

    def test_20k_rev_increments_on_pending_update(self):
        self.write_pending(
            [{"id": "pend-item", "created": "2026-01-01", "updated": "2026-01-01",
              "status": "waiting_for_reply", "description": "w", "kind": "email"}]
        )
        dev_status.cmd_pending_update(
            _args(id="pend-item", patch='{"status": "reply_received"}')
        )
        self.assertEqual(self.read_rev(), 1)

    def test_20l_rev_unchanged_on_reads(self):
        self.write_items([make_item("my-item")])
        out, err = io.StringIO(), io.StringIO()
        with patch("sys.stdout", out), patch("sys.stderr", err):
            dev_status.cmd_render(_args())
            dev_status.cmd_list(_args())
        dev_status.cmd_show(_args(id="my-item"))
        self.assertEqual(self.read_rev(), 0)

    # ── 21: numeric id without --if-rev refused, no write ─────────────────

    def test_21_numeric_id_without_if_rev_refused(self):
        self.write_items([make_item("item-a", status="in-progress")])
        err = io.StringIO()
        with self.assertRaises(SystemExit) as cm:
            with patch("sys.stderr", err):
                dev_status.cmd_start(_args(id="1", if_rev=None))
        self.assertEqual(cm.exception.code, 1)
        self.assertIn("requires --if-rev", err.getvalue())
        # no write, no rev bump
        self.assertEqual(self.read_rev(), 0)
        self.assertEqual(
            {i["id"]: i["status"] for i in self.read_items()}["item-a"],
            "in-progress",
        )

    # ── 22: stale --if-rev refused, no write ──────────────────────────────

    def test_22_numeric_id_stale_if_rev_refused(self):
        self.write_items([make_item("item-a", status="in-progress")])
        # bump rev to 1 via a slug mutation first
        dev_status.cmd_done(_args(id="item-a"))
        self.assertEqual(self.read_rev(), 1)
        items = self.read_items()
        items[0]["status"] = "in-progress"
        items[0]["updated"] = "2026-01-01"
        self.write_items(items)
        self.write_text_meta(0)  # simulate a stale caller's view
        # actually set rev back to 0 on disk to force mismatch with stale --if-rev 0
        err = io.StringIO()
        with self.assertRaises(SystemExit) as cm:
            with patch("sys.stderr", err):
                dev_status.cmd_start(_args(id="1", if_rev=0))
        self.assertEqual(cm.exception.code, 1)
        self.assertIn("stale rev", err.getvalue())

    # ── 23: matching --if-rev succeeds ─────────────────────────────────────

    def test_23_numeric_id_matching_if_rev_succeeds(self):
        self.write_items([make_item("item-a", status="in-progress")])
        dev_status.cmd_done(_args(id="item-a"))  # rev -> 1, item now done
        # reset item to in-progress on disk without bumping rev, so numeric call is meaningful
        items = self.read_items()
        items[0]["status"] = "in-progress"
        self.write_items(items)
        cur_rev = self.read_rev()
        dev_status.cmd_start(_args(id="1", if_rev=cur_rev))
        self.assertEqual(
            {i["id"]: i["status"] for i in self.read_items()}["item-a"],
            "in-progress",
        )
        self.assertEqual(self.read_rev(), cur_rev + 1)

    # ── 24: slug id never requires --if-rev ────────────────────────────────

    def test_24_slug_id_never_requires_if_rev(self):
        self.write_items([make_item("my-task", status="in-progress")])
        dev_status.cmd_done(_args(id="my-task"))
        self.assertEqual(
            {i["id"]: i["status"] for i in self.read_items()}["my-task"], "done"
        )

    # ── 25: _meta.json lazy-created on first mutation ─────────────────────

    def test_25_meta_file_lazy_created(self):
        self.assertFalse(self.meta_file.exists())
        dev_status.cmd_add(_args(json='{"id": "my-item", "summary": "x"}'))
        self.assertTrue(self.meta_file.exists())
        self.assertEqual(json.loads(self.meta_file.read_text()), {"rev": 1})

    # ── 26: concurrent writes serialize, no lost update ───────────────────

    def test_26_concurrent_writes_serialize_no_lost_update(self):
        import threading
        self.write_items([make_item("item-a")])
        results = {}

        def writer(name):
            # each thread opens its own fd on LOCK_FILE (per open-file-description flock)
            dev_status.cmd_add(
                _args(json=f'{{"id": "child-{name}", "summary": "x"}}')
            )
            results[name] = self.read_rev()

        # hold the lock in the main thread so the spawned writers block
        with dev_status.backlog_lock():
            t1 = threading.Thread(target=writer, args=("one",))
            t2 = threading.Thread(target=writer, args=("two",))
            t1.start()
            t2.start()
            # give them a moment to block on flock
            import time
            time.sleep(0.1)
            self.assertTrue(t1.is_alive())
            self.assertTrue(t2.is_alive())
            # now release; both should proceed serialized
        t1.join(timeout=5)
        t2.join(timeout=5)
        self.assertFalse(t1.is_alive())
        self.assertFalse(t2.is_alive())
        # both writes landed + rev bumped twice (no lost update)
        ids = {i["id"] for i in self.read_items()}
        self.assertIn("child-one", ids)
        self.assertIn("child-two", ids)
        self.assertEqual(self.read_rev(), 2)

    # ── helper for test_22: write _meta.json with a chosen rev ─────────────

    def write_text_meta(self, rev):
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.meta_file.write_text(json.dumps({"rev": rev}))


# ── arg helper ────────────────────────────────────────────────────────────────


class _args:
    """Minimal argparse.Namespace stand-in."""

    if_rev = None  # default; argparse always sets --if-rev (default None)

    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)


if __name__ == "__main__":
    unittest.main(verbosity=2)
