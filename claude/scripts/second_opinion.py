#!/usr/bin/env python3
"""second_opinion.py — one-shot adversarial critique of a plan from a non-Claude
backend. Single-round by design: the multi-round loop, plan revision, and
convergence judgment all require LLM reasoning and live in second-opinion.md's
prose instructions, not here.
"""

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

BACKEND_PRIORITY = ["agy", "ollama"]

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
    path = Path(arg).expanduser()
    if path.is_file():
        return path.read_text()
    return arg


def run_agy(prompt: str) -> str:
    result = subprocess.run(
        ["agy", "-p", prompt, "--model", "Gemini 3.1 Pro (High)"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        die(f"agy exited {result.returncode}: {result.stderr.strip()}")
    return result.stdout.strip()


def run_ollama(prompt: str) -> str:
    result = subprocess.run(
        ["ollama", "run", "gemma4:26b", prompt],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        die(f"ollama exited {result.returncode}: {result.stderr.strip()}")
    return result.stdout.strip()


BACKEND_RUNNERS = {"agy": run_agy, "ollama": run_ollama}
BACKEND_LABELS = {
    "agy": "agy (Gemini 3.1 Pro, High)",
    "ollama": "ollama (gemma4:26b)",
}


def cmd_detect(args: argparse.Namespace) -> None:
    print(json.dumps({b: shutil.which(b) is not None for b in BACKEND_PRIORITY}))


def cmd_review(args: argparse.Namespace) -> None:
    backend = resolve_backend()
    if backend is None:
        die("no backend available — install one of: " + ", ".join(BACKEND_PRIORITY))

    plan_text = resolve_plan_text(args.plan)
    prompt = CRITIQUE_PROMPT.format(plan_text=plan_text)
    critique = BACKEND_RUNNERS[backend](prompt)

    print(f"Second opinion via {BACKEND_LABELS[backend]}:")
    print(critique)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="one-shot adversarial critique of a plan from a non-Claude backend",
    )
    sub = parser.add_subparsers(dest="cmd", metavar="{detect,review}")

    sub.add_parser("detect", help="list available backends as JSON")

    p = sub.add_parser(
        "review", help="get one critique from the priority-selected backend"
    )
    p.add_argument("plan", metavar="<plan-file-or-text>")

    args = parser.parse_args()

    dispatch = {"detect": cmd_detect, "review": cmd_review}
    if args.cmd in dispatch:
        dispatch[args.cmd](args)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
