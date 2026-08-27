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
import re
import shutil
import signal
import subprocess
from contextlib import suppress

BACKEND_PRIORITY = ["agy", "opencode", "copilot"]

# Each pattern captures one shape a tool-hungry model can emit as literal
# text when every tool is denied (opencode's "permission": "deny") or a
# swapped-in model is otherwise tool-starved: XML with the <tool_calls>
# wrapper, bare XML without it, or a JSON-shaped tool-call block — attribute
# order and the presence of a wrapper/parameters aren't guaranteed, so each
# form is matched independently. Every pattern requires an actual *closing*
# marker (</tool_calls>, </invoke>, a <parameter ...name=, or a closing `]`/
# `}`); none fall back to matching to end-of-string on an unclosed tag, since
# that would also swallow prose that merely *mentions* an opening tag without
# ever completing the structure (e.g. "will never emit <tool_calls>
# wrappers").
_TOOL_CALL_LEAK_PATTERNS = (
    re.compile(r"<tool_calls\b.*?</tool_calls>", re.IGNORECASE | re.DOTALL),
    re.compile(
        r"<invoke\b[^>]*\bname\s*=.*?(?:<parameter\b[^>]*\bname\s*=|</invoke>)",
        re.IGNORECASE | re.DOTALL,
    ),
    re.compile(r'"tool_calls"\s*:\s*\[.*?\]', re.IGNORECASE | re.DOTALL),
    re.compile(r'"type"\s*:\s*"tool_use".*?\}', re.IGNORECASE | re.DOTALL),
)

# Caps how much of a response the leak check scans. Bounds worst-case regex
# cost on a degenerate repetition-loop response (measured 15-42s unbounded at
# 500KB-2MB of repeated markup-like text) while covering any realistic
# critique length with room to spare — a genuine leak is the model's entire
# turn, so it always appears well within this window.
_TOOL_CALL_LEAK_SCAN_LIMIT = 20_000

# Fraction of the scanned text that must fall inside a matched block before
# it's treated as a leak rather than prose that quotes/discusses an example.
# A genuine leaked tool call largely *is* the response; a critique that
# illustrates the failure mode with a quoted snippet has that snippet
# embedded in much more surrounding analysis, so its ratio stays low.
_TOOL_CALL_LEAK_DOMINANCE_RATIO = 0.4

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


def _run_command(
    cmd: list[str], timeout: float, *, retries: int = 0
) -> tuple[int, str, str]:
    """Run ``cmd`` as a subprocess, capturing its output.

    Tracks the running process in :data:`_active_process` so a termination
    signal or a timeout can kill it (and its whole process group).

    ``stdin`` is always :data:`subprocess.DEVNULL`, never inherited: every
    backend here is a one-shot, non-interactive prompt, so a child never
    needs to read from our stdin. Without this, a child that reads stdin at
    all (opencode's CLI does, unconditionally, per
    ``anomalyco/opencode#38723``) inherits our own process's stdin file
    descriptor by default and blocks forever if that descriptor never
    reaches EOF (e.g. an open pipe with no writer, which is exactly what a
    long-lived parent process like this one hands its children) — this
    reproduced reliably in this environment and is a confirmed root cause of
    "opencode intermittently hangs" reports upstream, not merely a
    hypothesis.

    Args:
        cmd: The command and arguments to execute.
        timeout: Seconds to wait before killing the process.
        retries: Extra attempts after an initial timeout, before raising —
            each retry re-runs ``cmd`` from scratch (a fresh subprocess),
            waiting up to ``timeout`` again, so worst-case wall time is
            ``timeout * (1 + retries)``. Only a *timeout* triggers a retry;
            a nonzero exit or a failed-to-start error still raises/returns
            on the first attempt (those aren't the observed opencode
            failure mode this exists for — see ``run_opencode``). Default
            0 preserves the original no-retry behavior for every other
            caller (agy, copilot).

    Returns:
        ``(returncode, stdout, stderr)``.

    Raises:
        BackendError: If ``cmd``'s executable can't be started, or every
            attempt (the initial one plus ``retries``) times out.
    """
    global _active_process
    attempt = 0
    while True:
        try:
            proc = subprocess.Popen(
                cmd,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                start_new_session=True,
            )
        except OSError as e:
            # e.g. the backend vanished from PATH between `shutil.which` and
            # here — without this, an unhandled OSError would crash the
            # whole program instead of letting the caller's per-backend
            # fallback run.
            raise BackendError(f"failed to start {cmd[0]}: {e}") from e
        _active_process = proc
        try:
            stdout, stderr = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            _kill_active_process()
            # `_kill_active_process` only reaps the process (`wait()`); the
            # stdout/stderr pipes opened by Popen(..., stdout=PIPE, stderr=PIPE)
            # are still open at this point. A second `communicate()` on the
            # now-dead process drains and closes them — without it, the fds
            # leak until the Popen object happens to get garbage-collected.
            proc.communicate()
            if attempt < retries:
                attempt += 1
                continue
            suffix = f" (all {retries + 1} attempts timed out)" if retries else ""
            raise BackendError(f"timed out after {timeout}s — killed{suffix}")
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


def _raise_on_emitted_tool_call(text: str, *, context: str) -> None:
    """Raise :class:`BackendError` if ``text`` looks like a leaked tool call.

    A text-only or tool-starved caller must never return a "critique" that is
    actually a model's rejected tool invocation. Some tool-hungry models
    respond to having no usable tool by emitting their attempted call as
    literal markup (XML or JSON-shaped) inside a text event instead of a real
    ``tool_use`` event — which :func:`_raise_on_tool_use` can't see. This is
    the second backstop for that leak.

    Detection combines two checks against :data:`_TOOL_CALL_LEAK_PATTERNS`,
    scanned up to :data:`_TOOL_CALL_LEAK_SCAN_LIMIT`:

    - At least one pattern must match a *closed* block (never a bare opening
      tag), so prose that only mentions tool-call syntax in passing never
      matches at all.
    - The matched block(s) must make up at least
      :data:`_TOOL_CALL_LEAK_DOMINANCE_RATIO` of the scanned text, so a
      critique that quotes/discusses a complete example snippet as one small
      part of much larger prose isn't treated the same as a response that
      *is* the leaked call.

    Args:
        text: The joined text chunks to inspect.
        context: Label for the failing caller, used in the error message.

    Raises:
        BackendError: If ``text`` is dominated by a leaked tool-call block.
    """
    scanned = text[:_TOOL_CALL_LEAK_SCAN_LIMIT]
    spans = sorted(
        (m.start(), m.end())
        for pattern in _TOOL_CALL_LEAK_PATTERNS
        for m in pattern.finditer(scanned)
    )
    if not spans:
        return
    merged: list[list[int]] = []
    for start, end in spans:
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    matched_chars = sum(end - start for start, end in merged)
    if matched_chars / len(scanned) < _TOOL_CALL_LEAK_DOMINANCE_RATIO:
        return
    raise BackendError(f"{context} returned tool-call markup instead of prose")


def _finalize_text_response(chunks: list[str], *, context: str) -> str:
    """Join text chunks, apply the leaked-tool-call backstop, and return the result.

    Shared by both ``run_opencode`` variants (the generic one in this module
    and second_opinion.py's adversary-agent one) so the two call sites can't
    drift out of sync with each other.

    Raises:
        BackendError: Via :func:`_raise_on_emitted_tool_call`.
    """
    text = "".join(chunks).strip()
    _raise_on_emitted_tool_call(text, context=context)
    return text


def run_opencode(prompt: str, *, model: str | None, timeout: float) -> str:
    """Run opencode's default agent (no ``--agent`` override) and return its text output.

    Generic invocation for callers that just want a plain completion (e.g.
    dev_status.py's recap prose). second_opinion.py's adversarial critique
    uses its own ``run_opencode`` with ``--agent adversary``, built on the
    same ``_run_command``/event-parsing helpers this module exports.

    Raises:
        BackendError: If the event stream contains a ``tool_use`` event (the
            agent took a real shell/file action instead of returning text),
            the returned text is dominated by leaked tool-call markup (an
            attempted tool call leaking through as text instead of a real
            ``tool_use`` event), or if it has no text chunks — either because
            an explicit error event was emitted, or because nothing
            recognizable was produced at all.

    Retries once on a timeout (``retries=1``): confirmed via direct
    bisection (2026-08-17) that opencode's CLI intermittently stalls its
    event stream (emits ``step_start`` and then nothing, no ``text``/
    ``step_finish``/error — a genuine stall, not merely slow) at roughly a
    20-33% rate on prompts around 20KB, independent of exact byte count,
    the model-pool index, and whether the prompt is passed inline or via
    ``-f``/``--file`` — no fix is available at this layer, so one retry is
    the practical mitigation (drops the practical failure rate to roughly
    4-10%).
    """
    cmd = ["opencode", "run", "--auto", "--format", "json"]
    if model:
        cmd += ["-m", model]
    cmd.append(prompt)
    _, stdout, stderr = _run_command(cmd, timeout, retries=1)
    events = _opencode_json_events(stdout)
    _raise_on_tool_use(events, context="opencode")
    chunks = _opencode_text_chunks(events)
    if chunks:
        return _finalize_text_response(chunks, context="opencode")
    for e in events:
        if e.get("type") == "error":
            message = _safe_get(e, "error", "data", "message")
            raise BackendError(f"error: {message or e.get('error')}")
    raise BackendError(f"no text output: {stderr.strip() or stdout.strip()[:200]}")
