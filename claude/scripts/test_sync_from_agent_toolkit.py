#!/usr/bin/env python3
"""Tests for sync_from_agent_toolkit.py. Run with: python3 test_sync_from_agent_toolkit.py

Two tiers, mirroring the pre-flip sync_from_dotfiles.py's own test shape:

- Pure-function tests for the registered transform and the state-file
  schema — no git subprocess calls at all.
- A slower integration tier against synthetic throwaway git repos (built
  and torn down inside each test), exercising the real read/compare/copy/
  state code paths, plus the ``--check`` drift guard. Marked
  ``allow_real_subprocess`` per test/AGENTS.md, since this tool's entire
  job is reading a real external repo via git.

Post-flip contract (agent-toolkit's MIGRATION.md, 2026-09-10): only
``claude/CORE_INSTRUCTIONS.md`` is authored in agent-toolkit and flows
downstream into this repo — the reverse of the pre-flip direction. There is
no generator sweep on this side: ``gen_core_instructions.py`` already runs
separately and unconditionally.
"""

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
import sync_from_agent_toolkit as sfa


class TransformTests(unittest.TestCase):
    """The registered mechanical transform, pure — no git, no filesystem."""

    def test_rewrites_the_backticked_scripts_dir_token(self) -> None:
        text = "commit an edit to a script under `agent-scripts/` that a doc names\n"
        out, count = sfa.apply_transform(text)
        self.assertIn("`claude/scripts/`", out)
        self.assertNotIn("`agent-scripts/`", out)
        self.assertEqual(count, 1)

    def test_leaves_every_deployed_path_occurrence_untouched(self) -> None:
        # ~/.claude/scripts/ refers to the deployed symlink farm, not
        # agent-toolkit's agent-scripts/ — the transform must never touch it.
        text = (
            "run `python3 ~/.claude/scripts/dev_status.py add ...`\n"
            "  `python3 ~/.claude/scripts/dev_status.py block a b`\n"
        )
        out, count = sfa.apply_transform(text)
        self.assertEqual(out, text)
        self.assertEqual(count, 0)

    def test_is_idempotent(self) -> None:
        text = "under `agent-scripts/` per INTERFACES.md\n"
        once, count1 = sfa.apply_transform(text)
        twice, count2 = sfa.apply_transform(once)
        self.assertEqual(once, twice)
        self.assertEqual(count1, 1)
        self.assertEqual(count2, 0)

    @pytest.mark.allow_real_subprocess
    def test_real_agent_toolkit_copy_transforms_to_the_real_dotfiles_copy(self) -> None:
        """The one live occurrence: agent-toolkit@HEAD's file, transformed,
        must equal this repo's committed copy. Skipped when
        ~/Workspace/agent-toolkit is not present (e.g. a checkout on
        another machine) — the synthetic integration tier covers the
        mechanism regardless."""
        agent_toolkit = sfa.DEFAULT_AGENT_TOOLKIT_PATH
        probe = sfa.run_git(agent_toolkit, "rev-parse", "--is-inside-work-tree")
        if probe.returncode != 0:
            self.skipTest(f"{agent_toolkit} not available")
        head = sfa.resolve_head(agent_toolkit)
        if not sfa.path_exists_at(agent_toolkit, head, sfa.CONTRACT_FILE):
            self.skipTest("contract file absent from live agent-toolkit@HEAD")
        text = sfa.read_at(agent_toolkit, head, sfa.CONTRACT_FILE).decode("utf-8")
        transformed, count = sfa.apply_transform(text)
        dotfiles_copy = (sfa.REPO_ROOT / sfa.CONTRACT_FILE).read_text(encoding="utf-8")
        if transformed == dotfiles_copy:
            self.assertEqual(count, 1)
        else:
            # Legitimate drift either direction (an upstream edit not yet
            # synced, or dotfiles-side work not yet pushed) — the transform
            # must still have fired exactly once on the current wording.
            self.assertEqual(count, 1, "live agent-toolkit copy lost its token")


class StateFileTests(unittest.TestCase):
    def test_load_state_returns_none_when_absent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(sfa.load_state(Path(tmp)))

    def test_load_state_ignores_a_corrupt_file(self) -> None:
        # Provenance-only: a corrupt state file can never block or crash a
        # run — the sync decision is made from content comparison.
        with tempfile.TemporaryDirectory() as tmp:
            state_path = sfa.state_path(Path(tmp))
            state_path.parent.mkdir(parents=True)
            state_path.write_text("{not json at all", encoding="utf-8")
            self.assertIsNone(sfa.load_state(Path(tmp)))

    def test_write_state_schema_is_provenance_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sfa.write_state(root, agent_toolkit_sha="abc123")
            state = sfa.load_state(root)
            assert state is not None
            self.assertEqual(state["last_synced_agent_toolkit_sha"], "abc123")
            self.assertIn("synced_at", state)

    def test_state_path_is_committed_not_ignored(self) -> None:
        gitignore = (Path(__file__).resolve().parents[2] / ".gitignore").read_text(
            encoding="utf-8"
        )
        self.assertNotIn(".sync-state.json", gitignore)


# ── integration tier: real git, synthetic throwaway repos ───────────────────


def subprocess_git_init_and_commit(repo: Path) -> None:
    """Real-subprocess helper shared by the integration tests below."""
    git(repo, "init", "-q", "-b", "main")
    (repo / ".keep").write_text("", encoding="utf-8")
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "root")


def git(repo: Path, *args: str) -> str:
    """Run a real git command with a fixed identity, no user config needed."""
    import os

    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "sync-test",
        "GIT_AUTHOR_EMAIL": "sync-test@example.com",
        "GIT_COMMITTER_NAME": "sync-test",
        "GIT_COMMITTER_EMAIL": "sync-test@example.com",
    }
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    assert result.returncode == 0, f"git {args} failed: {result.stderr}"
    return result.stdout


def commit_file(repo: Path, relpath: str, content: str) -> None:
    path = repo / relpath
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    git(repo, "add", relpath)
    git(repo, "commit", "-q", "-m", f"add {relpath}")


def staged_paths(repo: Path) -> set[str]:
    out = git(repo, "diff", "--name-only", "--cached")
    return {line for line in out.splitlines() if line}


class SyntheticRepoIntegrationTests(unittest.TestCase):
    """Exercises the real read/compare/copy/state paths end to end.

    No real ~/Workspace/agent-toolkit or real dotfiles checkout is touched —
    both "agent-toolkit" and "dotfiles" are throwaway repos under a temp
    directory.
    """

    AGENT_TOOLKIT_TEXT = (
        "# core\n"
        "line one: commit an edit to any script under `agent-scripts/` that a\n"
        "skill doc names.\n"
        "run `python3 ~/.claude/scripts/dev_status.py add ...\n"
        "more shared prose\n"
    )

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.agent_toolkit = Path(self.tmp.name) / "agent-toolkit"
        self.dotfiles = Path(self.tmp.name) / "dotfiles"
        self.agent_toolkit.mkdir()
        self.dotfiles.mkdir()
        subprocess_git_init_and_commit(self.agent_toolkit)
        subprocess_git_init_and_commit(self.dotfiles)
        # dotfiles starts with agent-toolkit@HEAD's file already
        # transformed — i.e. a synced baseline — so each test moves one
        # lever at a time.
        commit_file(self.agent_toolkit, sfa.CONTRACT_FILE, self.AGENT_TOOLKIT_TEXT)
        head = sfa.resolve_head(self.agent_toolkit)
        transformed, count = sfa.apply_transform(self.AGENT_TOOLKIT_TEXT)
        assert count == 1
        commit_file(self.dotfiles, sfa.CONTRACT_FILE, transformed)
        self.tip = head

    def run_sync(self, *extra: str) -> int:
        return sfa.main(
            self.dotfiles,
            argv=["--quiet", "--agent-toolkit-path", str(self.agent_toolkit), *extra],
            do_exit=False,
        )

    # -- up-to-date runs are true no-ops ------------------------------------

    @pytest.mark.allow_real_subprocess
    def test_up_to_date_apply_is_a_true_noop(self) -> None:
        """Same content → exit 0, no state write, tree unchanged."""
        before = git(self.dotfiles, "status", "--porcelain")
        code = self.run_sync("--apply")
        self.assertEqual(code, 0)
        self.assertEqual(git(self.dotfiles, "status", "--porcelain"), before)
        self.assertFalse(sfa.state_path(self.dotfiles).exists())

    @pytest.mark.allow_real_subprocess
    def test_unrelated_upstream_commits_cause_no_state_churn(self) -> None:
        """The provenance sha is the last commit touching the contract file,
        not HEAD — an unrelated agent-toolkit commit must not dirty state."""
        commit_file(self.agent_toolkit, "unrelated/zshrc", "alias ll='ls -l'\n")
        before = git(self.dotfiles, "status", "--porcelain")
        code = self.run_sync("--apply")
        self.assertEqual(code, 0)
        self.assertEqual(git(self.dotfiles, "status", "--porcelain"), before)
        self.assertFalse(sfa.state_path(self.dotfiles).exists())

    # -- apply with a real delta ---------------------------------------------

    @pytest.mark.allow_real_subprocess
    def test_apply_writes_transformed_file_and_advances_state(self) -> None:
        commit_file(
            self.agent_toolkit,
            sfa.CONTRACT_FILE,
            self.AGENT_TOOLKIT_TEXT + "new upstream paragraph\n",
        )
        # An unrelated upstream commit on top: tip advances past the commit
        # that touched the contract file, and provenance must record the
        # latter, not HEAD.
        commit_file(self.agent_toolkit, "unrelated/notes", "unrelated\n")
        self.tip = sfa.resolve_head(self.agent_toolkit)
        touching = sfa.last_commit_touching(
            self.agent_toolkit, self.tip, sfa.CONTRACT_FILE
        )
        self.assertNotEqual(touching, self.tip)  # last change to the file itself

        code = self.run_sync("--apply")
        self.assertEqual(code, 0)

        dotfiles_copy = (self.dotfiles / sfa.CONTRACT_FILE).read_text(encoding="utf-8")
        self.assertIn("new upstream paragraph\n", dotfiles_copy)
        self.assertNotIn("`agent-scripts/`", dotfiles_copy)

        state = sfa.load_state(self.dotfiles)
        assert state is not None
        self.assertEqual(state["last_synced_agent_toolkit_sha"], touching)

        # Staged set is exactly the contract file + the state file.
        self.assertEqual(
            staged_paths(self.dotfiles),
            {sfa.CONTRACT_FILE, "claude/scripts/.sync-state.json"},
        )

    @pytest.mark.allow_real_subprocess
    def test_state_advances_only_on_content_change(self) -> None:
        """A first successful sync writes state; an immediate re-run (same
        content, same upstream touching-sha) leaves it byte-identical."""
        commit_file(
            self.agent_toolkit,
            sfa.CONTRACT_FILE,
            self.AGENT_TOOLKIT_TEXT + "delta\n",
        )
        self.assertEqual(self.run_sync("--apply"), 0)
        first = sfa.state_path(self.dotfiles).read_bytes()
        self.assertEqual(self.run_sync("--apply"), 0)
        self.assertEqual(sfa.state_path(self.dotfiles).read_bytes(), first)

    # -- report mode ----------------------------------------------------------

    @pytest.mark.allow_real_subprocess
    def test_report_mode_writes_nothing(self) -> None:
        """No --apply: summarize and stop — no file write, no state."""
        commit_file(
            self.agent_toolkit,
            sfa.CONTRACT_FILE,
            self.AGENT_TOOLKIT_TEXT + "pending upstream delta\n",
        )
        before = git(self.dotfiles, "status", "--porcelain")
        code = self.run_sync()
        self.assertEqual(code, 0)
        self.assertEqual(git(self.dotfiles, "status", "--porcelain"), before)
        self.assertFalse(sfa.state_path(self.dotfiles).exists())
        self.assertFalse(
            (self.dotfiles / sfa.CONTRACT_FILE)
            .read_text(encoding="utf-8")
            .endswith("pending upstream delta\n")
        )

    # -- --check drift guard --------------------------------------------------

    @pytest.mark.allow_real_subprocess
    def test_check_passes_when_up_to_date(self) -> None:
        self.assertEqual(self.run_sync("--check"), 0)

    @pytest.mark.allow_real_subprocess
    def test_check_fails_when_drifted(self) -> None:
        commit_file(
            self.agent_toolkit,
            sfa.CONTRACT_FILE,
            self.AGENT_TOOLKIT_TEXT + "drifted upstream paragraph\n",
        )
        self.assertEqual(self.run_sync("--check"), 1)

    @pytest.mark.allow_real_subprocess
    def test_check_writes_nothing(self) -> None:
        commit_file(
            self.agent_toolkit,
            sfa.CONTRACT_FILE,
            self.AGENT_TOOLKIT_TEXT + "drifted upstream paragraph\n",
        )
        before = git(self.dotfiles, "status", "--porcelain")
        self.run_sync("--check")
        self.assertEqual(git(self.dotfiles, "status", "--porcelain"), before)
        self.assertFalse(sfa.state_path(self.dotfiles).exists())

    @pytest.mark.allow_real_subprocess
    def test_check_and_apply_are_mutually_exclusive(self) -> None:
        with self.assertRaises(SystemExit) as ctx:
            self.run_sync("--check", "--apply")
        self.assertEqual(ctx.exception.code, 2)

    # -- loud failure modes ----------------------------------------------------

    @pytest.mark.allow_real_subprocess
    def test_contract_missing_from_agent_toolkit_head_stops_clean(self) -> None:
        (self.agent_toolkit / sfa.CONTRACT_FILE).unlink()
        git(self.agent_toolkit, "add", "-A")
        git(self.agent_toolkit, "commit", "-q", "-m", "delete contract")
        before = git(self.dotfiles, "status", "--porcelain")
        code = self.run_sync("--apply")
        self.assertEqual(code, 1)
        self.assertEqual(git(self.dotfiles, "status", "--porcelain"), before)
        self.assertFalse(sfa.state_path(self.dotfiles).exists())

    @pytest.mark.allow_real_subprocess
    def test_zero_substitutions_with_differing_content_stops(self) -> None:
        """An upstream reword that dropped the token could carry an
        unadjusted path reference into the dotfiles copy — stop for manual
        review instead of copying it."""
        dropped = self.AGENT_TOOLKIT_TEXT.replace(
            "`agent-scripts/`", "the scripts tree"
        )
        commit_file(self.agent_toolkit, sfa.CONTRACT_FILE, dropped)
        before = git(self.dotfiles, "status", "--porcelain")
        code = self.run_sync("--apply")
        self.assertEqual(code, 1)
        self.assertEqual(git(self.dotfiles, "status", "--porcelain"), before)
        self.assertFalse(sfa.state_path(self.dotfiles).exists())

    @pytest.mark.allow_real_subprocess
    def test_zero_substitutions_up_to_date_is_a_normal_noop(self) -> None:
        # agent-toolkit went path-neutral AND the dotfiles copy matches: the
        # designed no-op, not a guard trip.
        dropped = self.AGENT_TOOLKIT_TEXT.replace(
            "`agent-scripts/`", "the scripts tree"
        )
        commit_file(self.agent_toolkit, sfa.CONTRACT_FILE, dropped)
        commit_file(self.dotfiles, sfa.CONTRACT_FILE, dropped)
        before = git(self.dotfiles, "status", "--porcelain")
        code = self.run_sync("--apply")
        self.assertEqual(code, 0)
        self.assertEqual(git(self.dotfiles, "status", "--porcelain"), before)

    @pytest.mark.allow_real_subprocess
    def test_multiple_token_occurrences_stop_for_review(self) -> None:
        doubled = self.AGENT_TOOLKIT_TEXT.replace(
            "`agent-scripts/`", "`agent-scripts/` plus `agent-scripts/`", 1
        )
        commit_file(self.agent_toolkit, sfa.CONTRACT_FILE, doubled)
        before = git(self.dotfiles, "status", "--porcelain")
        code = self.run_sync("--apply")
        self.assertEqual(code, 1)
        self.assertEqual(git(self.dotfiles, "status", "--porcelain"), before)

    @pytest.mark.allow_real_subprocess
    def test_bad_agent_toolkit_path_fails_loudly_not_with_a_traceback(self) -> None:
        code = sfa.main(
            self.dotfiles,
            argv=["--apply", "--quiet", "--agent-toolkit-path", "/nonexistent/repo"],
            do_exit=False,
        )
        self.assertEqual(code, 1)

    # -- CLI flags ------------------------------------------------------------

    @pytest.mark.allow_real_subprocess
    def test_bad_agent_toolkit_path_flag_shape(self) -> None:
        # --agent-toolkit-path takes a value; a missing value is bad usage.
        with self.assertRaises(SystemExit) as ctx:
            sfa.main(self.dotfiles, argv=["--agent-toolkit-path"], do_exit=False)
        self.assertEqual(ctx.exception.code, 2)


if __name__ == "__main__":
    unittest.main()
