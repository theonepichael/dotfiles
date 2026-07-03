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
        self._patches = [
            patch.object(dev_status, "DATA_DIR", self.data_dir),
            patch.object(dev_status, "ITEMS_FILE", self.items_file),
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

    def read_items(self):
        return dev_status.load_items()

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
        args = _args(id="2")
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
        pairs = map_line.replace("item-map: ", "").split(",")
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


# ── arg helper ────────────────────────────────────────────────────────────────


class _args:
    """Minimal argparse.Namespace stand-in."""

    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)


if __name__ == "__main__":
    unittest.main(verbosity=2)
