"""Guards the herdr launch recipe that `/swarm` drives.

Every assertion here corresponds to a mistake that actually happened on
2026-09-03 while launching a pi worker by hand, which is the whole reason the
recipe moved out of prose and into a script:

- The tab was created without ``PI_AGENT_UNATTENDED=1``, so the agent stalled on
  a permission dialog at its first ``git status`` while herdr still reported it
  as ``working``. The flag must be on the TAB, because ``permission-gate.ts``
  reads it at module load -- before pi's first tool call -- and fails closed on
  anything but exactly ``"1"``.
- ``--model`` handed straight to ``herdr agent start`` is an unknown flag to
  herdr itself; it reaches pi only after a bare ``--`` separator.

Whether a worker may take an item is a property of its prefix, so there is no
per-item classifier to drift. The rule itself lives in ``dev_status`` and is
tested in ``test_dev_status.py``; what is asserted here is that this module
imports it rather than keeping a second copy.
"""

import os
import sys
from pathlib import Path

import pytest

import conftest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "claude" / "scripts"))

# dev_status.py itself lives in agent-toolkit post meta-agent-toolkit-
# migration-cutover -- herdr_delegate.py finds it via the live install
# directory (see its own sys.path.insert comment), which this test can't
# replicate without an actual install in place. Same AGENT_TOOLKIT_PATH
# convention as scripts/install-with-agent-toolkit.sh, so both point at the
# same checkout by default. conftest._REAL_HOME, not Path.home(): this
# module-level code runs after conftest.py has already redirected HOME to
# its per-run sandbox, so Path.home() here would never find a real checkout
# regardless of what's actually on disk. Skips rather than fails when
# agent-toolkit isn't cloned alongside dotfiles -- a real state for anyone
# besides this machine's own dotfiles checkout.
_AGENT_TOOLKIT_PATH = Path(
    os.environ.get(
        "AGENT_TOOLKIT_PATH", str(conftest._REAL_HOME / "Workspace" / "agent-toolkit")
    )
)
_AGENT_TOOLKIT_SCRIPTS = _AGENT_TOOLKIT_PATH / "claude" / "scripts"
if not (_AGENT_TOOLKIT_SCRIPTS / "dev_status.py").is_file():
    pytest.skip(
        f"dev_status.py not found under {_AGENT_TOOLKIT_SCRIPTS} -- set "
        "AGENT_TOOLKIT_PATH to your agent-toolkit checkout to run this test",
        allow_module_level=True,
    )
sys.path.insert(0, str(_AGENT_TOOLKIT_SCRIPTS))

import dev_status  # noqa: E402 — must follow sys.path.insert above
import herdr_delegate  # noqa: E402 — must follow sys.path.insert above

UNATTENDED = "PI_AGENT_UNATTENDED=1"


def test_tab_argv_always_carries_the_unattended_env():
    argv = herdr_delegate.build_tab_argv(cwd="/repo", label="slug")
    assert "--env" in argv
    assert UNATTENDED in argv
    assert argv[argv.index("--env") + 1] == UNATTENDED


def test_tab_argv_is_created_unfocused_in_the_named_cwd():
    argv = herdr_delegate.build_tab_argv(cwd="/repo", label="slug")
    assert argv[:2] == ["tab", "create"]
    assert argv[argv.index("--cwd") + 1] == "/repo"
    assert "--no-focus" in argv


def test_agent_start_never_carries_the_unattended_env():
    # It belongs on the tab, not here: pi reads it at module load, so setting it
    # on `agent start` would arrive too late to disarm anything.
    argv = herdr_delegate.build_agent_start_argv(name="w1", pane="w1:p2", model=None)
    assert UNATTENDED not in argv
    assert "--env" not in argv


def test_model_reaches_pi_only_after_a_bare_separator():
    argv = herdr_delegate.build_agent_start_argv(
        name="w1", pane="w1:p2", model="prov/m"
    )
    assert "--" in argv, "herdr rejects --model as its own unknown flag"
    assert argv.index("--model") > argv.index("--")
    assert argv[argv.index("--model") + 1] == "prov/m"


def test_no_separator_is_emitted_when_no_model_is_requested():
    argv = herdr_delegate.build_agent_start_argv(name="w1", pane="w1:p2", model=None)
    assert "--" not in argv
    assert "--model" not in argv


def test_agent_start_names_pi_and_the_target_pane():
    argv = herdr_delegate.build_agent_start_argv(name="w1", pane="w1:p2", model=None)
    assert argv[:3] == ["agent", "start", "w1"]
    assert argv[argv.index("--kind") + 1] == "pi"
    assert argv[argv.index("--pane") + 1] == "w1:p2"


def test_worker_prompt_runs_one_item_unattended():
    assert herdr_delegate.worker_prompt("atk-thing") == "/backlog-item --auto atk-thing"


def test_orchestrator_prompt_fans_out_and_names_no_single_item():
    prompt = herdr_delegate.orchestrator_prompt(3)
    assert prompt == "/backlog-item --swarm=3"


def test_plan_groups_ready_slugs_by_prefix_with_counts_and_safety():
    rows = herdr_delegate.group_by_prefix(["atk-a", "atk-b", "iron-lb-c", "meta-d"])
    by = {r["prefix"]: r for r in rows}
    assert by["atk"]["count"] == 2
    assert by["atk"]["worker_safe"] is True
    assert by["iron-lb"]["count"] == 1
    assert by["meta"]["worker_safe"] is False


def test_plan_orders_the_largest_worker_safe_prefix_first():
    # The skill recommends the first row, so ordering is behaviour, not cosmetics.
    rows = herdr_delegate.group_by_prefix(
        ["meta-a", "meta-b", "meta-c", "atk-d", "atk-e", "iron-lb-f"]
    )
    assert rows[0]["prefix"] == "atk"
    assert rows[0]["worker_safe"] is True


def test_it_does_not_define_a_second_copy_of_the_prefix_rule():
    # The property that is genuinely this module's own. The rule itself is
    # dev_status's and is tested there; asserting its behaviour through this
    # module would be testing at a distance and would hide where it lives.
    source = (REPO_ROOT / "claude" / "scripts" / "herdr_delegate.py").read_text()
    assert "def prefix_of(" not in source
    assert "def is_worker_safe(" not in source
    assert herdr_delegate.prefix_of is dev_status.prefix_of
    assert herdr_delegate.is_worker_safe is dev_status.is_worker_safe


def test_launching_the_harness_prefix_is_refused():
    harness = dev_status.REPO_PREFIXES[dev_status.HARNESS_REPO]
    with pytest.raises(herdr_delegate.RefusedError) as exc:
        herdr_delegate.check_launchable(prefix=harness)
    assert harness in str(exc.value)


def test_a_slug_under_the_harness_prefix_is_refused_too():
    with pytest.raises(herdr_delegate.RefusedError):
        herdr_delegate.check_launchable(slug="meta-swarm-eligibility-guard")


def test_a_worker_safe_slug_is_allowed():
    herdr_delegate.check_launchable(slug="atk-publish-remote")


def test_refuses_when_not_running_inside_herdr():
    for value in ("", "0", "true", None):
        env = {} if value is None else {"HERDR_ENV": value}
        with pytest.raises(herdr_delegate.RefusedError):
            herdr_delegate.require_herdr_env(env)


def test_accepts_only_the_exact_herdr_env_value():
    herdr_delegate.require_herdr_env({"HERDR_ENV": "1"})
