#!/usr/bin/env python3
"""Tests for ../../scripts/watchcommit.py. Run with: python3 test_watchcommit.py

Uses real temporary git repos rather than mocking subprocess — watchcommit's
whole job is orchestrating git, so a scenario test against real repos catches
what a mocked unit test would miss (e.g. actual rebase/conflict behavior).
"""

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

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
        return sh(
            repo, "git", "rev-list", "--left-right", "--count", "origin/main...HEAD"
        ).stdout.strip()

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
        self.assertFalse(
            rebase_in_progress, "rebase --abort should leave no rebase state behind"
        )

        local_log = sh(self.repo_b, "git", "log", "--oneline", "-1").stdout
        self.assertIn(
            "shared edit from B",
            local_log,
            "B's commit must survive locally, not be lost",
        )

        remote_log = sh(
            self.repo_b, "git", "log", "origin/main", "--oneline", "-1"
        ).stdout
        self.assertNotIn(
            "shared edit from B",
            remote_log,
            "B's conflicting commit must not force-land on remote",
        )
        self.assertIn("shared edit from A", remote_log)


class AgentDetectionTestCase(unittest.TestCase):
    """Unit tests for agent_active() against a fake /proc layout in tmpdir.
    Redirects watchcommit.PROC_DIR and os.getpid via patch."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="wc-proc-"))
        self.fake_proc = self.tmp / "proc"
        self.fake_proc.mkdir()
        self.repo = self.tmp / "repo"
        init_repo(self.repo)
        (self.repo / "seed.txt").write_text("seed\n")
        sh(self.repo, "git", "add", "-A")
        sh(self.repo, "git", "commit", "-q", "-m", "seed")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def _fake_pid(
        self, pid: int, *, cwd: Path, comm: str, environ: bytes = b""
    ) -> None:
        d = self.fake_proc / str(pid)
        d.mkdir()
        # /proc/[pid]/cwd is a symlink — emulate so .resolve() works
        (d / "cwd").symlink_to(cwd)
        (d / "comm").write_text(comm)
        (d / "environ").write_bytes(environ)

    def test_cwd_inside_repo_name_match_detected(self) -> None:
        self._fake_pid(1234, cwd=self.repo, comm="claude")
        with (
            patch.object(watchcommit, "PROC_DIR", self.fake_proc),
            patch("os.getpid", return_value=1),
        ):
            agents = watchcommit.agent_active(self.repo)
        self.assertEqual([a.pid for a in agents], [1234])
        self.assertEqual(agents[0].name, "claude")
        self.assertEqual(agents[0].via, "name")

    def test_cwd_outside_repo_not_detected(self) -> None:
        self._fake_pid(1234, cwd=Path("/tmp"), comm="claude")
        with (
            patch.object(watchcommit, "PROC_DIR", self.fake_proc),
            patch("os.getpid", return_value=1),
        ):
            self.assertEqual(watchcommit.agent_active(self.repo), [])

    def test_env_marker_detected_when_name_not_in_list(self) -> None:
        env = b"WATCHCOMMIT_AGENT=1\x00HOME=/tmp\x00"
        # comm is not in default list
        self._fake_pid(5678, cwd=self.repo, comm="non-agent-name", environ=env)
        with (
            patch.object(watchcommit, "PROC_DIR", self.fake_proc),
            patch("os.getpid", return_value=1),
        ):
            agents = watchcommit.agent_active(self.repo)
        self.assertEqual([a.pid for a in agents], [5678])
        self.assertEqual(agents[0].via, "marker")

    def test_env_marker_exact_match_no_substring_false_positive(self) -> None:
        # WATCHCOMMIT_AGENT=0 must NOT match — exact val comparison
        env = b"WATCHCOMMIT_AGENT=0\x00"
        self._fake_pid(5678, cwd=self.repo, comm="non-agent-name", environ=env)
        with (
            patch.object(watchcommit, "PROC_DIR", self.fake_proc),
            patch("os.getpid", return_value=1),
        ):
            self.assertEqual(watchcommit.agent_active(self.repo), [])

    def test_own_pid_excluded(self) -> None:
        self._fake_pid(9999, cwd=self.repo, comm="claude")
        with (
            patch.object(watchcommit, "PROC_DIR", self.fake_proc),
            patch("os.getpid", return_value=9999),
        ):
            self.assertEqual(watchcommit.agent_active(self.repo), [])

    def test_watchcommit_ignore_agents_env_suppresses(self) -> None:
        self._fake_pid(1234, cwd=self.repo, comm="claude")
        with (
            patch.object(watchcommit, "PROC_DIR", self.fake_proc),
            patch("os.getpid", return_value=1),
            patch.dict(os.environ, {"WATCHCOMMIT_IGNORE_AGENTS": "1"}, clear=False),
        ):
            self.assertEqual(watchcommit.agent_active(self.repo), [])

    def test_agent_names_env_extends_default_list(self) -> None:
        # 'foo' is not in the default list
        self._fake_pid(1234, cwd=self.repo, comm="foo")
        with (
            patch.object(watchcommit, "PROC_DIR", self.fake_proc),
            patch("os.getpid", return_value=1),
            patch.dict(os.environ, {"WATCHCOMMIT_AGENT_NAMES": "foo,bar"}, clear=False),
        ):
            agents = watchcommit.agent_active(self.repo)
        self.assertEqual([a.pid for a in agents], [1234])

    def test_comm_truncation_15_chars(self) -> None:
        # Linux /proc/[pid]/comm stores at most COMM_MAX=15 chars. A
        # registered agent name of length 20 should match by prefix.
        long_name = "supercalafragilistic"
        self._fake_pid(1234, cwd=self.repo, comm=long_name[:15])
        with (
            patch.object(watchcommit, "PROC_DIR", self.fake_proc),
            patch("os.getpid", return_value=1),
            patch.dict(os.environ, {"WATCHCOMMIT_AGENT_NAMES": long_name}, clear=False),
        ):
            agents = watchcommit.agent_active(self.repo)
        self.assertEqual([a.pid for a in agents], [1234])
        self.assertEqual(agents[0].name, long_name)

    def test_proc_read_permission_error_fail_safe(self) -> None:
        # broken cwd symlink — .resolve() raises (or returns the literal),
        # walker should skip not crash
        d = self.fake_proc / "7777"
        d.mkdir()
        (d / "cwd").symlink_to("/nonexistent-target-forbidden")
        (d / "comm").write_text("claude")
        # also add a well-formed agent proc to confirm walker continued past
        self._fake_pid(1234, cwd=self.repo, comm="claude")
        with (
            patch.object(watchcommit, "PROC_DIR", self.fake_proc),
            patch("os.getpid", return_value=1),
        ):
            agents = watchcommit.agent_active(self.repo)
        pids = {a.pid for a in agents}
        self.assertIn(1234, pids)
        self.assertNotIn(7777, pids)

    def test_non_linux_no_proc_returns_empty(self) -> None:
        # point PROC_DIR at a path that doesn't exist
        with patch.object(watchcommit, "PROC_DIR", Path("/no/such/proc")):
            self.assertEqual(watchcommit.agent_active(self.repo), [])


class AgentDetectionIntegrationTestCase(unittest.TestCase):
    """Spawns a real subprocess with the WATCHCOMMIT_AGENT=1 env marker so
    agent_active() sees it via the live /proc. Real claude/opencode sessions
    on the host would also legitimately be detected — that's correct behavior,
    so each test filters the returned list down to its own spawned pid."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="wc-int-"))
        self.repo = self.tmp / "repo"
        init_repo(self.repo)
        (self.repo / "seed.txt").write_text("seed\n")
        sh(self.repo, "git", "add", "-A")
        sh(self.repo, "git", "commit", "-q", "-m", "seed")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.children: list[subprocess.Popen] = []

    def tearDown(self) -> None:
        for p in self.children:
            p.terminate()
            try:
                p.wait(timeout=2)
            except subprocess.TimeoutExpired:
                p.kill()

    def test_real_subprocess_env_marker_detected(self) -> None:
        env = {**os.environ, "WATCHCOMMIT_AGENT": "1"}
        proc = subprocess.Popen(
            ["python3", "-c", "import time; time.sleep(30)"],
            cwd=str(self.repo),
            env=env,
        )
        self.children.append(proc)
        agents = watchcommit.agent_active(self.repo)
        ours = [a for a in agents if a.pid == proc.pid]
        self.assertTrue(ours, f"spawned proc {proc.pid} not detected: {agents}")
        self.assertEqual(ours[0].via, "marker")

    def test_real_subprocess_without_marker_not_detected(self) -> None:
        # plain python3 in repo, no marker, name not in list → not detected
        env = {k: v for k, v in os.environ.items() if k != "WATCHCOMMIT_AGENT"}
        proc = subprocess.Popen(
            ["python3", "-c", "import time; time.sleep(30)"],
            cwd=str(self.repo),
            env=env,
        )
        self.children.append(proc)
        agents = watchcommit.agent_active(self.repo)
        ours = [a for a in agents if a.pid == proc.pid]
        self.assertEqual(
            ours, [], "non-agent python3 subprocess must not trigger detection"
        )


if __name__ == "__main__":
    unittest.main()
