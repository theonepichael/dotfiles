#!/usr/bin/env python3
"""second_opinion.py — one-shot adversarial critique of a plan from a non-Claude
backend. Single-round by design: the multi-round loop, plan revision, and
convergence judgment all require LLM reasoning and live in second-opinion.md's
prose instructions, not here.

Flags
  --quiet, -q    suppress non-essential output
  --verbose, -v  emit extra diagnostic messages to stderr

Env vars
  SECOND_OPINION_TIMEOUT_SECONDS  per-backend timeout in seconds (default 300)
  SECOND_OPINION_AGY_MODEL        force the agy model (default "Gemini 3.7 Flash (High)")
  SECOND_OPINION_COPILOT_MODEL    force the Copilot model (default: unset)

Files read: <plan-file-or-text> (if a path), --focus-file. Nothing written.

Exit codes: 0 success; 1 any failure (no backend available, all backends failed,
bad --focus-file, unknown subcommand).

Requires Python 3.12+.
"""

import argparse
import json
import os
import shutil
import signal
import sys
from pathlib import Path
from types import FrameType
from typing import NoReturn

import cli_common
import llm_backends
from llm_backends import (
    BackendError,
    _opencode_json_events,
    _opencode_text_chunks,
    _safe_get,
    available_backends,
)

BACKEND_PRIORITY = llm_backends.BACKEND_PRIORITY
BACKEND_TIMEOUT_SECONDS = int(os.environ.get("SECOND_OPINION_TIMEOUT_SECONDS", "300"))


CRITIQUE_PROMPT = """\
You are reviewing a plan written by another AI assistant (Claude).
Your job is to find problems, not to summarize or agree.

Be specific and concrete:
- What could go wrong or is underspecified?
- What did the author miss or assume without justification?
- Where do you disagree, and why?
- Is there a simpler approach?
{focus_section}
If the plan is genuinely solid, say so briefly — but don't pad
agreement with praise. Skip preamble.

---
{plan_text}
"""

FOCUS_SECTION = """
The plan's author flagged these as this plan's specific risk points —
scrutinize them closely, but don't let them limit the rest of your review:
{hints}
"""


def build_prompt(plan_text: str, focus_hints: str | None) -> str:
    """Build the critique prompt, optionally inserting plan-specific focus hints.

    Args:
        plan_text: The plan to review.
        focus_hints: Bullet-point text naming risk areas specific to this
            plan, or ``None``/blank to omit the section entirely.

    Returns:
        The fully-formatted critique prompt.
    """
    focus_section = (
        FOCUS_SECTION.format(hints=focus_hints.strip())
        if focus_hints and focus_hints.strip()
        else ""
    )
    return CRITIQUE_PROMPT.format(plan_text=plan_text, focus_section=focus_section)


def die(msg: str) -> NoReturn:
    """Print an error to stderr, prefixed for this script, and exit with status 1."""
    print(f"[second_opinion] {msg}", file=sys.stderr)
    sys.exit(1)


def resolve_plan_text(arg: str) -> str:
    """Resolve a CLI argument to plan text: a file's contents, or the arg itself.

    Args:
        arg: A filesystem path or inline plan text.

    Returns:
        The file's contents if ``arg`` names an existing file, otherwise
        ``arg`` unchanged (treated as inline text).
    """
    try:
        path = Path(arg).expanduser()
        if path.is_file():
            return path.read_text()
    except OSError:
        pass  # arg is inline text too long/invalid to be a filesystem path
    return arg


def _kill_active_process() -> None:
    """Kill the currently-running backend subprocess's entire process group, if any."""
    llm_backends._kill_active_process()


def _handle_termination(signum: int, frame: FrameType | None) -> NoReturn:
    """Signal handler: kill any active backend subprocess, then exit.

    Registered for SIGTERM/SIGINT so an interrupted `review` doesn't leave
    an orphaned backend process running in the background.
    """
    _kill_active_process()
    sys.exit(128 + signum)


def _run_command(cmd: list[str]) -> tuple[int, str, str]:
    """Run ``cmd`` as a subprocess, capturing its output, at this module's timeout.

    Thin wrapper around :func:`llm_backends._run_command` — kept local (rather
    than a bare re-export) so :data:`BACKEND_TIMEOUT_SECONDS` is read from
    *this* module's global at call time, matching pre-extraction behavior for
    anything that patches it.
    """
    return llm_backends._run_command(cmd, BACKEND_TIMEOUT_SECONDS)


DEFAULT_AGY_MODEL = "Gemini 3.7 Flash (High)"


def run_agy(prompt: str) -> str:
    """Run the ``agy`` backend and return its critique text.

    An explicit model can be forced via the ``SECOND_OPINION_AGY_MODEL`` env
    var; unset means :data:`DEFAULT_AGY_MODEL`.
    """
    model = os.environ.get("SECOND_OPINION_AGY_MODEL", DEFAULT_AGY_MODEL)
    return llm_backends.run_agy(prompt, model=model, timeout=BACKEND_TIMEOUT_SECONDS)


def run_opencode(prompt: str) -> str:
    """Run the ``opencode`` backend's adversary agent and return its critique text.

    Not routed through :func:`llm_backends.run_opencode` (the generic
    variant): the adversary agent is second_opinion-specific (``--agent
    adversary``), so this builds its own command but reuses the shared
    event-parsing helpers.

    Raises:
        BackendError: If the event stream has no text chunks — either
            because an explicit error event was emitted, or because
            nothing recognizable was produced at all.
    """
    _, stdout, stderr = _run_command(
        [
            "opencode",
            "run",
            "--agent",
            "adversary",
            "--auto",
            "--format",
            "json",
            prompt,
        ]
    )
    events = _opencode_json_events(stdout)
    chunks = _opencode_text_chunks(events)
    if chunks:
        return "".join(chunks).strip()
    for e in events:
        if e.get("type") == "error":
            message = _safe_get(e, "error", "data", "message")
            raise BackendError(f"adversary agent error: {message or e.get('error')}")
    raise BackendError(f"no text output: {stderr.strip() or stdout.strip()[:200]}")


def run_copilot(prompt: str) -> str:
    """Run the ``copilot`` backend and return its critique text.

    An explicit model can be forced via the ``SECOND_OPINION_COPILOT_MODEL``
    env var (empty/unset means no ``--model`` flag — see
    :func:`llm_backends.run_copilot` for why that's the safe default).
    """
    return llm_backends.run_copilot(
        prompt,
        model=os.environ.get("SECOND_OPINION_COPILOT_MODEL"),
        timeout=BACKEND_TIMEOUT_SECONDS,
    )


BACKEND_RUNNERS = {"agy": run_agy, "opencode": run_opencode, "copilot": run_copilot}
BACKEND_LABELS = {
    "agy": f"agy ({DEFAULT_AGY_MODEL})",
    "opencode": "opencode (adversary agent)",
    "copilot": "GitHub Copilot CLI",
}


def backend_label(backend: str) -> str:
    """Return ``backend``'s display label, appending an overridden agy/copilot model if set."""
    label = BACKEND_LABELS[backend]
    if backend == "agy":
        model = os.environ.get("SECOND_OPINION_AGY_MODEL")
        if model and model != DEFAULT_AGY_MODEL:
            return f"agy ({model})"
        return label
    if backend == "copilot":
        model = os.environ.get("SECOND_OPINION_COPILOT_MODEL")
        if model:
            return f"{label} ({model})"
    return label


def cmd_detect(args: argparse.Namespace) -> None:
    """Handle ``detect``: print backend availability as a JSON object."""
    print(json.dumps({b: shutil.which(b) is not None for b in BACKEND_PRIORITY}))


def cmd_review(args: argparse.Namespace) -> None:
    """Handle ``review``: get one critique from the priority-selected backend.

    Tries each candidate backend in priority order (or just the forced
    ``--backend``, if given) until one succeeds; prints the first
    successful critique and returns. Exits nonzero only if every candidate
    fails.
    """
    if args.backend:
        if not shutil.which(args.backend):
            die(f"{args.backend} not found on PATH")
        candidates = [args.backend]
    else:
        candidates = available_backends()
        if not candidates:
            die("no backend available — install one of: " + ", ".join(BACKEND_PRIORITY))

    plan_text = resolve_plan_text(args.plan)
    focus_hints = None
    if args.focus_file:
        focus_path = Path(args.focus_file).expanduser()
        if not focus_path.is_file():
            die(f"--focus-file not found: {focus_path}")
        focus_hints = focus_path.read_text()
    prompt = build_prompt(plan_text, focus_hints)

    failures = []
    for backend in candidates:
        try:
            critique = BACKEND_RUNNERS[backend](prompt)
        except BackendError as exc:
            cli_common.vprint(
                f"[second_opinion] {backend_label(backend)} failed: {exc}",
                verbose=getattr(args, "verbose", False),
            )
            failures.append(f"{backend}: {exc}")
            continue
        print(f"Second opinion via {backend_label(backend)}:")
        print(critique)
        return

    die("all backends failed — " + "; ".join(failures))


def main() -> None:
    """Register termination handlers, parse argv, and dispatch to a subcommand."""
    signal.signal(signal.SIGTERM, _handle_termination)
    signal.signal(signal.SIGINT, _handle_termination)

    parser = argparse.ArgumentParser(
        description="one-shot adversarial critique of a plan from a non-Claude backend",
    )
    cli_common.add_verbosity_args(parser)
    sub = parser.add_subparsers(dest="cmd", metavar="{detect,review}")

    sub.add_parser("detect", help="list available backends as JSON")

    p = sub.add_parser(
        "review", help="get one critique from the priority-selected backend"
    )
    p.add_argument("plan", metavar="<plan-file-or-text>")
    p.add_argument(
        "--backend",
        choices=BACKEND_PRIORITY,
        default=None,
        help="force this backend instead of priority-order fallback",
    )
    p.add_argument(
        "--focus-file",
        default=None,
        help="path to a file of plan-specific risk hints, appended to the "
        "critique prompt as areas to scrutinize (supplements, not replaces, "
        "the generic adversarial mandate)",
    )

    args = parser.parse_args()

    dispatch = {"detect": cmd_detect, "review": cmd_review}
    if args.cmd in dispatch:
        dispatch[args.cmd](args)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
