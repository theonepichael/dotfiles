#!/usr/bin/env python3
"""Tests for gen_core_instructions.py.

The shareable core lives in agent-toolkit, not here, so composition is
tested against a fixture git checkout and fixture overlay, and the committed
output is checked only for what this repo alone can vouch for: its overlay
suffix and the absence of pre-migration paths in its core portion. No test
needs a real agent-toolkit checkout -- CI has none.
"""

import contextlib
import fcntl
import io
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).parent))
import gen_core_instructions as gci

pytestmark = pytest.mark.allow_real_subprocess

REPO_ROOT = Path(__file__).resolve().parents[2]
COMMITTED_OUTPUT = REPO_ROOT / "claude" / "global-instructions.md"
COMMITTED_OVERLAY = REPO_ROOT / "claude" / "personal-overlay.md"
CORE_REL = "claude/CORE_INSTRUCTIONS.md"

# Personal-only strings that must never appear in the shareable core (only
# in the composed output, via the overlay).
PERSONAL_ONLY_STRINGS = (
    "iron-lb-",
    "ajhp-",
    "watchcommit",
    "dev_status_sync.py",
)

# Paths that stopped existing when the toolkit moved to ~/.agent-toolkit/.
# The overlay may still name them deliberately (dotfiles' own sync script,
# the legacy data layout), so only the core portion is checked.
PRE_MIGRATION_PATHS = ("~/.claude/scripts/", "~/.claude/data/")

PROVENANCE = re.compile(r"\n<!-- agent-toolkit source: ([0-9a-f]{40}|unknown) -->\n")


def _split_committed() -> tuple[str, str, str]:
    """Return the committed output's (core portion, source, overlay suffix)."""
    text = COMMITTED_OUTPUT.read_text(encoding="utf-8")
    core, marker, rest = text.partition(gci.DO_NOT_EDIT_MARKER)
    if not marker:
        raise AssertionError("committed global-instructions.md has no marker")
    match = PROVENANCE.match(rest)
    if match is None:
        raise AssertionError("no provenance line right after the marker")
    return core, match.group(1), rest[match.end() :]


def git(cwd: Path, *args: str) -> str:
    """Run git in ``cwd`` and return stdout; fail the test on error."""
    result = subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, check=True
    )
    return result.stdout.strip()


class _FixtureTestCase(unittest.TestCase):
    """A fixture agent-toolkit git checkout plus a fixture dotfiles tree."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="gen-core-test-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.checkout = self.tmp / "agent-toolkit"
        (self.checkout / "agent-scripts").mkdir(parents=True)
        (self.checkout / "install.py").write_text("")
        (self.checkout / "links.toml").write_text("")
        self.core = self.checkout / CORE_REL
        self.core.parent.mkdir()
        git(self.checkout, "init", "-q", "-b", "main")
        git(self.checkout, "config", "user.email", "test@test.com")
        git(self.checkout, "config", "user.name", "test")
        self.sha_a = self._commit_core("# CLAUDE.md\n\n## Planning Gate\n\nA.\n")

        self.repo = self.tmp / "dotfiles"
        self.overlay = self.repo / "claude" / "personal-overlay.md"
        self.overlay.parent.mkdir(parents=True)
        self.overlay.write_text("## Personal Policy\n\nuse iron-lb-\n")
        self.output = self.repo / "claude" / "global-instructions.md"

    def _commit_core(self, text: str) -> str:
        self.core.write_text(text)
        git(self.checkout, "add", "-A")
        git(self.checkout, "commit", "-q", "-m", "core")
        return git(self.checkout, "rev-parse", "HEAD")

    def _commit_other(self) -> str:
        (self.checkout / "other.txt").write_text(str(time.time_ns()))
        git(self.checkout, "add", "-A")
        git(self.checkout, "commit", "-q", "-m", "other")
        return git(self.checkout, "rev-parse", "HEAD")

    def _main(self, *args: str) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            status = gci.main(
                [
                    "--repo-root",
                    str(self.repo),
                    "--agent-toolkit-path",
                    str(self.checkout),
                    *args,
                ]
            )
        return status, out.getvalue(), err.getvalue()

    def _recorded(self) -> str:
        match = PROVENANCE.search(self.output.read_text())
        assert match is not None
        return match.group(1)


class ComposeTests(_FixtureTestCase):
    def test_core_then_marker_then_provenance_then_overlay(self) -> None:
        composed = gci.compose_text("core\n\n", "overlay\n", "abc")
        self.assertEqual(
            composed,
            f"core\n\n{gci.DO_NOT_EDIT_MARKER}\n"
            "<!-- agent-toolkit source: abc -->\n\noverlay\n",
        )

    def test_marker_names_the_real_sources(self) -> None:
        self.assertIn("agent-toolkit", gci.DO_NOT_EDIT_MARKER)
        self.assertIn("claude/personal-overlay.md", gci.DO_NOT_EDIT_MARKER)


class ProvenanceTests(_FixtureTestCase):
    def test_records_the_last_core_commit_not_head(self) -> None:
        sha_b = self._commit_core("B\n")
        self._commit_other()
        status, _, err = self._main()
        self.assertEqual(status, 0, err)
        self.assertEqual(self._recorded(), sha_b)

    def test_composes_the_committed_core_not_the_working_tree(self) -> None:
        self.core.write_text("UNCOMMITTED\n")
        self._main()
        text = self.output.read_text()
        self.assertTrue(text.startswith("# CLAUDE.md\n\n## Planning Gate\n\nA."))
        self.assertNotIn("UNCOMMITTED", text)

    def test_core_path_records_unknown(self) -> None:
        loose = self.tmp / "loose.md"
        loose.write_text("loose\n")
        status, _, _ = self._main("--core-path", str(loose))
        self.assertEqual(status, 0)
        self.assertEqual(self._recorded(), "unknown")


class ForwardOnlyTests(_FixtureTestCase):
    def test_moves_forward(self) -> None:
        self._main()
        sha_b = self._commit_core("B\n")
        status, _, err = self._main()
        self.assertEqual(status, 0, err)
        self.assertEqual(self._recorded(), sha_b)

    def test_skips_when_the_checkout_is_behind(self) -> None:
        sha_b = self._commit_core("B\n")
        self._main()
        git(self.checkout, "reset", "-q", "--hard", self.sha_a)
        status, _, err = self._main()
        self.assertEqual(status, 3)
        self.assertIn("skipped", err)
        self.assertEqual(self._recorded(), sha_b)

    def test_skips_an_unknown_recorded_commit(self) -> None:
        self._main()
        text = self.output.read_text().replace(self.sha_a, "f" * 40)
        self.output.write_text(text)
        status, _, _ = self._main()
        self.assertEqual(status, 3)
        self.assertEqual(self._recorded(), "f" * 40)

    def test_a_merge_that_kept_the_old_core_is_not_forward(self) -> None:
        git(self.checkout, "checkout", "-q", "-b", "side")
        sha_b = self._commit_core("B\n")
        git(self.checkout, "checkout", "-q", "main")
        self._commit_other()
        git(self.checkout, "merge", "-q", "-s", "ours", "side", "-m", "ours")
        # HEAD contains B, but the core's last change on HEAD's path is A.
        text = gci.compose_text("x\n", "y\n", sha_b)
        self.output.write_text(text)
        status, _, _ = self._main()
        self.assertEqual(status, 3)
        self.assertEqual(self._recorded(), sha_b)

    def test_skip_is_silent_under_quiet(self) -> None:
        self._commit_core("B\n")
        self._main()
        git(self.checkout, "reset", "-q", "--hard", self.sha_a)
        status, out, err = self._main("-q")
        self.assertEqual((status, out, err), (3, "", ""))

    def test_force_overrides_a_skip(self) -> None:
        self._commit_core("B\n")
        self._main()
        git(self.checkout, "reset", "-q", "--hard", self.sha_a)
        status, _, _ = self._main("--force")
        self.assertEqual(status, 0)
        self.assertEqual(self._recorded(), self.sha_a)

    def test_a_file_without_provenance_is_overwritten(self) -> None:
        self.output.write_text("old composed file, no provenance\n")
        status, _, _ = self._main()
        self.assertEqual(status, 0)
        self.assertEqual(self._recorded(), self.sha_a)

    def test_identical_compose_leaves_the_file_untouched(self) -> None:
        self._main()
        os.utime(self.output, (1, 1))
        status, _, _ = self._main("-q")
        self.assertEqual(status, 0)
        self.assertEqual(self.output.stat().st_mtime, 1)


class ErrorTests(_FixtureTestCase):
    def test_non_git_checkout_exits_2(self) -> None:
        shutil.rmtree(self.checkout / ".git")
        status, _, err = self._main("-q")
        self.assertEqual(status, 2)
        self.assertIn(str(self.checkout), err)

    def test_checkout_without_core_history_exits_2(self) -> None:
        shutil.rmtree(self.checkout / ".git")
        self.core.unlink()
        git(self.checkout, "init", "-q", "-b", "main")
        git(self.checkout, "config", "user.email", "test@test.com")
        git(self.checkout, "config", "user.name", "test")
        self._commit_other()
        self.core.write_text("untracked\n")
        status, _, _ = self._main("-q")
        self.assertEqual(status, 2)

    def test_recorded_object_that_is_not_a_commit_exits_2_even_quiet(self) -> None:
        self._main()
        tree = git(self.checkout, "rev-parse", "HEAD^{tree}")
        self.output.write_text(gci.compose_text("x\n", "y\n", tree))
        status, _, err = self._main("-q")
        self.assertEqual(status, 2)
        self.assertIn(tree, err)

    def test_unresolvable_checkout_exits_2_with_the_reason(self) -> None:
        missing = self.tmp / "nowhere"
        for extra in ((), ("--check",), ("--print-agent-toolkit-path",)):
            out, err = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                status = gci.main(
                    [
                        "--repo-root",
                        str(self.repo),
                        "--agent-toolkit-path",
                        str(missing),
                        *extra,
                    ]
                )
            self.assertEqual(status, 2, extra)
            self.assertEqual(out.getvalue(), "", extra)
            self.assertIn(str(missing), err.getvalue(), extra)
        self.assertFalse(self.output.exists())

    def test_unreadable_overlay_exits_2(self) -> None:
        self.overlay.chmod(0)
        self.addCleanup(self.overlay.chmod, 0o644)
        for extra in ((), ("--check",)):
            status, _, err = self._main(*extra)
            self.assertEqual(status, 2, extra)
            self.assertIn(str(self.overlay), err, extra)


class CliTests(_FixtureTestCase):
    def test_check_reports_stale_without_writing(self) -> None:
        self.output.write_text("stale\n")
        status, _, err = self._main("--check")
        self.assertEqual(status, 1)
        self.assertIn("stale", err)
        self.assertEqual(self.output.read_text(), "stale\n")

    def test_check_is_fresh_after_a_write(self) -> None:
        self._main()
        status, _, _ = self._main("--check")
        self.assertEqual(status, 0)

    def test_stdout_writes_nothing(self) -> None:
        status, out, _ = self._main("--stdout")
        self.assertEqual(status, 0)
        self.assertIn(self.sha_a, out)
        self.assertFalse(self.output.exists())

    def test_write_leaves_no_stray_file_behind(self) -> None:
        self._main()
        names = {p.name for p in self.output.parent.iterdir()}
        self.assertLessEqual(
            names - {"global-instructions.md", "personal-overlay.md"},
            {".global-instructions.md.lock"},
        )

    def test_print_agent_toolkit_path(self) -> None:
        status, out, _ = self._main("--print-agent-toolkit-path")
        self.assertEqual(status, 0)
        self.assertEqual(out, f"{self.checkout.resolve()}\n")


class LockTests(_FixtureTestCase):
    def test_a_write_waits_for_the_lock(self) -> None:
        lock_path = self.output.with_name(".global-instructions.md.lock")
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        done = threading.Event()
        with open(lock_path, "w") as held:
            fcntl.flock(held, fcntl.LOCK_EX)
            worker = threading.Thread(target=lambda: (self._main("-q"), done.set()))
            worker.start()
            time.sleep(0.3)
            self.assertFalse(done.is_set(), "composer wrote while the lock was held")
            self.assertFalse(self.output.exists())
        worker.join(timeout=10)
        self.assertTrue(done.is_set())
        self.assertEqual(self._recorded(), self.sha_a)


SCRIPTS_DIR = Path(__file__).parent
sys.path.insert(0, str(SCRIPTS_DIR.parent.parent / "scripts"))
import watchcommit  # noqa: E402

GI_REL = "claude/global-instructions.md"


class _MergeFixture(_FixtureTestCase):
    """Fixture checkout with core commits A -> B -> C, plus composer helpers."""

    def setUp(self) -> None:
        super().setUp()
        self.sha_b = self._commit_core("# CLAUDE.md\n\n## Planning Gate\n\nB.\n")
        self.sha_c = self._commit_core("# CLAUDE.md\n\n## Planning Gate\n\nC.\n")
        self.overlay_text = self.overlay.read_text()

    def composed(self, sha: str, overlay: str | None = None) -> str:
        core = git(self.checkout, "show", f"{sha}:{CORE_REL}") + "\n"
        return gci.compose_text(core, overlay or self.overlay_text, sha)

    def _driver(self, ours: bytes, theirs: bytes) -> tuple[int, Path, str]:
        base_p, ours_p, theirs_p = (self.tmp / n for n in ("O", "A", "B"))
        base_p.write_bytes(b"base")
        ours_p.write_bytes(ours)
        theirs_p.write_bytes(theirs)
        err = io.StringIO()
        with contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
            status = gci.main(
                [
                    "--agent-toolkit-path",
                    str(self.checkout),
                    "--merge-driver",
                    str(base_p),
                    str(ours_p),
                    str(theirs_p),
                ]
            )
        return status, ours_p, err.getvalue()


class MergeDriverTests(_MergeFixture):
    def test_keeps_ours_when_ours_is_forward(self) -> None:
        ours = self.composed(self.sha_c).encode()
        status, out, _ = self._driver(ours, self.composed(self.sha_b).encode())
        self.assertEqual(status, 0)
        self.assertEqual(out.read_bytes(), ours)

    def test_takes_theirs_when_theirs_is_forward(self) -> None:
        theirs = self.composed(self.sha_c).encode()
        status, out, _ = self._driver(self.composed(self.sha_b).encode(), theirs)
        self.assertEqual(status, 0)
        self.assertEqual(out.read_bytes(), theirs)

    def test_forward_crlf_theirs_is_validated_and_copied_byte_for_byte(self) -> None:
        theirs = self.composed(self.sha_c).replace("\n", "\r\n").encode()
        status, out, err = self._driver(self.composed(self.sha_b).encode(), theirs)
        self.assertEqual(status, 0, err)
        self.assertEqual(out.read_bytes(), theirs)

    def test_identical_sides_merge_even_when_unknown(self) -> None:
        same = gci.compose_text("x\n", "y\n", "unknown").encode()
        status, out, _ = self._driver(same, same)
        self.assertEqual(status, 0)
        self.assertEqual(out.read_bytes(), same)

    def _assert_unresolved(self, ours: bytes, theirs: bytes) -> str:
        status, out, err = self._driver(ours, theirs)
        self.assertEqual(status, 1, err)
        self.assertEqual(out.read_bytes(), ours)
        self.assertTrue(err.strip())
        return err

    def test_same_source_different_bytes_is_unresolved(self) -> None:
        ours = self.composed(self.sha_b).encode()
        self._assert_unresolved(ours, ours + b"extra\n")

    def test_overlay_mismatch_is_unresolved(self) -> None:
        self._assert_unresolved(
            self.composed(self.sha_b).encode(),
            self.composed(
                self.sha_c, overlay="## Personal Policy\n\nedited\n"
            ).encode(),
        )

    def test_diverged_history_is_unresolved(self) -> None:
        git(self.checkout, "checkout", "-q", "-b", "side", self.sha_a)
        sha_d = self._commit_core("D\n")
        git(self.checkout, "checkout", "-q", "main")
        self._assert_unresolved(
            self.composed(self.sha_c).encode(), self.composed(sha_d).encode()
        )

    def test_missing_or_unknown_source_is_unresolved(self) -> None:
        missing = gci.compose_text("x\n", self.overlay_text, "f" * 40).encode()
        unknown = gci.compose_text("x\n", self.overlay_text, "unknown").encode()
        for other in (missing, unknown):
            self._assert_unresolved(self.composed(self.sha_b).encode(), other)

    def test_unparseable_side_is_unresolved(self) -> None:
        self._assert_unresolved(b"not composed\n", self.composed(self.sha_b).encode())

    def test_unresolvable_checkout_is_unresolved(self) -> None:
        ours = self.composed(self.sha_b).encode()
        theirs = self.composed(self.sha_c).encode()
        shutil.rmtree(self.checkout / ".git")
        self._assert_unresolved(ours, theirs)

    def test_hand_edited_core_with_a_valid_older_sha_is_unresolved(self) -> None:
        edited = self.composed(self.sha_b).replace("B.", "B, hand-edited.")
        self._assert_unresolved(self.composed(self.sha_c).encode(), edited.encode())


class _GitDotfilesFixture(_MergeFixture):
    """The fixture dotfiles tree as a git repo, so registration applies."""

    def setUp(self) -> None:
        super().setUp()
        git(self.repo, "init", "-q", "-b", "main")

    def _driver_config(self) -> str:
        result = subprocess.run(
            [
                "git",
                "-C",
                str(self.repo),
                "config",
                "--local",
                "--get",
                "merge.global-instructions.driver",
            ],
            capture_output=True,
            text=True,
        )
        return result.stdout.strip()


class RegistrationTests(_GitDotfilesFixture):
    def test_write_registers_the_driver_with_python_and_checkout(self) -> None:
        self._main("-q")
        self.assertEqual(
            self._driver_config(),
            f"'{sys.executable}' claude/scripts/gen_core_instructions.py "
            f"--agent-toolkit-path '{self.checkout.resolve()}' "
            "--merge-driver %O %A %B",
        )

    def test_registration_is_idempotent_and_replaces_a_stale_command(self) -> None:
        git(self.repo, "config", "merge.global-instructions.driver", "stale")
        self._main("-q")
        first = self._driver_config()
        self._main("-q")
        self.assertEqual(self._driver_config(), first)
        self.assertNotEqual(first, "stale")

    def test_an_up_to_date_run_still_refreshes_registration(self) -> None:
        self._main("-q")
        git(self.repo, "config", "merge.global-instructions.driver", "stale")
        status, _, _ = self._main("-q")
        self.assertEqual(status, 0)
        self.assertNotEqual(self._driver_config(), "stale")

    def test_config_failure_refuses_the_write(self) -> None:
        (self.repo / ".git" / "config.lock").write_text("held")
        status, _, err = self._main("-q")
        self.assertEqual(status, 2)
        self.assertIn("merge driver", err)
        self.assertFalse(self.output.exists())

    def test_quote_in_a_path_is_refused_and_never_written(self) -> None:
        with self.assertRaises(gci.ComposeError):
            gci.register_merge_driver(self.repo, "/opt/it's/python3", self.checkout)
        self.assertEqual(self._driver_config(), "")

    def test_check_and_stdout_never_register(self) -> None:
        self._main("--check")
        self._main("--stdout")
        self.assertEqual(self._driver_config(), "")

    def test_a_non_git_repo_root_writes_without_registering(self) -> None:
        shutil.rmtree(self.repo / ".git")
        status, _, _ = self._main("-q")
        self.assertEqual(status, 0)
        self.assertTrue(self.output.exists())


class RebaseRaceTests(_MergeFixture):
    """Two dotfiles clones racing to commit different recompositions."""

    def setUp(self) -> None:
        super().setUp()
        remote = self.tmp / "remote.git"
        remote.mkdir()
        git(remote, "init", "-q", "--bare", "-b", "main")
        seed = self.tmp / "seed"
        (seed / "claude" / "scripts").mkdir(parents=True)
        for name in ("gen_core_instructions.py", "dotfiles_cli_common.py"):
            shutil.copy(SCRIPTS_DIR / name, seed / "claude" / "scripts" / name)
        (seed / "claude" / "personal-overlay.md").write_text(self.overlay_text)
        (seed / ".gitattributes").write_text((REPO_ROOT / ".gitattributes").read_text())
        (seed / GI_REL).write_text(self.composed(self.sha_a))
        (seed / "notes.txt").write_text("notes\n")
        git(seed, "init", "-q", "-b", "main")
        git(seed, "config", "user.email", "t@t")
        git(seed, "config", "user.name", "t")
        git(seed, "add", "-A")
        git(seed, "commit", "-q", "-m", "seed")
        git(seed, "remote", "add", "origin", str(remote))
        git(seed, "push", "-q", "-u", "origin", "main")
        self.clones = []
        for name in ("one", "two"):
            clone = self.tmp / name
            git(self.tmp, "clone", "-q", str(remote), str(clone))
            git(clone, "config", "user.email", f"{name}@t")
            git(clone, "config", "user.name", name)
            self.clones.append(clone)
        # The driver must find the checkout through its registered command.
        self.enterContext(patch.dict(os.environ))
        os.environ.pop("AGENT_TOOLKIT_PATH", None)

    def _commit_composition(
        self, clone: Path, sha: str, *, extra: bool = False
    ) -> None:
        (clone / GI_REL).write_text(self.composed(sha))
        if extra:
            (clone / "notes.txt").write_text("notes\nlocal edit\n")
        git(clone, "commit", "-q", "-am", f"recompose {sha[:7]}")

    def _register(self, clone: Path) -> None:
        gci.register_merge_driver(clone, sys.executable, self.checkout)

    def _rebase(self, clone: Path) -> bool:
        with contextlib.redirect_stderr(io.StringIO()):
            return watchcommit.rebase_onto_remote(clone)

    def _recorded_in(self, clone: Path) -> str | None:
        return gci.recorded_source((clone / GI_REL).read_text())

    def test_local_forward_wins(self) -> None:
        one, two = self.clones
        self._commit_composition(one, self.sha_b)
        git(one, "push", "-q")
        self._register(two)
        self._commit_composition(two, self.sha_c)
        self.assertTrue(self._rebase(two))
        self.assertEqual(self._recorded_in(two), self.sha_c)
        git(two, "push", "-q")

    def test_remote_forward_wins_and_the_stale_commit_is_dropped(self) -> None:
        one, two = self.clones
        self._commit_composition(one, self.sha_c)
        git(one, "push", "-q")
        self._register(two)
        self._commit_composition(two, self.sha_b)
        self.assertTrue(self._rebase(two))
        self.assertEqual(self._recorded_in(two), self.sha_c)
        self.assertEqual(
            git(two, "rev-parse", "HEAD"), git(two, "rev-parse", "origin/main")
        )

    def test_without_registration_it_still_conflicts(self) -> None:
        one, two = self.clones
        self._commit_composition(one, self.sha_b)
        git(one, "push", "-q")
        self._commit_composition(two, self.sha_c)
        self.assertFalse(self._rebase(two))

    def test_mixed_commit_keeps_its_unrelated_change(self) -> None:
        one, two = self.clones
        self._commit_composition(one, self.sha_c)
        git(one, "push", "-q")
        self._register(two)
        self._commit_composition(two, self.sha_b, extra=True)
        self.assertTrue(self._rebase(two))
        self.assertEqual(self._recorded_in(two), self.sha_c)
        self.assertIn("local edit", (two / "notes.txt").read_text())
        self.assertEqual(git(two, "rev-list", "--count", "origin/main..HEAD"), "1")

    def test_apply_backend_config_does_not_bypass_the_driver(self) -> None:
        one, two = self.clones
        self._commit_composition(one, self.sha_b)
        git(one, "push", "-q")
        self._register(two)
        git(two, "config", "rebase.backend", "apply")
        self._commit_composition(two, self.sha_c)
        self.assertTrue(self._rebase(two))
        self.assertEqual(self._recorded_in(two), self.sha_c)


class CommittedOutputTests(unittest.TestCase):
    def test_dotfiles_keeps_no_copy_of_the_core(self) -> None:
        self.assertFalse((REPO_ROOT / "claude" / "CORE_INSTRUCTIONS.md").exists())

    def test_committed_output_records_a_real_source_commit(self) -> None:
        _, source, _ = _split_committed()
        self.assertRegex(source, r"^[0-9a-f]{40}$")

    def test_core_portion_has_no_pre_migration_path(self) -> None:
        core, _, _ = _split_committed()
        for path in PRE_MIGRATION_PATHS:
            self.assertNotIn(path, core, f"core portion still references {path}")

    def test_core_portion_has_no_personal_only_string(self) -> None:
        core, _, _ = _split_committed()
        for needle in PERSONAL_ONLY_STRINGS:
            self.assertNotIn(needle, core, f"core leaks personal-only {needle!r}")

    def test_overlay_suffix_matches_committed_overlay(self) -> None:
        """Catches an overlay edit committed without regenerating."""
        _, _, suffix = _split_committed()
        overlay = COMMITTED_OVERLAY.read_text(encoding="utf-8").rstrip("\n")
        self.assertEqual(suffix, f"\n{overlay}\n")


if __name__ == "__main__":
    unittest.main()
