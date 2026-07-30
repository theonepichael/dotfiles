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
    created="2026-01-01",
    priority=None,
):
    item = {
        "id": slug,
        "created": created,
        "updated": updated,
        "status": status,
        "summary": summary or f"Summary of {slug}",
        "category": "feature",
        "blocked_by": blocked_by or [],
        "related_files": [],
        "context": context,
        "next_steps": next_steps,
    }
    if priority is not None:
        item["priority"] = priority
    return item


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

    def test_03e_add_hyphenated_reserved_prefix_accepted(self):
        # Exact-match invariant: only the bare verb is reserved. A slug like
        # `remove-probe` (first hyphen-segment is a reserved verb) must be
        # accepted — argparse never confuses it with the `remove`
        # subcommand, which is parsed positionally from argv. Prefix-match
        # refusal was considered and rejected (2026-07-25) to keep natural
        # slugs like `update-deps` addable.
        args = _args(json='{"id": "remove-probe", "summary": "x"}')
        dev_status.cmd_add(args)
        items = self.read_items()
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["id"], "remove-probe")

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

        in_progress, ready, blocked, done = dev_status._render_order(items)
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

    # ── 18: DONE section is a recency window, not a fixed top-5 ────────────

    def test_18_done_section_keeps_only_recency_window_items(self):
        recent = (date.today() - timedelta(days=1)).isoformat()
        stale = (date.today() - timedelta(days=10)).isoformat()
        items = [
            make_item("recent", status="done", updated=recent),
            make_item("stale", status="done", updated=stale),
        ]
        out = io.StringIO()
        dev_status.render(items, out=out, err=io.StringIO())
        text = out.getvalue()
        self.assertIn("DONE", text)
        self.assertIn("recent", text)
        self.assertNotIn("stale", text)
        # No "(showing N of M)" denominator anymore.
        self.assertNotIn("showing", text)

    def test_18b_done_section_omitted_when_window_empty(self):
        stale = (date.today() - timedelta(days=10)).isoformat()
        items = [make_item("stale", status="done", updated=stale)]
        out = io.StringIO()
        dev_status.render(items, out=out, err=io.StringIO())
        self.assertNotIn("DONE", out.getvalue())

    def test_18c_done_recency_keys_on_completed_at(self):
        # `completed_at` stamps the actual completion; `updated` getting
        # bumped later (e.g. an edit to a done item) must NOT resurface a
        # stale completion into the window. Regression test for the exact
        # bug class `completed_at`-first lookup prevents.
        old_completion = (date.today() - timedelta(days=10)).isoformat()
        items = [
            make_item(
                "edited-old",
                status="done",
                updated=date.today().isoformat(),
            ),
        ]
        items[0]["completed_at"] = old_completion
        _, _, _, done = dev_status._render_order(items)
        self.assertEqual([i["id"] for i in done], [])

    def test_18d_done_recency_falls_back_to_updated_without_completed_at(self):
        # Legacy done items without `completed_at` fall back to `updated`.
        recent = (date.today() - timedelta(days=1)).isoformat()
        items = [make_item("legacy", status="done", updated=recent)]
        _, _, _, done = dev_status._render_order(items)
        self.assertEqual([i["id"] for i in done], ["legacy"])

    def test_18e_done_section_orders_by_completed_at_not_updated(self):
        # Regression test: an item finished earlier but edited afterward
        # (bumping `updated`) must not outrank an item that finished more
        # recently. Both fall inside the recency window; only completion
        # order should decide DONE ordering.
        items = [
            make_item(
                "finished-earlier-edited-later",
                status="done",
                updated=date.today().isoformat(),
            ),
            make_item(
                "finished-later-not-edited",
                status="done",
                updated=(date.today() - timedelta(days=1)).isoformat(),
            ),
        ]
        items[0]["completed_at"] = (date.today() - timedelta(days=1)).isoformat()
        items[1]["completed_at"] = date.today().isoformat()
        _, _, _, done = dev_status._render_order(items)
        self.assertEqual(
            [i["id"] for i in done],
            ["finished-later-not-edited", "finished-earlier-edited-later"],
        )

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
        self.write_items(
            [make_item("item-a"), make_item("item-b", blocked_by=["item-a"])]
        )
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
            [
                {
                    "id": "pend-item",
                    "created": "2026-01-01",
                    "updated": "2026-01-01",
                    "status": "waiting_for_reply",
                    "description": "w",
                    "kind": "email",
                }
            ]
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

    def test_20m_list_status_filter(self):
        self.write_items(
            [
                make_item("a-open", status="open"),
                make_item("b-done", status="done"),
                make_item("c-progress", status="in-progress"),
                make_item("d-done", status="done"),
            ]
        )
        for value in sorted(dev_status.VALID_STATUSES):
            out, err = io.StringIO(), io.StringIO()
            with patch("sys.stdout", out), patch("sys.stderr", err):
                dev_status.cmd_list(_args(status=value))
            lines = [
                ln
                for ln in out.getvalue().splitlines()
                if ln and not ln.startswith("#")
            ]
            ids = {ln.split("\t")[0] for ln in lines}
            statuses = {ln.split("\t")[1] for ln in lines}
            self.assertEqual(statuses, {value}, f"status={value}")
            if value == "done":
                self.assertEqual(ids, {"b-done", "d-done"})
            elif value == "open":
                self.assertEqual(ids, {"a-open"})
            elif value == "in-progress":
                self.assertEqual(ids, {"c-progress"})

    def test_20n_list_status_invalid_rejected(self):
        # argparse rejects unknown --status values with exit code 2.
        err = io.StringIO()
        with patch("sys.stderr", err):
            with self.assertRaises(SystemExit) as cm:
                with patch("sys.argv", ["dev_status", "list", "--status", "bogus"]):
                    dev_status.main()
        self.assertEqual(cm.exception.code, 2)
        self.assertIn("bogus", err.getvalue())

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
        # slug mutation bumps rev to 1
        dev_status.cmd_done(_args(id="item-a"))
        self.assertEqual(self.read_rev(), 1)
        # reset item to in-progress on disk WITHOUT bumping rev (disk rev stays 1)
        items = self.read_items()
        items[0]["status"] = "in-progress"
        self.write_items(items)
        # caller still holds stale rev 0 and uses a numeric id — must refuse
        err = io.StringIO()
        with self.assertRaises(SystemExit) as cm:
            with patch("sys.stderr", err):
                dev_status.cmd_start(_args(id="1", if_rev=0))
        self.assertEqual(cm.exception.code, 1)
        self.assertIn("stale rev", err.getvalue())
        # no write, rev unchanged
        self.assertEqual(self.read_rev(), 1)
        self.assertEqual(
            {i["id"]: i["status"] for i in self.read_items()}["item-a"],
            "in-progress",
        )

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
            dev_status.cmd_add(_args(json=f'{{"id": "child-{name}", "summary": "x"}}'))
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

    # ── 27-36: priority field (high/normal/low, absence == normal) ───────────

    def test_27_add_priority_high_present_in_show(self):
        dev_status.cmd_add(
            _args(json='{"id": "p-high", "summary": "x", "priority": "high"}')
        )
        items = self.read_items()
        self.assertEqual(items[0].get("priority"), "high")

    def test_28_add_priority_absent_omits_key(self):
        dev_status.cmd_add(_args(json='{"id": "p-none", "summary": "x"}'))
        items = self.read_items()
        self.assertNotIn("priority", items[0])

    def test_29_add_invalid_priority_refused(self):
        err = io.StringIO()
        with self.assertRaises(SystemExit) as cm:
            with patch("sys.stderr", err):
                dev_status.cmd_add(
                    _args(json='{"id": "p-bad", "summary": "x", "priority": "urgent"}')
                )
        self.assertEqual(cm.exception.code, 1)
        msg = err.getvalue()
        self.assertIn("invalid priority 'urgent'", msg)
        self.assertIn("high, low, normal", msg)
        self.assertEqual(len(self.read_items()), 0)

    def test_30_update_priority_high_floats_to_top_of_ready(self):
        # FIFO setup: A (oldest), B, C (newest) — READY renders A,B,C
        self.write_items(
            [
                make_item("a", created="2026-01-01"),
                make_item("b", created="2026-01-02"),
                make_item("c", created="2026-01-03"),
            ]
        )
        dev_status.cmd_update(_args(id="b", patch='{"priority": "high"}'))
        _, ready, _, _ = dev_status._render_order(self.read_items())
        self.assertEqual([i["id"] for i in ready], ["b", "a", "c"])

    def test_31_update_priority_low_sinks_to_bottom_of_ready(self):
        self.write_items(
            [
                make_item("a", created="2026-01-01"),
                make_item("b", created="2026-01-02"),
                make_item("c", created="2026-01-03"),
            ]
        )
        dev_status.cmd_update(_args(id="a", patch='{"priority": "low"}'))
        _, ready, _, _ = dev_status._render_order(self.read_items())
        self.assertEqual([i["id"] for i in ready], ["b", "c", "a"])

    def test_32_priority_does_not_move_item_after_update_other_field(self):
        self.write_items(
            [
                make_item("a", created="2026-01-01"),
                make_item("b", created="2026-01-02", priority="high"),
                make_item("c", created="2026-01-03"),
            ]
        )
        # B starts at top due to high priority.
        _, ready_before, _, _ = dev_status._render_order(self.read_items())
        self.assertEqual([i["id"] for i in ready_before], ["b", "a", "c"])
        # Updating B's context bumps `updated` but must not change READY order.
        dev_status.cmd_update(_args(id="b", patch='{"context": "x"}'))
        _, ready_after, _, _ = dev_status._render_order(self.read_items())
        self.assertEqual([i["id"] for i in ready_after], ["b", "a", "c"])

    def test_33_blocked_priority_secondary_within_blocker_count(self):
        # Three blocked items, all with 1 blocker each, created A<B<C.
        # B is high-priority; A,C normal. BLOCKED order should be B,A,C —
        # priority lands between blocker-count and updated-asc tiebreak.
        # All share updated==created so the A-vs-C tiebreak is created-asc.
        blocker = make_item("blocker", status="open", updated="2026-01-01")
        self.write_items(
            [
                blocker,
                make_item(
                    "a",
                    created="2026-01-01",
                    updated="2026-01-01",
                    blocked_by=["blocker"],
                ),
                make_item(
                    "b",
                    created="2026-01-02",
                    updated="2026-01-02",
                    blocked_by=["blocker"],
                    priority="high",
                ),
                make_item(
                    "c",
                    created="2026-01-03",
                    updated="2026-01-03",
                    blocked_by=["blocker"],
                ),
            ]
        )
        _, _, blocked, _ = dev_status._render_order(self.read_items())
        self.assertEqual([i["id"] for i in blocked], ["b", "a", "c"])

    def test_34_done_ignores_priority(self):
        old = (date.today() - timedelta(days=10)).isoformat()
        newer = (date.today() - timedelta(days=1)).isoformat()
        newest = date.today().isoformat()
        self.write_items(
            [
                make_item("old", status="done", updated=old, priority="high"),
                make_item("newer", status="done", updated=newer),
                make_item("newest", status="done", updated=newest),
            ]
        )
        _, _, _, done = dev_status._render_order(self.read_items())
        # Only the two in-window items appear, in updated-desc order; the
        # high-priority "old" is dropped by the recency window (and would
        # not float even if included — done is pure updated-desc).
        self.assertEqual([i["id"] for i in done], ["newest", "newer"])

    def test_35_render_priority_badge_for_high_and_low(self):
        self.write_items(
            [
                make_item("hip", priority="high"),
                make_item("lop", priority="low"),
                make_item("nor"),
            ]
        )
        out = _TtyStringIO()
        dev_status.render(self.read_items(), out=out, err=io.StringIO())
        rendered = out.getvalue()
        # _priority_glyph renders ▲ for high, ▽ for low, and ·
        # for absent/normal on every row (no blank gutter) -- this test
        # previously asserted literal "\u00b7high"/"\u00b7low" text, which
        # _priority_glyph has never emitted; fixed to check the glyph that
        # actually appears on each item's own line.
        hip_line = next(ln for ln in rendered.splitlines() if "Summary of hip" in ln)
        lop_line = next(ln for ln in rendered.splitlines() if "Summary of lop" in ln)
        nor_line = next(ln for ln in rendered.splitlines() if "Summary of nor" in ln)
        self.assertIn("\u25b2", hip_line)
        self.assertIn("\u25bd", lop_line)
        # "nor" has no explicit priority -- still gets the normal-priority
        # middle-dot glyph, not the high/low triangle.
        self.assertIn("\u00b7", nor_line)
        self.assertNotIn("\u25b2", nor_line)
        self.assertNotIn("\u25bd", nor_line)

    def test_36_render_priority_default_for_unknown_value_is_normal(self):
        # Bypass validation: hand-write an item with priority:"urgent".
        items = [
            make_item("weird", priority="urgent"),
            make_item("ok"),
        ]
        # _render_order must not raise; unknown collapses to normal's rank.
        _, ready, _, _ = dev_status._render_order(items)
        self.assertEqual([i["id"] for i in ready], ["weird", "ok"])
        # _priority_rank treats both as rank 1 (normal).
        self.assertEqual(
            dev_status._priority_rank(items[0]), dev_status._priority_rank(items[1])
        )

    # ── 37: remove by slug deletes one item ─────────────────────────────────

    def test_37_remove_by_slug_deletes_item(self):
        self.write_items([make_item("item-a"), make_item("item-b")])
        err = io.StringIO()
        with patch("sys.stderr", err):
            dev_status.cmd_remove(_args(id="item-a"))
        self.assertEqual(len(self.read_items()), 1)
        self.assertEqual(self.read_items()[0]["id"], "item-b")
        self.assertIn("[remove] item-a: Summary of item-a", err.getvalue())

    # ── 38: remove by number resolves through the unified render order ──────

    def test_38_remove_by_number_resolves_via_render(self):
        # two open items; render orders them by created (FIFO), so a=1, b=2.
        self.write_items([make_item("item-a"), make_item("item-b")])
        err = io.StringIO()
        with patch("sys.stderr", err):
            dev_status.cmd_remove(_args(id="2", if_rev=0))
        remaining = [i["id"] for i in self.read_items()]
        self.assertEqual(remaining, ["item-a"])
        self.assertIn("[remove] 2 → item-b: Summary of item-b", err.getvalue())

    # ── 39: numeric id without --if-rev refused, no write ──────────────────

    def test_39_remove_numeric_without_if_rev_refused(self):
        self.write_items([make_item("item-a"), make_item("item-b")])
        err = io.StringIO()
        with self.assertRaises(SystemExit) as cm:
            with patch("sys.stderr", err):
                dev_status.cmd_remove(_args(id="1"))
        self.assertEqual(cm.exception.code, 1)
        self.assertIn("requires --if-rev", err.getvalue())
        # no write, no rev bump
        self.assertEqual(self.read_rev(), 0)
        self.assertEqual({i["id"] for i in self.read_items()}, {"item-a", "item-b"})

    # ── 40: stale --if-rev refused, no write ───────────────────────────────

    def test_40_remove_numeric_stale_if_rev_refused(self):
        self.write_items([make_item("item-a"), make_item("item-b")])
        err = io.StringIO()
        with self.assertRaises(SystemExit) as cm:
            with patch("sys.stderr", err):
                dev_status.cmd_remove(_args(id="1", if_rev=99))
        self.assertEqual(cm.exception.code, 1)
        self.assertIn("stale rev", err.getvalue())
        self.assertEqual(self.read_rev(), 0)
        self.assertEqual({i["id"] for i in self.read_items()}, {"item-a", "item-b"})

    # ── 41: slug id never requires --if-rev ────────────────────────────────

    def test_41_remove_slug_never_requires_if_rev(self):
        # write items, bump rev by writing meta directly so rev is non-zero
        self.write_items([make_item("item-a")])
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.meta_file.write_text(json.dumps({"rev": 7}))
        # slug call succeeds without --if-rev regardless of rev state
        dev_status.cmd_remove(_args(id="item-a"))
        self.assertEqual(self.read_items(), [])

    # ── 42: pending item refused by require_kind ────────────────────────────

    def test_42_remove_pending_refused(self):
        self.write_pending(
            [
                {
                    "id": "pend-item",
                    "description": "waiting",
                    "kind": "email",
                    "status": "waiting_for_reply",
                    "created": "2026-01-01",
                    "updated": "2026-01-01",
                }
            ]
        )
        # No backlog items, so the pending item sits at render position 1;
        # pass --if-rev to get past the rev-guard and reach require_kind.
        err = io.StringIO()
        with self.assertRaises(SystemExit) as cm:
            with patch("sys.stderr", err):
                dev_status.cmd_remove(_args(id="1", if_rev=0))
        self.assertEqual(cm.exception.code, 1)
        self.assertIn("is a pending item", err.getvalue())
        # pending item survives intact
        self.assertEqual(len(dev_status.load_pending()), 1)
        self.assertEqual(dev_status.load_pending()[0]["id"], "pend-item")

    # ── 43: unknown slug exits cleanly, no write ────────────────────────────

    def test_43_remove_unknown_slug_exits(self):
        self.write_items([make_item("item-a")])
        err = io.StringIO()
        with self.assertRaises(SystemExit) as cm:
            with patch("sys.stderr", err):
                dev_status.cmd_remove(_args(id="nope-item"))
        self.assertEqual(cm.exception.code, 1)
        # resolve_id surfaces not-found before _backlog_mutation's per-cmd
        # check would (so an unknown slug doesn't get mis-resolved as
        # "wrong kind" by require_kind).
        self.assertIn("[resolve] not found: nope-item", err.getvalue())
        self.assertEqual(len(self.read_items()), 1)
        self.assertEqual(self.read_rev(), 0)

    # ── 44: out-of-range numeric exits via resolve_id, no write ─────────────

    def test_44_remove_unknown_number_exits(self):
        self.write_items([make_item("item-a")])
        err = io.StringIO()
        with self.assertRaises(SystemExit) as cm:
            with patch("sys.stderr", err):
                dev_status.cmd_remove(_args(id="9", if_rev=0))
        self.assertEqual(cm.exception.code, 1)
        self.assertIn("no item at position 9", err.getvalue())
        self.assertEqual(len(self.read_items()), 1)
        self.assertEqual(self.read_rev(), 0)

    # ── 45: remove PURGES inbound blocked_by refs (bug #4 fix — the previous
    #         behavior was a regression-lock on the bug, leaving a dangling ref
    #         that effective_blockers treated as unresolved, retroactively
    #         flipping dependents from READY into BLOCKED).

    def test_45_remove_purges_inbound_blocked_by_refs(self):
        self.write_items(
            [
                make_item("blocker", status="done"),
                make_item("dep", blocked_by=["blocker"]),
            ]
        )
        dev_status.cmd_remove(_args(id="blocker"))
        remaining = self.read_items()
        self.assertEqual([i["id"] for i in remaining], ["dep"])
        # blocked_by has been purged — no dangling reference to the
        # removed slug, so effective_blockers is empty (dep moves back to
        # READY) instead of treating the missing slug as unresolved.
        self.assertEqual(remaining[0]["blocked_by"], [])
        index = dev_status.build_index(remaining)
        self.assertEqual(dev_status.effective_blockers(remaining[0], index), [])

    # ── 46: remove bumps rev exactly once ──────────────────────────────────

    def test_46_remove_bumps_rev(self):
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.meta_file.write_text(json.dumps({"rev": 5}))
        self.write_items([make_item("item-a"), make_item("item-b")])
        dev_status.cmd_remove(_args(id="item-a"))
        self.assertEqual(self.read_rev(), 6)

    # ── 47: remove subparser has no --force flag ────────────────────────────

    def test_47_remove_no_force_flag_required(self):
        # The remove subparser must parse a bare "remove <id>" call without
        # error (no --force required for symmetry with start/done/etc.), and
        # must reject --force (it is not a registered argument of remove).
        self.write_items([make_item("item-a")])
        out = io.StringIO()
        err = io.StringIO()
        with (
            patch("sys.argv", ["dev_status", "remove", "item-a"]),
            patch("sys.stdout", out),
            patch("sys.stderr", err),
        ):
            try:
                dev_status.main()
            except SystemExit as e:
                self.fail(
                    f"argparse rejected a --force-free remove call: {e.code} / "
                    f"stderr={err.getvalue()!r}"
                )
        # --force is not a registered argument of the remove subparser.
        with self.assertRaises(SystemExit):
            with (
                patch("sys.argv", ["dev_status", "remove", "item-a", "--force"]),
                patch("sys.stderr", io.StringIO()),
                patch("sys.stdout", io.StringIO()),
            ):
                dev_status.main()

    # ── 48: metavar string lists remove between rename and block ───────────

    def test_48_remove_acked_in_help_metavar(self):
        # The top-level subparsers metavar is the {…} set shown next to the
        # program name in --help. Parse with -h and capture the usage line.
        out = io.StringIO()
        with self.assertRaises(SystemExit):
            with patch("sys.argv", ["dev_status", "-h"]), patch("sys.stdout", out):
                dev_status.main()
        usage = out.getvalue()
        # rename appears before remove, remove before block in the metavar.
        self.assertLess(usage.index("rename"), usage.index("remove"))
        self.assertLess(usage.index("remove"), usage.index("block"))

    # ── 49-54: update field allowlist + pending blocking validation ────────

    def test_49_update_unrecognized_field_rejected(self):
        self.write_items([make_item("a")])
        rev_before = self.read_rev()
        err = io.StringIO()
        with self.assertRaises(SystemExit) as cm:
            with patch("sys.stderr", err):
                dev_status.cmd_update(_args(id="a", patch='{"typo_sumamry": "oops"}'))
        self.assertEqual(cm.exception.code, 1)
        self.assertIn("unrecognized field(s): typo_sumamry", err.getvalue())
        self.assertNotIn("typo_sumamry", self.read_items()[0])
        self.assertEqual(self.read_rev(), rev_before)

    def test_50_update_mutable_fields_all_accepted(self):
        # blocked_by is excluded from this test: cmd_update refuses
        # blocked_by patches outright (see test_bug03), redirecting to
        # block/unblock so update's raw merge can't bypass their
        # existence/cycle/self-block checks.
        self.write_items([make_item("a"), make_item("b")])
        patch_json = json.dumps(
            {
                "summary": "new summary",
                "category": "bug",
                "related_files": [{"path": "/x", "note": "y"}],
                "context": "ctx",
                "next_steps": "next",
                "priority": "high",
                "status": "in-progress",
            }
        )
        dev_status.cmd_update(_args(id="a", patch=patch_json))
        item = dev_status.build_index(self.read_items())["a"]
        self.assertEqual(item["summary"], "new summary")
        self.assertEqual(item["category"], "bug")
        self.assertEqual(item["related_files"], [{"path": "/x", "note": "y"}])
        self.assertEqual(item["context"], "ctx")
        self.assertEqual(item["next_steps"], "next")
        self.assertEqual(item["priority"], "high")
        self.assertEqual(item["status"], "in-progress")

    def test_51_update_priority_null_unsets_key(self):
        self.write_items([make_item("a", priority="high")])
        dev_status.cmd_update(_args(id="a", patch='{"priority": null}'))
        item = dev_status.build_index(self.read_items())["a"]
        self.assertNotIn("priority", item)

    def test_52_update_invalid_priority_rejected(self):
        self.write_items([make_item("a")])
        err = io.StringIO()
        with self.assertRaises(SystemExit) as cm:
            with patch("sys.stderr", err):
                dev_status.cmd_update(_args(id="a", patch='{"priority": "urgent"}'))
        self.assertEqual(cm.exception.code, 1)
        self.assertIn("invalid priority 'urgent'", err.getvalue())
        self.assertNotIn("priority", dev_status.build_index(self.read_items())["a"])

    def test_53_pending_add_blocking_unknown_slug_rejected(self):
        self.write_items([make_item("a")])
        err = io.StringIO()
        with self.assertRaises(SystemExit) as cm:
            with patch("sys.stderr", err):
                dev_status.cmd_pending_add(
                    _args(
                        json='{"id": "wait-x", "description": "waiting", '
                        '"kind": "email", "blocking": ["ghost-slug"]}'
                    )
                )
        self.assertEqual(cm.exception.code, 1)
        self.assertIn("blocking references unknown slug: ghost-slug", err.getvalue())
        self.assertEqual(dev_status.load_pending(), [])

    def test_54_pending_add_blocking_known_slug_accepted(self):
        self.write_items([make_item("a")])
        dev_status.cmd_pending_add(
            _args(
                json='{"id": "wait-x", "description": "waiting", '
                '"kind": "email", "blocking": ["a"]}'
            )
        )
        pending = dev_status.load_pending()
        self.assertEqual(pending[0]["blocking"], ["a"])

    def test_55_add_blocker_reminder_shown_with_ready_item(self):
        self.write_items([make_item("a")])
        err = io.StringIO()
        with patch("sys.stderr", err):
            dev_status.cmd_add(_args(json='{"id": "new-item", "summary": "New item"}'))
        self.assertIn(
            "check the READY/IN PROGRESS items above for blocker relationships",
            err.getvalue(),
        )

    def test_56_add_blocker_reminder_shown_with_in_progress_item(self):
        self.write_items([make_item("a", status="in-progress")])
        err = io.StringIO()
        with patch("sys.stderr", err):
            dev_status.cmd_add(_args(json='{"id": "new-item", "summary": "New item"}'))
        self.assertIn(
            "check the READY/IN PROGRESS items above for blocker relationships",
            err.getvalue(),
        )

    def test_57_add_blocker_reminder_silent_when_only_new_item(self):
        err = io.StringIO()
        with patch("sys.stderr", err):
            dev_status.cmd_add(_args(json='{"id": "new-item", "summary": "New item"}'))
        self.assertNotIn("check the READY/IN PROGRESS items", err.getvalue())

    def test_58_add_blocker_reminder_silent_when_none_ready_or_in_progress(self):
        # "b" is blocked (its blocker "ghost" isn't in the index at all, which
        # effective_blockers still counts as unresolved) so it's neither READY
        # nor IN PROGRESS, and "a" is done — no candidates besides the new item.
        self.write_items(
            [
                make_item("a", status="done"),
                make_item("b", status="open", blocked_by=["ghost"]),
            ]
        )
        err = io.StringIO()
        with patch("sys.stderr", err):
            dev_status.cmd_add(
                _args(json='{"id": "third-item", "summary": "New item"}')
            )
        self.assertNotIn("check the READY/IN PROGRESS items", err.getvalue())

    def test_59_pending_add_blocker_reminder_shown(self):
        self.write_items([make_item("a")])
        err = io.StringIO()
        with patch("sys.stderr", err):
            dev_status.cmd_pending_add(
                _args(
                    json='{"id": "wait-y", "description": "waiting", "kind": "email"}'
                )
            )
        self.assertIn(
            "check the READY/IN PROGRESS items above for blocker relationships",
            err.getvalue(),
        )

    def test_60_pending_add_blocker_reminder_silent_when_backlog_empty(self):
        err = io.StringIO()
        with patch("sys.stderr", err):
            dev_status.cmd_pending_add(
                _args(
                    json='{"id": "wait-z", "description": "waiting", "kind": "email"}'
                )
            )
        self.assertNotIn("check the READY/IN PROGRESS items", err.getvalue())

    # ── 61-70: explicit JSON null vs. missing field ──────────────────────────
    # Regression coverage for a real bug: `cast(str, patch.get(key, default))`
    # only applies `default` when the key is *absent* — an explicit JSON
    # `null` sails through as None, and `.strip()` on it crashes with
    # AttributeError instead of producing the intended "is required" error.
    # `_str_field`/`_list_field`/`_dict_field` must treat null and missing
    # identically everywhere a JSON patch field is extracted.

    def test_61_add_explicit_null_summary_rejected_not_crash(self):
        err = io.StringIO()
        with self.assertRaises(SystemExit) as cm:
            with patch("sys.stderr", err):
                dev_status.cmd_add(_args(json='{"id": "my-item", "summary": null}'))
        self.assertEqual(cm.exception.code, 1)
        self.assertIn("'summary' is required", err.getvalue())
        self.assertEqual(self.read_items(), [])

    def test_61b_add_explicit_null_id_rejected_not_crash(self):
        err = io.StringIO()
        with self.assertRaises(SystemExit) as cm:
            with patch("sys.stderr", err):
                dev_status.cmd_add(_args(json='{"id": null, "summary": "x"}'))
        self.assertEqual(cm.exception.code, 1)
        self.assertIn("'id' is required", err.getvalue())

    def test_62_add_explicit_null_blocked_by_treated_as_empty(self):
        dev_status.cmd_add(
            _args(json='{"id": "my-item", "summary": "x", "blocked_by": null}')
        )
        items = self.read_items()
        self.assertEqual(items[0]["blocked_by"], [])

    def test_63_add_explicit_null_category_falls_back_to_default(self):
        dev_status.cmd_add(
            _args(json='{"id": "my-item", "summary": "x", "category": null}')
        )
        items = self.read_items()
        self.assertEqual(items[0]["category"], "feature")

    def test_64_update_explicit_null_summary_rejected(self):
        self.write_items([make_item("a")])
        err = io.StringIO()
        with self.assertRaises(SystemExit) as cm:
            with patch("sys.stderr", err):
                dev_status.cmd_update(_args(id="a", patch='{"summary": null}'))
        self.assertEqual(cm.exception.code, 1)
        self.assertIn("cannot be null: summary", err.getvalue())
        self.assertEqual(
            dev_status.build_index(self.read_items())["a"]["summary"], "Summary of a"
        )

    def test_64b_update_explicit_null_blocked_by_rejected_not_crash(self):
        # Update refuses blocked_by entirely now (null or any value), redirecting
        # to block/unblock — see test_bug03. This test keeps its original name
        # so the historical "explicit-null-no-crash" intent is preserved while
        # the rejection reason has changed from "cannot be null" to "cannot
        # modify directly".
        self.write_items([make_item("a")])
        err = io.StringIO()
        with self.assertRaises(SystemExit) as cm:
            with patch("sys.stderr", err):
                dev_status.cmd_update(_args(id="a", patch='{"blocked_by": null}'))
        self.assertEqual(cm.exception.code, 1)
        self.assertIn("cannot modify 'blocked_by'", err.getvalue())

    def test_64c_update_multiple_null_fields_all_named_in_error(self):
        self.write_items([make_item("a")])
        err = io.StringIO()
        with self.assertRaises(SystemExit):
            with patch("sys.stderr", err):
                dev_status.cmd_update(
                    _args(id="a", patch='{"summary": null, "context": null}')
                )
        self.assertIn("context", err.getvalue())
        self.assertIn("summary", err.getvalue())

    def test_64d_update_priority_null_still_means_unset_not_rejected(self):
        # priority keeps its own "null means unset" contract (test_51);
        # the new null-rejection guard must not regress that.
        self.write_items([make_item("a", priority="high")])
        dev_status.cmd_update(_args(id="a", patch='{"priority": null}'))
        self.assertNotIn("priority", dev_status.build_index(self.read_items())["a"])

    def test_65_pending_add_explicit_null_description_rejected_not_crash(self):
        err = io.StringIO()
        with self.assertRaises(SystemExit) as cm:
            with patch("sys.stderr", err):
                dev_status.cmd_pending_add(
                    _args(json='{"id": "wait-x", "description": null, "kind": "email"}')
                )
        self.assertEqual(cm.exception.code, 1)
        self.assertIn("'description' is required", err.getvalue())

    def test_65b_pending_add_explicit_null_source_ref_treated_as_empty(self):
        dev_status.cmd_pending_add(
            _args(
                json='{"id": "wait-x", "description": "waiting", "kind": "email", '
                '"source_ref": null}'
            )
        )
        pending = dev_status.load_pending()
        self.assertEqual(pending[0]["source_ref"], {})

    def test_66_pending_update_explicit_null_description_rejected(self):
        self.write_pending(
            [
                {
                    "id": "pend-item",
                    "created": "2026-01-01",
                    "updated": "2026-01-01",
                    "status": "waiting_for_reply",
                    "description": "w",
                    "kind": "email",
                }
            ]
        )
        err = io.StringIO()
        with self.assertRaises(SystemExit) as cm:
            with patch("sys.stderr", err):
                dev_status.cmd_pending_update(
                    _args(id="pend-item", patch='{"description": null}')
                )
        self.assertEqual(cm.exception.code, 1)
        self.assertIn("cannot be null: description", err.getvalue())

    def test_66b_pending_update_explicit_null_outcome_allowed(self):
        # outcome is legitimately nullable (its state before resolution),
        # unlike the other pending-mutable fields — must not be rejected.
        self.write_pending(
            [
                {
                    "id": "pend-item",
                    "created": "2026-01-01",
                    "updated": "2026-01-01",
                    "status": "waiting_for_reply",
                    "description": "w",
                    "kind": "email",
                    "outcome": "something",
                }
            ]
        )
        dev_status.cmd_pending_update(_args(id="pend-item", patch='{"outcome": null}'))
        pending = {p["id"]: p for p in dev_status.load_pending()}
        self.assertIsNone(pending["pend-item"]["outcome"])

    # ───────────────────────────────────────────────────────────────────────
    # Bug-analysis regression tests (meta-devstatus-bug-analysis-2607)
    # Mirrors the 14 fixes chosen in the grilled plan; numbering matches the
    # bug-report findings 1-13,15,16 (we accepted #9 as a documented
    # limitation and split #14/#17 into separate backlog items).
    # ───────────────────────────────────────────────────────────────────────

    # ── #1: stale-rev race in read paths — render/list/show take the lock

    def test_bug01_read_paths_acquire_lock(self):
        self.write_items([make_item("alph-item")])
        # Render cmd path: invoke while holding the lock from another fd
        # would self-deadlock; instead assert the lock file is touched and
        # cmd_render completes without raising, which it can only do if it
        # does NOT try to acquire a second non-recursive lock in the same fd.
        # The actual atomic-items+rev guarantee is exercised via the
        # _MutationResult exposure in #15's test.
        out = io.StringIO()
        with patch("sys.stdout", out):
            dev_status.cmd_render(_args())
        self.assertIn("alph-item", out.getvalue())

    # ── #2: cross-pool id collision

    def test_bug02_add_refuses_slug_in_pending_pool(self):
        self.write_pending(
            [
                {
                    "id": "shared-slug",
                    "created": "2026-01-01",
                    "updated": "2026-01-01",
                    "status": "waiting_for_reply",
                    "description": "x",
                    "kind": "email",
                    "outcome": None,
                }
            ]
        )
        err = io.StringIO()
        with self.assertRaises(SystemExit) as cm:
            with patch("sys.stderr", err):
                dev_status.cmd_add(_args(json='{"id": "shared-slug", "summary": "x"}'))
        self.assertEqual(cm.exception.code, 1)
        self.assertIn("already exists as a pending item", err.getvalue())

    def test_bug02_pending_add_refuses_slug_in_backlog_pool(self):
        self.write_items([make_item("shared-slug")])
        err = io.StringIO()
        with self.assertRaises(SystemExit) as cm:
            with patch("sys.stderr", err):
                dev_status.cmd_pending_add(
                    _args(
                        json='{"id": "shared-slug", "description": "x", "kind": "email"}'
                    )
                )
        self.assertEqual(cm.exception.code, 1)
        self.assertIn("already exists as a backlog item", err.getvalue())

    # ── #3: update refuses blocked_by, forces block/unblock

    def test_bug03_update_refuses_blocked_by_patch(self):
        self.write_items([make_item("aaa-item"), make_item("bbb-item")])
        err = io.StringIO()
        with self.assertRaises(SystemExit) as cm:
            with patch("sys.stderr", err):
                dev_status.cmd_update(
                    _args(id="aaa-item", patch='{"blocked_by": ["bbb-item"]}')
                )
        self.assertEqual(cm.exception.code, 1)
        msg = err.getvalue()
        self.assertIn("cannot modify 'blocked_by'", msg)
        self.assertIn("block <id> <blocker>", msg)
        # Must NOT have silently landed in stored blocked_by
        items = self.read_items()
        self.assertEqual(items[0]["blocked_by"], [])

    # ── #4: remove purges inbound blocked_by (already covered in test_45;
    #         prune variant below)

    def test_bug04_prune_purges_inbound_blocked_by_refs(self):
        from datetime import date, timedelta

        old = (date.today() - timedelta(days=30)).isoformat()
        self.write_items(
            [
                {
                    "id": "old-blocker",
                    "created": old,
                    "updated": old,
                    "status": "done",
                    "completed_at": old,
                    "summary": "Old",
                    "category": "feature",
                    "blocked_by": [],
                    "related_files": [],
                    "context": "",
                    "next_steps": "",
                },
                make_item("dep", blocked_by=["old-blocker"]),
            ]
        )
        # Patch _backup_before_bulk_delete to a no-op so we don't write
        # stray backup files in the test tmpdir.
        with patch.object(dev_status, "_backup_before_bulk_delete", lambda _p: None):
            dev_status.cmd_prune(_args(force=True))
        remaining = self.read_items()
        self.assertEqual([i["id"] for i in remaining], ["dep"])
        self.assertEqual(remaining[0]["blocked_by"], [])

    # ── #5: rename rewrites pending blocking list + related_files[].note

    def test_bug05_rename_rewrites_pending_blocking_and_related_files_note(self):
        self.write_items([make_item("old-slug")])
        self.write_pending(
            [
                {
                    "id": "pend-x",
                    "created": "2026-01-01",
                    "updated": "2026-01-01",
                    "status": "waiting_for_reply",
                    "description": "blocks old-slug elsewhere",
                    "kind": "email",
                    "blocking": ["old-slug"],
                    "related_files": [
                        {"path": "/x", "note": "see old-slug for context"}
                    ],
                    "next_steps": ["ping old-slug owner"],
                    "outcome": None,
                }
            ]
        )
        dev_status.cmd_rename(_args(old_slug="old-slug", new_slug="new-slug"))
        pend = dev_status.load_pending()[0]
        self.assertEqual(pend["blocking"], ["new-slug"])
        self.assertEqual(pend["related_files"][0]["note"], "see new-slug for context")
        self.assertEqual(pend["next_steps"][0], "ping new-slug owner")
        self.assertEqual(pend["description"], "blocks new-slug elsewhere")

    # ── #6: rename boundary regex doesn't over-match longer sibling slugs

    def test_bug06_rename_regex_does_not_overmatch_sibling_slug(self):
        self.write_items(
            [
                make_item(
                    "foo-bar",
                    summary="refs foo-bar-baz elsewhere",
                    context="context-foo-bar-baz-end",
                    next_steps="x-foo-bar-baz",
                ),
                make_item("foo-bar-baz"),
            ]
        )
        dev_status.cmd_rename(_args(old_slug="foo-bar", new_slug="renamed-slug"))
        items = {i["id"]: i for i in self.read_items()}
        # foo-bar-baz slug itself is unchanged
        self.assertIn("foo-bar-baz", items)
        # prose mentions of foo-bar-baz are NOT rewritten
        self.assertEqual(items["foo-bar-baz"]["summary"], "Summary of foo-bar-baz")
        # the renamed item's prose: its own slug was foo-bar (now renamed-slug),
        # but mentions of foo-bar-baz are preserved (NOT truncated to renamed-slug-baz)
        self.assertEqual(items["renamed-slug"]["summary"], "refs foo-bar-baz elsewhere")
        self.assertEqual(items["renamed-slug"]["context"], "context-foo-bar-baz-end")
        self.assertEqual(items["renamed-slug"]["next_steps"], "x-foo-bar-baz")

    # ── #7: _age_hours handles timezone-aware stamps

    def test_bug07_age_hours_handles_tz_aware_stamp(self):
        from datetime import datetime, timezone

        now_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")
        age = dev_status._age_hours(now_iso)
        self.assertIsNotNone(age)
        self.assertGreaterEqual(age, 0.0)
        self.assertLess(age, 1.0)

    # ── #8: _list_field rejects non-list (string) values

    def test_bug08_list_field_rejects_string(self):
        err = io.StringIO()
        with self.assertRaises(SystemExit) as cm:
            with patch("sys.stderr", err):
                dev_status._list_field({"blocked_by": "not-a-list"}, "blocked_by")
        self.assertEqual(cm.exception.code, 1)
        self.assertIn("must be a list", err.getvalue())

    def test_bug08_effective_blockers_coerces_corrupt_stored_string(self):
        # A legacy corrupt record with blocked_by as a string would
        # previously be iterated char-by-char; effective_blockers now
        # coerces to [] with a stderr warning instead.
        item = make_item("corrupt-item", blocked_by="blocker-slug")  # type: ignore[arg-type]
        err = io.StringIO()
        with patch("sys.stderr", err):
            result = dev_status.effective_blockers(item, {})
        self.assertEqual(result, [])
        self.assertIn("not list", err.getvalue())

    # ── #10: pending update validates blocking slugs against backlog index

    def test_bug10_pending_update_validates_blocking_slugs(self):
        self.write_items([make_item("real-item")])
        self.write_pending(
            [
                {
                    "id": "pend-x",
                    "created": "2026-01-01",
                    "updated": "2026-01-01",
                    "status": "waiting_for_reply",
                    "description": "x",
                    "kind": "email",
                    "blocking": ["real-item"],
                    "next_steps": [],
                    "outcome": None,
                }
            ]
        )
        err = io.StringIO()
        with self.assertRaises(SystemExit) as cm:
            with patch("sys.stderr", err):
                dev_status.cmd_pending_update(
                    _args(id="pend-x", patch='{"blocking": ["typo-slug"]}')
                )
        self.assertEqual(cm.exception.code, 1)
        self.assertIn("blocking references unknown slug: typo-slug", err.getvalue())

    # ── #11: unknown slug emits not-found, not wrong-kind

    def test_bug11_update_unknown_slug_emits_not_found(self):
        err = io.StringIO()
        with self.assertRaises(SystemExit) as cm:
            with patch("sys.stderr", err):
                dev_status.cmd_update(_args(id="typo-slug", patch='{"summary": "x"}'))
        self.assertEqual(cm.exception.code, 1)
        self.assertIn("[resolve] not found: typo-slug", err.getvalue())
        self.assertNotIn("is a", err.getvalue())

    def test_bug11_pending_update_unknown_slug_emits_not_found(self):
        err = io.StringIO()
        with self.assertRaises(SystemExit) as cm:
            with patch("sys.stderr", err):
                dev_status.cmd_pending_update(
                    _args(id="typo-slug", patch='{"description": "x"}')
                )
        self.assertEqual(cm.exception.code, 1)
        self.assertIn("[resolve] not found: typo-slug", err.getvalue())
        self.assertNotIn("is a backlog item", err.getvalue())

    # ── #12: load_rev handles non-dict / non-int rev

    def test_bug12_load_rev_non_dict_meta_falls_back_to_zero(self):
        self.data_dir.mkdir(parents=True, exist_ok=True)
        # A structurally valid JSON list, not a dict — previously AttributeError
        self.meta_file.write_text("[]")
        self.assertEqual(dev_status.load_rev(), 0)

    def test_bug12_load_rev_non_int_rev_falls_back_to_zero(self):
        self.data_dir.mkdir(parents=True, exist_ok=True)
        # rev present but a string — would brick every numeric --if-rev call
        self.meta_file.write_text(json.dumps({"rev": "five"}))
        self.assertEqual(dev_status.load_rev(), 0)

    # ── #13: falsy-zero DONE window bug (age 0.0 excluded before)

    def test_bug13_done_age_zero_still_in_window(self):
        from datetime import datetime

        now_iso = datetime.now().isoformat(timespec="seconds")
        items = [
            {
                "id": "fresh-done",
                "created": now_iso,
                "updated": now_iso,
                "status": "done",
                "completed_at": now_iso,
                "summary": "Fresh",
                "category": "feature",
                "blocked_by": [],
                "related_files": [],
                "context": "",
                "next_steps": "",
            }
        ]
        self.write_items(items)
        _, _, _, done = dev_status._render_order(items)
        self.assertEqual([i["id"] for i in done], ["fresh-done"])

    # ── #15: mutators render inside the lock using in-memory pending_items

    def test_bug15_done_renders_in_memory_pending(self):
        self.write_items([make_item("alph-item")])
        self.write_pending(
            [
                {
                    "id": "pend-x",
                    "created": "2026-01-01",
                    "updated": "2026-01-01",
                    "status": "waiting_for_reply",
                    "description": "w",
                    "kind": "email",
                    "next_steps": [],
                    "blocking": [],
                    "outcome": None,
                }
            ]
        )
        out, err = io.StringIO(), io.StringIO()
        with patch("sys.stdout", out), patch("sys.stderr", err):
            dev_status.cmd_done(_args(id="alph-item"))
        # stdout renders the dashboard with the pending item's description; stderr
        # gets the item-map line naming pend-x. Both prove the in-memory
        # pending_items from the locked mutation was used (no unlocked re-read).
        self.assertIn("w (waiting", out.getvalue())
        self.assertIn("pend-x", err.getvalue())

    # ── #16: prune prints the item-map/rev line (via render) after pruning

    def test_bug16_prune_prints_dashboard_and_item_map(self):
        from datetime import date, timedelta

        old = (date.today() - timedelta(days=30)).isoformat()
        self.write_items(
            [
                {
                    "id": "old-done",
                    "created": old,
                    "updated": old,
                    "status": "done",
                    "completed_at": old,
                    "summary": "Old",
                    "category": "feature",
                    "blocked_by": [],
                    "related_files": [],
                    "context": "",
                    "next_steps": "",
                }
            ]
        )
        out, err = io.StringIO(), io.StringIO()
        with patch("sys.stdout", out), patch("sys.stderr", err):
            with patch.object(
                dev_status, "_backup_before_bulk_delete", lambda _p: None
            ):
                dev_status.cmd_prune(_args(force=True))
        # Render prints the dashboard to stdout and the item-map line to stderr
        self.assertIn("backlog is empty", out.getvalue())
        self.assertIn("item-map:", err.getvalue())


# ── arg helper ────────────────────────────────────────────────────────────────


class _args:
    """Minimal argparse.Namespace stand-in."""

    if_rev = None  # default; argparse always sets --if-rev (default None)
    status = None  # default; argparse sets --status (default None) for `list`

    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)


if __name__ == "__main__":
    unittest.main(verbosity=2)
