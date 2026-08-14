# STYLE.md

House style (short, prescriptive)

Scope & philosophy
- Uniformity is paramount. Keep interfaces small, explicit, and testable.
- No runtime third-party dependencies in harness code. Tests and CI must be runnable with the system Python and standard tools.

Python
- Target: Python 3.12+ for all non-trivial scripts.
- Shebangs: use #!/usr/bin/env python3 for Python entrypoints.
- Use the standard library for CLIs: argparse or getopt only (no external CLI libs). Prefer argparse for new CLIs.
- Type hints encouraged; keep runtime behavior compatible with 3.12+.
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
- Config: XDG-style YAML (default: $XDG_CONFIG_HOME/ai-harness/config.yml or ~/.config/ai-harness/config.yml).
- Precedence: CLI flags > ENV vars > per-user config > system defaults.
- Secrets: Must never be committed. Use environment variables or system vaults. Add checks in code and tests to avoid accidental logging of secrets.

Logging & output
- Scripts should write normal results to stdout and diagnostics to stderr.
- Provide --quiet / --verbose toggles.
- Keep prompts and secrets out of logs by default.

Tests & CI
- Use standard library unittest for unit tests (no pytest dependency).
- Tests should not require network or live LLMs. Use fake backends via PATH manipulation (tests/fixtures/fake_backends).
- CI verifies unit tests only — no dependency installation.

Formatting & linting (developer guidance)
- No enforced formatter required. Prefer readable code and run local linters if available (shellcheck/shfmt) but CI will not install extra tools.

Files & docs
- Add a short module docstring to each CLI script listing: flags, env vars, files read/written, and primary exit codes. This lets INTERFACES.md be auto-generated later.
- commands.md should list each user-facing command and examples.


