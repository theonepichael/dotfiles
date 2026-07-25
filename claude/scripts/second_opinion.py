#!/usr/bin/env python3
"""second_opinion.py — one-shot adversarial critique of a plan from a non-Claude
backend. Single-round by design: the multi-round loop, plan revision, and
convergence judgment all require LLM reasoning and live in second-opinion.md's
prose instructions, not here.
"""

import argparse
import json
import os
import shutil
import signal
import subprocess
import sys
from pathlib import Path
from types import FrameType

BACKEND_PRIORITY = ["agy", "opencode"]
BACKEND_TIMEOUT_SECONDS = 300

_active_process: subprocess.Popen[str] | None = None


class BackendError(Exception):
    """A backend was invoked but failed (timeout or nonzero exit)."""


CRITIQUE_PROMPT = """\
You are reviewing a plan written by another AI assistant (Claude).
Your job is to find problems, not to summarize or agree.

Be specific and concrete:
- What could go wrong or is underspecified?
- What did the author miss or assume without justification?
- Where do you disagree, and why?
- Is there a simpler approach?

If the plan is genuinely solid, say so briefly — but don't pad
agreement with praise. Skip preamble.

---
{plan_text}
"""


def die(msg: str) -> None:
    print(f"[second_opinion] {msg}", file=sys.stderr)
    sys.exit(1)


def available_backends() -> list[str]:
    return [b for b in BACKEND_PRIORITY if shutil.which(b)]


def resolve_backend() -> str | None:
    backends = available_backends()
    return backends[0] if backends else None


def resolve_plan_text(arg: str) -> str:
    try:
        path = Path(arg).expanduser()
        if path.is_file():
            return path.read_text()
    except OSError:
        pass  # arg is inline text too long/invalid to be a filesystem path
    return arg


def _kill_active_process() -> None:
    global _active_process
    proc = _active_process
    if proc is None or proc.poll() is not None:
        return
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except ProcessLookupError:
        pass
    proc.wait()


def _handle_termination(signum: int, frame: FrameType | None) -> None:
    _kill_active_process()
    sys.exit(128 + signum)


def _run_command(cmd: list[str]) -> tuple[int, str, str]:
    global _active_process
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    _active_process = proc
    try:
        stdout, stderr = proc.communicate(timeout=BACKEND_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        _kill_active_process()
        raise BackendError(f"timed out after {BACKEND_TIMEOUT_SECONDS}s — killed")
    finally:
        _active_process = None
    return proc.returncode, stdout, stderr


def run_backend_command(cmd: list[str]) -> str:
    returncode, stdout, stderr = _run_command(cmd)
    if returncode != 0:
        raise BackendError(f"exited {returncode}: {stderr.strip()}")
    return stdout.strip()


def run_agy(prompt: str) -> str:
    return run_backend_command(
        ["agy", "-p", prompt, "--model", "Gemini 3.1 Pro (High)"]
    )


def _opencode_json_events(raw_output: str) -> list[dict[str, object]]:
    events = []
    for line in raw_output.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return events


def run_opencode(prompt: str) -> str:
    # Not run_backend_command: opencode's --format json puts both the
    # critique text and any error detail in stdout JSON events regardless of
    # exit code, so the error message has to come from parsing stdout, not
    # from stderr or the bare exit code.
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
    chunks = [
        e["part"]["text"]
        for e in events
        if e.get("type") == "text" and e.get("part", {}).get("text")
    ]
    if chunks:
        return "".join(chunks).strip()
    for e in events:
        if e.get("type") == "error":
            message = e.get("error", {}).get("data", {}).get("message")
            raise BackendError(f"adversary agent error: {message or e['error']}")
    raise BackendError(f"no text output: {stderr.strip() or stdout.strip()[:200]}")


BACKEND_RUNNERS = {"agy": run_agy, "opencode": run_opencode}
BACKEND_LABELS = {
    "agy": "agy (Gemini 3.1 Pro, High)",
    "opencode": "opencode (adversary agent, deepinfra/Qwen/Qwen3.7-Max)",
}


def cmd_detect(args: argparse.Namespace) -> None:
    print(json.dumps({b: shutil.which(b) is not None for b in BACKEND_PRIORITY}))


def cmd_review(args: argparse.Namespace) -> None:
    if args.backend:
        if not shutil.which(args.backend):
            die(f"{args.backend} not found on PATH")
        candidates = [args.backend]
    else:
        candidates = available_backends()
        if not candidates:
            die("no backend available — install one of: " + ", ".join(BACKEND_PRIORITY))

    plan_text = resolve_plan_text(args.plan)
    prompt = CRITIQUE_PROMPT.format(plan_text=plan_text)

    failures = []
    for backend in candidates:
        try:
            critique = BACKEND_RUNNERS[backend](prompt)
        except BackendError as exc:
            print(
                f"[second_opinion] {BACKEND_LABELS[backend]} failed: {exc}",
                file=sys.stderr,
            )
            failures.append(f"{backend}: {exc}")
            continue
        print(f"Second opinion via {BACKEND_LABELS[backend]}:")
        print(critique)
        return

    die("all backends failed — " + "; ".join(failures))


def main() -> None:
    signal.signal(signal.SIGTERM, _handle_termination)
    signal.signal(signal.SIGINT, _handle_termination)

    parser = argparse.ArgumentParser(
        description="one-shot adversarial critique of a plan from a non-Claude backend",
    )
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

    args = parser.parse_args()

    dispatch = {"detect": cmd_detect, "review": cmd_review}
    if args.cmd in dispatch:
        dispatch[args.cmd](args)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
