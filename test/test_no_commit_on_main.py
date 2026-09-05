#!/usr/bin/env python3
"""Integration tests for githooks-global/lib/no-commit-on-main.sh.

The lib is sourced by both the dotfiles repo's own githooks/pre-commit and
the global core.hooksPath pre-commit, so its contract is verified at the
same boundary git actually invokes it: a real repo, a real hook exec. The
watchcommit daemon exemption (WATCHCOMMIT_DAEMON=1) is the load-bearing
case — it must open the bypass for the daemon's process tree only, and
stay shut for every other value.
"""

import os
import subprocess
from pathlib import Path

import pytest

LIB = (
    Path(__file__).resolve().parent.parent
    / "githooks-global"
    / "lib"
    / "no-commit-on-main.sh"
)

# Real git repos under tmp_path and a real hook exec — the guard's whole
# point is behavior after the shell has already run, which a mocked
# subprocess cannot show. Nothing outside tmp_path is touched.
pytestmark = pytest.mark.allow_real_subprocess


def _git(
    repo: Path, *args: str, env: dict | None = None
) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        env=env,
    )


def _init_repo(path: Path) -> Path:
    path.mkdir(parents=True)
    _git(path, "init", "-q", "-b", "main")
    _git(path, "config", "user.email", "test@test.com")
    _git(path, "config", "user.name", "test")
    return path


def _install_hook(repo: Path) -> None:
    hooks = repo.parent / f"hooks-{repo.name}"
    hooks.mkdir(exist_ok=True)
    script = f'#!/bin/sh\n. "{LIB}"\nrefuse_if_protected_branch\n'
    (hooks / "pre-commit").write_text(script)
    (hooks / "pre-commit").chmod(0o755)
    _git(repo, "config", "core.hooksPath", str(hooks))


def _attempt_commit(
    repo: Path, env_extra: dict[str, str] | None = None
) -> subprocess.CompletedProcess:
    (repo / "f.txt").write_text("x\n")
    _git(repo, "add", "-A")
    env = {**os.environ, **(env_extra or {})}
    return _git(repo, "commit", "-m", "test commit", env=env)


def _seed(repo: Path) -> None:
    (repo / "seed.txt").write_text("seed\n")
    _git(repo, "add", "-A")
    # Seed commit before the hook is installed, so seeding itself never
    # trips the guard.
    assert _git(repo, "commit", "-q", "-m", "seed").returncode == 0


def test_commit_on_main_with_history_blocked(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "repo")
    _seed(repo)
    _install_hook(repo)

    result = _attempt_commit(repo)

    assert result.returncode != 0
    assert "direct commits to 'main' are blocked" in result.stderr


def test_daemon_marker_commit_on_main_allowed(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "repo")
    _seed(repo)
    _install_hook(repo)

    result = _attempt_commit(repo, {"WATCHCOMMIT_DAEMON": "1"})

    assert result.returncode == 0, result.stderr
    assert "test commit" in _git(repo, "log", "--oneline").stdout


def test_wrong_marker_value_still_blocked(tmp_path: Path) -> None:
    # Exact-value contract: WATCHCOMMIT_DAEMON=0 bypasses nothing.
    repo = _init_repo(tmp_path / "repo")
    _seed(repo)
    _install_hook(repo)

    result = _attempt_commit(repo, {"WATCHCOMMIT_DAEMON": "0"})

    assert result.returncode != 0


def test_feature_branch_allowed(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "repo")
    _seed(repo)
    _install_hook(repo)
    _git(repo, "checkout", "-q", "-b", "feature")

    result = _attempt_commit(repo)

    assert result.returncode == 0, result.stderr


def test_first_commit_on_unborn_main_allowed(tmp_path: Path) -> None:
    # The very first commit lands on main by construction — the lib allows
    # it when the branch has no history yet.
    repo = _init_repo(tmp_path / "repo")
    _install_hook(repo)

    result = _attempt_commit(repo)

    assert result.returncode == 0, result.stderr
