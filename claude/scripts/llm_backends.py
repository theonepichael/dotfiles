#!/usr/bin/env python3
"""llm_backends.py — shared subprocess plumbing for CLI-agent backends
(agy, opencode, copilot). Extracted from second_opinion.py so dev_status.py's
recap generation can reuse the same process-lifecycle handling (timeouts,
process-group kills, opencode JSON-event parsing) with its own timeout and
model choices, without duplicating it.

Requires Python 3.12+.
"""

import json
import os
import shutil
import signal
import subprocess
from contextlib import suppress

BACKEND_PRIORITY = ["agy", "opencode", "copilot"]

_active_process: subprocess.Popen[str] | None = None


class BackendError(Exception):
    """A backend was invoked but failed (timeout or nonzero exit)."""


def available_backends() -> list[str]:
    """Return the backends in :data:`BACKEND_PRIORITY` that are on ``PATH``."""
    return [b for b in BACKEND_PRIORITY if shutil.which(b)]


def resolve_backend() -> str | None:
    """Return the highest-priority available backend, or ``None`` if none is."""
    backends = available_backends()
    return backends[0] if backends else None


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
    proc = _active_process
    if proc is None or proc.poll() is not None:
        return
    with suppress(ProcessLookupError):
        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    proc.wait()


def _run_command(cmd: list[str], timeout: float) -> tuple[int, str, str]:
    """Run ``cmd`` as a subprocess, capturing its output.

    Tracks the running process in :data:`_active_process` so a termination
    signal or a timeout can kill it (and its whole process group).

    Args:
        cmd: The command and arguments to execute.
        timeout: Seconds to wait before killing the process.

    Returns:
        ``(returncode, stdout, stderr)``.

    Raises:
        BackendError: If ``cmd``'s executable can't be started, or the
            process doesn't finish within ``timeout`` (it's killed before
            this is raised).
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
        # program instead of letting the caller's per-backend fallback run.
        raise BackendError(f"failed to start {cmd[0]}: {e}") from e
    _active_process = proc
    try:
        stdout, stderr = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        _kill_active_process()
        # `_kill_active_process` only reaps the process (`wait()`); the
        # stdout/stderr pipes opened by Popen(..., stdout=PIPE, stderr=PIPE)
        # are still open at this point. A second `communicate()` on the now-
        # dead process drains and closes them — without it, the fds leak
        # until the Popen object happens to get garbage-collected.
        proc.communicate()
        raise BackendError(f"timed out after {timeout}s — killed")
    finally:
        _active_process = None
    return proc.returncode, stdout, stderr


def run_backend_command(cmd: list[str], timeout: float) -> str:
    """Run a backend CLI command and return its critique/prose text.

    Args:
        cmd: The command and arguments to execute.
        timeout: Seconds to wait before killing the process.

    Returns:
        The backend's stripped stdout.

    Raises:
        BackendError: If the process exits nonzero, or exits 0 with empty
            stdout (still a failure — see inline comment).
    """
    returncode, stdout, stderr = _run_command(cmd, timeout)
    if returncode != 0:
        raise BackendError(f"exited {returncode}: {stderr.strip()}")
    result = stdout.strip()
    if not result:
        # Exit 0 with empty stdout is still a failure — e.g. agy in headless
        # mode has its tool calls auto-denied, prints "no output produced" to
        # stderr, and exits 0. Treating that as success would silently pass
        # empty output through and skip the caller's priority fallback.
        detail = stderr.strip() or "(no stderr)"
        raise BackendError(f"exited 0 but produced no output: {detail}")
    return result


def run_agy(prompt: str, *, model: str, timeout: float) -> str:
    """Run the ``agy`` backend with the given model and return its text output."""
    return run_backend_command(["agy", "-p", prompt, "--model", model], timeout)


def run_copilot(prompt: str, *, model: str | None, timeout: float) -> str:
    """Run the ``copilot`` backend and return its text output.

    No tool-permission flags are passed. Copilot CLI's permission system
    (``copilot help permissions``) only gates ``shell``, ``write``, ``url``,
    and MCP-server tools — its built-in file-read tool isn't gated at all.
    An adversarial critique or a recap prompt never needs to write or run
    shell commands, so there's nothing to allow.

    ``model``, when truthy, forces ``--model``. Copilot CLI's ``--model``
    flag is gated by a per-account "model picker" policy — on a
    policy-disabled account every explicit model is rejected, and only the
    implicit default routing (no ``--model`` flag at all) works. Callers
    should leave ``model`` unset/empty unless their account is confirmed to
    allow explicit model selection.
    """
    cmd = ["copilot", "-p", prompt, "--silent"]
    if model:
        cmd += ["--model", model]
    return run_backend_command(cmd, timeout)


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
    """Extract text chunks from opencode's parsed event stream."""
    chunks = []
    for e in events:
        if e.get("type") != "text":
            continue
        text = _safe_get(e, "part", "text")
        if isinstance(text, str) and text:
            chunks.append(text)
    return chunks


def _opencode_tool_use_events(
    events: list[dict[str, object]],
) -> list[dict[str, object]]:
    """Return the subset of opencode events that represent a tool invocation.

    opencode's ``--format json`` stream emits one ``tool_use`` event per
    completed or failed tool call. A text-only caller (an adversarial
    critique, a recap) must never see one: its presence means the agent took
    a real shell/file action instead of replying with prose.
    """
    return [e for e in events if e.get("type") == "tool_use"]


def _raise_on_tool_use(events: list[dict[str, object]], *, context: str) -> None:
    """Raise :class:`BackendError` if any opencode event is a tool invocation.

    Defense in depth on top of per-agent permission config: a future agent
    or config could reintroduce the gap a permission deny closes today, and
    silently ignoring a tool_use event (the pre-fix behavior) is exactly how
    a swapped-in model's real shell/file actions went unnoticed.

    Args:
        events: Parsed opencode ``--format json`` events.
        context: Label for the failing caller, used in the error message.

    Raises:
        BackendError: If any event in ``events`` has ``type == "tool_use"``,
            naming the tools that were invoked.
    """
    tool_uses = _opencode_tool_use_events(events)
    if not tool_uses:
        return
    tools = sorted(
        {str(tool) for e in tool_uses if (tool := _safe_get(e, "part", "tool"))}
    )
    names = ", ".join(tools) if tools else "unknown tools"
    raise BackendError(f"{context} used tools instead of returning text: {names}")


def run_opencode(prompt: str, *, model: str | None, timeout: float) -> str:
    """Run opencode's default agent (no ``--agent`` override) and return its text output.

    Generic invocation for callers that just want a plain completion (e.g.
    dev_status.py's recap prose). second_opinion.py's adversarial critique
    uses its own ``run_opencode`` with ``--agent adversary``, built on the
    same ``_run_command``/event-parsing helpers this module exports.

    Raises:
        BackendError: If the event stream contains a ``tool_use`` event (the
            agent took a real shell/file action instead of returning text),
            or if it has no text chunks — either because an explicit error
            event was emitted, or because nothing recognizable was produced
            at all.
    """
    cmd = ["opencode", "run", "--auto", "--format", "json"]
    if model:
        cmd += ["-m", model]
    cmd.append(prompt)
    _, stdout, stderr = _run_command(cmd, timeout)
    events = _opencode_json_events(stdout)
    _raise_on_tool_use(events, context="opencode")
    chunks = _opencode_text_chunks(events)
    if chunks:
        return "".join(chunks).strip()
    for e in events:
        if e.get("type") == "error":
            message = _safe_get(e, "error", "data", "message")
            raise BackendError(f"error: {message or e.get('error')}")
    raise BackendError(f"no text output: {stderr.strip() or stdout.strip()[:200]}")
