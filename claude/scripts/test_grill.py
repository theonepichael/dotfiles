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
            grill.cmd_rm(ns(decision_id="token-storage", session=slug))
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


if __name__ == "__main__":
    unittest.main(verbosity=1)
