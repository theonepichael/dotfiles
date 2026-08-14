#!/usr/bin/env python3
"""Tests for grill.py. Run with: python3 test_grill.py"""

import argparse
import io
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent))
import grill


def ns(**kwargs: object) -> argparse.Namespace:
    kwargs.setdefault("session", None)
    kwargs.setdefault("backlog_slug", None)
    return argparse.Namespace(**kwargs)


class GrillTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.mkdtemp()
        self.data_dir = Path(self.tmpdir) / "grill"
        self._patch = patch.object(grill, "DATA_DIR", self.data_dir)
        self._patch.start()

    def tearDown(self) -> None:
        self._patch.stop()
        shutil.rmtree(self.tmpdir)

    def new_session(self, topic: str = "Auth token design") -> str:
        out = io.StringIO()
        with patch("sys.stdout", out), patch("sys.stderr", io.StringIO()):
            grill.cmd_new(ns(json=json.dumps({"topic": topic})))
        return out.getvalue().strip()

    def decide(self, slug: str | None = None, **fields: object) -> None:
        payload = {
            "id": "token-storage",
            "question": "Where do tokens live?",
            "decision": "httpOnly cookie",
            **fields,
        }
        with patch("sys.stderr", io.StringIO()):
            grill.cmd_decide(ns(json=json.dumps(payload), session=slug))

    # ── new ──────────────────────────────────────────────────────────────────

    def test_01_new_creates_session_with_derived_slug(self) -> None:
        slug = self.new_session("Auth token design!")
        self.assertTrue(slug.endswith("-auth-token-design"))
        session = grill.load_session(slug)
        self.assertEqual(session["topic"], "Auth token design!")
        self.assertEqual(session["decisions"], [])

    def test_02_new_duplicate_topic_auto_suffixes(self) -> None:
        first = self.new_session()
        second = self.new_session()
        self.assertNotEqual(first, second)
        self.assertEqual(second, f"{first}-2")

    def test_03_new_missing_topic_rejected(self) -> None:
        err = io.StringIO()
        with patch("sys.stderr", err), self.assertRaises(SystemExit):
            grill.cmd_new(ns(json="{}"))
        self.assertIn("'topic' is required", err.getvalue())

    # ── ask / decide lifecycle ───────────────────────────────────────────────

    def test_04_decide_one_shot_appends_with_null_verdict(self) -> None:
        slug = self.new_session()
        self.decide(slug)
        session = grill.load_session(slug)
        decision = session["decisions"][0]
        self.assertEqual(decision["id"], "token-storage")
        self.assertEqual(decision["source"], "user")
        self.assertIsNone(decision["verdict"])

    def test_05_decide_on_already_decided_rejected(self) -> None:
        slug = self.new_session()
        self.decide(slug)
        err = io.StringIO()
        with patch("sys.stderr", err), self.assertRaises(SystemExit):
            grill.cmd_decide(
                ns(
                    json=json.dumps({"id": "token-storage", "decision": "again"}),
                    session=slug,
                )
            )
        self.assertIn("already decided", err.getvalue())

    def test_05b_ask_registers_open_then_decide_resolves(self) -> None:
        slug = self.new_session()
        with patch("sys.stderr", io.StringIO()):
            grill.cmd_ask(
                ns(
                    json=json.dumps({"id": "token-storage", "question": "Where?"}),
                    session=slug,
                )
            )
        decision = grill.load_session(slug)["decisions"][0]
        self.assertIsNone(decision["decision"])
        self.assertIsNone(decision["source"])
        with patch("sys.stderr", io.StringIO()):
            grill.cmd_decide(
                ns(
                    json=json.dumps(
                        {
                            "id": "token-storage",
                            "decision": "httpOnly cookie",
                            "source": "defaulted",
                        }
                    ),
                    session=slug,
                )
            )
        decision = grill.load_session(slug)["decisions"][0]
        self.assertEqual(decision["decision"], "httpOnly cookie")
        self.assertEqual(decision["source"], "defaulted")

    def test_05c_next_prints_first_open_question(self) -> None:
        slug = self.new_session()
        with patch("sys.stderr", io.StringIO()):
            grill.cmd_ask(
                ns(
                    json=json.dumps({"id": "token-storage", "question": "Where?"}),
                    session=slug,
                )
            )
        out = io.StringIO()
        with patch("sys.stdout", out):
            grill.cmd_next(ns(session=slug))
        self.assertIn("token-storage: Where?", out.getvalue())

    def test_06_decide_invalid_source_rejected(self) -> None:
        slug = self.new_session()
        with self.assertRaises(SystemExit):
            self.decide(slug, source="vibes")

    # ── verdict ──────────────────────────────────────────────────────────────

    def test_07_verified_without_evidence_rejected(self) -> None:
        slug = self.new_session()
        self.decide(slug)
        err = io.StringIO()
        with patch("sys.stderr", err), self.assertRaises(SystemExit):
            grill.cmd_verdict(
                ns(
                    decision_id="token-storage",
                    json=json.dumps({"result": "VERIFIED"}),
                    session=slug,
                )
            )
        self.assertIn("requires 'evidence'", err.getvalue())

    def test_08_verdict_recorded_structured(self) -> None:
        slug = self.new_session()
        self.decide(slug)
        with patch("sys.stderr", io.StringIO()):
            grill.cmd_verdict(
                ns(
                    decision_id="token-storage",
                    json=json.dumps(
                        {"result": "VERIFIED", "evidence": "ran auth tests"}
                    ),
                    session=slug,
                )
            )
        verdict = grill.load_session(slug)["decisions"][0]["verdict"]
        assert verdict is not None
        self.assertEqual(verdict["result"], "VERIFIED")
        self.assertEqual(verdict["evidence"], "ran auth tests")

    def test_09_unverifiable_allowed_without_evidence(self) -> None:
        slug = self.new_session()
        self.decide(slug)
        with patch("sys.stderr", io.StringIO()):
            grill.cmd_verdict(
                ns(
                    decision_id="token-storage",
                    json=json.dumps({"result": "UNVERIFIABLE"}),
                    session=slug,
                )
            )
        verdict = grill.load_session(slug)["decisions"][0]["verdict"]
        assert verdict is not None
        self.assertEqual(verdict["result"], "UNVERIFIABLE")

    def test_09b_verdict_on_open_decision_rejected(self) -> None:
        slug = self.new_session()
        with patch("sys.stderr", io.StringIO()):
            grill.cmd_ask(
                ns(
                    json=json.dumps({"id": "token-storage", "question": "Where?"}),
                    session=slug,
                )
            )
        err = io.StringIO()
        with patch("sys.stderr", err), self.assertRaises(SystemExit):
            grill.cmd_verdict(
                ns(
                    decision_id="token-storage",
                    json=json.dumps({"result": "UNVERIFIABLE"}),
                    session=slug,
                )
            )
        self.assertIn("still open", err.getvalue())

    # ── revise ───────────────────────────────────────────────────────────────

    def test_10_revise_resets_verdict(self) -> None:
        slug = self.new_session()
        self.decide(slug)
        with patch("sys.stderr", io.StringIO()):
            grill.cmd_verdict(
                ns(
                    decision_id="token-storage",
                    json=json.dumps(
                        {"result": "DISPUTED", "evidence": "cookie blocked"}
                    ),
                    session=slug,
                )
            )
            grill.cmd_revise(
                ns(
                    decision_id="token-storage",
                    patch=json.dumps({"decision": "session storage + short TTL"}),
                    session=slug,
                )
            )
        decision = grill.load_session(slug)["decisions"][0]
        self.assertEqual(decision["decision"], "session storage + short TTL")
        self.assertIsNone(decision["verdict"])

    def test_11_revise_unknown_field_rejected(self) -> None:
        slug = self.new_session()
        self.decide(slug)
        err = io.StringIO()
        with patch("sys.stderr", err), self.assertRaises(SystemExit):
            grill.cmd_revise(
                ns(
                    decision_id="token-storage",
                    patch=json.dumps({"verdict": {"result": "VERIFIED"}}),
                    session=slug,
                )
            )
        self.assertIn("cannot revise", err.getvalue())

    # ── session resolution ───────────────────────────────────────────────────

    def test_12_omitted_session_resolves_to_most_recent(self) -> None:
        self.new_session("First topic")
        second = self.new_session("Second topic")
        # both created today; ties break by slug — force distinct updated dates
        session = grill.load_session(second)
        session["updated"] = "2099-01-01"
        grill.save_session(session)
        resolved = grill.resolve_session(None, "test")
        self.assertEqual(resolved["slug"], second)

    def test_12b_same_day_sessions_resolve_to_newest_not_alphabetical(self) -> None:
        # regression: day-granular 'updated' tied for same-day sessions and the
        # alphabetically-later slug won; timestamps must break the tie by recency
        self.new_session("Zeta topic")
        second = self.new_session("Alpha topic")
        resolved = grill.resolve_session(None, "test")
        self.assertEqual(resolved["slug"], second)

    def test_12c_timestamp_sorts_after_legacy_date_only_value(self) -> None:
        first = self.new_session("Legacy session")
        session = grill.load_session(first)
        session["updated"] = "2099-01-01"  # date-only, as pre-timestamp files have
        grill.save_session(session)
        second = self.new_session("New session")
        newer = grill.load_session(second)
        newer["updated"] = "2099-01-01T08:00:00"
        grill.save_session(newer)
        resolved = grill.resolve_session(None, "test")
        self.assertEqual(resolved["slug"], second)

    def test_13_substring_match_unique_and_ambiguous(self) -> None:
        self.new_session("Alpha plan")
        self.new_session("Beta plan")
        resolved = grill.resolve_session("alpha", "test")
        self.assertIn("alpha-plan", resolved["slug"])
        with patch("sys.stderr", io.StringIO()), self.assertRaises(SystemExit):
            grill.resolve_session("plan", "test")

    # ── rm ───────────────────────────────────────────────────────────────────

    def test_13b_rm_removes_decision_point(self) -> None:
        slug = self.new_session()
        with patch("sys.stderr", io.StringIO()):
            grill.cmd_ask(
                ns(
                    json=json.dumps({"id": "token-storage", "question": "Where?"}),
                    session=slug,
                )
            )
        err = io.StringIO()
        with patch("sys.stderr", err):
            grill.cmd_rm(ns(decision_id="token-storage", session=slug, verbose=True))
        self.assertIn("removed token-storage (open)", err.getvalue())
        self.assertEqual(grill.load_session(slug)["decisions"], [])

    def test_13c_rm_unknown_id_rejected(self) -> None:
        slug = self.new_session()
        err = io.StringIO()
        with patch("sys.stderr", err), self.assertRaises(SystemExit):
            grill.cmd_rm(ns(decision_id="nope", session=slug))
        self.assertIn("no decision 'nope'", err.getvalue())

    # ── render / plan artifact ───────────────────────────────────────────────

    def test_14_render_status_table_open_questions_and_escaping(self) -> None:
        slug = self.new_session()
        self.decide(slug, decision="cookie | not localStorage")
        with patch("sys.stderr", io.StringIO()):
            grill.cmd_ask(
                ns(
                    json=json.dumps(
                        {"id": "ttl-length", "question": "How long a TTL?"}
                    ),
                    session=slug,
                )
            )
        md = grill.render_markdown(grill.load_session(slug))
        self.assertIn("# Grill status:", md)
        self.assertIn("1/2 decided · 0 verified", md)
        self.assertIn("cookie \\| not localStorage", md)
        self.assertIn("## Open questions", md)
        self.assertIn("**ttl-length**", md)
        self.assertIn("plan: not written yet", md)

    def test_15_plan_records_existing_artifact_path(self) -> None:
        slug = self.new_session()
        artifact = Path(self.tmpdir) / "auth-plan.md"
        err = io.StringIO()
        with patch("sys.stderr", err), self.assertRaises(SystemExit):
            grill.cmd_plan(ns(path=str(artifact), session=slug))
        self.assertIn("not found", err.getvalue())

        artifact.write_text("# Auth plan\n")
        with patch("sys.stderr", io.StringIO()):
            grill.cmd_plan(ns(path=str(artifact), session=slug))
        session = grill.load_session(slug)
        self.assertEqual(session["plan_path"], str(artifact))
        self.assertIn(str(artifact), grill.render_markdown(session))

    # ── corruption ───────────────────────────────────────────────────────────

    def test_16_corrupted_session_fails_loudly(self) -> None:
        slug = self.new_session()
        grill.session_path(slug).write_text("{not json")
        err = io.StringIO()
        with patch("sys.stderr", err), self.assertRaises(SystemExit):
            grill.load_session(slug)
        self.assertIn("corrupted", err.getvalue())

    # ── list ─────────────────────────────────────────────────────────────────

    def test_17_list_prints_one_line_per_session(self) -> None:
        slug = self.new_session("Alpha topic")
        self.decide(slug)
        out = io.StringIO()
        with patch("sys.stdout", out):
            grill.cmd_list(ns())
        line = out.getvalue().strip()
        self.assertTrue(line.startswith(slug))
        self.assertIn("1/1 decided", line)
        self.assertIn("0 verified", line)
        self.assertIn("Alpha topic", line)

    def test_17b_list_empty_prints_nothing(self) -> None:
        out = io.StringIO()
        with patch("sys.stdout", out):
            grill.cmd_list(ns())
        self.assertEqual(out.getvalue(), "")

    # ── explicit JSON null vs. missing field ────────────────────────────────
    # Regression coverage for a real bug: `str(patch.get(key, default))`
    # turns an explicit JSON `null` into the four-character string "None",
    # which then passes any `if not field:` required-field check instead of
    # being rejected. `_text()` must treat null and "missing" identically.

    def test_18_new_explicit_null_topic_rejected_like_missing(self) -> None:
        err = io.StringIO()
        with patch("sys.stderr", err), self.assertRaises(SystemExit):
            grill.cmd_new(ns(json=json.dumps({"topic": None})))
        self.assertIn("'topic' is required", err.getvalue())
        self.assertEqual(grill.all_session_slugs(), [])

    def test_18b_ask_explicit_null_question_rejected_like_missing(self) -> None:
        slug = self.new_session()
        err = io.StringIO()
        with patch("sys.stderr", err), self.assertRaises(SystemExit):
            grill.cmd_ask(
                ns(
                    json=json.dumps({"id": "token-storage", "question": None}),
                    session=slug,
                )
            )
        self.assertIn("'question' is required", err.getvalue())
        self.assertEqual(grill.load_session(slug)["decisions"], [])

    def test_18c_decide_explicit_null_source_falls_back_to_default(self) -> None:
        slug = self.new_session()
        with patch("sys.stderr", io.StringIO()):
            grill.cmd_decide(
                ns(
                    json=json.dumps(
                        {
                            "id": "token-storage",
                            "question": "Where?",
                            "decision": "httpOnly cookie",
                            "source": None,
                        }
                    ),
                    session=slug,
                )
            )
        decision = grill.load_session(slug)["decisions"][0]
        self.assertEqual(decision["source"], "user")

    # ── revise can't blank/reopen a decided item ────────────────────────────
    # Regression coverage for a real bug: `revise <id> '{"decision": null}'`
    # used to pass straight through to `decision.update(patch)`, setting
    # `decision["decision"] = None` — which is exactly what `is_open()`
    # checks for, silently reopening an already-decided item outside of
    # `cmd_decide`'s own validation.

    def test_19_revise_null_decision_rejected(self) -> None:
        slug = self.new_session()
        self.decide(slug)
        err = io.StringIO()
        with patch("sys.stderr", err), self.assertRaises(SystemExit):
            grill.cmd_revise(
                ns(
                    decision_id="token-storage",
                    patch=json.dumps({"decision": None}),
                    session=slug,
                )
            )
        self.assertIn("cannot be blank", err.getvalue())
        decision = grill.load_session(slug)["decisions"][0]
        self.assertFalse(grill.is_open(decision))
        self.assertEqual(decision["decision"], "httpOnly cookie")

    def test_19b_revise_blank_string_decision_rejected(self) -> None:
        slug = self.new_session()
        self.decide(slug)
        err = io.StringIO()
        with patch("sys.stderr", err), self.assertRaises(SystemExit):
            grill.cmd_revise(
                ns(
                    decision_id="token-storage",
                    patch=json.dumps({"decision": "   "}),
                    session=slug,
                )
            )
        self.assertIn("cannot be blank", err.getvalue())

    # ── concurrency: locking prevents lost updates ──────────────────────────

    def test_20_concurrent_mutators_of_same_session_serialize(self) -> None:
        import threading

        slug = self.new_session()
        with patch("sys.stderr", io.StringIO()):
            grill.cmd_ask(
                ns(json=json.dumps({"id": "item-a", "question": "A?"}), session=slug)
            )
            grill.cmd_ask(
                ns(json=json.dumps({"id": "item-b", "question": "B?"}), session=slug)
            )

        def decide_a() -> None:
            with patch("sys.stderr", io.StringIO()):
                grill.cmd_decide(
                    ns(
                        json=json.dumps({"id": "item-a", "decision": "answer A"}),
                        session=slug,
                    )
                )

        def decide_b() -> None:
            with patch("sys.stderr", io.StringIO()):
                grill.cmd_decide(
                    ns(
                        json=json.dumps({"id": "item-b", "decision": "answer B"}),
                        session=slug,
                    )
                )

        with grill.session_lock(slug):
            t1 = threading.Thread(target=decide_a)
            t2 = threading.Thread(target=decide_b)
            t1.start()
            t2.start()
            import time

            time.sleep(0.1)
            self.assertTrue(t1.is_alive())
            self.assertTrue(t2.is_alive())
        t1.join(timeout=5)
        t2.join(timeout=5)
        self.assertFalse(t1.is_alive())
        self.assertFalse(t2.is_alive())

        decisions = {
            d["id"]: d["decision"] for d in grill.load_session(slug)["decisions"]
        }
        self.assertEqual(decisions["item-a"], "answer A")
        self.assertEqual(decisions["item-b"], "answer B")

    def test_20b_concurrent_new_with_same_topic_gets_distinct_slugs(self) -> None:
        import threading

        results: dict[str, str] = {}

        def create(name: str) -> None:
            out = io.StringIO()
            with patch("sys.stdout", out), patch("sys.stderr", io.StringIO()):
                grill.cmd_new(ns(json=json.dumps({"topic": "Same topic"})))
            results[name] = out.getvalue().strip()

        with grill._new_session_lock():
            t1 = threading.Thread(target=create, args=("one",))
            t2 = threading.Thread(target=create, args=("two",))
            t1.start()
            t2.start()
            import time

            time.sleep(0.1)
            self.assertTrue(t1.is_alive())
            self.assertTrue(t2.is_alive())
        t1.join(timeout=5)
        t2.join(timeout=5)

        self.assertNotEqual(results["one"], results["two"])
        self.assertEqual(len(grill.all_session_slugs()), 2)

    # ── pending-execution (clear-and-go) ────────────────────────────────────

    def test_21_pending_plan_silent_when_nothing_pending(self) -> None:
        self.new_session()
        out = io.StringIO()
        with patch("sys.stdout", out):
            grill.cmd_pending_plan(ns(consume=False))
        self.assertEqual(out.getvalue(), "")

    def test_21b_mark_pending_execution_sets_flag(self) -> None:
        slug = self.new_session()
        with patch("sys.stderr", io.StringIO()):
            grill.cmd_mark_pending_execution(ns(session=slug))
        self.assertTrue(grill.load_session(slug)["pending_execution"])

    def test_21c_pending_plan_prints_and_consumes(self) -> None:
        slug = self.new_session()
        artifact = Path(self.tmpdir) / "plan.md"
        artifact.write_text("# Plan\n")
        with patch("sys.stderr", io.StringIO()):
            grill.cmd_plan(ns(path=str(artifact), session=slug))
            grill.cmd_mark_pending_execution(ns(session=slug))

        out = io.StringIO()
        with patch("sys.stdout", out):
            grill.cmd_pending_plan(ns(consume=True))
        self.assertIn(slug, out.getvalue())
        self.assertIn(str(artifact), out.getvalue())
        self.assertFalse(grill.load_session(slug)["pending_execution"])

    def test_21d_pending_plan_without_consume_leaves_flag_set(self) -> None:
        slug = self.new_session()
        with patch("sys.stderr", io.StringIO()):
            grill.cmd_mark_pending_execution(ns(session=slug))

        out = io.StringIO()
        with patch("sys.stdout", out):
            grill.cmd_pending_plan(ns(consume=False))
        self.assertIn(slug, out.getvalue())
        self.assertTrue(grill.load_session(slug)["pending_execution"])

    def test_21e_pending_plan_picks_most_recently_updated(self) -> None:
        first = self.new_session("First topic")
        second = self.new_session("Second topic")
        with patch("sys.stderr", io.StringIO()):
            grill.cmd_mark_pending_execution(ns(session=first))
            grill.cmd_mark_pending_execution(ns(session=second))
        session = grill.load_session(second)
        session["updated"] = "2099-01-01T00:00:00"
        grill.save_session(session)

        out = io.StringIO()
        with patch("sys.stdout", out):
            grill.cmd_pending_plan(ns(consume=False))
        self.assertIn(second, out.getvalue())
        self.assertNotIn(first, out.getvalue())

    def test_21f_mark_pending_execution_records_backlog_slug(self) -> None:
        slug = self.new_session()
        with patch("sys.stderr", io.StringIO()):
            grill.cmd_mark_pending_execution(
                ns(session=slug, backlog_slug="iron-lb-example")
            )
        self.assertEqual(grill.load_session(slug)["backlog_slug"], "iron-lb-example")

    def test_21g_mark_pending_execution_rejects_invalid_backlog_slug(self) -> None:
        slug = self.new_session()
        with patch("sys.stderr", io.StringIO()), self.assertRaises(SystemExit):
            grill.cmd_mark_pending_execution(
                ns(session=slug, backlog_slug="Not Kebab Case")
            )

    def test_21h_pending_plan_prints_backlog_item_resume_line(self) -> None:
        slug = self.new_session()
        with patch("sys.stderr", io.StringIO()):
            grill.cmd_mark_pending_execution(
                ns(session=slug, backlog_slug="iron-lb-example")
            )

        out = io.StringIO()
        with patch("sys.stdout", out):
            grill.cmd_pending_plan(ns(consume=False))
        self.assertIn("/backlog-item iron-lb-example", out.getvalue())

    def test_21i_pending_plan_omits_backlog_item_line_when_absent(self) -> None:
        slug = self.new_session()
        with patch("sys.stderr", io.StringIO()):
            grill.cmd_mark_pending_execution(ns(session=slug))

        out = io.StringIO()
        with patch("sys.stdout", out):
            grill.cmd_pending_plan(ns(consume=False))
        self.assertNotIn("/backlog-item", out.getvalue())


if __name__ == "__main__":
    unittest.main(verbosity=1)
