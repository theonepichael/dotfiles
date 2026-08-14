#!/usr/bin/env python3
"""Claude Code status line: render the model name and a color-coded context
window usage bar with the used percentage, from the JSON session payload
Claude Code pipes to this script on stdin.

This script is wired into Claude Code via the ``statusLine`` setting in
~/.claude/settings.json. It runs on every assistant message, so it must
stay fast: stdlib-only, no subprocess, no network, nothing beyond stdin
and stdout.

Stdin:  JSON session payload from Claude Code's statusline contract —
        ``model.display_name`` and ``context_window.used_percentage``.
Stdout: One line, e.g. ``[Opus] ▓▓▓▓░░░░░░ 42%``, with ANSI color wrapping
        only the 10-cell bar (green <70.0, yellow 70.0-89.9, red >=90.0)
        and a trailing reset. Always emitted (including 0%) except when
        stdin is empty, not valid JSON, or parses to a non-dict — those
        cases print nothing.
Flags:  none.
Env vars: none.
Files read/written: none.
Exit codes: 0 (always succeeds; failures produce empty output, never a
nonzero exit or a traceback).
"""

import json
import math
import sys

GREEN = "\033[32m"
YELLOW = "\033[33m"
RED = "\033[31m"
RESET = "\033[0m"

BAR_WIDTH = 10
GREEN_MAX = 70.0
YELLOW_MAX = 90.0


def _bar(pct: float) -> tuple[str, int, int]:
    """Return ``(color_code, filled_cells, displayed_percent)`` for a
    clamped percentage in [0.0, 100.0]. One cell per 10 points,
    truncating: 42% -> 4 cells, 99% -> 9 cells, 100% -> 10 cells."""
    if pct < GREEN_MAX:
        color = GREEN
    elif pct < YELLOW_MAX:
        color = YELLOW
    else:
        color = RED
    filled = min(BAR_WIDTH, int(pct / 10))
    return color, filled, round(pct)


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
    line = _render(payload)
    if line:
        sys.stdout.write(line + "\n")
    sys.exit(0)


if __name__ == "__main__":
    main()
