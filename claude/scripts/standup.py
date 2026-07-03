#!/usr/bin/env python3
"""standup.py — /standup skill CLI: local data gathering + pending-items state.

`fetch` gathers everything a standup draft needs and prints it as JSON: two
fully-implemented local sources (git commits, scoped backlog items) plus
four adapter-backed sources (issue tracker, chat, email, calendar) that stay
stubbed — and get reported under "skipped" — until the workplace's actual tools
are known. `pending` manages the cross-day blocker/reply-tracking list; see
standup_adapters.py for the adapter interfaces.
"""

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from dataclasses import asdict
from datetime import date, datetime, timedelta
from pathlib import Path

from standup_adapters import ADAPTERS, NotConfiguredError

DATA_DIR = Path.home() / ".claude" / "data" / "standup"
CONFIG_FILE = DATA_DIR / "config.json"
PENDING_FILE = DATA_DIR / "pending_items.json"
BACKLOG_FILE = Path.home() / ".claude" / "data" / "backlog" / "items.json"
SCHEMA_VERSION = 1

VALID_KINDS = {"email", "chat", "approval"}
ID_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")


def die(context: str, msg: str) -> None:
    print(f"[{context}] {msg}", file=sys.stderr)
    sys.exit(1)


def today() -> str:
    return date.today().isoformat()


# ── config ────────────────────────────────────────────────────────────────


def load_config() -> dict[str, object]:
    if not CONFIG_FILE.exists():
        return {}
    return json.loads(CONFIG_FILE.read_text())


# ── pending items ─────────────────────────────────────────────────────────


def load_pending() -> dict[str, object]:
    if not PENDING_FILE.exists():
        return {"schema_version": SCHEMA_VERSION, "items": []}
    data = json.loads(PENDING_FILE.read_text())
    if data.get("schema_version") != SCHEMA_VERSION:
        die("pending", f"{PENDING_FILE} is not schema_version {SCHEMA_VERSION}")
    return data


def save_pending(data: dict[str, object]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(data, indent=2)
    fd, tmp_path = tempfile.mkstemp(dir=DATA_DIR, prefix=".pending_tmp_")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(payload)
        os.replace(tmp_path, PENDING_FILE)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def cmd_pending_add(args: argparse.Namespace) -> None:
    patch = json.loads(args.json)
    item_id = str(patch.get("id", "")).strip()
    if not item_id:
        die("pending add", "'id' is required")
    if not ID_RE.match(item_id):
        die("pending add", f"invalid id '{item_id}' — lowercase kebab-case")

    description = str(patch.get("description", "")).strip()
    if not description:
        die("pending add", "'description' is required")

    kind = str(patch.get("kind", ""))
    if kind not in VALID_KINDS:
        die("pending add", f"invalid kind '{kind}' — one of: {', '.join(sorted(VALID_KINDS))}")

    data = load_pending()
    items: list[dict[str, object]] = data["items"]  # type: ignore[assignment]
    if any(i["id"] == item_id for i in items):
        die("pending add", f"duplicate id: {item_id}")

    items.append(
        {
            "id": item_id,
            "description": description,
            "source_ref": str(patch.get("source_ref", "")).strip(),
            "kind": kind,
            "since": today(),
            "status": "open",
        }
    )
    save_pending(data)
    print(f"[pending add] {item_id} — {description[:60]}", file=sys.stderr)


def cmd_pending_resolve(args: argparse.Namespace) -> None:
    data = load_pending()
    items: list[dict[str, object]] = data["items"]  # type: ignore[assignment]
    item = next((i for i in items if i["id"] == args.id), None)
    if item is None:
        die("pending resolve", f"no pending item '{args.id}'")
    item["status"] = "resolved"
    save_pending(data)
    print(f"[pending resolve] {args.id}", file=sys.stderr)


def cmd_pending_list(_args: argparse.Namespace) -> None:
    data = load_pending()
    for item in data["items"]:  # type: ignore[union-attr]
        print(json.dumps(item))


# ── local sources: git commits ───────────────────────────────────────────


def git_commits(repos: list[str], since_days: int) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    commits: list[dict[str, str]] = []
    skipped: list[dict[str, str]] = []
    since = f"{since_days}.days.ago"

    for raw_repo in repos:
        repo = Path(raw_repo).expanduser()
        if not (repo / ".git").exists():
            skipped.append({"source": "git", "reason": f"{repo} is not a git repo"})
            continue

        email_result = subprocess.run(
            ["git", "-C", str(repo), "config", "user.email"],
            capture_output=True,
            text=True,
        )
        author = email_result.stdout.strip()
        if not author:
            skipped.append({"source": "git", "reason": f"{repo} has no configured user.email"})
            continue

        log_result = subprocess.run(
            [
                "git", "-C", str(repo), "log",
                f"--since={since}", f"--author={author}",
                "--pretty=format:%h\t%ad\t%s", "--date=short",
            ],
            capture_output=True,
            text=True,
        )
        for line in log_result.stdout.splitlines():
            parts = line.split("\t", 2)
            if len(parts) == 3:
                commits.append(
                    {"repo": repo.name, "hash": parts[0], "date": parts[1], "subject": parts[2]}
                )

    return commits, skipped


# ── local sources: backlog ───────────────────────────────────────────────


def backlog_items(prefixes: list[str], recent_done_days: int) -> tuple[list[dict[str, object]], list[dict[str, object]], list[dict[str, str]]]:
    if not prefixes:
        return [], [], [{"source": "backlog", "reason": "work_backlog_prefixes not configured in config.json — backlog source skipped"}]
    if not BACKLOG_FILE.exists():
        return [], [], [{"source": "backlog", "reason": f"{BACKLOG_FILE} not found"}]

    data = json.loads(BACKLOG_FILE.read_text())
    cutoff = datetime.now() - timedelta(days=recent_done_days)

    in_progress: list[dict[str, object]] = []
    recent_done: list[dict[str, object]] = []
    for item in data.get("items", []):
        item_id = str(item.get("id", ""))
        if not any(item_id.startswith(p) for p in prefixes):
            continue
        status = item.get("status")
        if status == "in-progress":
            in_progress.append(item)
        elif status == "done":
            updated = str(item.get("updated", ""))
            if updated and updated >= cutoff.isoformat():
                recent_done.append(item)

    return in_progress, recent_done, []


# ── fetch ─────────────────────────────────────────────────────────────────


def cmd_fetch(args: argparse.Namespace) -> None:
    config = load_config()
    skipped: list[dict[str, str]] = []

    commits, git_skips = git_commits(
        [str(r) for r in config.get("git_repos", [])],  # type: ignore[union-attr]
        int(config.get("commit_days", 1)),  # type: ignore[arg-type]
    )
    skipped.extend(git_skips)

    in_progress, recent_done, backlog_skips = backlog_items(
        [str(p) for p in config.get("work_backlog_prefixes", [])],  # type: ignore[union-attr]
        int(config.get("recent_done_days", 2)),  # type: ignore[arg-type]
    )
    skipped.extend(backlog_skips)

    assigned_items: list[dict[str, str]] = []
    try:
        assigned_items = [asdict(i) for i in ADAPTERS["issue_tracker"].get_assigned_items()]  # type: ignore[attr-defined]
    except NotConfiguredError as e:
        skipped.append({"source": "issue_tracker", "reason": str(e)})

    messages: list[dict[str, str]] = []
    try:
        since = date.today() - timedelta(days=int(config.get("commit_days", 1)))  # type: ignore[arg-type]
        messages = [asdict(m) for m in ADAPTERS["chat"].get_relevant_messages(since)]  # type: ignore[attr-defined]
    except NotConfiguredError as e:
        skipped.append({"source": "chat", "reason": str(e)})

    pending_from_adapter: list[dict[str, str]] = []
    try:
        pending_from_adapter = [asdict(p) for p in ADAPTERS["email"].get_pending_items()]  # type: ignore[attr-defined]
    except NotConfiguredError as e:
        skipped.append({"source": "email", "reason": str(e)})

    calendar_events: list[dict[str, object]] = []
    try:
        calendar_events = [asdict(c) for c in ADAPTERS["calendar"].get_calendar_events(date.today())]  # type: ignore[attr-defined]
    except NotConfiguredError as e:
        skipped.append({"source": "calendar", "reason": str(e)})

    pending_state = load_pending()

    result = {
        "date": today(),
        "git_commits": commits,
        "backlog_in_progress": in_progress,
        "backlog_recent_done": recent_done,
        "assigned_items": assigned_items,
        "messages": messages,
        "calendar_events": [e for e in calendar_events if not e.get("is_recurring")],
        "pending_items_open": [i for i in pending_state["items"] if i.get("status") == "open"],  # type: ignore[union-attr]
        "pending_items_from_adapter": pending_from_adapter,
        "skipped": skipped,
    }
    print(json.dumps(result, indent=2))


# ── main ──────────────────────────────────────────────────────────────────


def main() -> None:
    parser = argparse.ArgumentParser(description="/standup skill CLI")
    sub = parser.add_subparsers(dest="cmd")

    sub.add_parser("fetch", help="gather all sources as JSON")

    pending = sub.add_parser("pending", help="manage the pending-items list")
    pending_sub = pending.add_subparsers(dest="pending_cmd")

    p = pending_sub.add_parser("add", help="track a new pending item")
    p.add_argument("json", metavar='\'{"id", "description", "source_ref", "kind"}\'')

    p = pending_sub.add_parser("resolve", help="mark a pending item resolved")
    p.add_argument("id")

    pending_sub.add_parser("list", help="list pending items as JSON lines")

    args = parser.parse_args()

    if args.cmd == "fetch":
        cmd_fetch(args)
    elif args.cmd == "pending":
        pending_dispatch = {
            "add": cmd_pending_add,
            "resolve": cmd_pending_resolve,
            "list": cmd_pending_list,
        }
        if args.pending_cmd in pending_dispatch:
            pending_dispatch[args.pending_cmd](args)
        else:
            pending.print_help()
            sys.exit(1)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
