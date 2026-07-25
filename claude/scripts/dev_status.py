#!/usr/bin/env python3
"""dev_status.py v2 — slug IDs, structured dependency graph, pure render."""

import argparse
import fcntl
import json
import os
import re
import sys
import tempfile
from contextlib import contextmanager
from datetime import date, datetime
from pathlib import Path

DATA_DIR = Path.home() / ".claude" / "data" / "backlog"
ITEMS_FILE = DATA_DIR / "items.json"
PENDING_FILE = DATA_DIR / "pending_items.json"
META_FILE = DATA_DIR / "_meta.json"
LOCK_FILE = DATA_DIR / ".backlog.lock"

VALID_STATUSES = {"open", "in-progress", "done"}
VALID_PRIORITIES = {"high", "normal", "low"}

# Sort rank for priority (absence == normal). Lower sorts first.
_PRIORITY_RANK = {"high": 0, "normal": 1, "low": 2}

VALID_PENDING_STATUSES = {"waiting_for_reply", "reply_received", "resolved"}
VALID_PENDING_KINDS = {"email", "chat", "approval"}
PENDING_MUTABLE_FIELDS = {
    "status",
    "description",
    "context",
    "next_steps",
    "blocking",
    "outcome",
    "source_ref",
}
IMMUTABLE_FIELDS = {"id", "created"}
# Subcommand names blocked from use as item slugs. Match is exact: only a
# slug equal to one of these bare verbs is refused — `remove-probe`,
# `add-feature`, etc. are accepted. Argparse never confuses a hyphenated
# slug with a subcommand (the subcommand is parsed from argv positionally),
# and the bare-verb reservation exists purely for dashboard clarity (no
# item literally named `remove`). Prefix-match refusal was considered and
# rejected: it would forbid natural slugs like `update-deps` for no real
# dispatch-safety gain (2026-07-25 decision).
RESERVED_SLUGS = {
    "render",
    "list",
    "show",
    "add",
    "update",
    "start",
    "done",
    "rename",
    "remove",
    "block",
    "unblock",
    "prune",
    "pending",
    "all",
    "help",
    "new",
}
SLUG_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)+$")
SLUG_MIN, SLUG_MAX = 3, 40

CATEGORY_TAG = {"bug": "bug", "feature": "feat", "chore": "chore", "research": "rsrch"}
STALE_DAYS = 7
SECTION_WIDTH = 44
_RESET = "\x1b[0m"
_COLORS = {
    "in_progress": "\x1b[33m",
    "ready": "\x1b[32m",
    "blocked": "\x1b[31m",
    "done": "\x1b[2m",
    "pending": "\x1b[35m",
    "warn": "\x1b[31m",
    "prio_high": "\x1b[1;31m",
    "prio_low": "\x1b[2m",
}


# ── helpers ───────────────────────────────────────────────────────────────────


def today():
    return date.today().isoformat()


def _category_tag(category):
    if not category:
        return ""
    tag = CATEGORY_TAG.get(category, category[:5])
    return f"[{tag}] "


def _age_days(updated_str):
    try:
        d = date.fromisoformat(updated_str)
    except (TypeError, ValueError):
        return None
    return (date.today() - d).days


def _use_color(out):
    return hasattr(out, "isatty") and out.isatty()


def _colorize(text, color_code, enabled):
    return f"{color_code}{text}{_RESET}" if enabled else text


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


def load_pending():
    if not PENDING_FILE.exists():
        return []
    try:
        data = json.loads(PENDING_FILE.read_text())
    except json.JSONDecodeError as e:
        print(
            f"pending-items file corrupted at {PENDING_FILE}; restore from backup. ({e})",
            file=sys.stderr,
        )
        sys.exit(1)
    if not isinstance(data, dict) or data.get("schema_version") != 1:
        print(
            f"pending-items file at {PENDING_FILE} is not schema_version 1; "
            "check file or run migration.",
            file=sys.stderr,
        )
        sys.exit(1)
    return data.get("items", [])


def save_pending(pending_items):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    payload = json.dumps({"schema_version": 1, "items": pending_items}, indent=2)
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


@contextmanager
def backlog_lock():
    """Exclusive lock over the full read-modify-write cycle of any mutating
    command. Held across items.json + pending_items.json + _meta.json so two
    concurrent writers (e.g. Claude Code and opencode sharing this store)
    serialize instead of racing."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with open(LOCK_FILE, "w") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


def load_rev():
    """Read current rev. Missing file == rev 0 (lazy auto-init, no migration)."""
    if not META_FILE.exists():
        return 0
    try:
        return json.loads(META_FILE.read_text()).get("rev", 0)
    except json.JSONDecodeError:
        return 0


def bump_rev():
    """Increment and persist rev. MUST be called while holding backlog_lock().
    Returns the new value."""
    rev = load_rev() + 1
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    payload = json.dumps({"rev": rev})
    fd, tmp_path = tempfile.mkstemp(dir=DATA_DIR, prefix=".meta_tmp_")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(payload)
        os.replace(tmp_path, META_FILE)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise
    return rev


def _backup_before_bulk_delete(path):
    """Snapshot a data file before an operation that removes records by a
    computed filter rather than by explicit id — the one class of mutation
    that isn't trivially reversible by re-running a single command."""
    if not path.exists():
        return
    stamp = datetime.now().strftime("%Y%m%dT%H%M%S")
    backup_path = path.with_name(f"{path.stem}.bak-{stamp}{path.suffix}")
    backup_path.write_bytes(path.read_bytes())


# ── graph helpers ─────────────────────────────────────────────────────────────


def build_index(items):
    return {i["id"]: i for i in items}


def effective_blockers(item, index):
    """Return blocked_by slugs whose referent item is not done."""
    return [
        s
        for s in item.get("blocked_by", [])
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


def _priority_rank(item):
    """Lower sorts first. Absence and any unknown value collapse to 'normal'."""
    return _PRIORITY_RANK.get(item.get("priority", "normal"), 1)


def _priority_glyph(item, color):
    """Leading 2-char gutter: bold-red up-triangle for high, dim down-triangle
    for low, two spaces for normal/absent \u2014 keeps every line's tag column
    aligned regardless of priority."""
    p = item.get("priority")
    if p == "high":
        return _colorize("\u25b2", _COLORS["prio_high"], color) + " "
    if p == "low":
        return _colorize("\u25bd", _COLORS["prio_low"], color) + " "
    return "  "


def _section_top(title, width=SECTION_WIDTH):
    prefix = f"\u250c\u2500 {title} "
    fill = max(width - len(prefix), 3)
    return prefix + ("\u2500" * fill)


def _section_bottom(width=SECTION_WIDTH):
    return "\u2514" + ("\u2500" * (width - 1))


def _render_order(items):
    """Return (in_progress, ready, blocked, done, done_total) in render order."""
    index = build_index(items)

    in_progress = sorted(
        [i for i in items if i.get("status") == "in-progress"],
        key=lambda i: i.get("updated", ""),
        reverse=True,
    )
    in_progress = sorted(in_progress, key=_priority_rank)  # stable
    open_items = [i for i in items if i.get("status") == "open"]
    ready = sorted(
        [i for i in open_items if not effective_blockers(i, index)],
        key=lambda i: i.get("created", ""),
    )
    ready = sorted(ready, key=_priority_rank)  # stable
    blocked = sorted(
        [i for i in open_items if effective_blockers(i, index)],
        key=lambda i: (len(effective_blockers(i, index)), i.get("updated", "")),
    )
    blocked = sorted(blocked, key=_priority_rank)  # stable
    done_all = sorted(
        [i for i in items if i.get("status") == "done"],
        key=lambda i: i.get("updated", ""),
        reverse=True,
    )
    done_total = len(done_all)
    done = done_all[:5]

    return in_progress, ready, blocked, done, done_total


def _pending_render_order(pending_items):
    """Unresolved pending items: reply_received group first, each group newest-first."""
    unresolved = [p for p in pending_items if p.get("status") != "resolved"]
    by_recency = sorted(unresolved, key=lambda p: p.get("updated", ""), reverse=True)
    return sorted(
        by_recency, key=lambda p: 0 if p.get("status") == "reply_received" else 1
    )


# ── number resolution ─────────────────────────────────────────────────────────


def _unified_order(items, pending_items):
    """Full cross-section render order: pending first, then the four backlog sections."""
    pending_ordered = _pending_render_order(pending_items)
    in_progress, ready, blocked, done, _ = _render_order(items)
    return pending_ordered + in_progress + ready + blocked + done


def resolve_id(arg, items, pending_items):
    """Resolve a display number or slug to (kind, slug). kind is 'backlog' or 'pending'.
    Exits on failure. For a non-numeric arg, kind is inferred by pending-id membership;
    caller still validates existence in whichever pool it expects."""
    try:
        n = int(arg)
    except ValueError:
        pending_ids = {p["id"] for p in pending_items}
        return ("pending" if arg in pending_ids else "backlog"), arg

    ordered = _unified_order(items, pending_items)
    if not (1 <= n <= len(ordered)):
        print(f"[resolve] no item at position {n}", file=sys.stderr)
        sys.exit(1)
    resolved = ordered[n - 1]
    pending_ids = {p["id"] for p in pending_items}
    kind = "pending" if resolved["id"] in pending_ids else "backlog"
    return kind, resolved["id"]


def require_kind(cmd, arg, kind, expected):
    if kind != expected:
        other = (
            "pending update/list"
            if expected == "backlog"
            else "update/start/done/block/unblock"
        )
        print(
            f"[{cmd}] position {arg} is a {kind} item — use '{other}' instead",
            file=sys.stderr,
        )
        sys.exit(1)


def enforce_rev_guard(cmd, id_arg, if_rev_arg, current_rev, items, pending_items):
    """Numeric id args must carry a matching --if-rev or the command refuses
    with no write. Slug id args are exempt — slug identity never goes stale."""
    try:
        int(id_arg)
    except (ValueError, TypeError):
        return  # slug-based call, no guard needed

    if if_rev_arg is None:
        print(
            f"[{cmd}] numeric id '{id_arg}' requires --if-rev <N> to guard "
            f"against a stale position — refusing (no write).",
            file=sys.stderr,
        )
        print(
            f"[{cmd}] current rev is {current_rev}. Re-confirm your target "
            f"below, then retry with --if-rev {current_rev}.",
            file=sys.stderr,
        )
        render(items, pending_items, rev=current_rev)
        sys.exit(1)

    if if_rev_arg != current_rev:
        print(
            f"[{cmd}] stale rev: --if-rev {if_rev_arg} given, current is "
            f"{current_rev} — the backlog changed since you last read it. "
            f"Refusing (no write).",
            file=sys.stderr,
        )
        render(items, pending_items, rev=current_rev)
        sys.exit(1)


# ── render ────────────────────────────────────────────────────────────────────


def _pending_suffix(item, color):
    marker = ""
    if item.get("status") == "reply_received":
        marker = " " + _colorize("reply received", _COLORS["pending"], color)
    age = _age_days(item.get("created", ""))
    since = f" (waiting {age}d)" if age is not None else ""
    return marker + since


def render(items=None, pending_items=None, *, out=None, err=None, rev=None):
    """Pure render — no writes, no side effects."""
    if out is None:
        out = sys.stdout
    if err is None:
        err = sys.stderr

    if items is None:
        items = load_items()
    if pending_items is None:
        pending_items = load_pending()
    if rev is None:
        rev = load_rev()

    index = build_index(items)
    in_progress, ready, blocked, done, done_total = _render_order(items)
    pending_ordered = _pending_render_order(pending_items)
    ordered = pending_ordered + in_progress + ready + blocked + done

    if not ordered:
        print("(backlog is empty)", file=out)
        print(f"item-map: rev={rev}", file=err)
        return

    color = _use_color(out)
    pending_id_set = {p["id"] for p in pending_items}

    # Pre-assign all numbers so blocked-by annotations can reference any item
    slug_to_num = {item["id"]: n + 1 for n, item in enumerate(ordered)}
    item_map = {
        n + 1: (
            f"pending:{item['id']}"
            if item["id"] in pending_id_set
            else f"backlog:{item['id']}"
        )
        for n, item in enumerate(ordered)
    }

    sections = []

    def add_section(
        title,
        section_items,
        show_blockers=False,
        show_age=False,
        color_code=None,
        summary_key="summary",
        show_category=True,
        line_suffix=None,
        show_priority=False,
    ):
        if not section_items:
            return
        frame_code = _COLORS.get(color_code)
        top = (
            _colorize(_section_top(title), frame_code, color)
            if frame_code
            else _section_top(title)
        )
        bottom = (
            _colorize(_section_bottom(), frame_code, color)
            if frame_code
            else _section_bottom()
        )
        lines = [top]
        for item in section_items:
            n = slug_to_num[item["id"]]
            badge = _priority_glyph(item, color) if show_priority else ""
            tag = _category_tag(item.get("category", "")) if show_category else ""
            line = f"\u2502  {n:2}  {badge}{tag}{item.get(summary_key, '')}"
            if line_suffix:
                line += line_suffix(item, color)
            if show_age:
                age = _age_days(item.get("updated", ""))
                if age is not None:
                    line += f" \u00b7 {age}d"
                    if age > STALE_DAYS:
                        line += " " + _colorize("\u26a0\ufe0f", _COLORS["warn"], color)
            lines.append(line)
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
                    lines.append(f"\u2502      \u21b3 blocked by: {', '.join(parts)}")
        lines.append(bottom)
        sections.append(lines)

    add_section(
        "PENDING",
        pending_ordered,
        color_code="pending",
        summary_key="description",
        show_category=False,
        line_suffix=_pending_suffix,
    )
    add_section(
        "IN PROGRESS",
        in_progress,
        show_age=True,
        show_priority=True,
        color_code="in_progress",
    )
    add_section("READY", ready, show_priority=True, color_code="ready")
    add_section(
        "BLOCKED",
        blocked,
        show_blockers=True,
        show_age=True,
        show_priority=True,
        color_code="blocked",
    )
    done_title = (
        f"DONE (showing {len(done)} of {done_total})"
        if done_total > len(done)
        else "DONE"
    )
    add_section(done_title, done, color_code="done")

    for i, section_lines in enumerate(sections):
        if i > 0:
            print("", file=out)
        for line in section_lines:
            print(line, file=out)

    map_str = ",".join(f"{n}={tag}" for n, tag in item_map.items())
    print(f"item-map: rev={rev} {map_str}", file=err)


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
    print(f"# rev={load_rev()}")
    for item in items:
        print(f"{item['id']}\t{item.get('status', '')}\t{item.get('summary', '')}")


def cmd_show(args):
    items = load_items()
    pending_items = load_pending()
    kind, slug = resolve_id(args.id, items, pending_items)
    index = (
        {p["id"]: p for p in pending_items} if kind == "pending" else build_index(items)
    )
    item = index.get(slug)
    if item is None:
        print(f"[show] not found: {slug}", file=sys.stderr)
        sys.exit(1)
    print(f"# rev={load_rev()}", file=sys.stderr)
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

    if "priority" in patch and patch["priority"] not in VALID_PRIORITIES:
        print(
            f"[add] invalid priority '{patch['priority']}' — must be one of: "
            f"{', '.join(sorted(VALID_PRIORITIES))}",
            file=sys.stderr,
        )
        sys.exit(1)

    with backlog_lock():
        items = load_items()
        index = build_index(items)

        if slug in index:
            print(f"[add] duplicate slug: {slug}", file=sys.stderr)
            sys.exit(1)

        blocked_by = patch.get("blocked_by", [])
        for dep in blocked_by:
            if dep not in index:
                print(
                    f"[add] blocked_by references unknown slug: {dep}", file=sys.stderr
                )
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
        if "priority" in patch:
            item["priority"] = patch["priority"]

        items.append(item)
        save_items(items)
        new_rev = bump_rev()
    render(items, rev=new_rev)


def confirm_resolution(cmd, arg, item, summary_key="summary"):
    """Echo what a mutating command resolved to, so misresolution is visible."""
    ref = f"{arg} → " if str(arg) != item["id"] else ""
    print(f"[{cmd}] {ref}{item['id']}: {item.get(summary_key, '')}", file=sys.stderr)


def cmd_update(args):
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

    if "priority" in patch and patch["priority"] not in VALID_PRIORITIES:
        print(
            f"[update] invalid priority '{patch['priority']}' — must be one of: "
            f"{', '.join(sorted(VALID_PRIORITIES))}",
            file=sys.stderr,
        )
        sys.exit(1)

    with backlog_lock():
        items = load_items()
        pending_items = load_pending()
        current_rev = load_rev()
        enforce_rev_guard(
            "update", args.id, args.if_rev, current_rev, items, pending_items
        )

        kind, slug = resolve_id(args.id, items, pending_items)
        require_kind("update", args.id, kind, "backlog")

        index = build_index(items)
        item = index.get(slug)
        if item is None:
            print(f"[update] not found: {slug}", file=sys.stderr)
            sys.exit(1)

        item.update(patch)
        item["updated"] = today()
        save_items(items)
        new_rev = bump_rev()
        confirm_resolution("update", args.id, item)
    render(items, rev=new_rev)


def cmd_start(args):
    with backlog_lock():
        items = load_items()
        pending_items = load_pending()
        current_rev = load_rev()
        enforce_rev_guard(
            "start", args.id, args.if_rev, current_rev, items, pending_items
        )

        kind, slug = resolve_id(args.id, items, pending_items)
        require_kind("start", args.id, kind, "backlog")
        index = build_index(items)
        item = index.get(slug)
        if item is None:
            print(f"[start] not found: {slug}", file=sys.stderr)
            sys.exit(1)
        item["status"] = "in-progress"
        item["updated"] = today()
        save_items(items)
        new_rev = bump_rev()
        confirm_resolution("start", args.id, item)
    render(items, rev=new_rev)


def cmd_done(args):
    with backlog_lock():
        items = load_items()
        pending_items = load_pending()
        current_rev = load_rev()
        enforce_rev_guard(
            "done", args.id, args.if_rev, current_rev, items, pending_items
        )

        kind, slug = resolve_id(args.id, items, pending_items)
        require_kind("done", args.id, kind, "backlog")
        index = build_index(items)
        item = index.get(slug)
        if item is None:
            print(f"[done] not found: {slug}", file=sys.stderr)
            sys.exit(1)
        item["status"] = "done"
        item["updated"] = today()
        save_items(items)
        new_rev = bump_rev()
        confirm_resolution("done", args.id, item)
    render(items, rev=new_rev)


def cmd_rename(args):
    old_slug = args.old_slug
    new_slug = args.new_slug

    err = validate_slug(new_slug, "rename")
    if err:
        print(err, file=sys.stderr)
        sys.exit(1)

    with backlog_lock():
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
                new_slug if s == old_slug else s for s in item.get("blocked_by", [])
            ]
            for field in ("summary", "context", "next_steps"):
                if old_slug in item.get(field, ""):
                    item[field] = word_re.sub(new_slug, item[field])

        save_items(items)
        new_rev = bump_rev()
    print(f"[rename] {old_slug} \u2192 {new_slug}", file=sys.stderr)
    render(items, rev=new_rev)


def cmd_block(args):
    blocker = args.blocker
    with backlog_lock():
        items = load_items()
        pending_items = load_pending()
        current_rev = load_rev()
        enforce_rev_guard(
            "block", args.id, args.if_rev, current_rev, items, pending_items
        )

        kind, slug = resolve_id(args.id, items, pending_items)
        require_kind("block", args.id, kind, "backlog")
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
        new_rev = bump_rev()
    render(items, rev=new_rev)


def cmd_unblock(args):
    blocker = args.blocker
    with backlog_lock():
        items = load_items()
        pending_items = load_pending()
        current_rev = load_rev()
        enforce_rev_guard(
            "unblock", args.id, args.if_rev, current_rev, items, pending_items
        )

        kind, slug = resolve_id(args.id, items, pending_items)
        require_kind("unblock", args.id, kind, "backlog")
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
        new_rev = bump_rev()
    render(items, rev=new_rev)


def cmd_pending_add(args):
    patch = _parse_json_arg(args.json, "pending add")

    slug = patch.get("id", "").strip()
    if not slug:
        print("[pending add] 'id' is required", file=sys.stderr)
        sys.exit(1)
    err = validate_slug(slug, "pending add")
    if err:
        print(err, file=sys.stderr)
        sys.exit(1)

    description = patch.get("description", "").strip()
    if not description:
        print("[pending add] 'description' is required", file=sys.stderr)
        sys.exit(1)

    kind = patch.get("kind", "")
    if kind not in VALID_PENDING_KINDS:
        print(
            f"[pending add] invalid kind '{kind}' — one of: "
            f"{', '.join(sorted(VALID_PENDING_KINDS))}",
            file=sys.stderr,
        )
        sys.exit(1)

    with backlog_lock():
        pending_items = load_pending()
        if any(p["id"] == slug for p in pending_items):
            print(f"[pending add] duplicate id: {slug}", file=sys.stderr)
            sys.exit(1)

        pending_items.append(
            {
                "id": slug,
                "created": today(),
                "updated": today(),
                "status": "waiting_for_reply",
                "description": description,
                "kind": kind,
                "source_ref": patch.get("source_ref", {}),
                "context": patch.get("context", ""),
                "next_steps": patch.get("next_steps", []),
                "blocking": patch.get("blocking", []),
                "outcome": None,
            }
        )
        save_pending(pending_items)
        new_rev = bump_rev()
    print(f"[pending add] {slug} — {description[:60]}", file=sys.stderr)
    render(pending_items=pending_items, rev=new_rev)


def cmd_pending_update(args):
    patch = _parse_json_arg(args.patch, "pending update")

    bad = set(patch) - PENDING_MUTABLE_FIELDS
    if bad:
        print(
            f"[pending update] cannot update field(s): {', '.join(sorted(bad))}",
            file=sys.stderr,
        )
        sys.exit(1)
    if "status" in patch and patch["status"] not in VALID_PENDING_STATUSES:
        print(
            f"[pending update] invalid status '{patch['status']}' — one of: "
            f"{', '.join(sorted(VALID_PENDING_STATUSES))}",
            file=sys.stderr,
        )
        sys.exit(1)

    with backlog_lock():
        items = load_items()
        pending_items = load_pending()
        current_rev = load_rev()
        enforce_rev_guard(
            "pending update", args.id, args.if_rev, current_rev, items, pending_items
        )

        kind, slug = resolve_id(args.id, items, pending_items)
        require_kind("pending update", args.id, kind, "pending")

        index = {p["id"]: p for p in pending_items}
        item = index.get(slug)
        if item is None:
            print(f"[pending update] not found: {slug}", file=sys.stderr)
            sys.exit(1)
        item.update(patch)
        item["updated"] = today()
        save_pending(pending_items)
        new_rev = bump_rev()
        confirm_resolution("pending update", args.id, item, summary_key="description")
    render(pending_items=pending_items, rev=new_rev)


def cmd_pending_list(args):
    for item in load_pending():
        print(json.dumps(item))


def cmd_remove(args):
    with backlog_lock():
        items = load_items()
        pending_items = load_pending()
        current_rev = load_rev()
        enforce_rev_guard(
            "remove", args.id, args.if_rev, current_rev, items, pending_items
        )

        kind, slug = resolve_id(args.id, items, pending_items)
        require_kind("remove", args.id, kind, "backlog")
        index = build_index(items)
        item = index.get(slug)
        if item is None:
            print(f"[remove] not found: {slug}", file=sys.stderr)
            sys.exit(1)
        keep = [i for i in items if i["id"] != slug]
        save_items(keep)
        new_rev = bump_rev()
    confirm_resolution("remove", args.id, item)
    render(keep, rev=new_rev)


def cmd_prune(args):
    cutoff_days = 14

    with backlog_lock():
        items = load_items()
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
            _backup_before_bulk_delete(ITEMS_FILE)
            save_items(keep)

        pending_items = load_pending()
        pending_keep, pending_pruned = [], 0
        for item in pending_items:
            if item.get("status") == "resolved":
                try:
                    age = (date.today() - date.fromisoformat(item["updated"])).days
                except (KeyError, ValueError):
                    age = 0
                if age >= cutoff_days:
                    pending_pruned += 1
                    continue
            pending_keep.append(item)
        if pending_pruned:
            _backup_before_bulk_delete(PENDING_FILE)
            save_pending(pending_keep)

        total = pruned + pending_pruned
        if total:
            bump_rev()
            print(
                f"[prune] removed {pruned} backlog item(s), "
                f"{pending_pruned} pending item(s) — backup written to {DATA_DIR}"
            )
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
        metavar="{render,list,show,add,update,start,done,rename,remove,block,unblock,prune}",
    )

    sub.add_parser("render", help="render dashboard (pure — no side effects)")
    sub.add_parser("list", help="flat tab-separated output")

    p = sub.add_parser("show", help="print full JSON for an item")
    p.add_argument("id", metavar="<slug|N>")

    p = sub.add_parser("add", help="append a new item (id required in JSON)")
    p.add_argument(
        "json",
        metavar='\'{"id": "my-slug", "summary": "...", "priority": "high"}\'',
    )

    p = sub.add_parser("update", help="merge JSON patch into an item")
    p.add_argument("id", metavar="<slug|N>")
    p.add_argument("patch", metavar='\'{"field": "value", "priority": "high"}\'')
    p.add_argument(
        "--if-rev",
        type=int,
        default=None,
        metavar="<N>",
        help="required when <id> is numeric; get the current value from "
        "render/list/show immediately before this call",
    )

    p = sub.add_parser("start", help="mark item in-progress")
    p.add_argument("id", metavar="<slug|N>")
    p.add_argument(
        "--if-rev",
        type=int,
        default=None,
        metavar="<N>",
        help="required when <id> is numeric; get the current value from "
        "render/list/show immediately before this call",
    )

    p = sub.add_parser("done", help="mark item done")
    p.add_argument("id", metavar="<slug|N>")
    p.add_argument(
        "--if-rev",
        type=int,
        default=None,
        metavar="<N>",
        help="required when <id> is numeric; get the current value from "
        "render/list/show immediately before this call",
    )

    p = sub.add_parser("rename", help="rename slug (rewrites all references)")
    p.add_argument("old_slug")
    p.add_argument("new_slug")

    p = sub.add_parser("remove", help="permanently remove one item by slug or number")
    p.add_argument("id", metavar="<slug|N>")
    p.add_argument(
        "--if-rev",
        type=int,
        default=None,
        metavar="<N>",
        help="required when <id> is numeric; get the current value from "
        "render/list/show immediately before this call",
    )

    p = sub.add_parser("block", help="add a blocker to an item")
    p.add_argument("id", metavar="<slug|N>")
    p.add_argument("blocker", metavar="<blocker-slug>")
    p.add_argument(
        "--if-rev",
        type=int,
        default=None,
        metavar="<N>",
        help="required when <id> is numeric; get the current value from "
        "render/list/show immediately before this call",
    )

    p = sub.add_parser("unblock", help="remove a blocker from an item")
    p.add_argument("id", metavar="<slug|N>")
    p.add_argument("blocker", metavar="<blocker-slug>")
    p.add_argument(
        "--if-rev",
        type=int,
        default=None,
        metavar="<N>",
        help="required when <id> is numeric; get the current value from "
        "render/list/show immediately before this call",
    )

    p = sub.add_parser(
        "prune", help="permanently remove done/resolved items older than 14 days"
    )
    p.add_argument(
        "--force",
        action="store_true",
        required=True,
        help="required to prevent accidental prune",
    )

    pending = sub.add_parser("pending", help="manage pending (waiting-on-reply) items")
    pending_sub = pending.add_subparsers(dest="pending_cmd")

    p = pending_sub.add_parser("add", help="track a new pending item")
    p.add_argument(
        "json",
        metavar='\'{"id", "description", "kind", ["source_ref"], ["context"], '
        '["next_steps"], ["blocking"]}\'',
    )

    p = pending_sub.add_parser(
        "update", help="merge a JSON patch into an existing pending item"
    )
    p.add_argument("id", metavar="<slug|N>")
    p.add_argument("patch", metavar='\'{"status": "reply_received", ...}\'')
    p.add_argument(
        "--if-rev",
        type=int,
        default=None,
        metavar="<N>",
        help="required when <id> is numeric; get the current value from "
        "render/list/show immediately before this call",
    )

    pending_sub.add_parser("list", help="list pending items as JSON lines")

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
        "remove": cmd_remove,
        "block": cmd_block,
        "unblock": cmd_unblock,
        "prune": cmd_prune,
    }

    if args.cmd == "pending":
        pending_dispatch = {
            "add": cmd_pending_add,
            "update": cmd_pending_update,
            "list": cmd_pending_list,
        }
        if args.pending_cmd in pending_dispatch:
            pending_dispatch[args.pending_cmd](args)
        else:
            pending.print_help()
            sys.exit(1)
    elif args.cmd in dispatch:
        dispatch[args.cmd](args)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
