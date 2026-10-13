# claude/scripts/ — agent notes

Post meta-agent-toolkit-migration-cutover, this directory holds two kinds
of file, not the full shared harness-tooling set it once did: this
machine's personal-only scripts (`dev_status_sync.py`,
`watchcommit_activity.py`, `gen_core_instructions.py`, never
moving to agent-toolkit), and `install.py`'s own local dependencies
(`dotfiles_cli_common.py`, `settings_seed_drift_check.py`, `gen_interfaces.py`) —
kept here because `install.py` imports them from its own repo checkout,
never via the live `~/.claude/scripts/` symlink (see `install.py`'s own
`sys.path.insert` comment). The harness-runtime scripts every harness
actually calls through `~/.claude/scripts/` (`dev_status.py`, `grill.py`,
`second_opinion.py`, and the rest) live in agent-toolkit now. Only what is
easy to get wrong here; general conventions are in the repo root's
`AGENTS.md` and `STYLE.md`.

## The core instructions are read from agent-toolkit, never copied here

`CORE_INSTRUCTIONS.md` is authored in agent-toolkit, and this repo keeps no
copy of it: a pulled copy went stale unnoticed once, leaving every harness
loading two dozen paths that no longer existed. `gen_core_instructions.py`
reads the core live from the checkout that
`dotfiles_cli_common.agent_toolkit_checkout` resolves (`$AGENT_TOOLKIT_PATH`,
then `~/Workspace/agent-toolkit/agent-toolkit`, then `~/Workspace/agent-toolkit`)
and composes it with `personal-overlay.md` into `global-instructions.md`.
Any script that needs the agent-toolkit checkout uses that resolver rather
than its own default path.

The core is taken from the last agent-toolkit commit that touched it (its
committed blob, never the working tree), and that commit is recorded in the
output. Because the output is shared through git while each machine composes
from its own checkout, a write is skipped (exit 3) unless the recorded commit
is an ancestor of the local one, under an exclusive lock on
`claude/.global-instructions.md.lock`. watchcommit runs it through
`scripts/watchcommit-pre-tick` after every clean background pull; keep that
hook's exit-3-is-fine mapping in step with the composer's exit codes.

Every write-mode run also registers `--merge-driver` as the repo-local git
merge driver for `global-instructions.md` (git takes driver commands only
from config, never from `.gitattributes`), baking in `sys.executable` and the
resolved checkout, and refuses to write if that fails. watchcommit rebases
with `--merge` explicitly because drivers only run under the merge backend.

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
passes. No skill or command doc naming anything in this directory is left
in this repo — the swarm command and its `herdr_delegate.py` launcher were
the last, and the source-of-truth verdict moved both to agent-toolkit
(`claude/commands/swarm.md` is generated there from `templates/swarm.md.tmpl`);
after changing a script's behaviour there, the docs to re-read live on that
side.

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
