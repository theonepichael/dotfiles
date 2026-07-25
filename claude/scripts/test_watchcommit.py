#!/usr/bin/env python3
"""Tests for ../../scripts/watchcommit.py. Run with: python3 test_watchcommit.py

Uses real temporary git repos rather than mocking subprocess — watchcommit's
whole job is orchestrating git, so a scenario test against real repos catches
what a mocked unit test would miss (e.g. actual rebase/conflict behavior).
"""

import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "scripts"))
import watchcommit  # noqa: E402


def sh(cwd: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(args, cwd=cwd, capture_output=True, text=True)


def init_repo(path: Path) -> None:
    path.mkdir(parents=True)
    sh(path, "git", "init", "-q", "-b", "main")
    sh(path, "git", "config", "user.email", "test@test.com")
    sh(path, "git", "config", "user.name", "test")


class WatchcommitRebaseTestCase(unittest.TestCase):
    """Simulates two machines running watchcommit against the same remote —
    the race that used to leave a commit silently stuck forever."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="wc-test-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

        remote = self.tmp / "remote.git"
        remote.mkdir()
        sh(remote, "git", "init", "-q", "--bare", "-b", "main")

        seed = self.tmp / "seed"
        init_repo(seed)
        (seed / "shared.txt").write_text("line1\n")
        (seed / "a-only.txt").write_text("a\n")
        (seed / "b-only.txt").write_text("b\n")
        sh(seed, "git", "add", "-A")
        sh(seed, "git", "commit", "-q", "-m", "seed")
        sh(seed, "git", "remote", "add", "origin", str(remote))
        sh(seed, "git", "push", "-q", "-u", "origin", "main")

        self.repo_a = self.tmp / "machine-a"
        self.repo_b = self.tmp / "machine-b"
        sh(self.tmp, "git", "clone", "-q", str(remote), str(self.repo_a))
        sh(self.tmp, "git", "clone", "-q", str(remote), str(self.repo_b))
        for r in (self.repo_a, self.repo_b):
            sh(r, "git", "config", "user.email", "test@test.com")
            sh(r, "git", "config", "user.name", "test")

    def ahead_behind(self, repo: Path) -> str:
        return sh(repo, "git", "rev-list", "--left-right", "--count", "origin/main...HEAD").stdout.strip()

    def test_nonconflicting_divergence_resolves(self) -> None:
        (self.repo_a / "a-only.txt").write_text("a-changed\n")
        watchcommit.commit_and_push(self.repo_a, "chore: a-only edit")

        (self.repo_b / "b-only.txt").write_text("b-changed\n")
        watchcommit.commit_and_push(self.repo_b, "chore: b-only edit")

        self.assertEqual(self.ahead_behind(self.repo_b), "0\t0")
        remote_log = sh(self.repo_b, "git", "log", "origin/main", "--oneline").stdout
        self.assertIn("a-only edit", remote_log)
        self.assertIn("b-only edit", remote_log)

    def test_stuck_unpushed_commit_gets_retried(self) -> None:
        # Reproduces the exact bug: B has a local commit + clean working
        # tree (has_changes() False), so the old loop would never look at
        # it again. has_unpushed_commits() exists precisely for this state.
        (self.repo_a / "a-only.txt").write_text("a-changed\n")
        watchcommit.commit_and_push(self.repo_a, "chore: a-only edit")

        (self.repo_b / "b-only.txt").write_text("b-changed\n")
        sh(self.repo_b, "git", "add", "-A")
        sh(self.repo_b, "git", "commit", "-q", "-m", "chore: b-only edit")

        self.assertTrue(watchcommit.has_unpushed_commits(self.repo_b))
        self.assertFalse(watchcommit.has_changes(self.repo_b))

        watchcommit.push(self.repo_b, "retried stuck commit")
        self.assertEqual(self.ahead_behind(self.repo_b), "0\t0")

    def test_real_conflict_aborts_without_data_loss(self) -> None:
        (self.repo_a / "shared.txt").write_text("line1\nA's version\n")
        watchcommit.commit_and_push(self.repo_a, "chore: shared edit from A")

        (self.repo_b / "shared.txt").write_text("line1\nB's CONFLICTING version\n")
        watchcommit.commit_and_push(self.repo_b, "chore: shared edit from B")

        rebase_in_progress = (self.repo_b / ".git" / "rebase-merge").exists() or (
            self.repo_b / ".git" / "rebase-apply"
        ).exists()
        self.assertFalse(rebase_in_progress, "rebase --abort should leave no rebase state behind")

        local_log = sh(self.repo_b, "git", "log", "--oneline", "-1").stdout
        self.assertIn("shared edit from B", local_log, "B's commit must survive locally, not be lost")

        remote_log = sh(self.repo_b, "git", "log", "origin/main", "--oneline", "-1").stdout
        self.assertNotIn("shared edit from B", remote_log, "B's conflicting commit must not force-land on remote")
        self.assertIn("shared edit from A", remote_log)


if __name__ == "__main__":
    unittest.main()
