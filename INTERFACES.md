# INTERFACES.md

Scope: harness-only inventory (claude/, copilot/, opencode/ plus shared scripts linked from `links.toml`).

This document lists observed public interfaces for the harness scripts and related files, their inputs, outputs, side effects, environment variables, error modes, and a recommended uniform interface shape to adopt across the harnesses. It was generated from a repository scan and reading the files in `claude/scripts/` plus installer and mapping files.

Note: this is a human-readable summary oriented around the harness code. It is intentionally conservative where the source is ambiguous — verify runtime behavior when applying automated changes.

---

1) claude/scripts/llm_backends.py
- Purpose: Central adapter for invoking external CLI backends (agy, opencode, copilot). Provides subprocess lifecycle management, timeouts, process-group kills, and opencode JSON-event parsing.

- Public functions (observed):
  - available_backends() -> list[str]
  - resolve_backend() -> str | None
  - run_agy(prompt: str, *, model: str, timeout: float) -> str
  - run_copilot(prompt: str, *, model: str | None, timeout: float) -> str
  - run_opencode(prompt: str, *, model: str | None, timeout: float) -> str
  - run_backend_command(cmd: list[str], timeout: float) -> str

- Inputs:
  - prompt string (passed on the command line to backends)
  - optional `model` string for provider-specific selection
  - timeout (seconds)
  - looks up executables on PATH (shutil.which)

- Environment:
  - inherits the process environment; callers may pass explicit env via subprocess if added later (currently not in the module).

- Outputs:
  - returns stripped text (stdout) for agy/copilot/generic opencode
  - opencode may emit JSON event stream; module converts that into concatenated text chunks

- Error modes / exceptions:
  - raises BackendError on process start failures, timeouts, nonzero exit codes, or exit 0 with empty stdout (treated as failure)
  - opencode-specific errors: emits BackendError with either parsed error event message or a preview of stderr/stdout

- Side effects:
  - spawns subprocesses (may run external network calls depending on backend)
  - kills/cleans up process groups on timeout/termination

- Observations and risks:
  - Robust subprocess handling (start_new_session, process-group kill, drain pipes) — good.
  - No direct logging inside this module; callers handle logging.
  - Ensure callers never interpolate secrets into `prompt` or model args or log them.

- Recommended uniform interface (short):
  - Keep this module as the single adapter layer for any model invocation. Standardize signature: call prompt (str), model (Optional[str]), timeout (float), *, allow_side_effects (bool) if needed in future.
  - Return a small typed result: dataclass { text: str, raw_stdout: str, raw_stderr: str, exit_code: int } so callers can inspect but normal paths use `text`.
  - Provide an optional `env` parameter to allow test harnesses to inject a PATH/FAKE backends mapping.

---

2) claude/scripts/second_opinion.py
- Purpose: High-level reviewer that runs harness backends (agy/opencode/copilot) to produce an adversarial or second-opinion critique.

- Observed helpers and behavior:
  - _run_command wrapper that delegates to llm_backends._run_command with module-level BACKEND_TIMEOUT_SECONDS
  - run_agy(prompt: str) -> str uses llm_backends.run_agy with SINGLE env override SECOND_OPINION_AGY_MODEL
  - run_opencode(prompt: str) -> str constructs specific opencode invocation (opencode run --agent adversary --auto --format json <prompt>), parses events and returns concatenated text chunks or raises BackendError
  - run_copilot(prompt: str) -> str delegates to llm_backends.run_copilot and can be controlled by SECOND_OPINION_COPILOT_MODEL env var
  - there is a review loop that registers signal handlers and kills backends on termination

- Inputs:
  - prompt strings
  - environment overrides: SECOND_OPINION_AGY_MODEL, SECOND_OPINION_COPILOT_MODEL (observed)

- Outputs:
  - plain text critique on success
  - raises BackendError for errors/timeouts/no-output

- Side effects:
  - spawns backend CLIs, may write to stdout/stderr (the CLI does)
  - installs signal handlers during run (SIGTERM/SIGINT)

- Recommended uniform interface:
  - Expose a single entrypoint function: second_opinion(prompt: str, backend_preference: list[str] | None = None, timeout: float | None = None) -> str
  - Accept either an explicit backend name or let resolver pick using llm_backends.resolve_backend().
  - Avoid module-global timeouts; prefer explicit timeout passed through for testability.

---

3) Other shared scripts (short inventory)
The following files are linked into runtime locations by links.toml and act as entrypoints or supporting utilities. I recommend adding explicit interface headers in each file (top-level docstring briefly stating CLI args, env vars, outputs) so this file can be auto-generated later.

- claude/scripts/dev_status.py
  - Purpose: render a repo/dev dashboard recap. Likely calls llm_backends.run_opencode or run_copilot to generate recap prose.
  - Inputs/outputs: reads repository state, prints plain text (dashboard) to stdout; likely accepts CLI flags (render/summary); see README references.
  - Side effects: none beyond calling backends and printing.

- claude/scripts/dev_status_sync.py
  - Purpose: sync status across machines; likely interacts with dotfiles_sync_check and may write local markers.

- claude/scripts/gen_claude_completion.py
  - Purpose: helper to generate completions for harness-specific usage.

- claude/scripts/grill.py
  - Purpose: orchestrates interactive Q&A (grill-me). Likely reads spec files, prompts the model, returns plan text, and writes to `~/.claude/data/grill/...` per docs.

- claude/scripts/standup.py and standup_adapters.py
  - Purpose: produce standup notes across harnesses (formatting adapters, prompt building).

- claude/scripts/dotfiles_sync_check.py
  - Purpose: hook utilities; CLI includes 'mark' command to mark machine as synced (per README). Writes a local marker file as side effect.
  - Interface: observed usage `python3 ~/.claude/scripts/dotfiles_sync_check.py mark` in README.

- claude/scripts/settings_seed_drift_check.py
  - Purpose: detect config seed drift; likely prints diagnostics and exits nonzero when drift detected.

- claude/scripts/vitals_promotion.py
  - Purpose: promote vitals (noted in links.toml). Implementation-specific side effects.

Notes: For the above, I did not fetch each file content in full while producing this inventory. The README and links.toml indicate their existence and expected behavior. Recommend adding standardized CLI docstrings and a thin argparse/typer-based interface so automated discovery can parse interfaces.

---

4) Installer / runtime plumbing
- install.sh
  - POSIX sh bootstrap that searches for a Python 3.12+ interpreter and execs install.py
  - Inputs: CLI flags `--harness`, `--profile`, `--dry-run` as documented in README
  - Output: executes install.py which performs symlink creation per links.toml and other machine configuration
  - Side effects: writes symlinks/files to home, may install packages

- install.py
  - Primary installer; requires Python 3.12+ per README. Exposes the CLI used by install.sh.

- links.toml
  - Mapping doc: src -> dest, harness field controls which symlinks are created. This file serves as the machine-readable command/symlink inventory.

Recommended uniformization:
- Make install.py and any other entrypoint use a shared CLI helper for logging and config precedence. Document the exact flags in the file's module docstring in a standard header format.
- Add a small validator script (can be part of CI) that parses links.toml and reports unreachable src files or invalid dest expansions.

---

5) docs that act as external interfaces
- claude/CLAUDE.md, claude/commands/*.md, copilot/CLAUDE_CODE_PARITY.md, opencode/command/spec.md
  - Purpose: these markdown files define the skill surfaces (trigger phrases, expected behavior) and are treated as the external interface consumers rely on.

Recommendation:
- Keep these docs as the canonical human-visible interface, but add in-repo machine-readable frontmatter for each command (YAML header) describing: CLI trigger, expected args, environment variables, files read/written, and exit codes. This will allow generating tests and verifying parity across harnesses.

---

6) Test and mock recommendations for harness interfaces
- All modules that call external CLIs MUST have test fixtures that stub the executable on PATH. Suggested techniques:
  - create a temporary directory with small shell scripts that replicate the backend CLI contract (exit codes, stdout/stderr, JSON event lines) and prepend it to PATH in tests.
  - expose an optional `env` or `path` parameter in llm_backends.run_* helpers to accept a fake PATH or environment for tests.
  - return structured results (text + raw) instead of just text when practical, easing assertions in tests.

---

7) Immediate actionable items (to commit next on style/uniformity)
- Add this INTERFACES.md to the branch (done).
- Add STYLE.md describing canonical shapes for CLI args, env var precedence, config location (XDG), logging rules, and secrets handling.
- Add a tiny `tests/fixtures/fake_backends/` and one pytest test that ensures llm_backends.run_opencode handles a JSON-lines event stream correctly.
- Add pre-commit, black/ruff, and a small GitHub Actions workflow to run tests/linters.

---

Appendix: how to use this file
- This file is intentionally human-readable and conservative. Use it as the source of truth for implementing the automated uniformity changes (pyproject, pre-commit, CLI helper, tests). After the first round of standardization, generate an automated machine manifest from annotated docstrings in each script so this file can be kept in sync.

Generated on: repository scan (branch: style/uniformity)

