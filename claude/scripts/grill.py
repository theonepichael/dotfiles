#!/usr/bin/env python3
"""grill.py — grill-me session state CLI. All session mutations go through here.

The JSON session file is the capture mechanism: every decision point is recorded
the moment it's identified (open), then resolved (decided), then verified. An
unfinished session keeps its open questions, so it can resume in a later
conversation. The plan document itself is authored by the model as a separate
markdown artifact, informed by these decision points; the session stores only a
pointer to it (plan_path). `render` prints session status, not the plan.
"""

import argparse
import json
import os
import re
import sys
import tempfile
from datetime import date, datetime
from pathlib import Path

DATA_DIR = Path.home() / ".claude" / "data" / "grill"
SCHEMA_VERSION = 1

VALID_SOURCES = {"user", "defaulted", "assumed"}
VALID_RESULTS = {"VERIFIED", "DISPUTED", "UNVERIFIABLE"}
EVIDENCE_REQUIRED = {"VERIFIED", "DISPUTED"}
REVISABLE_FIELDS = {"question", "decision", "reasoning", "source"}
ID_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
ID_MIN, ID_MAX = 2, 48


# ── helpers ───────────────────────────────────────────────────────────────────


def today() -> str:
    return date.today().isoformat()


def now() -> str:
    # Full timestamp so same-day sessions never tie in resolve_session's
    # latest-when-omitted rule. Date-only values in old files sort correctly
    # against these (prefix ordering), so no migration is needed.
    return datetime.now().isoformat()


def die(context: str, msg: str) -> None:
    print(f"[{context}] {msg}", file=sys.stderr)
    sys.exit(1)


def slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def validate_decision_id(decision_id: str, context: str) -> None:
    if not ID_RE.match(decision_id):
        die(context, f"invalid decision id '{decision_id}' — lowercase kebab-case")
    if not (ID_MIN <= len(decision_id) <= ID_MAX):
        die(
            context,
            f"decision id '{decision_id}' length {len(decision_id)} "
            f"out of range [{ID_MIN},{ID_MAX}]",
        )


def parse_json_arg(raw: str, context: str) -> dict[str, object]:
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as e:
        die(context, f"invalid JSON: {e}")
    if not isinstance(parsed, dict):
        die(context, "expected a JSON object")
    return parsed


# ── I/O ───────────────────────────────────────────────────────────────────────


def session_path(slug: str) -> Path:
    return DATA_DIR / f"{slug}.json"


def load_session(slug: str) -> dict[str, object]:
    path = session_path(slug)
    try:
        data = json.loads(path.read_text())
    except json.JSONDecodeError as e:
        print(
            f"session file corrupted at {path}; fix or restore from backup. ({e})",
            file=sys.stderr,
        )
        sys.exit(1)
    if data.get("schema_version") != SCHEMA_VERSION:
        print(
            f"session file at {path} is not schema_version {SCHEMA_VERSION}; "
            "check file or run migration.",
            file=sys.stderr,
        )
        sys.exit(1)
    return data


def save_session(session: dict[str, object]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(session, indent=2)
    fd, tmp_path = tempfile.mkstemp(dir=DATA_DIR, prefix=".session_tmp_")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(payload)
        os.replace(tmp_path, session_path(str(session["slug"])))
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def all_session_slugs() -> list[str]:
    if not DATA_DIR.exists():
        return []
    return sorted(p.stem for p in DATA_DIR.glob("*.json"))


def resolve_session(arg: str | None, context: str) -> dict[str, object]:
    """Resolve --session (exact slug, unique substring, or latest when omitted)."""
    slugs = all_session_slugs()
    if not slugs:
        die(context, "no grill sessions exist — create one with 'new'")

    if arg is None:
        sessions = [load_session(s) for s in slugs]
        return max(sessions, key=lambda s: (str(s.get("updated", "")), str(s["slug"])))

    if arg in slugs:
        return load_session(arg)

    matches = [s for s in slugs if arg in s]
    if len(matches) == 1:
        return load_session(matches[0])
    if not matches:
        die(context, f"no session matches '{arg}' — have: {', '.join(slugs)}")
    die(context, f"'{arg}' is ambiguous: {', '.join(matches)}")
    raise AssertionError("unreachable")


def find_decision(
    session: dict[str, object], decision_id: str, context: str
) -> dict[str, object]:
    for decision in session["decisions"]:  # type: ignore[union-attr]
        if decision["id"] == decision_id:
            return decision
    die(context, f"no decision '{decision_id}' in session {session['slug']}")
    raise AssertionError("unreachable")


def is_open(decision: dict[str, object]) -> bool:
    return decision.get("decision") is None


def confirm(context: str, session: dict[str, object], detail: str) -> None:
    print(f"[{context}] {session['slug']}: {detail}", file=sys.stderr)


def touch(session: dict[str, object]) -> None:
    session["updated"] = now()


# ── render ────────────────────────────────────────────────────────────────────


def _cell(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")


def render_markdown(session: dict[str, object]) -> str:
    decisions: list[dict[str, object]] = session["decisions"]  # type: ignore
    open_qs = [d for d in decisions if is_open(d)]
    decided = [d for d in decisions if not is_open(d)]
    verdicts = [d for d in decided if d.get("verdict")]

    plan_path = session.get("plan_path")
    lines = [
        f"# Grill status: {session['topic']}",
        "",
        f"_{str(session['created'])[:10]} · updated {str(session['updated'])[:10]} · "
        f"{len(decided)}/{len(decisions)} decided · {len(verdicts)} verified_",
        "",
        f"_plan: {plan_path}_" if plan_path else "_plan: not written yet_",
    ]

    if open_qs:
        lines += ["", "## Open questions", ""]
        for d in open_qs:
            lines.append(f"- **{d['id']}** — {d['question']}")

    if decided:
        lines += [
            "",
            "## Decisions",
            "",
            "| Decision | What we decided | Source | Verified |",
            "|----------|-----------------|--------|----------|",
        ]
        for d in decided:
            verdict = d.get("verdict") or {}
            result = str(verdict.get("result", "")) if verdict else ""
            lines.append(
                f"| {_cell(str(d['id']))} | {_cell(str(d['decision']))} "
                f"| {d['source']} | {result} |"
            )

    if verdicts:
        lines += ["", "## Verification evidence", ""]
        for d in verdicts:
            v: dict[str, object] = d["verdict"]  # type: ignore[assignment]
            lines.append(
                f"- **{d['id']}** — {v['result']} ({v['date']}): {v['evidence']}"
            )

    return "\n".join(lines) + "\n"


# ── subcommand handlers ───────────────────────────────────────────────────────


def cmd_new(args: argparse.Namespace) -> None:
    patch = parse_json_arg(args.json, "new")
    topic = str(patch.get("topic", "")).strip()
    if not topic:
        die("new", "'topic' is required")

    base = str(patch.get("slug", "")).strip() or f"{today()}-{slugify(topic)[:32]}"
    if not ID_RE.match(base):
        die("new", f"invalid slug '{base}' — lowercase kebab-case")

    slug = base
    existing = set(all_session_slugs())
    for n in range(2, 100):
        if slug not in existing:
            break
        slug = f"{base}-{n}"
    else:
        die("new", f"could not find a free slug for '{base}'")

    session: dict[str, object] = {
        "schema_version": SCHEMA_VERSION,
        "slug": slug,
        "topic": topic,
        "created": now(),
        "updated": now(),
        "plan_path": None,
        "decisions": [],
    }
    save_session(session)
    confirm("new", session, topic)
    print(slug)


def cmd_ask(args: argparse.Namespace) -> None:
    session = resolve_session(args.session, "ask")
    patch = parse_json_arg(args.json, "ask")

    decision_id = str(patch.get("id", "")).strip()
    if not decision_id:
        die("ask", "'id' is required")
    validate_decision_id(decision_id, "ask")

    question = str(patch.get("question", "")).strip()
    if not question:
        die("ask", "'question' is required")

    decisions: list[dict[str, object]] = session["decisions"]  # type: ignore
    if any(d["id"] == decision_id for d in decisions):
        die("ask", f"duplicate decision id: {decision_id}")

    decisions.append(
        {
            "id": decision_id,
            "question": question,
            "reasoning": str(patch.get("reasoning", "")).strip(),
            "decision": None,
            "source": None,
            "verdict": None,
        }
    )
    touch(session)
    save_session(session)
    confirm("ask", session, f"? {decision_id} — {question[:60]}")


def cmd_decide(args: argparse.Namespace) -> None:
    session = resolve_session(args.session, "decide")
    patch = parse_json_arg(args.json, "decide")

    decision_id = str(patch.get("id", "")).strip()
    if not decision_id:
        die("decide", "'id' is required")
    validate_decision_id(decision_id, "decide")

    question = str(patch.get("question", "")).strip()
    decision_text = str(patch.get("decision", "")).strip()
    if not decision_text:
        die("decide", "'decision' is required")

    source = str(patch.get("source", "user"))
    if source not in VALID_SOURCES:
        die(
            "decide",
            f"invalid source '{source}' — one of: {', '.join(sorted(VALID_SOURCES))}",
        )

    decisions: list[dict[str, object]] = session["decisions"]  # type: ignore
    existing = next((d for d in decisions if d["id"] == decision_id), None)

    if existing is not None:
        if not is_open(existing):
            die("decide", f"'{decision_id}' is already decided — use revise")
        existing["decision"] = decision_text
        existing["source"] = source
        if question:
            existing["question"] = question
        if "reasoning" in patch:
            existing["reasoning"] = str(patch["reasoning"]).strip()
    else:
        if not question:
            die(
                "decide",
                "'question' is required for a decision point not registered via ask",
            )
        decisions.append(
            {
                "id": decision_id,
                "question": question,
                "reasoning": str(patch.get("reasoning", "")).strip(),
                "decision": decision_text,
                "source": source,
                "verdict": None,
            }
        )

    touch(session)
    save_session(session)
    confirm("decide", session, f"{decision_id} ({source}) — {decision_text[:60]}")


def cmd_revise(args: argparse.Namespace) -> None:
    session = resolve_session(args.session, "revise")
    patch = parse_json_arg(args.patch, "revise")

    bad = set(patch) - REVISABLE_FIELDS
    if bad:
        die("revise", f"cannot revise field(s): {', '.join(sorted(bad))}")
    if "source" in patch and patch["source"] not in VALID_SOURCES:
        die("revise", f"invalid source '{patch['source']}'")

    decision = find_decision(session, args.decision_id, "revise")
    if is_open(decision) and ({"decision", "source"} & set(patch)):
        die("revise", f"'{args.decision_id}' is still open — resolve it with decide")
    decision.update(patch)
    note = ""
    if decision.get("verdict"):
        decision["verdict"] = None
        note = " (verdict reset — re-verify)"
    touch(session)
    save_session(session)
    confirm("revise", session, f"{args.decision_id} updated{note}")


def cmd_rm(args: argparse.Namespace) -> None:
    session = resolve_session(args.session, "rm")
    decision = find_decision(session, args.decision_id, "rm")
    decisions: list[dict[str, object]] = session["decisions"]  # type: ignore
    decisions.remove(decision)
    state = "open" if is_open(decision) else "decided"
    touch(session)
    save_session(session)
    confirm("rm", session, f"removed {args.decision_id} ({state})")


def cmd_verdict(args: argparse.Namespace) -> None:
    session = resolve_session(args.session, "verdict")
    patch = parse_json_arg(args.json, "verdict")

    result = str(patch.get("result", ""))
    if result not in VALID_RESULTS:
        die(
            "verdict",
            f"invalid result '{result}' — one of: {', '.join(sorted(VALID_RESULTS))}",
        )

    evidence = str(patch.get("evidence", "")).strip()
    if result in EVIDENCE_REQUIRED and not evidence:
        die(
            "verdict",
            f"{result} requires 'evidence' — what experiment was run, what happened",
        )

    decision = find_decision(session, args.decision_id, "verdict")
    if is_open(decision):
        die(
            "verdict",
            f"'{args.decision_id}' is still open — decide it before verifying",
        )
    decision["verdict"] = {"result": result, "evidence": evidence, "date": today()}
    touch(session)
    save_session(session)
    confirm("verdict", session, f"{args.decision_id}: {result}")


def cmd_plan(args: argparse.Namespace) -> None:
    session = resolve_session(args.session, "plan")
    path = Path(args.path).expanduser()
    if not path.exists():
        die(
            "plan",
            f"plan artifact not found at {path} — write it first, then record it",
        )
    session["plan_path"] = str(path)
    touch(session)
    save_session(session)
    confirm("plan", session, f"plan artifact recorded: {path}")


def cmd_next(args: argparse.Namespace) -> None:
    session = resolve_session(args.session, "next")
    for d in session["decisions"]:  # type: ignore[union-attr]
        if is_open(d):
            print(f"{d['id']}: {d['question']}")
            if d.get("reasoning"):
                print(f"  context: {d['reasoning']}")
            return
    print("(no open questions)")


def cmd_render(args: argparse.Namespace) -> None:
    session = resolve_session(args.session, "render")
    print(render_markdown(session), end="")


def cmd_list(args: argparse.Namespace) -> None:
    for slug in all_session_slugs():
        session = load_session(slug)
        decisions: list[dict[str, object]] = session["decisions"]  # type: ignore
        decided = [d for d in decisions if not is_open(d)]
        verified = sum(1 for d in decided if d.get("verdict"))
        print(
            f"{slug}\t{str(session.get('updated', ''))[:10]}\t"
            f"{len(decided)}/{len(decisions)} decided\t{verified} verified\t"
            f"{session.get('topic', '')}"
        )


def cmd_show(args: argparse.Namespace) -> None:
    session = resolve_session(args.session, "show")
    if args.decision_id:
        print(json.dumps(find_decision(session, args.decision_id, "show"), indent=2))
    else:
        print(json.dumps(session, indent=2))


# ── main ──────────────────────────────────────────────────────────────────────


def main() -> None:
    parser = argparse.ArgumentParser(
        description="grill-me session state CLI (all mutations go through here)",
    )
    sub = parser.add_subparsers(
        dest="cmd",
        metavar="{new,ask,decide,revise,rm,verdict,plan,next,render,list,show}",
    )

    def add_session_flag(p: argparse.ArgumentParser) -> None:
        p.add_argument(
            "--session",
            "-s",
            default=None,
            help="session slug or unique substring (default: most recent)",
        )

    p = sub.add_parser("new", help="create a session")
    p.add_argument("json", metavar='\'{"topic": "..."}\'')

    p = sub.add_parser("ask", help="register an open decision point")
    p.add_argument("json", metavar='\'{"id", "question", ["reasoning"]}\'')
    add_session_flag(p)

    p = sub.add_parser(
        "decide", help="resolve an open decision point (or add+decide in one shot)"
    )
    p.add_argument(
        "json",
        metavar='\'{"id", "decision", ["question"], ["reasoning"], ["source"]}\'',
    )
    add_session_flag(p)

    p = sub.add_parser("revise", help="amend a decision (resets its verdict)")
    p.add_argument("decision_id")
    p.add_argument("patch", metavar='\'{"decision": "..."}\'')
    add_session_flag(p)

    p = sub.add_parser("rm", help="remove a decision point from a session")
    p.add_argument("decision_id")
    add_session_flag(p)

    p = sub.add_parser("verdict", help="record a verification verdict")
    p.add_argument("decision_id")
    p.add_argument(
        "json",
        metavar='\'{"result": "VERIFIED|DISPUTED|UNVERIFIABLE", "evidence": "..."}\'',
    )
    add_session_flag(p)

    p = sub.add_parser(
        "plan", help="record the path of the model-authored plan artifact"
    )
    p.add_argument("path")
    add_session_flag(p)

    p = sub.add_parser("next", help="print the first open decision point")
    add_session_flag(p)

    p = sub.add_parser("render", help="print session status as markdown")
    add_session_flag(p)

    sub.add_parser("list", help="list sessions")

    p = sub.add_parser("show", help="print session (or one decision) as JSON")
    p.add_argument("decision_id", nargs="?", default=None)
    add_session_flag(p)

    args = parser.parse_args()

    dispatch = {
        "new": cmd_new,
        "ask": cmd_ask,
        "decide": cmd_decide,
        "revise": cmd_revise,
        "rm": cmd_rm,
        "verdict": cmd_verdict,
        "plan": cmd_plan,
        "next": cmd_next,
        "render": cmd_render,
        "list": cmd_list,
        "show": cmd_show,
    }

    if args.cmd in dispatch:
        dispatch[args.cmd](args)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
