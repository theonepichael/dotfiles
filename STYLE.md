# STYLE.md

House style (short, prescriptive)

Scope & philosophy
- Uniformity is paramount. Keep interfaces small, explicit, and testable.
- No runtime third-party dependencies in harness code. The claude/scripts tools and their colocated tests must stay runnable with the system Python and the standard library alone.
- Development tooling is a separate concern: test/ and CI use uv with pinned pytest and ruff. Keep those dependencies out of anything that runs at harness runtime.

Python
- Target: Python 3.12+ for all non-trivial scripts.
- Shebangs: use #!/usr/bin/env python3 for Python entrypoints.
- Use the standard library for CLIs: argparse or getopt only (no external CLI libs). Prefer argparse for new CLIs.
- Type hints are required on every function/method signature (all parameters and the return type), using modern 3.12+ syntax: built-in generics (`list[str]`, `dict[str, int]`), not `typing.List`/`typing.Dict`; `X | None`, not `Optional[X]`; `X | Y`, not `Union[X, Y]`. Avoid `Any` unless genuinely unavoidable; prefer `object` or a narrower union. Enforced by ruff's ANN rules (see Formatting & linting).
- Keep modules importable from repository root (tests may insert repo root on sys.path).

Shell
- Bootstraps and tiny wrappers: POSIX sh (#!/usr/bin/env sh).
- User shell config under zsh/ should be explicit zsh only; document shell-specific files.
- Strict shell invocation where appropriate: set -eu; use set -o pipefail in bash scripts that require it.

CLI ergonomics
- Use long-form flags with short aliases where appropriate (e.g., --verbose / -v).
- Prefer subcommands for multi-action tools (argparse subparsers).
- Provide --help and clear exit codes. 0 = success; nonzero for failures.

Config & secrets
- Config: JSON, under the owning tool's data directory — e.g. ~/.claude/data/standup/config.json, with backlog state in ~/.claude/data/backlog/. Harness settings stay in their tool-owned files (~/.claude/settings.json, ~/.config/opencode/opencode.jsonc).
- Prefer JSON over YAML for new config: the standard library parses JSON, and YAML would pull in a third-party dependency the harness rule above forbids.
- XDG paths where a tool writes transient state: honor $XDG_STATE_HOME, falling back to ~/.local/state (see scripts/watchcommit.py).
- Precedence: CLI flags > ENV vars > per-user config > system defaults.
- Secrets: Must never be committed. Use environment variables or system vaults. Add checks in code and tests to avoid accidental logging of secrets.

Logging & output
- Scripts should write normal results to stdout and diagnostics to stderr.
- Provide --quiet / --verbose toggles.
- Keep prompts and secrets out of logs by default.

Tests & CI
- Two intentional test tiers; keep new tests in whichever tier matches the code under test.
- test/ — pytest suites covering the top-level tooling: test_install.py, test_depart.py, test_depart_transactions.py, and test_lint.py (a guard that shells out to `uv run ruff check .` and asserts a clean exit). Run with `uv run pytest test/`.
- claude/scripts/test_*.py — standard library unittest, colocated with the scripts they cover and deliberately dependency-free so those tools stay runnable without a `uv sync`. Run with `python -m unittest discover -s claude/scripts -p 'test_*.py'`.
- Dev tooling is declared in the `dev` dependency group in pyproject.toml (pytest and ruff, both pinned) and managed with uv; uv.lock is committed and CI installs from it.
- Tests should not require network or live LLMs. Mock at the subprocess boundary (`_run_command` / `run_backend_command`) rather than invoking real agy/opencode/copilot binaries.
- CI (.github/workflows/python-quality.yml) runs on pushes to main and on every pull request: `uv sync --locked --dev`, then `ruff check .`, `ruff format --check .`, pytest over test/test_install.py and test/test_lint.py, then the claude/scripts unittest discovery.
- test/run.sh drives the containerized install.sh scenario suite (test/scenarios.sh) against Ubuntu and Fedora images. It needs Docker/Podman and is run locally, not in CI.

Formatting & linting
- Ruff is the enforced formatter and linter, configured under [tool.ruff] and [tool.ruff.lint] in pyproject.toml (88-char lines; E, F, W, UP, SIM, I, PIE, ISC, FURB, TRY, ANN selected). No black, no isort.
- ANN (flake8-annotations) enforces the type-hint requirement above on every function, new or existing.
- Run `uv run ruff format .` and `uv run ruff check --fix .` before committing Python changes. CI fails on either check, and test/test_lint.py fails the suite as well.
- Ruff excludes test files (`**/test_*.py`, `test/`). They are lint-exempt, but should still follow the same conventions.
- Shell has no enforced formatter. Prefer readable code and run local linters if available (shellcheck/shfmt).
- .github/workflows/python-quality-autofix.yml runs on a weekly schedule and on manual dispatch: it applies safe Ruff fixes, re-verifies the tree, and opens a pull request with the result.

Files & docs
- Add a short module docstring to each CLI script listing: flags, env vars, files read/written, and primary exit codes. This discipline is what lets INTERFACES.md be generated from source.
- INTERFACES.md is the interface inventory for the harness scripts, generated from those docstrings and argparse definitions. Fix the source when an interface changes; do not hand-edit the generated inventory. Regenerate with `python3 claude/scripts/gen_interfaces.py`, or check for staleness with `--check` (exit 1 if stale, which is what the test suite asserts).
- User-facing commands are documented in README.md, per harness — that is where behavioral differences between the Claude Code, Copilot, opencode, and agy ports belong.


