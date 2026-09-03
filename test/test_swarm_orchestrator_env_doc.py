"""Guards the orchestrator tab's unattended env flag in the swarm docs.

`swarm_spawn` creates every WORKER tab with ``--env PI_AGENT_UNATTENDED=1``
(``buildTabCreateArgv`` in ``pi/extensions/swarm-tool.ts``), so a worker's
two gate extensions settle themselves at pi's module load. The ORCHESTRATOR
tab is created by hand, and nothing did the equivalent for it -- so every
bash command the orchestrator ran to verify a worker blocked on
permission-gate's "Run bash command?" dialog (observed live 2026-09-03).

Two costs, and the second is the one that bites. Friction: an orchestrator
that verifies anything needs a human keystroke per bash call, which is
expensive at exactly the moment verification should be cheap. Ambiguity:
``herdr-blocked-bridge.ts`` reports a session as ``blocked`` for ANY
blocking ui prompt, so from outside the pane a permission dialog and a
genuine relay are indistinguishable. Marking the orchestrator unattended
removes the prompts entirely, which makes every ``blocked`` it reports
unambiguously a relay.

The fix is documentation -- the orchestrator tab is created by a human
following ``pi/prompts/backlog-item.md`` -- so the doc is the artifact that
can drift, and this is what holds it in place.

The assertions key on the env assignment and on the flag being stated as
required rather than optional, not on exact prose, so rewording the section
does not false-positive.
"""

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

UNATTENDED_ENV = "PI_AGENT_UNATTENDED=1"


def _swarm_section() -> str:
    text = (REPO_ROOT / "pi" / "prompts" / "backlog-item.md").read_text(encoding="utf-8")
    match = re.search(r"^## `--swarm\[=N\]` mode.*?(?=\n^## |\Z)", text, re.DOTALL | re.MULTILINE)
    assert match is not None, "backlog-item.md lost its `--swarm[=N]` mode section"
    return match.group(0)


def test_swarm_section_documents_creating_the_orchestrator_tab() -> None:
    section = _swarm_section()
    assert "herdr tab create" in section, (
        "the --swarm section never says how the orchestrator tab is created, so "
        "the human creating it has nothing telling them to pass the env flag"
    )


def test_orchestrator_tab_carries_the_unattended_env() -> None:
    section = _swarm_section()
    # The worker mention already existed; the orchestrator's is what was missing.
    # Require the flag to appear in the same paragraph as `herdr tab create`,
    # so a stray worker-side mention elsewhere cannot satisfy this.
    paragraphs = [p for p in re.split(r"\n\s*\n", section) if "herdr tab create" in p]
    assert paragraphs, "no paragraph shows the orchestrator tab-create command"
    assert any(UNATTENDED_ENV in p for p in paragraphs), (
        f"the orchestrator tab-create command does not pass {UNATTENDED_ENV}; "
        "without it every bash command the orchestrator runs blocks on a dialog"
    )


def _orchestrator_block() -> str:
    """The subsection covering the orchestrator tab, on its own.

    Scoped to the subsection rather than the whole `--swarm` section: that
    section already contains "required", "relay" and "permission" for
    unrelated reasons (HERDR_ENV, the `prefix` argument, the relay loop), so
    an unscoped search would pass against the pre-fix document and prove
    nothing. Falls back to the paragraphs around the tab-create command if
    the subsection is ever renamed, so a rename is a failure to fix rather
    than a silent skip.
    """
    section = _swarm_section()
    match = re.search(
        r"^### .*orchestrator tab.*?(?=\n^### |\n^## |\Z)",
        section,
        re.DOTALL | re.MULTILINE | re.IGNORECASE,
    )
    if match is not None:
        return match.group(0)
    paragraphs = re.split(r"\n\s*\n", section)
    hits = [i for i, para in enumerate(paragraphs) if "herdr tab create" in para]
    assert hits, "no paragraph shows the orchestrator tab-create command"
    return "\n\n".join(paragraphs[min(hits) : max(hits) + 4])


def test_the_flag_is_stated_as_required_not_optional() -> None:
    block = _orchestrator_block()
    assert "required" in block.lower(), (
        "nothing near the tab-create command marks the env flag as required, "
        "so it reads as an optional convenience and will be dropped"
    )


def test_the_reason_names_both_costs() -> None:
    block = _orchestrator_block().lower()
    assert "relay" in block, (
        "the orchestrator tab-create guidance does not say that without the "
        "flag a permission dialog is indistinguishable from a pending relay"
    )
    assert "verif" in block, (
        "the guidance does not say the flag is what makes the orchestrator's "
        "own verification commands cheap"
    )
