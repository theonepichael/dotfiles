#!/usr/bin/env python3
"""second_opinion.py — one-shot adversarial critique of a plan from a non-Claude
backend. Single-round by design: the multi-round loop, plan revision, and
convergence judgment all require LLM reasoning and live in second-opinion.md's
prose instructions, not here.

Requires Python 3.12+.
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
from typing import NoReturn

BACKEND_PRIORITY = ["agy", "opencode", "copilot"]
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


def die(msg: str) -> NoReturn:
    """Print an error to stderr, prefixed for this script, and exit with status 1."""
    print(f"[second_opinion] {msg}", file=sys.stderr)
    sys.exit(1)


def available_backends() -> list[str]:
    """Return the backends in :data:`BACKEND_PRIORITY` that are on ``PATH``."""
    return [b for b in BACKEND_PRIORITY if shutil.which(b)]


def resolve_backend() -> str | None:
    """Return the highest-priority available backend, or ``None`` if none is."""
    backends = available_backends()
    return backends[0] if backends else None


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


def _safe_get(obj: object, *keys: str) -> object | None:
    """Walk nested dict keys, returning ``None`` at the first missing or non-dict step.

    Backend subprocess output is external, loosely-specified JSON — a field
    documented as "usually a nested object" can still show up as a bare
    string or be absent entirely on a given event. A chained
    ``d.get("a", {}).get("b")`` crashes with ``AttributeError`` the moment
    any intermediate value isn't actually a dict; this never does.

    Args:
        obj: The value to walk, expected to be a dict (or nested dicts).
        *keys: Keys to look up in sequence.

    Returns:
        The value at the end of the key path, or ``None`` if ``obj`` (or
        any intermediate value) isn't a dict, or a key is missing.
    """
    current = obj
    for key in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def _kill_active_process() -> None:
    """Kill the currently-running backend subprocess's entire process group, if any."""
    global _active_process
    proc = _active_process
    if proc is None or proc.poll() is not None:
        return
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except ProcessLookupError:
        pass
    proc.wait()


def _handle_termination(signum: int, frame: FrameType | None) -> NoReturn:
    """Signal handler: kill any active backend subprocess, then exit.

    Registered for SIGTERM/SIGINT so an interrupted `review` doesn't leave
    an orphaned backend process running in the background.
    """
    _kill_active_process()
    sys.exit(128 + signum)


def _run_command(cmd: list[str]) -> tuple[int, str, str]:
    """Run ``cmd`` as a subprocess, capturing its output.

    Tracks the running process in :data:`_active_process` so a termination
    signal or a timeout can kill it (and its whole process group).

    Args:
        cmd: The command and arguments to execute.

    Returns:
        ``(returncode, stdout, stderr)``.

    Raises:
        BackendError: If ``cmd``'s executable can't be started, or the
            process doesn't finish within :data:`BACKEND_TIMEOUT_SECONDS`
            (it's killed before this is raised).
    """
    global _active_process
    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
        )
    except OSError as e:
        # e.g. the backend vanished from PATH between `shutil.which` and
        # here — without this, an unhandled OSError would crash the whole
        # program instead of letting cmd_review's per-backend fallback run.
        raise BackendError(f"failed to start {cmd[0]}: {e}") from e
    _active_process = proc
    try:
        stdout, stderr = proc.communicate(timeout=BACKEND_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        _kill_active_process()
        # `_kill_active_process` only reaps the process (`wait()`); the
        # stdout/stderr pipes opened by Popen(..., stdout=PIPE, stderr=PIPE)
        # are still open at this point. A second `communicate()` on the now-
        # dead process drains and closes them — without it, the fds leak
        # until the Popen object happens to get garbage-collected.
        proc.communicate()
        raise BackendError(f"timed out after {BACKEND_TIMEOUT_SECONDS}s — killed")
    finally:
        _active_process = None
    return proc.returncode, stdout, stderr


def run_backend_command(cmd: list[str]) -> str:
    """Run a backend CLI command and return its critique text.

    Args:
        cmd: The command and arguments to execute.

    Returns:
        The backend's stripped stdout.

    Raises:
        BackendError: If the process exits nonzero, or exits 0 with empty
            stdout (still a failure — see inline comment).
    """
    returncode, stdout, stderr = _run_command(cmd)
    if returncode != 0:
        raise BackendError(f"exited {returncode}: {stderr.strip()}")
    result = stdout.strip()
    if not result:
        # Exit 0 with empty stdout is still a failure — e.g. agy in headless
        # mode has its tool calls auto-denied, prints "no output produced" to
        # stderr, and exits 0. Treating that as success would silently pass
        # the empty critique through and skip the priority fallback.
        detail = stderr.strip() or "(no stderr)"
        raise BackendError(f"exited 0 but produced no output: {detail}")
    return result


def run_agy(prompt: str) -> str:
    """Run the ``agy`` backend and return its critique text."""
    return run_backend_command(
        ["agy", "-p", prompt, "--model", "Gemini 3.1 Pro (High)"]
    )


def _opencode_json_events(raw_output: str) -> list[dict[str, object]]:
    """Parse opencode's ``--format json`` stdout into a list of event objects.

    Each non-blank line is expected to be one JSON object. Lines that
    aren't valid JSON, or that decode to something other than a JSON
    object (e.g. a stray log line, or a bare string/array), are silently
    skipped rather than raising — opencode's output stream isn't
    guaranteed to be pure JSON-lines.
    """
    events = []
    for line in raw_output.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            parsed = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            events.append(parsed)
    return events


def _opencode_text_chunks(events: list[dict[str, object]]) -> list[str]:
    """Extract the critique text chunks from opencode's parsed event stream."""
    chunks = []
    for e in events:
        if e.get("type") != "text":
            continue
        text = _safe_get(e, "part", "text")
        if isinstance(text, str) and text:
            chunks.append(text)
    return chunks


def run_opencode(prompt: str) -> str:
    """Run the ``opencode`` backend's adversary agent and return its critique text.

    Not routed through :func:`run_backend_command`: opencode's
    ``--format json`` puts both the critique text and any error detail in
    stdout JSON events regardless of exit code, so the error message has
    to come from parsing stdout, not from stderr or the bare exit code.

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

    No tool-permission flags are passed. Copilot CLI's permission system
    (``copilot help permissions``) only gates ``shell``, ``write``, ``url``,
    and MCP-server tools — its built-in file-read tool isn't gated at all,
    confirmed empirically (a headless ``-p`` prompt asking it to read a
    file succeeded with no ``--allow-*`` flags). A critique never needs to
    write or run shell commands, so there's nothing to allow: unlike agy
    (whose headless mode auto-denies even reads, see
    ``meta-agy-headless-permission-skip``), Copilot needs no allow-rule
    workaround here.
    """
    return run_backend_command(["copilot", "-p", prompt, "--silent"])


BACKEND_RUNNERS = {"agy": run_agy, "opencode": run_opencode, "copilot": run_copilot}
BACKEND_LABELS = {
    "agy": "agy (Gemini 3.1 Pro, High)",
    "opencode": "opencode (adversary agent, deepinfra/Qwen/Qwen3.7-Max)",
    "copilot": "GitHub Copilot CLI",
}


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
    """Register termination handlers, parse argv, and dispatch to a subcommand."""
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
