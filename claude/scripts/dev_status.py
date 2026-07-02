#!/usr/bin/env python3
"""dev_status.py v2 — slug IDs, structured dependency graph, pure render."""

import argparse
import json
import os
import re
import sys
import tempfile
from datetime import date
from pathlib import Path

DATA_DIR = Path.home() / ".claude" / "data" / "backlog"
ITEMS_FILE = DATA_DIR / "items.json"

VALID_STATUSES = {"open", "in-progress", "done"}
IMMUTABLE_FIELDS = {"id", "created"}
RESERVED_SLUGS = {
    "render", "list", "show", "add", "update", "start", "done",
    "rename", "block", "unblock", "prune", "all", "help", "new",
}
SLUG_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)+$")
SLUG_MIN, SLUG_MAX = 3, 40


# ── helpers ───────────────────────────────────────────────────────────────────

def today():
    return date.today().isoformat()


def validate_slug(slug, context=""):
    prefix = f"[{context}] " if context else ""
    if slug in RESERVED_SLUGS:
        return f"{prefix}slug '{slug}' is a reserved word"
    if not SLUG_RE.match(slug):
        return (
            f"{prefix}invalid slug '{slug}' — must match "
            r"^[a-z0-9]+(-[a-z0-9]+)+$ (lowercase, hyphen-separated segments)"
        )
    if not (SLUG_MIN <= len(slug) <= SLUG_MAX):
        return f"{prefix}slug '{slug}' length {len(slug)} out of range [{SLUG_MIN},{SLUG_MAX}]"
    return None


# ── I/O ───────────────────────────────────────────────────────────────────────

def load_items():
    if not ITEMS_FILE.exists():
        return []
    try:
        data = json.loads(ITEMS_FILE.read_text())
    except json.JSONDecodeError as e:
        print(
            f"backlog file corrupted at {ITEMS_FILE}; restore from backup. ({e})",
            file=sys.stderr,
        )
        sys.exit(1)
    if not isinstance(data, dict) or data.get("schema_version") != 2:
        print(
            f"backlog file at {ITEMS_FILE} is not schema_version 2; "
            "check file or run migration.",
            file=sys.stderr,
        )
        sys.exit(1)
    return data.get("items", [])


def save_items(items):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    payload = json.dumps({"schema_version": 2, "items": items}, indent=2)
    fd, tmp_path = tempfile.mkstemp(dir=DATA_DIR, prefix=".items_tmp_")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(payload)
        os.replace(tmp_path, ITEMS_FILE)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


# ── graph helpers ─────────────────────────────────────────────────────────────

def build_index(items):
    return {i["id"]: i for i in items}


def effective_blockers(item, index):
    """Return blocked_by slugs whose referent item is not done."""
    return [
        s for s in item.get("blocked_by", [])
        if index.get(s, {}).get("status") != "done"
    ]


def detect_cycle(start, new_dep, index):
    """Return True if adding new_dep as a blocker of start would create a cycle."""
    visited = set()
    stack = [new_dep]
    while stack:
        node = stack.pop()
        if node == start:
            return True
        if node in visited:
            continue
        visited.add(node)
        dep = index.get(node)
        if dep:
            stack.extend(dep.get("blocked_by", []))
    return False


# ── render order ──────────────────────────────────────────────────────────────

def _render_order(items):
    """Return (in_progress, ready, blocked, done) lists in render order."""
    index = build_index(items)

    in_progress = sorted(
        [i for i in items if i.get("status") == "in-progress"],
        key=lambda i: i.get("updated", ""),
        reverse=True,
    )
    open_items = [i for i in items if i.get("status") == "open"]
    ready = sorted(
        [i for i in open_items if not effective_blockers(i, index)],
        key=lambda i: i.get("updated", ""),
        reverse=True,
    )
    blocked = sorted(
        [i for i in open_items if effective_blockers(i, index)],
        key=lambda i: (len(effective_blockers(i, index)), i.get("updated", "")),
    )
    done = sorted(
        [i for i in items if i.get("status") == "done"],
        key=lambda i: i.get("updated", ""),
        reverse=True,
    )[:5]

    return in_progress, ready, blocked, done


# ── number resolution ─────────────────────────────────────────────────────────

def resolve_id(arg, items):
    """Resolve a display number or slug string to a slug. Exits on failure."""
    try:
        n = int(arg)
    except ValueError:
        return arg  # already a slug — caller validates existence

    in_progress, ready, blocked, done = _render_order(items)
    ordered = in_progress + ready + blocked + done
    if not (1 <= n <= len(ordered)):
        print(f"[resolve] no item at position {n}", file=sys.stderr)
        sys.exit(1)
    return ordered[n - 1]["id"]


# ── render ────────────────────────────────────────────────────────────────────

def render(items=None, *, out=None, err=None):
    """Pure render — no writes, no side effects."""
    if out is None:
        out = sys.stdout
    if err is None:
        err = sys.stderr

    if items is None:
        items = load_items()

    index = build_index(items)
    in_progress, ready, blocked, done = _render_order(items)
    ordered = in_progress + ready + blocked + done

    if not ordered:
        print("(backlog is empty)", file=out)
        print("item-map:", file=err)
        return

    # Pre-assign all numbers so blocked-by annotations can reference any item
    slug_to_num = {item["id"]: n + 1 for n, item in enumerate(ordered)}
    item_map = {n + 1: item["id"] for n, item in enumerate(ordered)}

    sections = []

    def add_section(title, section_items, show_blockers=False):
        if not section_items:
            return
        lines = [title]
        for item in section_items:
            n = slug_to_num[item["id"]]
            lines.append(f"  {n:2}  {item.get('summary', '')}")
            if show_blockers:
                eff = effective_blockers(item, index)
                if eff:
                    parts = []
                    for i, slug in enumerate(eff):
                        dep = index.get(slug)
                        dep_n = slug_to_num.get(slug)
                        ref = f"#{dep_n}" if dep_n else slug
                        if i == 0 and dep:
                            hint = dep.get("summary", "")[:55]
                            parts.append(f"{ref} ({hint})")
                        else:
                            parts.append(ref)
                    lines.append(f"      \u21b3 blocked by: {', '.join(parts)}")
        sections.append(lines)

    add_section("IN PROGRESS", in_progress)
    add_section("READY", ready)
    add_section("BLOCKED", blocked, show_blockers=True)
    add_section("DONE", done)

    for i, section_lines in enumerate(sections):
        if i > 0:
            print("", file=out)
        for line in section_lines:
            print(line, file=out)

    map_str = ",".join(f"{n}={slug}" for n, slug in item_map.items())
    print(f"item-map: {map_str}", file=err)


# ── subcommand handlers ───────────────────────────────────────────────────────

def _parse_json_arg(raw, context):
    try:
        return json.loads(raw)
    except json.JSONDecodeError as e:
        print(f"[{context}] invalid JSON: {e}", file=sys.stderr)
        sys.exit(1)


def cmd_render(args):
    render()


def cmd_list(args):
    items = load_items()
    for item in items:
        print(f"{item['id']}\t{item.get('status', '')}\t{item.get('summary', '')}")


def cmd_show(args):
    items = load_items()
    slug = resolve_id(args.id, items)
    index = build_index(items)
    item = index.get(slug)
    if item is None:
        print(f"[show] not found: {slug}", file=sys.stderr)
        sys.exit(1)
    print(json.dumps(item, indent=2))


def cmd_add(args):
    patch = _parse_json_arg(args.json, "add")

    slug = patch.get("id", "").strip()
    if not slug:
        summary = patch.get("summary", "")
        suggestion = re.sub(r"[^a-z0-9]+", "-", summary.lower()).strip("-")[:38]
        if not suggestion:
            suggestion = "my-item"
        if "-" not in suggestion:
            suggestion = f"item-{suggestion}"
        print(
            f"[add] 'id' is required — suggested slug: {suggestion}",
            file=sys.stderr,
        )
        sys.exit(1)

    err = validate_slug(slug, "add")
    if err:
        print(err, file=sys.stderr)
        sys.exit(1)

    if not patch.get("summary", "").strip():
        print("[add] 'summary' is required", file=sys.stderr)
        sys.exit(1)

    items = load_items()
    index = build_index(items)

    if slug in index:
        print(f"[add] duplicate slug: {slug}", file=sys.stderr)
        sys.exit(1)

    blocked_by = patch.get("blocked_by", [])
    for dep in blocked_by:
        if dep not in index:
            print(f"[add] blocked_by references unknown slug: {dep}", file=sys.stderr)
            sys.exit(1)

    item = {
        "id": slug,
        "created": today(),
        "updated": today(),
        "status": "open",
        "summary": patch["summary"].strip(),
        "category": patch.get("category", "feature"),
        "blocked_by": blocked_by,
        "related_files": patch.get("related_files", []),
        "context": patch.get("context", ""),
        "next_steps": patch.get("next_steps", ""),
    }

    items.append(item)
    save_items(items)
    render(items)


def confirm_resolution(cmd, arg, item):
    """Echo what a mutating command resolved to, so misresolution is visible."""
    ref = f"{arg} → " if str(arg) != item["id"] else ""
    print(f"[{cmd}] {ref}{item['id']}: {item.get('summary', '')}", file=sys.stderr)


def cmd_update(args):
    items = load_items()
    slug = resolve_id(args.id, items)
    patch = _parse_json_arg(args.patch, "update")

    bad = set(patch) & IMMUTABLE_FIELDS
    if bad:
        print(
            f"[update] cannot modify immutable field(s): {', '.join(sorted(bad))}",
            file=sys.stderr,
        )
        sys.exit(1)

    if "status" in patch and patch["status"] not in VALID_STATUSES:
        print(
            f"[update] invalid status '{patch['status']}' — must be one of: "
            f"{', '.join(sorted(VALID_STATUSES))}",
            file=sys.stderr,
        )
        sys.exit(1)

    index = build_index(items)
    item = index.get(slug)
    if item is None:
        print(f"[update] not found: {slug}", file=sys.stderr)
        sys.exit(1)

    item.update(patch)
    item["updated"] = today()
    save_items(items)
    confirm_resolution("update", args.id, item)
    render(items)


def cmd_start(args):
    items = load_items()
    slug = resolve_id(args.id, items)
    index = build_index(items)
    item = index.get(slug)
    if item is None:
        print(f"[start] not found: {slug}", file=sys.stderr)
        sys.exit(1)
    item["status"] = "in-progress"
    item["updated"] = today()
    save_items(items)
    confirm_resolution("start", args.id, item)
    render(items)


def cmd_done(args):
    items = load_items()
    slug = resolve_id(args.id, items)
    index = build_index(items)
    item = index.get(slug)
    if item is None:
        print(f"[done] not found: {slug}", file=sys.stderr)
        sys.exit(1)
    item["status"] = "done"
    item["updated"] = today()
    save_items(items)
    confirm_resolution("done", args.id, item)
    render(items)


def cmd_rename(args):
    old_slug = args.old_slug
    new_slug = args.new_slug

    err = validate_slug(new_slug, "rename")
    if err:
        print(err, file=sys.stderr)
        sys.exit(1)

    items = load_items()
    index = build_index(items)

    if old_slug not in index:
        print(f"[rename] not found: {old_slug}", file=sys.stderr)
        sys.exit(1)
    if new_slug in index:
        print(f"[rename] collision: '{new_slug}' already exists", file=sys.stderr)
        sys.exit(1)

    word_re = re.compile(r"\b" + re.escape(old_slug) + r"\b")

    for item in items:
        if item["id"] == old_slug:
            item["id"] = new_slug
        item["blocked_by"] = [
            new_slug if s == old_slug else s
            for s in item.get("blocked_by", [])
        ]
        for field in ("summary", "context", "next_steps"):
            if old_slug in item.get(field, ""):
                item[field] = word_re.sub(new_slug, item[field])

    save_items(items)
    print(f"[rename] {old_slug} \u2192 {new_slug}", file=sys.stderr)
    render(items)


def cmd_block(args):
    items = load_items()
    slug = resolve_id(args.id, items)
    blocker = args.blocker
    index = build_index(items)

    item = index.get(slug)
    if item is None:
        print(f"[block] not found: {slug}", file=sys.stderr)
        sys.exit(1)
    if blocker not in index:
        print(f"[block] blocker not found: {blocker}", file=sys.stderr)
        sys.exit(1)
    if blocker in item.get("blocked_by", []):
        print(f"[block] {slug} already blocked by {blocker}", file=sys.stderr)
        sys.exit(1)
    if detect_cycle(slug, blocker, index):
        print(
            f"[block] would create a cycle: {blocker} already depends on {slug}",
            file=sys.stderr,
        )
        sys.exit(1)

    item.setdefault("blocked_by", []).append(blocker)
    item["updated"] = today()
    save_items(items)
    render(items)


def cmd_unblock(args):
    items = load_items()
    slug = resolve_id(args.id, items)
    blocker = args.blocker
    index = build_index(items)

    item = index.get(slug)
    if item is None:
        print(f"[unblock] not found: {slug}", file=sys.stderr)
        sys.exit(1)
    if blocker not in item.get("blocked_by", []):
        print(f"[unblock] {slug} is not blocked by {blocker}", file=sys.stderr)
        sys.exit(1)

    item["blocked_by"] = [s for s in item["blocked_by"] if s != blocker]
    item["updated"] = today()
    save_items(items)
    render(items)


def cmd_prune(args):
    items = load_items()
    cutoff_days = 14
    keep, pruned = [], 0
    for item in items:
        if item.get("status") == "done":
            try:
                age = (date.today() - date.fromisoformat(item["updated"])).days
            except (KeyError, ValueError):
                age = 0
            if age >= cutoff_days:
                pruned += 1
                continue
        keep.append(item)
    if pruned:
        save_items(keep)
        print(f"[prune] removed {pruned} item(s)")
    else:
        print("[prune] nothing to prune")


# ── main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="deterministic backlog dashboard v2",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(
        dest="cmd",
        metavar="{render,list,show,add,update,start,done,rename,block,unblock,prune}",
    )

    sub.add_parser("render", help="render dashboard (pure — no side effects)")
    sub.add_parser("list", help="flat tab-separated output")

    p = sub.add_parser("show", help="print full JSON for an item")
    p.add_argument("id", metavar="<slug|N>")

    p = sub.add_parser("add", help="append a new item (id required in JSON)")
    p.add_argument("json", metavar="'{\"id\": \"my-slug\", \"summary\": \"...\"}'")

    p = sub.add_parser("update", help="merge JSON patch into an item")
    p.add_argument("id", metavar="<slug|N>")
    p.add_argument("patch", metavar="'{\"field\": \"value\"}'")

    p = sub.add_parser("start", help="mark item in-progress")
    p.add_argument("id", metavar="<slug|N>")

    p = sub.add_parser("done", help="mark item done")
    p.add_argument("id", metavar="<slug|N>")

    p = sub.add_parser("rename", help="rename slug (rewrites all references)")
    p.add_argument("old_slug")
    p.add_argument("new_slug")

    p = sub.add_parser("block", help="add a blocker to an item")
    p.add_argument("id", metavar="<slug|N>")
    p.add_argument("blocker", metavar="<blocker-slug>")

    p = sub.add_parser("unblock", help="remove a blocker from an item")
    p.add_argument("id", metavar="<slug|N>")
    p.add_argument("blocker", metavar="<blocker-slug>")

    p = sub.add_parser("prune", help="permanently remove done items older than 14 days")
    p.add_argument("--force", action="store_true", required=True,
                   help="required to prevent accidental prune")

    args = parser.parse_args()

    dispatch = {
        "render": cmd_render,
        "list": cmd_list,
        "show": cmd_show,
        "add": cmd_add,
        "update": cmd_update,
        "start": cmd_start,
        "done": cmd_done,
        "rename": cmd_rename,
        "block": cmd_block,
        "unblock": cmd_unblock,
        "prune": cmd_prune,
    }

    if args.cmd in dispatch:
        dispatch[args.cmd](args)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
