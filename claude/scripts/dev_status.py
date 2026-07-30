#!/usr/bin/env python3
"""dev_status.py v2 — slug IDs, structured dependency graph, pure render.

Backs the personal task/pending-item dashboard shared by Claude Code and
other harnesses that read and write the same on-disk JSON store. Every
mutating subcommand acquires an exclusive file lock, reads the current
state, applies its change, writes atomically, and bumps a monotonic
revision counter so numeric positional references (e.g. ``done 3``) can be
guarded against staleness with ``--if-rev``.

Requires Python 3.12+.
"""

import argparse
import fcntl
import json
import os
import re
import sys
import tempfile
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import NotRequired, TextIO, TypedDict, cast

DATA_DIR = Path.home() / ".claude" / "data" / "backlog"
ITEMS_FILE = DATA_DIR / "items.json"
PENDING_FILE = DATA_DIR / "pending_items.json"
META_FILE = DATA_DIR / "_meta.json"
LOCK_FILE = DATA_DIR / ".backlog.lock"

VALID_STATUSES = {"open", "in-progress", "done"}
VALID_PRIORITIES = {"high", "normal", "low"}

# Recency window (in hours) for the dashboard's DONE section: only items
# completed within this many hours appear. Keyed on `completed_at`, falling
# back to `updated` when `completed_at` is absent (legacy items). An hours
# helper (vs. the days granularity of `_age_days`) keeps the window honest
# and survives a future upgrade to full timestamps — `datetime.fromisoformat`
# accepts both date-only and full ISO strings, where `date.fromisoformat`
# would raise on the latter.
DONE_RECENCY_HOURS = 48

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
IMMUTABLE_FIELDS = {"id", "created", "completed_at"}
BACKLOG_MUTABLE_FIELDS = {
    "summary",
    "category",
    "blocked_by",
    "related_files",
    "context",
    "next_steps",
    "priority",
    "status",
}
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


# ── data model ───────────────────────────────────────────────────────────────


class BacklogItem(TypedDict):
    """A single backlog item as stored in ``items.json`` (schema v2).

    ``priority`` and ``completed_at`` are absent unless explicitly set —
    absence of ``priority`` is equivalent to ``"normal"`` (see
    :data:`_PRIORITY_RANK`), and ``completed_at`` only exists while
    ``status`` is ``"done"`` (stamped and cleared by
    :func:`_apply_status_transition`).
    """

    id: str
    created: str
    updated: str
    status: str
    summary: str
    category: str
    blocked_by: list[str]
    related_files: list[dict[str, object]]
    context: str
    next_steps: str
    priority: NotRequired[str]
    completed_at: NotRequired[str]


class PendingItem(TypedDict):
    """A single waiting-on-someone-else item as stored in ``pending_items.json``.

    ``resolved_at`` is absent unless ``status`` is ``"resolved"`` (stamped
    and cleared by :func:`_apply_status_transition`).
    """

    id: str
    created: str
    updated: str
    status: str
    description: str
    kind: str
    source_ref: dict[str, object]
    context: str
    next_steps: list[str]
    blocking: list[str]
    outcome: str | None
    resolved_at: NotRequired[str]


type BacklogIndex = dict[str, BacklogItem]
type RenderOrder = tuple[
    list[BacklogItem], list[BacklogItem], list[BacklogItem], list[BacklogItem]
]


# ── helpers ───────────────────────────────────────────────────────────────────


def today() -> str:
    """Return today's date as an ISO-8601 string (``YYYY-MM-DD``)."""
    return date.today().isoformat()


def _apply_status_transition(
    item: dict[str, object], new_status: str, stamp_field: str, done_value: str
) -> None:
    """Stamp or clear a completion timestamp as an item's status changes.

    Stamps ``stamp_field`` with today's date when ``new_status`` enters
    ``done_value``, and clears it when the item's status leaves
    ``done_value``. Called from every path that can change status, so the
    stamp can't be bypassed or left stale.

    Args:
        item: The backlog or pending item being mutated, in place.
        new_status: The status value about to be applied.
        stamp_field: Name of the field to stamp/clear (e.g.
            ``"completed_at"`` for backlog items, ``"resolved_at"`` for
            pending items).
        done_value: The status value that triggers stamping (e.g.
            ``"done"`` or ``"resolved"``).
    """
    old_status = item.get("status")
    if new_status == done_value and old_status != done_value:
        item[stamp_field] = today()
    elif old_status == done_value and new_status != done_value:
        item.pop(stamp_field, None)


def _category_tag(category: str) -> str:
    """Render a category as a bracketed line prefix, e.g. ``"[bug] "``.

    Unknown categories are truncated to 5 characters rather than rejected,
    since the tag is cosmetic only.

    Args:
        category: The item's category, or ``""`` for none.

    Returns:
        A bracketed, space-suffixed tag, or ``""`` if ``category`` is falsy.
    """
    if not category:
        return ""
    tag = CATEGORY_TAG.get(category, category[:5])
    return f"[{tag}] "


def _age_days(updated_str: str) -> int | None:
    """Return the number of days between today and an ISO date string.

    Args:
        updated_str: An ISO-8601 date string, or any invalid/empty value.

    Returns:
        Whole days elapsed since ``updated_str``, or ``None`` if it isn't a
        valid ISO date.
    """
    try:
        d = date.fromisoformat(updated_str)
    except (TypeError, ValueError):
        return None
    return (date.today() - d).days


def _done_stamp(item: BacklogItem) -> str:
    """Return the completion timestamp to key the DONE recency window on.

    Prefers ``completed_at`` (stamped only on actual completion, so it's
    immune to later edits bumping ``updated``); falls back to ``updated``
    only for legacy done items that lack ``completed_at``.
    """
    return cast(str, item.get("completed_at") or item.get("updated", ""))


def _age_hours(stamp_str: str) -> float | None:
    """Return whole hours elapsed since an ISO date or datetime string.

    Unlike :func:`_age_days`, this parses with :func:`datetime.fromisoformat`
    so it accepts full ISO timestamps (with a time component) as well as
    bare dates (taken as midnight). This keeps the DONE-section recency
    window meaningful on today's date-only stamps and correct if the stamp
    format ever gains a time component.

    Timezone-aware stamps (e.g. ``2026-07-29T10:00:00+00:00``) are
    compared against a timezone-aware "now"; naive stamps against naive
    ``datetime.now()``. Mixing the two raises ``TypeError`` outside the
    try/except, which would crash :func:`render` end-to-end — a future
    upgrade to tz-aware ``completed_at`` stamps would trip this.

    Args:
        stamp_str: An ISO-8601 date or datetime string, or any invalid/empty
            value.

    Returns:
        Whole hours elapsed since ``stamp_str``, or ``None`` if it isn't a
        valid ISO date/datetime.
    """
    try:
        dt = datetime.fromisoformat(stamp_str)
    except (TypeError, ValueError):
        return None
    now = datetime.now(timezone.utc) if dt.tzinfo is not None else datetime.now()
    delta = now - dt
    return delta.total_seconds() / 3600.0


def _use_color(out: TextIO) -> bool:
    """Return whether ANSI color codes should be written to ``out``."""
    return hasattr(out, "isatty") and out.isatty()


def _colorize(text: str, color_code: str, enabled: bool) -> str:
    """Wrap ``text`` in an ANSI color code, or return it unchanged.

    Args:
        text: The text to colorize.
        color_code: An ANSI escape sequence (e.g. from :data:`_COLORS`).
        enabled: Whether coloring is active (typically from
            :func:`_use_color`).
    """
    return f"{color_code}{text}{_RESET}" if enabled else text


def validate_slug(slug: str, context: str = "") -> str | None:
    """Validate a candidate item slug.

    Args:
        slug: The candidate slug.
        context: Optional command name to prefix onto the error message.

    Returns:
        ``None`` if ``slug`` is valid, otherwise a human-readable error
        message describing which rule it violates.
    """
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


def load_items() -> list[BacklogItem]:
    """Load all backlog items from :data:`ITEMS_FILE`.

    Returns:
        The stored items, or ``[]`` if the file doesn't exist yet.

    Raises:
        SystemExit: If the file contains invalid JSON or an unrecognized
            schema version. The process exits with status 1 after printing
            a diagnostic to stderr.
    """
    if not ITEMS_FILE.exists():
        return []
    try:
        data = json.loads(ITEMS_FILE.read_text())
    except (json.JSONDecodeError, UnicodeDecodeError, OSError) as e:
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
    return cast(list[BacklogItem], data.get("items", []))


def _atomic_write_json(path: Path, payload: str, prefix: str) -> None:
    """Write serialized JSON to ``path`` via a temp file + ``os.replace``.

    Shared by every writer of the backlog data files. Cleans up the temp
    file on failure so a crash mid-write never leaves debris behind or
    corrupts the destination.

    Args:
        path: Destination file path.
        payload: Already-serialized JSON text to write.
        prefix: Prefix for the temporary file created alongside ``path``.
    """
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=DATA_DIR, prefix=prefix)
    try:
        with os.fdopen(fd, "w") as f:
            f.write(payload)
        os.replace(tmp_path, path)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def save_items(items: list[BacklogItem]) -> None:
    """Atomically persist ``items`` to :data:`ITEMS_FILE`."""
    payload = json.dumps({"schema_version": 2, "items": items}, indent=2)
    _atomic_write_json(ITEMS_FILE, payload, ".items_tmp_")


def load_pending() -> list[PendingItem]:
    """Load all pending items from :data:`PENDING_FILE`.

    Returns:
        The stored pending items, or ``[]`` if the file doesn't exist yet.

    Raises:
        SystemExit: If the file contains invalid JSON or an unrecognized
            schema version. The process exits with status 1 after printing
            a diagnostic to stderr.
    """
    if not PENDING_FILE.exists():
        return []
    try:
        data = json.loads(PENDING_FILE.read_text())
    except (json.JSONDecodeError, UnicodeDecodeError, OSError) as e:
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
    return cast(list[PendingItem], data.get("items", []))


def save_pending(pending_items: list[PendingItem]) -> None:
    """Atomically persist ``pending_items`` to :data:`PENDING_FILE`."""
    payload = json.dumps({"schema_version": 1, "items": pending_items}, indent=2)
    _atomic_write_json(PENDING_FILE, payload, ".pending_tmp_")


@contextmanager
def backlog_lock() -> Iterator[None]:
    """Hold an exclusive lock over a mutating command's full read-modify-write cycle.

    Held across items.json + pending_items.json + _meta.json so two
    concurrent writers (e.g. Claude Code and opencode sharing this store)
    serialize instead of racing.
    """
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with open(LOCK_FILE, "w") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


def load_rev() -> int:
    """Read the current revision counter.

    Returns:
        The stored revision, or ``0`` if :data:`META_FILE` is missing or
        unreadable (lazy auto-init, no migration needed). A structurally
        valid non-dict JSON value (e.g. ``[]``) or a non-int ``rev`` field
        also falls back to ``0`` — the previous narrow ``JSONDecodeError``
        catch alone would let a non-dict traceback with
        ``AttributeError`` on the ``.get`` and a non-int rev silently brick
        every numeric ``--if-rev`` mutation.
    """
    if not META_FILE.exists():
        return 0
    try:
        data = json.loads(META_FILE.read_text())
    except (json.JSONDecodeError, UnicodeDecodeError, OSError):
        return 0
    if not isinstance(data, dict):
        return 0
    rev = data.get("rev", 0)
    if not isinstance(rev, int) or isinstance(rev, bool):
        return 0
    return rev


def bump_rev() -> int:
    """Increment and persist the revision counter.

    Must be called while holding :func:`backlog_lock`.

    Returns:
        The new revision value.
    """
    rev = load_rev() + 1
    payload = json.dumps({"rev": rev})
    _atomic_write_json(META_FILE, payload, ".meta_tmp_")
    return rev


def _backup_before_bulk_delete(path: Path) -> None:
    """Snapshot a data file before a filter-based bulk deletion.

    Covers the one class of mutation that isn't trivially reversible by
    re-running a single command: records removed by a computed filter
    rather than by explicit id.

    Args:
        path: The data file to snapshot. No-op if it doesn't exist.
    """
    if not path.exists():
        return
    stamp = datetime.now().strftime("%Y%m%dT%H%M%S")
    backup_path = path.with_name(f"{path.stem}.bak-{stamp}{path.suffix}")
    backup_path.write_bytes(path.read_bytes())


# ── graph helpers ─────────────────────────────────────────────────────────────


def build_index(items: list[BacklogItem]) -> BacklogIndex:
    """Build a slug → item lookup for ``items``."""
    return {i["id"]: i for i in items}


def effective_blockers(item: BacklogItem, index: BacklogIndex) -> list[str]:
    """Return ``item``'s ``blocked_by`` slugs whose referent isn't done.

    A blocker slug that no longer resolves in ``index`` still counts as
    unresolved — it's returned as-is. This missing-slug fallback only
    fires for legacy or hand-edited data now that :func:`cmd_remove`/
    :func:`cmd_prune` purge inbound ``blocked_by`` references on delete.

    A stored ``blocked_by`` that isn't a list (legacy corruption — a
    string value would previously be iterated character-by-character) is
    coerced to ``[]`` with a stderr warning so :func:`render` doesn't
    walk a string one character at a time and emit a slot per character.

    Args:
        item: The item to check.
        index: Slug → item lookup, as built by :func:`build_index`.
    """
    bb = item.get("blocked_by", [])
    if not isinstance(bb, list):
        print(
            f"[effective_blockers] {item.get('id', '?')}.blocked_by is "
            f"{type(bb).__name__}, not list — coercing to []",
            file=sys.stderr,
        )
        return []
    result = []
    for s in bb:
        dep = index.get(s)
        if dep is None or dep.get("status") != "done":
            result.append(s)
    return result


def detect_cycle(start: str, new_dep: str, index: BacklogIndex) -> bool:
    """Check whether adding ``new_dep`` as a blocker of ``start`` would cycle.

    Args:
        start: Slug of the item that would gain ``new_dep`` as a blocker.
        new_dep: Slug of the proposed blocker.
        index: Slug → item lookup, as built by :func:`build_index`.

    Returns:
        ``True`` if ``new_dep`` (transitively, via its own blockers)
        already depends on ``start``.
    """
    visited: set[str] = set()
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


def _purge_inbound_refs(
    removed_slugs: set[str],
    items: list[BacklogItem],
    pending_items: list[PendingItem],
) -> None:
    """Strip every reference to ``removed_slugs`` from surviving records.

    Purges ``removed_slugs`` from backlog items' ``blocked_by`` lists and
    pending items' ``blocking`` lists. Without this, a deleted blocker
    would remain in its dependents' ``blocked_by`` and
    :func:`effective_blockers` would treat the missing slug as unresolved,
    retroactively flipping dependents from READY into BLOCKED.

    Mutates the passed records in place.
    """
    if not removed_slugs:
        return
    for item in items:
        if item.get("id") in removed_slugs:
            continue
        bb = item.get("blocked_by") or []
        if any(s in removed_slugs for s in bb):
            item["blocked_by"] = [s for s in bb if s not in removed_slugs]
    for p in pending_items:
        if p.get("id") in removed_slugs:
            continue
        blocking = p.get("blocking") or []
        if any(s in removed_slugs for s in blocking):
            p["blocking"] = [s for s in blocking if s not in removed_slugs]


# ── render order ──────────────────────────────────────────────────────────────


def _priority_rank(item: BacklogItem) -> int:
    """Sort rank for ``item``'s priority; lower sorts first.

    Absence and any unrecognized value both collapse to ``"normal"``'s rank.
    """
    return _PRIORITY_RANK.get(item.get("priority", "normal"), 1)


def _priority_glyph(item: BacklogItem, color: bool) -> str:
    """Render the leading 2-char priority gutter for one dashboard line.

    Bold-red up-triangle for high, dim down-triangle for low, dim middle
    dot for normal/absent — keeps every line's tag column aligned and
    gives every row a mark instead of a blank hole.
    """
    p = item.get("priority")
    if p == "high":
        return _colorize("▲", _COLORS["prio_high"], color) + " "
    if p == "low":
        return _colorize("▽", _COLORS["prio_low"], color) + " "
    return _colorize("·", _COLORS["prio_low"], color) + " "


def _section_top(title: str, width: int = SECTION_WIDTH) -> str:
    """Render a section's top border with an embedded title."""
    prefix = f"┌─ {title} "
    fill = max(width - len(prefix), 3)
    return prefix + ("─" * fill)


def _section_bottom(width: int = SECTION_WIDTH) -> str:
    """Render a section's bottom border."""
    return "└" + ("─" * (width - 1))


def _render_order(items: list[BacklogItem]) -> RenderOrder:
    """Bucket and sort backlog items into dashboard render order.

    Returns:
        A 4-tuple of ``(in_progress, ready, blocked, done)``, where
        ``done`` is the subset of done items completed within the last
        :data:`DONE_RECENCY_HOURS` (keyed on ``completed_at``, falling back
        to ``updated``), sorted most-recently-completed first using that
        same key. The dashboard omits the DONE section entirely when this
        is empty.

    Note:
        DONE-section membership is a function of ``datetime.now()`` vs
        :data:`DONE_RECENCY_HOURS`, so the numeric positions of DONE rows
        can shift with the passage of time alone, without a rev bump —
        defeating a downstream numeric ``--if-rev`` guard whose snapshot
        was taken before the wall-clock edge crossed. Accepted as a known
        low-severity limitation: DONE items are rarely the target of a
        numeric mutation, and the rev guard still catches concurrent
        *writes*. A systemic snapshot-or-stored-state fix would close this;
        see backlog ``meta-devstatus-atomicity-fsync``.
    """
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
    # Explicit `is None` check (not `or float("inf")`): an age of exactly
    # `0.0` (a stamp dated this very second) is falsy and would otherwise
    # be excluded from the window. None (invalid stamp) still excludes.
    done_in_window = [
        i
        for i in items
        if i.get("status") == "done"
        and _age_hours(_done_stamp(i)) is not None
        and cast(float, _age_hours(_done_stamp(i))) < DONE_RECENCY_HOURS
    ]
    done = sorted(done_in_window, key=_done_stamp, reverse=True)

    return in_progress, ready, blocked, done


def _blocker_check_reminder(
    items: list[BacklogItem],
    exclude_slug: str | None,
    *,
    cmd: str,
    err: TextIO | None = None,
) -> None:
    """Print a one-line stderr reminder to check for blocker relationships.

    Fires after a successful ``add``/``pending add`` when other READY or
    IN PROGRESS items exist — ``render()`` already printed the list above
    this, so this only adds the imperative, not a re-print. No matching
    heuristic; the caller (human or agent) makes the judgment call.

    Args:
        items: The current backlog items.
        exclude_slug: Slug to omit from the candidate count (typically the
            item that was just added), or ``None`` if nothing to exclude.
        cmd: Command name to prefix onto the reminder.
        err: Stream to print to; defaults to ``sys.stderr``.
    """
    if err is None:
        err = sys.stderr
    in_progress, ready, _, _ = _render_order(items)
    candidates = [i for i in in_progress + ready if i["id"] != exclude_slug]
    if not candidates:
        return
    print(
        f"[{cmd}] check the READY/IN PROGRESS items above for blocker relationships",
        file=err,
    )


def _pending_render_order(pending_items: list[PendingItem]) -> list[PendingItem]:
    """Order unresolved pending items: reply_received group first, each newest-first."""
    unresolved = [p for p in pending_items if p.get("status") != "resolved"]
    by_recency = sorted(unresolved, key=lambda p: p.get("updated", ""), reverse=True)
    return sorted(
        by_recency, key=lambda p: 0 if p.get("status") == "reply_received" else 1
    )


# ── number resolution ─────────────────────────────────────────────────────────


def _unified_order(
    items: list[BacklogItem], pending_items: list[PendingItem]
) -> list[BacklogItem | PendingItem]:
    """Return the full cross-section render order: pending first, then backlog."""
    pending_ordered = _pending_render_order(pending_items)
    in_progress, ready, blocked, done = _render_order(items)
    return [*pending_ordered, *in_progress, *ready, *blocked, *done]


def resolve_id(
    arg: str, items: list[BacklogItem], pending_items: list[PendingItem]
) -> tuple[str, str]:
    """Resolve a display number or slug to a ``(kind, slug)`` pair.

    Args:
        arg: A 1-based display number (as printed by ``render``) or a slug.
        items: The current backlog items.
        pending_items: The current pending items.

    Returns:
        ``(kind, slug)`` where ``kind`` is ``"backlog"`` or ``"pending"``.
        For a non-numeric ``arg``, ``kind`` is inferred by checking which
        pool the slug belongs to.

    Raises:
        SystemExit: If ``arg`` is numeric but out of range for the current
            render order, or if a non-numeric ``arg`` matches no item in
            either pool. Exits with status 1 after printing to stderr.
    """
    try:
        n = int(arg)
    except ValueError:
        pending_ids = {p["id"] for p in pending_items}
        if arg in pending_ids:
            return "pending", arg
        backlog_ids = {i["id"] for i in items}
        if arg in backlog_ids:
            return "backlog", arg
        # Unknown slug — surface not-found here, before require_kind gets
        # a chance to mis-resolve it as "wrong kind" (the previous
        # `("backlog", arg)` default made `pending update typo-slug ...`
        # claim the slug was "a backlog item" when no such item existed).
        print(f"[resolve] not found: {arg}", file=sys.stderr)
        sys.exit(1)

    ordered = _unified_order(items, pending_items)
    if not (1 <= n <= len(ordered)):
        print(f"[resolve] no item at position {n}", file=sys.stderr)
        sys.exit(1)
    resolved = ordered[n - 1]
    pending_ids = {p["id"] for p in pending_items}
    kind = "pending" if resolved["id"] in pending_ids else "backlog"
    return kind, resolved["id"]


def require_kind(cmd: str, arg: str, kind: str, expected: str) -> None:
    """Exit with a helpful message if ``kind`` doesn't match ``expected``.

    Args:
        cmd: Command name to prefix onto the error message.
        arg: The original id argument, echoed back to the user.
        kind: The kind actually resolved (``"backlog"`` or ``"pending"``).
        expected: The kind this command requires.

    Raises:
        SystemExit: If ``kind != expected``. Exits with status 1.
    """
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


def enforce_rev_guard(
    cmd: str,
    id_arg: str,
    if_rev_arg: int | None,
    current_rev: int,
    items: list[BacklogItem],
    pending_items: list[PendingItem],
) -> None:
    """Refuse a numeric-id mutation that lacks a fresh ``--if-rev``.

    Slug id args are exempt — slug identity never goes stale, only a
    numeric position can point at the wrong item after a concurrent
    change.

    Args:
        cmd: Command name to prefix onto any error message.
        id_arg: The raw id argument as given on the command line.
        if_rev_arg: The ``--if-rev`` value supplied, or ``None``.
        current_rev: The revision currently on disk.
        items: The current backlog items (used to re-render on refusal).
        pending_items: The current pending items (used to re-render on
            refusal).

    Raises:
        SystemExit: If ``id_arg`` is numeric and ``if_rev_arg`` is missing
            or doesn't match ``current_rev``. Exits with status 1 after
            re-rendering the dashboard so the caller can retry with a
            fresh revision.
    """
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


def _pending_suffix(item: dict[str, object], color: bool) -> str:
    """Render a pending item's line suffix: reply marker and waiting-age."""
    marker = ""
    if item.get("status") == "reply_received":
        marker = " " + _colorize("reply received", _COLORS["pending"], color)
    age = _age_days(cast(str, item.get("created", "")))
    since = f" (waiting {age}d)" if age is not None else ""
    return marker + since


def render(
    items: list[BacklogItem] | None = None,
    pending_items: list[PendingItem] | None = None,
    *,
    out: TextIO | None = None,
    err: TextIO | None = None,
    rev: int | None = None,
) -> None:
    """Render the full dashboard: pending items, then the four backlog sections.

    Pure render — no writes, no other side effects. Loads current state
    from disk for any of ``items``/``pending_items``/``rev`` left as
    ``None``, so callers that already hold the data in memory (e.g. inside
    a lock) can pass it through instead of re-reading.

    Args:
        items: Backlog items to render, or ``None`` to load from disk.
        pending_items: Pending items to render, or ``None`` to load from
            disk.
        out: Stream for the dashboard body; defaults to ``sys.stdout``.
        err: Stream for the trailing ``item-map:`` line; defaults to
            ``sys.stderr``.
        rev: Revision to report in the ``item-map:`` line, or ``None`` to
            load the current one from disk.
    """
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
    in_progress, ready, blocked, done = _render_order(items)
    pending_ordered = _pending_render_order(pending_items)
    ordered: list[BacklogItem | PendingItem] = [
        *pending_ordered,
        *in_progress,
        *ready,
        *blocked,
        *done,
    ]

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

    sections: list[list[str]] = []

    def add_section(
        title: str,
        section_items: Sequence[BacklogItem | PendingItem],
        show_blockers: bool = False,
        show_age: bool = False,
        color_code: str | None = None,
        summary_key: str = "summary",
        show_category: bool = True,
        line_suffix: Callable[[dict[str, object], bool], str] | None = None,
        show_priority: bool = False,
    ) -> None:
        """Append one rendered section (with its border) to ``sections``."""
        if not section_items:
            return
        frame_code = _COLORS.get(color_code) if color_code else None
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
            item_d = cast(dict[str, object], item)
            n = slug_to_num[item["id"]]
            badge = (
                _priority_glyph(cast(BacklogItem, item), color) if show_priority else ""
            )
            tag = (
                _category_tag(cast(str, item_d.get("category", "")))
                if show_category
                else ""
            )
            line = f"│  {n:2}  {badge}{tag}{item_d.get(summary_key, '')}"
            if line_suffix:
                line += line_suffix(item_d, color)
            if show_age:
                age = _age_days(cast(str, item_d.get("updated", "")))
                if age is not None:
                    line += f" · {age}d"
                    if age > STALE_DAYS:
                        line += " " + _colorize("⚠️", _COLORS["warn"], color)
            lines.append(line)
            if show_blockers:
                eff = effective_blockers(cast(BacklogItem, item), index)
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
                    lines.append(f"│      ↳ blocked by: {', '.join(parts)}")
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
    add_section("DONE", done, color_code="done")

    for i, section_lines in enumerate(sections):
        if i > 0:
            print("", file=out)
        for line in section_lines:
            print(line, file=out)

    map_str = ",".join(f"{n}={tag}" for n, tag in item_map.items())
    print(f"item-map: rev={rev} {map_str}", file=err)


# ── subcommand handlers ───────────────────────────────────────────────────────


def _parse_json_arg(raw: str, context: str) -> dict[str, object]:
    """Parse a CLI argument as a JSON object.

    Args:
        raw: The raw argument text.
        context: Command name to prefix onto any error message.

    Returns:
        The decoded JSON object.

    Raises:
        SystemExit: If ``raw`` isn't valid JSON. Exits with status 1 after
            printing to stderr.
    """
    try:
        return cast(dict[str, object], json.loads(raw))
    except json.JSONDecodeError as e:
        print(f"[{context}] invalid JSON: {e}", file=sys.stderr)
        sys.exit(1)


def _str_field(patch: dict[str, object], key: str, default: str = "") -> str:
    """Extract a string field from a JSON patch.

    Treats a missing key and an explicit JSON ``null`` identically, both
    collapsing to ``default``. Without this, code like
    ``cast(str, patch.get("summary", "")).strip()`` crashes with
    ``AttributeError`` on an explicit ``summary: null`` — the default only
    applies when the key is absent, not when it's present with value
    ``None`` — and every ``required`` check built on that pattern is
    bypassed the same way.

    Args:
        patch: The decoded JSON patch.
        key: The field to extract.
        default: Value to use when the field is missing or ``null``.
    """
    value = patch.get(key)
    return default if value is None else str(value)


def _list_field(patch: dict[str, object], key: str) -> list[object]:
    """Extract a list field from a JSON patch, treating null/missing as ``[]``.

    Without this, an explicit ``null`` for e.g. ``blocked_by`` passes
    ``.get(key, [])``'s default straight through as ``None`` (the default
    only applies when the key is absent), and the next ``for dep in
    blocked_by`` crashes with ``TypeError: 'NoneType' object is not
    iterable``.

    A non-null, non-list value (e.g. a string) is rejected with a
    :class:`SystemExit`. Previously it was passed through via ``cast`` and
    the next iteration walked the string one character at a time, corrupting
    stored state silently on update.
    """
    value = patch.get(key)
    if value is None:
        return []
    if not isinstance(value, list):
        print(
            f"field '{key}' must be a list, got {type(value).__name__}",
            file=sys.stderr,
        )
        sys.exit(1)
    return cast(list[object], value)


def _dict_field(patch: dict[str, object], key: str) -> dict[str, object]:
    """Extract a dict field from a JSON patch, treating null/missing as ``{}``."""
    value = patch.get(key)
    return {} if value is None else cast(dict[str, object], value)


def _reject_null_fields(
    cmd: str, patch: dict[str, object], fields: tuple[str, ...]
) -> None:
    """Refuse a patch that sets any of ``fields`` to explicit JSON ``null``.

    Used before a raw ``dict.update(patch)`` merge (``update``, ``pending
    update``) for fields that have no "unset" state in the schema — unlike
    ``priority``, they're always present with a meaningful default, never
    absent — so null has no defined meaning there. Without this check, the
    merge writes the null straight into a supposedly-never-null field:
    some call sites crash on the next read (e.g. ``blocked_by: null``
    breaks the next ``for dep in blocked_by``), others just quietly
    corrupt the stored record with a value that violates the schema until
    it's read again.

    Args:
        cmd: Command name to prefix onto the error message.
        patch: The JSON patch about to be merged.
        fields: Field names that must not be explicit null in ``patch``.

    Raises:
        SystemExit: If any of ``fields`` is present in ``patch`` with
            value ``None``. Exits with status 1 after printing to stderr.
    """
    nulled = sorted(f for f in fields if f in patch and patch[f] is None)
    if nulled:
        print(
            f"[{cmd}] field(s) cannot be null: {', '.join(nulled)} "
            "— omit the field to leave it unchanged",
            file=sys.stderr,
        )
        sys.exit(1)


def cmd_render(args: argparse.Namespace) -> None:
    """Handle ``render``: print the dashboard with no other side effects.

    Reads items + pending + rev atomically under :func:`backlog_lock` so the
    printed (items, rev) pair is self-consistent — a writer committing between
    the item read and the rev read would otherwise pair a stale item-map with a
    fresh rev, defeating a downstream numeric ``--if-rev`` guard.
    """
    with backlog_lock():
        render()


def cmd_list(args: argparse.Namespace) -> None:
    """Handle ``list``: print flat, tab-separated backlog items.

    Reads items + rev under :func:`backlog_lock` (same rationale as
    :func:`cmd_render`).
    """
    with backlog_lock():
        items = load_items()
        if args.status:
            items = [i for i in items if i.get("status") == args.status]
        print(f"# rev={load_rev()}")
        for item in items:
            print(f"{item['id']}\t{item.get('status', '')}\t{item.get('summary', '')}")


def cmd_show(args: argparse.Namespace) -> None:
    """Handle ``show``: print the full JSON record for one item.

    Reads items + pending + rev under :func:`backlog_lock` (same rationale
    as :func:`cmd_render`).
    """
    with backlog_lock():
        items = load_items()
        pending_items = load_pending()
        kind, slug = resolve_id(args.id, items, pending_items)
        index: dict[str, object] = (
            cast(dict[str, object], {p["id"]: p for p in pending_items})
            if kind == "pending"
            else cast(dict[str, object], build_index(items))
        )
        item = index.get(slug)
        if item is None:
            print(f"[show] not found: {slug}", file=sys.stderr)
            sys.exit(1)
        print(f"# rev={load_rev()}", file=sys.stderr)
        print(json.dumps(item, indent=2))


def cmd_add(args: argparse.Namespace) -> None:
    """Handle ``add``: append a new backlog item.

    ``args.json`` must decode to an object with at least ``id`` and
    ``summary``; see :data:`BacklogItem` for the full field set.
    """
    patch = _parse_json_arg(args.json, "add")

    slug = _str_field(patch, "id").strip()
    if not slug:
        summary = _str_field(patch, "summary")
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

    if not _str_field(patch, "summary").strip():
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
        pending_items = load_pending()
        index = build_index(items)

        if slug in index:
            print(f"[add] duplicate slug: {slug}", file=sys.stderr)
            sys.exit(1)
        # Cross-pool uniqueness: a pending item with the same id would
        # otherwise make resolve_id treat the backlog record as pending,
        # permanently orphaning it (it gets no number in the item-map at all).
        if any(p["id"] == slug for p in pending_items):
            print(
                f"[add] slug '{slug}' already exists as a pending item — "
                "remove or rename the pending item first",
                file=sys.stderr,
            )
            sys.exit(1)

        blocked_by = cast(list[str], _list_field(patch, "blocked_by"))
        for dep in blocked_by:
            if dep not in index:
                print(
                    f"[add] blocked_by references unknown slug: {dep}", file=sys.stderr
                )
                sys.exit(1)

        item: BacklogItem = {
            "id": slug,
            "created": today(),
            "updated": today(),
            "status": "open",
            "summary": _str_field(patch, "summary").strip(),
            "category": _str_field(patch, "category", "feature"),
            "blocked_by": blocked_by,
            "related_files": cast(
                list[dict[str, object]], _list_field(patch, "related_files")
            ),
            "context": _str_field(patch, "context"),
            "next_steps": _str_field(patch, "next_steps"),
        }
        if "priority" in patch:
            item["priority"] = cast(str, patch["priority"])

        items.append(item)
        save_items(items)
        new_rev = bump_rev()
        render(items, pending_items, rev=new_rev)
    _blocker_check_reminder(items, slug, cmd="add")


def confirm_resolution(
    cmd: str,
    arg: str | int,
    item: BacklogItem | PendingItem,
    summary_key: str = "summary",
) -> None:
    """Echo what a mutating command resolved to, so misresolution is visible."""
    ref = f"{arg} → " if str(arg) != item["id"] else ""
    summary = cast(dict[str, object], item).get(summary_key, "")
    print(f"[{cmd}] {ref}{item['id']}: {summary}", file=sys.stderr)


@dataclass
class _MutationResult:
    """Working set threaded through a :func:`_backlog_mutation` block.

    ``item``, ``items``, ``pending_items``, and ``slug`` are populated
    before the caller's block runs. ``new_rev`` is only set once the block
    completes and the revision has been bumped, but by then the same object
    is still in the caller's hands (the ``with`` statement never rebinds
    it), so accessing it after the block exits is safe.

    ``pending_items`` is exposed so mutators can render with the in-memory
    pending set instead of re-reading it unlocked post-lock (mixed-snapshot
    fix — the render call now belongs inside the lock).
    """

    item: BacklogItem
    items: list[BacklogItem]
    pending_items: list[PendingItem]
    slug: str
    new_rev: int | None = None


@contextmanager
def _backlog_mutation(
    cmd: str, id_arg: str, if_rev_arg: int | None, announce: bool = False
) -> Iterator[_MutationResult]:
    """Run the shared skeleton for update/start/done/block/unblock/remove.

    Acquires the lock, enforces the rev guard, resolves the target id, and
    refuses if it isn't a backlog item. Yields a :class:`_MutationResult`
    for the caller to mutate in place (or, for ``remove``, to reassign
    ``items`` to a filtered list). After the caller's block completes,
    saves ``items`` and bumps the rev while still holding the lock —
    matching the save-then-bump-then-optionally-announce order every
    extracted command used before this helper existed. ``announce=True``
    calls :func:`confirm_resolution` (still inside the lock) for the
    update/start/done shape; ``block``/``unblock`` stay silent and
    ``remove`` announces its own message afterward using the
    pre-removal item.

    Args:
        cmd: Command name, used in error messages and announcements.
        id_arg: The raw id argument (slug or numeric position).
        if_rev_arg: The ``--if-rev`` value supplied, or ``None``.
        announce: Whether to call :func:`confirm_resolution` on success.

    Yields:
        A :class:`_MutationResult` for the caller to mutate.

    Raises:
        SystemExit: Via :func:`enforce_rev_guard`, :func:`require_kind`, or
            directly, if the target can't be resolved.
    """
    with backlog_lock():
        items = load_items()
        pending_items = load_pending()
        current_rev = load_rev()
        enforce_rev_guard(cmd, id_arg, if_rev_arg, current_rev, items, pending_items)

        kind, slug = resolve_id(id_arg, items, pending_items)
        require_kind(cmd, id_arg, kind, "backlog")

        index = build_index(items)
        item = index.get(slug)
        if item is None:
            print(f"[{cmd}] not found: {slug}", file=sys.stderr)
            sys.exit(1)

        result = _MutationResult(
            item=item, items=items, pending_items=pending_items, slug=slug
        )

        yield result

        save_items(result.items)
        result.new_rev = bump_rev()
        if announce:
            confirm_resolution(cmd, id_arg, result.item)


def cmd_update(args: argparse.Namespace) -> None:
    """Handle ``update``: merge a JSON patch into a backlog item."""
    patch = _parse_json_arg(args.patch, "update")

    bad = set(patch) & IMMUTABLE_FIELDS
    if bad:
        print(
            f"[update] cannot modify immutable field(s): {', '.join(sorted(bad))}",
            file=sys.stderr,
        )
        sys.exit(1)

    unknown = set(patch) - BACKLOG_MUTABLE_FIELDS - IMMUTABLE_FIELDS
    if unknown:
        print(
            f"[update] unrecognized field(s): {', '.join(sorted(unknown))}",
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

    # `priority: null` means "unset" — revert to the no-priority/normal
    # state cmd_add allows by omission — not a rejected value.
    unset_priority = "priority" in patch and patch["priority"] is None
    if (
        "priority" in patch
        and not unset_priority
        and patch["priority"] not in VALID_PRIORITIES
    ):
        print(
            f"[update] invalid priority '{patch['priority']}' — must be one of: "
            f"{', '.join(sorted(VALID_PRIORITIES))}",
            file=sys.stderr,
        )
        sys.exit(1)

    # `blocked_by` is rejected on `update` outright, even though it's listed in
    # BACKLOG_MUTABLE_FIELDS — the raw `dict.update(patch)` merge bypasses every
    # guard `block`/`unblock` enforces (existence, self-block, cycle detection),
    # and CLAUDE.md already routes blocked_by edits through `block`/`unblock`.
    # Refusing here removes the bypass path by construction instead of trying
    # to mirror block's validation into update and keep both code paths in
    # sync. `unblock` cannot be done via update either (removing a slug from
    # `blocked_by` via raw replacement would skip its "is the slug actually
    # present" check), so the same error redirects both directions.
    if "blocked_by" in patch:
        print(
            "[update] cannot modify 'blocked_by' directly — use "
            "'block <id> <blocker>' to add or 'unblock <id> <blocker>' to "
            "remove. update's raw merge bypasses block/unblock's existence, "
            "self-block, and cycle checks.",
            file=sys.stderr,
        )
        sys.exit(1)

    _reject_null_fields(
        "update",
        patch,
        ("summary", "category", "related_files", "context", "next_steps"),
    )

    with _backlog_mutation("update", args.id, args.if_rev, announce=True) as m:
        if "status" in patch:
            _apply_status_transition(
                cast(dict[str, object], m.item),
                cast(str, patch["status"]),
                "completed_at",
                "done",
            )
        if unset_priority:
            m.item.pop("priority", None)
            del patch["priority"]
        cast(dict[str, object], m.item).update(patch)
        m.item["updated"] = today()
        render(m.items, m.pending_items, rev=m.new_rev)


def cmd_start(args: argparse.Namespace) -> None:
    """Handle ``start``: mark a backlog item in-progress."""
    with _backlog_mutation("start", args.id, args.if_rev, announce=True) as m:
        m.item["status"] = "in-progress"
        m.item["updated"] = today()
        render(m.items, m.pending_items, rev=m.new_rev)


def cmd_done(args: argparse.Namespace) -> None:
    """Handle ``done``: mark a backlog item done."""
    with _backlog_mutation("done", args.id, args.if_rev, announce=True) as m:
        _apply_status_transition(
            cast(dict[str, object], m.item), "done", "completed_at", "done"
        )
        m.item["status"] = "done"
        m.item["updated"] = today()
        render(m.items, m.pending_items, rev=m.new_rev)


def cmd_rename(args: argparse.Namespace) -> None:
    """Handle ``rename``: rename a slug and rewrite every reference to it.

    Rewrites references across both backlog and pending items: backlog
    ``blocked_by`` lists and prose fields (``summary``/``context``/
    ``next_steps``), pending ``blocking`` lists and ``related_files[].note``
    prose. Prose substitution uses a boundary anchored on the slug alphabet
    ``[a-z0-9-]`` (negative lookbehind/lookahead) — ``\\b`` would over-match
    because ``-`` is a non-word char, so ``\\bfoo-bar\\b`` matches the
    ``foo-bar`` prefix of the unrelated sibling slug ``foo-bar-baz``.
    """
    old_slug = args.old_slug
    new_slug = args.new_slug

    err = validate_slug(new_slug, "rename")
    if err:
        print(err, file=sys.stderr)
        sys.exit(1)

    with backlog_lock():
        items = load_items()
        pending_items = load_pending()
        index = build_index(items)
        pending_index = {p["id"]: p for p in pending_items}

        if old_slug not in index:
            print(f"[rename] not found: {old_slug}", file=sys.stderr)
            sys.exit(1)
        # Cross-pool collision check: new_slug must not exist in either pool,
        # and old_slug must not exist as a pending id (rename operates on
        # backlog items only; renaming a slug in use by a pending item would
        # create a cross-pool collision).
        if new_slug in index:
            print(f"[rename] collision: '{new_slug}' already exists", file=sys.stderr)
            sys.exit(1)
        if new_slug in pending_index:
            print(
                f"[rename] collision: '{new_slug}' already exists as a pending item",
                file=sys.stderr,
            )
            sys.exit(1)

        # Boundary anchored on the slug alphabet (not \w) so `foo-bar` does
        # not match the prefix of `foo-bar-baz`.
        word_re = re.compile(r"(?<![a-z0-9-])" + re.escape(old_slug) + r"(?![a-z0-9-])")

        def _rewrite_prose(text: str) -> str:
            if old_slug in text:
                return word_re.sub(new_slug, text)
            return text

        for item in items:
            if item["id"] == old_slug:
                item["id"] = new_slug
            item["blocked_by"] = [
                new_slug if s == old_slug else s for s in item.get("blocked_by", [])
            ]
            fields = cast(dict[str, str], item)
            for field in ("summary", "context", "next_steps"):
                fields[field] = _rewrite_prose(fields.get(field, ""))
            for rf in item.get("related_files", []):
                note = rf.get("note", "") if isinstance(rf, dict) else ""
                if isinstance(note, str) and note:
                    rf["note"] = _rewrite_prose(note)

        for p in pending_items:
            p["blocking"] = [
                new_slug if s == old_slug else s for s in p.get("blocking", [])
            ]
            fields = cast(dict[str, str], p)
            for field in ("description", "context"):
                fields[field] = _rewrite_prose(fields.get(field, ""))
            for step_idx, step in enumerate(p.get("next_steps", [])):
                if isinstance(step, str):
                    p["next_steps"][step_idx] = _rewrite_prose(step)
            for rf in p.get("related_files", []):
                note = rf.get("note", "") if isinstance(rf, dict) else ""
                if isinstance(note, str) and note:
                    rf["note"] = _rewrite_prose(note)

        save_items(items)
        save_pending(pending_items)
        new_rev = bump_rev()
        print(f"[rename] {old_slug} → {new_slug}", file=sys.stderr)
        render(items, pending_items, rev=new_rev)


def cmd_block(args: argparse.Namespace) -> None:
    """Handle ``block``: add a blocker to a backlog item.

    Refuses duplicates and cycle-creating blockers.
    """
    blocker = args.blocker
    with _backlog_mutation("block", args.id, args.if_rev) as m:
        index = build_index(m.items)
        if blocker not in index:
            print(f"[block] blocker not found: {blocker}", file=sys.stderr)
            sys.exit(1)
        if blocker in m.item.get("blocked_by", []):
            print(f"[block] {m.slug} already blocked by {blocker}", file=sys.stderr)
            sys.exit(1)
        if detect_cycle(m.slug, blocker, index):
            print(
                f"[block] would create a cycle: {blocker} already depends on {m.slug}",
                file=sys.stderr,
            )
            sys.exit(1)

        m.item.setdefault("blocked_by", []).append(blocker)
        m.item["updated"] = today()
        render(m.items, m.pending_items, rev=m.new_rev)


def cmd_unblock(args: argparse.Namespace) -> None:
    """Handle ``unblock``: remove a blocker from a backlog item."""
    blocker = args.blocker
    with _backlog_mutation("unblock", args.id, args.if_rev) as m:
        if blocker not in m.item.get("blocked_by", []):
            print(f"[unblock] {m.slug} is not blocked by {blocker}", file=sys.stderr)
            sys.exit(1)

        m.item["blocked_by"] = [s for s in m.item["blocked_by"] if s != blocker]
        m.item["updated"] = today()
        render(m.items, m.pending_items, rev=m.new_rev)


def cmd_pending_add(args: argparse.Namespace) -> None:
    """Handle ``pending add``: track a new waiting-on-someone-else item.

    ``args.json`` must decode to an object with at least ``id``,
    ``description``, and ``kind``; see :data:`PendingItem` for the full
    field set.
    """
    patch = _parse_json_arg(args.json, "pending add")

    slug = _str_field(patch, "id").strip()
    if not slug:
        print("[pending add] 'id' is required", file=sys.stderr)
        sys.exit(1)
    err = validate_slug(slug, "pending add")
    if err:
        print(err, file=sys.stderr)
        sys.exit(1)

    description = _str_field(patch, "description").strip()
    if not description:
        print("[pending add] 'description' is required", file=sys.stderr)
        sys.exit(1)

    kind = _str_field(patch, "kind")
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

        backlog_items = load_items()
        index = build_index(backlog_items)
        # Cross-pool uniqueness: a backlog item with the same id would
        # otherwise make resolve_id treat the pending record as backlog
        # (resolve_id looks at the pending pool first for non-numeric args),
        # permanently orphaning the pending item.
        if slug in index:
            print(
                f"[pending add] slug '{slug}' already exists as a backlog item — "
                "remove or rename the backlog item first",
                file=sys.stderr,
            )
            sys.exit(1)

        blocking = cast(list[str], _list_field(patch, "blocking"))
        for dep in blocking:
            if dep not in index:
                print(
                    f"[pending add] blocking references unknown slug: {dep}",
                    file=sys.stderr,
                )
                sys.exit(1)

        pending_items.append(
            {
                "id": slug,
                "created": today(),
                "updated": today(),
                "status": "waiting_for_reply",
                "description": description,
                "kind": kind,
                "source_ref": _dict_field(patch, "source_ref"),
                "context": _str_field(patch, "context"),
                "next_steps": cast(list[str], _list_field(patch, "next_steps")),
                "blocking": blocking,
                "outcome": None,
            }
        )
        save_pending(pending_items)
        new_rev = bump_rev()
        print(f"[pending add] {slug} — {description[:60]}", file=sys.stderr)
        render(backlog_items, pending_items, rev=new_rev)
    _blocker_check_reminder(backlog_items, None, cmd="pending add")


def cmd_pending_update(args: argparse.Namespace) -> None:
    """Handle ``pending update``: merge a JSON patch into a pending item."""
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

    # `outcome: null` is legitimate (that's its state until resolution), so
    # it's deliberately excluded here — everything else in this allowlist
    # is always-present/never-null per PendingItem's schema.
    _reject_null_fields(
        "pending update",
        patch,
        ("description", "context", "next_steps", "blocking", "source_ref"),
    )

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
        # Validate `blocking` slugs against the backlog index, mirroring
        # `pending add`'s validation — without this, a typo'd slug slips in
        # here and CLAUDE.md routes post-add blocker edits through this path.
        if "blocking" in patch:
            backlog_index = build_index(items)
            blocking_patch = cast(list[str], _list_field(patch, "blocking"))
            for dep in blocking_patch:
                if dep not in backlog_index:
                    print(
                        f"[pending update] blocking references unknown slug: {dep}",
                        file=sys.stderr,
                    )
                    sys.exit(1)
        if "status" in patch:
            _apply_status_transition(
                cast(dict[str, object], item),
                cast(str, patch["status"]),
                "resolved_at",
                "resolved",
            )
        cast(dict[str, object], item).update(patch)
        item["updated"] = today()
        save_pending(pending_items)
        new_rev = bump_rev()
        confirm_resolution("pending update", args.id, item, summary_key="description")
        render(items, pending_items, rev=new_rev)


def cmd_pending_list(args: argparse.Namespace) -> None:
    """Handle ``pending list``: print each pending item as one JSON line."""
    for item in load_pending():
        print(json.dumps(item))


def cmd_remove(args: argparse.Namespace) -> None:
    """Handle ``remove``: permanently delete one backlog item.

    Purges the removed slug from every surviving item's ``blocked_by``
    list and every pending item's ``blocking`` list, so deleting a
    completed blocker doesn't retroactively flip its dependents from READY
    into BLOCKED via :func:`effective_blockers`'s missing-slug-is-unresolved
    fallback.
    """
    with _backlog_mutation("remove", args.id, args.if_rev) as m:
        m.items = [i for i in m.items if i["id"] != m.slug]
        _purge_inbound_refs({m.slug}, m.items, m.pending_items)
        confirm_resolution("remove", args.id, m.item)
        render(m.items, m.pending_items, rev=m.new_rev)


def cmd_prune(args: argparse.Namespace) -> None:
    """Handle ``prune``: permanently remove done/resolved items older than 14 days.

    Backs up each data file before any deletion occurs (see
    :func:`_backup_before_bulk_delete`), purges inbound ``blocked_by``/
    ``blocking`` references to the pruned slugs (same rationale as
    :func:`cmd_remove`), and only bumps the revision if something was
    actually removed.
    """
    cutoff_days = 14

    with backlog_lock():
        items = load_items()
        keep: list[BacklogItem] = []
        pruned_slugs: set[str] = set()
        for item in items:
            if item.get("status") == "done":
                age = _age_days(item.get("completed_at") or item.get("updated", ""))
                if age is None:
                    print(
                        f"[prune] skipping {item.get('id', '?')}: "
                        "no valid completed_at/updated date",
                        file=sys.stderr,
                    )
                elif age >= cutoff_days:
                    pruned_slugs.add(item["id"])
                    continue
            keep.append(item)
        if pruned_slugs:
            _backup_before_bulk_delete(ITEMS_FILE)
            save_items(keep)

        pending_items = load_pending()
        pending_keep: list[PendingItem] = []
        pending_pruned_slugs: set[str] = set()
        for pending_item in pending_items:
            if pending_item.get("status") == "resolved":
                age = _age_days(
                    pending_item.get("resolved_at") or pending_item.get("updated", "")
                )
                if age is None:
                    print(
                        f"[prune] skipping {pending_item.get('id', '?')}: "
                        "no valid resolved_at/updated date",
                        file=sys.stderr,
                    )
                elif age >= cutoff_days:
                    pending_pruned_slugs.add(pending_item["id"])
                    continue
            pending_keep.append(pending_item)
        if pending_pruned_slugs:
            _backup_before_bulk_delete(PENDING_FILE)
            save_pending(pending_keep)

        total_removed = len(pruned_slugs) + len(pending_pruned_slugs)
        if total_removed:
            # Purge inbound refs from the surviving records. Need to re-save
            # if purge mutated a file we wouldn't otherwise have written.
            _purge_inbound_refs(pruned_slugs | pending_pruned_slugs, keep, pending_keep)
            # save_items was already called above only when pruned_slugs; if
            # purge mutated `keep` we must re-save. Same for pending. The
            # simplest invariant: re-save whichever file purge may have
            # touched when its removal set was non-empty.
            if pruned_slugs or pending_pruned_slugs:
                # If only pending was pruned, keep (backlog) may still have
                # been mutated by purge if a pruned pending slug was in any
                # backlog item's blocked_by — but pending slugs are not valid
                # blockers (cmd_block rejects non-backlog slugs), so purge
                # only touches `keep` when pruned_slugs is non-empty.
                if pruned_slugs:
                    save_items(keep)
                if pending_pruned_slugs:
                    save_pending(pending_keep)
            new_rev = bump_rev()
            print(
                f"[prune] removed {len(pruned_slugs)} backlog item(s), "
                f"{len(pending_pruned_slugs)} pending item(s) — "
                f"backup written to {DATA_DIR}"
            )
            # Match every other mutator: render prints the dashboard and the
            # item-map line so a caller's rev stays fresh.
            render(keep, pending_keep, rev=new_rev)
        else:
            print("[prune] nothing to prune")


# ── main ──────────────────────────────────────────────────────────────────────


def main() -> None:
    """Parse argv and dispatch to the matching subcommand handler."""
    parser = argparse.ArgumentParser(
        description="deterministic backlog dashboard v2",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(
        dest="cmd",
        metavar="{render,list,show,add,update,start,done,rename,remove,block,unblock,prune}",
    )

    sub.add_parser("render", help="render dashboard (pure — no side effects)")
    p = sub.add_parser("list", help="flat tab-separated output")
    p.add_argument(
        "--status",
        choices=sorted(VALID_STATUSES),
        default=None,
        help="only show items with this status",
    )

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

    dispatch: dict[str, Callable[[argparse.Namespace], None]] = {
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
        pending_dispatch: dict[str, Callable[[argparse.Namespace], None]] = {
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
