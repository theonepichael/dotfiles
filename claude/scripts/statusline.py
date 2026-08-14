#!/usr/bin/env python3
"""Claude Code status line: render the model name and a color-coded context
window usage bar with the used percentage, from the JSON session payload
Claude Code pipes to this script on stdin.

This script is wired into Claude Code via the ``statusLine`` setting in
~/.claude/settings.json. It runs on every assistant message, so it must
stay fast: stdlib-only, no subprocess, no network, nothing beyond stdin
and stdout.

Stdin:  JSON session payload from Claude Code's statusline contract —
        ``model.display_name`` and ``context_window.used_percentage``
        drive the left bar; ``rate_limits.five_hour.{used_percentage,
        resets_at}`` (optional) drives a right-aligned 5-hour quota
        countdown segment appended when present and well-formed.
Stdout: One line, e.g. ``[Opus] ▓▓▓▓░░░░░░ 42%``, with ANSI color wrapping
        only the 10-cell bar (green <70, yellow 70-89, red >=90 on the
        rounded displayed percent) and a trailing reset. When a valid
        ``rate_limits.five_hour`` is present, a ``5h: <colored %> (resets
        in <countdown>)`` segment is appended, right-aligned to the
        terminal width when ``COLUMNS`` is set. Always emitted (including
        0%) except when stdin is empty, not valid JSON, or parses to a
        non-dict — those cases print nothing.
Flags:  none.
Env vars: ``COLUMNS`` (optional, used for right-alignment; falls back
        gracefully to a ``" | "``-joined line when absent, unparseable, or
        too narrow to fit both segments).
Files read/written: none.
Exit codes: 0 (always succeeds; failures produce empty output, never a
nonzero exit or a traceback).
"""

import json
import math
import os
import re
import sys
import time
import unicodedata

GREEN = "\033[32m"
YELLOW = "\033[33m"
RED = "\033[31m"
RESET = "\033[0m"

BAR_WIDTH = 10
GREEN_MAX = 70.0
YELLOW_MAX = 90.0

# Defensive cap on a parsed COLUMNS value: a malformed/pathological value
# (e.g. "999999999999") reaching ' ' * columns would be an uncapped-memory
# crash risk that violates the script's never-raises invariant. No real
# terminal legitimately exceeds a few hundred columns.
_COLUMNS_CAP = 1024

_ANSI_RE = re.compile(r"\033\[[0-9;]*m")


def _bar(pct: float) -> tuple[str, int, int]:
    """Return ``(color_code, filled_cells, displayed_percent)`` for a
    clamped percentage in [0.0, 100.0]. Color is chosen from the rounded
    displayed integer so the color never contradicts the printed number
    (e.g. 69.6 displays ``70%`` and colors yellow, not green). Fill is
    one cell per 10 points via ``round``, capped at 9 whenever ``pct``
    is below 100 so the bar never reads as full before the account is
    actually at 100% (99% -> 9 cells)."""
    displayed = round(pct)
    if displayed < GREEN_MAX:
        color = GREEN
    elif displayed < YELLOW_MAX:
        color = YELLOW
    else:
        color = RED
    filled = round(pct / 10)
    if pct < 100:
        filled = min(BAR_WIDTH - 1, filled)
    filled = min(BAR_WIDTH, filled)
    return color, filled, displayed


def _display_width(s: str) -> int:
    """Return the visible column width of ``s``: strip the SGR color
    sequences this file emits (``\\033[...m``), then count 2 columns for
    each character whose East Asian width is Wide or Fullwidth, else 1.
    For the bar glyphs (Ambiguous/Narrow) this matches plain
    ``len(strip_ansi(s))``; the wide-character branch is defensive
    headroom for a future CJK or double-width name, not a fix for today's
    glyphs."""
    stripped = _ANSI_RE.sub("", s)
    return sum(
        2 if unicodedata.east_asian_width(c) in ("W", "F") else 1 for c in stripped
    )


def _format_countdown(seconds_remaining: float) -> str:
    """Format seconds remaining until reset as ``Hh Mm`` when there is at
    least one hour, else just ``Mm``. Floors at zero (a past ``resets_at``
    renders ``0m``, never negative) and truncates minutes with floor
    division so a minute is never rounded up (3599.9s -> ``59m``, not
    ``1h0m``)."""
    total_seconds = max(0.0, seconds_remaining)
    total_minutes = int(total_seconds // 60)
    hours, minutes = divmod(total_minutes, 60)
    if hours > 0:
        return f"{hours}h{minutes}m"
    return f"{minutes}m"


def _render_rate_limit(payload: object, now: float | None = None) -> str:
    """Render the right-side 5-hour quota countdown segment from a parsed
    payload dict, or an empty string when ``rate_limits.five_hour`` is
    absent or any required field is unusable. Mirrors ``_render``'s
    defend-every-level pattern: every missing or wrong-typed level degrades
    to ``""``. ``now`` defaults to ``time.time()`` at call time — real
    callers never pass it, only tests do, which keeps countdown assertions
    deterministic."""
    if not isinstance(payload, dict):
        return ""
    rate_limits = payload.get("rate_limits")
    if not isinstance(rate_limits, dict):
        return ""
    five_hour = rate_limits.get("five_hour")
    if not isinstance(five_hour, dict):
        return ""

    raw_pct = five_hour.get("used_percentage")
    if isinstance(raw_pct, bool):
        return ""
    try:
        pct = float(raw_pct)
    except (TypeError, ValueError):
        return ""
    if not math.isfinite(pct):
        return ""
    pct = max(0.0, min(100.0, pct))

    raw_resets = five_hour.get("resets_at")
    if isinstance(raw_resets, bool):
        return ""
    try:
        resets_at = float(raw_resets)
    except (TypeError, ValueError):
        return ""
    if not math.isfinite(resets_at):
        return ""

    if now is None:
        now = time.time()
    displayed = round(pct)
    if displayed < GREEN_MAX:
        color = GREEN
    elif displayed < YELLOW_MAX:
        color = YELLOW
    else:
        color = RED
    countdown = _format_countdown(resets_at - now)
    return f"5h: {color}{displayed}%{RESET} (resets in {countdown})"


def _parse_columns(raw: str | None) -> int | None:
    """Parse the ``COLUMNS`` environment value into a validated positive
    int already capped at ``_COLUMNS_CAP``, or ``None`` when ``raw`` is
    absent, empty, non-numeric, zero, or negative. Pure: no
    ``os.environ`` access — the caller reads the env and hands the raw
    string in."""
    if raw is None or raw == "":
        return None
    try:
        value = int(raw)
    except ValueError:
        return None
    if value <= 0:
        return None
    return min(value, _COLUMNS_CAP)


def _combine_segments(left: str, right: str, columns: int | None) -> str:
    """Combine the rendered left and right segments, right-aligning the
    right segment to ``columns`` when it fits, else falling back to a
    ``" | "``-joined line. Strictly typed to an already-parsed ``int |
    None`` — does no string parsing of its own. When ``right`` is empty
    (degraded 5h segment) no padding is attempted and ``left`` is returned
    unchanged. The absent/unparseable-``COLUMNS`` fallback (``columns is
    None``) and the too-narrow-to-fit fallback are the same code path and
    produce identical ``" | "``-joined output."""
    if right == "":
        return left
    if columns is None:
        return f"{left} | {right}"
    padding = columns - _display_width(left) - _display_width(right)
    if padding >= 1:
        return f"{left}{' ' * padding}{right}"
    return f"{left} | {right}"


def _render(payload: object) -> str:
    """Render the status line from a parsed payload dict, or an empty
    string when the payload isn't a dict (top-level parse failure is
    handled by the caller). Never raises on malformed shapes — every
    missing or wrong-typed field degrades to a safe default."""
    if not isinstance(payload, dict):
        return ""

    name: str | None = None
    model = payload.get("model")
    if isinstance(model, dict):
        candidate = model.get("display_name")
        if isinstance(candidate, str) and candidate.strip():
            name = candidate

    raw_pct: object = 0.0
    context_window = payload.get("context_window")
    if isinstance(context_window, dict):
        raw_pct = context_window.get("used_percentage", 0.0)

    pct = 0.0
    if isinstance(raw_pct, bool):
        pct = 0.0
    else:
        try:
            value = float(raw_pct)
        except (TypeError, ValueError):
            pct = 0.0
        else:
            pct = value if math.isfinite(value) else 0.0
    pct = max(0.0, min(100.0, pct))

    color, filled, display = _bar(pct)
    empty = BAR_WIDTH - filled
    bar = f"{color}{'▓' * filled}{'░' * empty}{RESET}"
    prefix = f"[{name}] " if name is not None else ""
    return f"{prefix}{bar} {display}%"


def main() -> None:
    """Read stdin, parse the payload, print the rendered line, and exit 0
    — never raising, regardless of what stdin contained."""
    try:
        text = sys.stdin.read()
        payload = json.loads(text)
    except Exception:
        sys.exit(0)
    left = _render(payload)
    right = _render_rate_limit(payload)
    columns = _parse_columns(os.environ.get("COLUMNS"))
    line = _combine_segments(left, right, columns)
    if line:
        sys.stdout.write(line + "\n")
    sys.exit(0)


if __name__ == "__main__":
    main()
