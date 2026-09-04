# claude/scripts/ — agent notes

Post meta-agent-toolkit-migration-cutover, this directory holds two kinds
of file, not the full shared harness-tooling set it once did: this
machine's personal-only scripts (`dev_status_sync.py`,
`watchcommit_activity.py`, `herdr_delegate.py`,
`opencode_skills_sync_activity.py`, `gen_core_instructions.py`, never
moving to agent-toolkit), and `install.py`'s own local dependencies
(`cli_common.py`, `settings_seed_drift_check.py`, `gen_interfaces.py`) —
kept here because `install.py` imports them from its own repo checkout,
never via the live `~/.claude/scripts/` symlink (see `install.py`'s own
`sys.path.insert` comment). The harness-runtime scripts every harness
actually calls through `~/.claude/scripts/` (`dev_status.py`, `grill.py`,
`second_opinion.py`, and the rest) live in agent-toolkit now. Only what is
easy to get wrong here; general conventions are in the repo root's
`AGENTS.md` and `STYLE.md`.

## Standard library only

No runtime third-party imports, ever. These tools have to run on a machine
that has never run `uv sync` — that is the whole reason the rule exists.
`argparse` for CLIs, `json` for config, `tomllib` for TOML. `pytest` and
`ruff` are development tooling and stay out of anything that runs at harness
runtime.

## The docstrings are source, not commentary

`INTERFACES.md` is **generated** from each script's module docstring and its
argparse definitions. When an interface changes, fix the docstring and the
argparse definition — never `INTERFACES.md` itself — then regenerate:

```sh
python3 claude/scripts/gen_interfaces.py           # rewrite
python3 claude/scripts/gen_interfaces.py --check   # 1 = stale, 3 = doc drift
```

`githooks/pre-commit` runs `--check` and blocks the commit on either exit
code, so a stale inventory cannot land. Exit 3 is the more interesting one:
it means a skill or command doc still describes a CLI contract the script no
longer has.

That check is a string comparison, not a reading. A flag can keep its name
while its behaviour changes underneath, and every doc naming it still
passes. `claude/commands/swarm.md` is the one skill doc left in this repo
that names anything here (`herdr_delegate.py`); after changing that
script's behaviour, re-read it and fix the wording before committing. The
generated skill docs for the scripts that moved to agent-toolkit
(`claude/commands/`, `opencode/skills/`, `copilot/skills/`, `agy/skills/`,
`pi/prompts/` for the harness-runtime tools) live there now, not here.

## Every production script needs a `links.toml` entry — and no test may have one

`test/test_install.py` asserts both directions. A production script with no
entry means `~/.claude/scripts/<name>` silently never exists, and the skills
that call it fail on a machine where it was never hand-linked. This has been
caught live twice.

Test files are the inverse: they are always run in-repo
(`python3 test_X.py`), never through the deployed path, so a `links.toml`
entry for one is dead state and the suite rejects it.

## Colocated tests are `unittest`, not pytest

`test_*.py` here use the standard library `unittest`, so they stay runnable
without a `uv sync`. They are *also* collected by `uv run pytest` from the
repo root — `testpaths` includes this directory — which means the root
`conftest.py` sandbox applies when they run that way and not when they are
run directly. See `test/AGENTS.md` before writing one.
