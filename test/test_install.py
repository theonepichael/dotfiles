#!/usr/bin/env python3
"""Fast-tier tests for install.py — no packages, no subprocesses, no real HOME.

Run with: ``uv run --with pytest pytest test/test_install.py`` (or
``pytest test/test_install.py`` in an environment that already has pytest).

Everything here runs against a throwaway home directory under ``tmp_path``
and a stubbed :func:`install.run_command`, so nothing touches the machine.
The container-based lifecycle suite (``test/run.sh`` → ``scenarios.sh``)
still owns the slow tier: real package managers, real service enablement.

Requires Python 3.12+.
"""

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

import depart  # noqa: E402 — must follow sys.path.insert above
import install  # noqa: E402 — must follow sys.path.insert above

# ── fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def no_subprocesses(monkeypatch):
    """Fail loudly if any test path tries to shell out for real."""

    def _forbidden(*args, **kwargs):
        raise AssertionError(f"unexpected subprocess call: {args!r}")

    monkeypatch.setattr(install, "run_command", _forbidden)


@pytest.fixture
def home(tmp_path):
    """A throwaway home directory, handed to Context instead of the real one.

    Context carries its home explicitly (rather than calling Path.home()
    per use), so pointing it here is enough to keep every test off the real
    machine — no HOME monkeypatching needed.
    """
    path = tmp_path / "home"
    path.mkdir()
    return path


def make_ctx(
    home,
    *,
    harnesses=("claude",),
    profile="personal",
    dry_run=False,
    force=False,
    wipe=False,
    no_nvim_pin=False,
    reseed=False,
    system="Linux",
    is_wsl=False,
    dotfiles=REPO_ROOT,
):
    """Build a Context pointed at a throwaway home and the real repo."""
    opts = install.Options(
        harnesses=tuple(harnesses),
        profile=profile,
        dry_run=dry_run,
        force=force,
        wipe=wipe,
        no_nvim_pin=no_nvim_pin,
        reseed=reseed,
    )
    state_dir = home / ".local" / "state" / "dotfiles"
    return install.Context(
        dotfiles=dotfiles,
        home=home,
        opts=opts,
        manifest=install.Manifest(state_dir / "history.jsonl", dry_run=dry_run),
        reporter=install.Reporter(),
        system=system,
        is_wsl=is_wsl,
    )


def history(ctx):
    """Return the recorded history entries, oldest first."""
    if not ctx.manifest.path.is_file():
        return []
    return ctx.manifest.entries()


def kinds(ctx, kind):
    """Return every history entry of one kind."""
    return [e for e in history(ctx) if e["kind"] == kind]


@pytest.fixture
def offline_install(monkeypatch):
    """Neuter every step that would install software or touch a service.

    Leaves the file-level steps (symlinks, seeds, marker, history) real, so
    a full ``run_install`` can be exercised end to end.
    """
    for name in (
        "install_mac_packages",
        "install_linux_packages",
        "install_node",
        "enable_watchcommit_service",
        "load_watchcommit_agent",
        "import_rectangle_prefs",
        "set_caps_lock_to_escape",
        "install_vim_plug",
        "bootstrap_neovim",
    ):
        monkeypatch.setattr(install, name, lambda *a, **k: None)
    monkeypatch.setattr(install, "install_npm_harness", lambda *a, **k: None)


@pytest.fixture
def links():
    """The repo's real links.toml, parsed."""
    return install.load_links(REPO_ROOT / "links.toml")


# ── CLI validation ────────────────────────────────────────────────────────────


def parse_error(argv, capsys):
    """Run parse_args expecting a refusal; return (exit code, stderr)."""
    with pytest.raises(SystemExit) as excinfo:
        install.parse_args(argv)
    return excinfo.value.code, capsys.readouterr().err


@pytest.mark.parametrize(
    ("argv", "expected"),
    [
        (["--harness="], "empty value"),
        (["--harness=claude,"], "empty value"),
        (["--harness=bogus"], "unknown harness: bogus"),
        ([], "no --harness specified"),
        (["--bogus"], "unknown argument: --bogus"),
        # The pre-harness flags were a hard cutover with no back-compat
        # shims, so they fall through to the generic unknown-argument path.
        (["--work"], "unknown argument: --work"),
        (["--copilot"], "unknown argument: --copilot"),
        (["--profile=nope", "--harness=claude"], "invalid --profile: nope"),
        (
            ["--profile=work", "--harness=opencode"],
            "--harness=opencode is not allowed with --profile=work",
        ),
        (
            ["--profile=work", "--harness=copilot,opencode"],
            "--harness=opencode is not allowed with --profile=work",
        ),
        (["--rollback", "--harness=claude"], "must be used alone"),
        (["--rollback", "--profile=work"], "must be used alone"),
        (["--rollback", "--force"], "must be used alone"),
        (["--rollback", "--reseed"], "must be used alone"),
        (["--reseed"], "no --harness specified"),
        # No --harness alongside --wipe, deliberately: exercises the
        # parse_args ordering fix (the --wipe-without-rollback check must
        # fire before the missing-harness check, not after).
        (["--wipe"], "--wipe can only be used with --rollback"),
    ],
)
def test_argument_errors_exit_2(argv, expected, capsys):
    code, err = parse_error(argv, capsys)
    assert code == 2
    assert expected in err


def test_help_exits_zero_with_usage(capsys):
    with pytest.raises(SystemExit) as excinfo:
        install.parse_args(["--help"])
    assert excinfo.value.code == 0
    assert capsys.readouterr().out.startswith("usage:")


def test_unknown_argument_reported_before_bad_value(capsys):
    """A typo'd flag names the typo, not the downstream validation failure."""
    _, err = parse_error(["--bogus", "--profile=nope"], capsys)
    assert "unknown argument: --bogus" in err
    assert "invalid --profile" not in err


def test_repeated_harness_flags_accumulate():
    opts = install.parse_args(["--harness=claude", "--harness=copilot"])
    assert opts.harnesses == ("claude", "copilot")


def test_comma_separated_harnesses():
    opts = install.parse_args(["--harness=claude,opencode,agy"])
    assert opts.harnesses == ("claude", "opencode", "agy")


def test_duplicate_harness_is_collapsed():
    opts = install.parse_args(["--harness=claude,claude", "--harness=claude"])
    assert opts.harnesses == ("claude",)


def test_rollback_allows_only_dry_run():
    opts = install.parse_args(["--rollback", "--dry-run"])
    assert opts.rollback and opts.dry_run and opts.harnesses == ()


def test_rollback_wipe_parses():
    opts = install.parse_args(["--rollback", "--wipe"])
    assert opts.rollback and opts.wipe and not opts.dry_run


def test_rollback_wipe_dry_run_parses():
    opts = install.parse_args(["--rollback", "--wipe", "--dry-run"])
    assert opts.rollback and opts.wipe and opts.dry_run


def test_defaults():
    opts = install.parse_args(["--harness=claude"])
    assert opts.profile == "personal"
    assert not (opts.rollback or opts.force or opts.dry_run or opts.no_nvim_pin)


def test_no_nvim_pin_flag_parses():
    opts = install.parse_args(["--harness=claude", "--no-nvim-pin"])
    assert opts.no_nvim_pin


def test_reseed_flag_parses():
    opts = install.parse_args(["--harness=claude", "--reseed"])
    assert opts.reseed


# ── links.toml ────────────────────────────────────────────────────────────────


def test_links_table_parses_and_sources_exist(links):
    assert links, "links.toml should not be empty"
    for spec in links:
        assert (REPO_ROOT / spec.src).exists(), f"missing repo source: {spec.src}"
        assert spec.dest.startswith("~/")


def test_every_claude_script_has_a_links_entry(links):
    """Every production (non-test) script in claude/scripts/ must have a
    links.toml entry, or ~/.claude/scripts/<name> silently never exists at
    install time -- caught live twice already (test_dev_status.py's
    pre-existing manual symlink papering over a missing entry, then
    vitals_promotion.py shipping with no entry at all, found via a live
    grill-me spot-check). Test files are excluded: they're always run
    in-repo (``python3 test_X.py`` from claude/scripts/), never invoked via
    the deployed ~/.claude/scripts/ path by any skill or production script.
    """
    linked_srcs = {spec.src for spec in links}
    scripts = sorted((REPO_ROOT / "claude" / "scripts").glob("*.py"))
    production_scripts = [p for p in scripts if not p.name.startswith("test_")]
    missing = [
        p.name
        for p in production_scripts
        if f"claude/scripts/{p.name}" not in linked_srcs
    ]
    assert not missing, f"claude/scripts scripts missing a links.toml entry: {missing}"


def test_no_test_file_has_a_links_entry(links):
    """The inverse of the coverage rule above: three test files
    (test_dev_status.py, test_dev_status_sync.py, test_vitals_promotion.py)
    had accumulated links.toml entries with no functional reason -- nothing
    in the repo ever invokes a test via the deployed ~/.claude/scripts/
    path, only via ``python3 test_X.py`` inside the checkout. Removed as
    unnecessary state; this guards against the pattern creeping back."""
    linked_test_files = [
        spec.src
        for spec in links
        if spec.src.startswith("claude/scripts/test_") and spec.src.endswith(".py")
    ]
    assert not linked_test_files, (
        f"unnecessary test-file links.toml entries: {linked_test_files}"
    )


def test_claude_script_links_use_correct_dest(links):
    """A typo'd dest is as broken as a missing entry -- the src still
    exists so test_links_table_parses_and_sources_exist won't catch it, but
    ~/.claude/scripts/<name> never gets the right symlink."""
    for spec in links:
        if spec.src.startswith("claude/scripts/") and spec.src.endswith(".py"):
            name = Path(spec.src).name
            assert spec.dest == f"~/.claude/scripts/{name}", spec.src


def test_links_reject_unknown_key(tmp_path):
    bad = tmp_path / "links.toml"
    bad.write_text('[[link]]\nsrc = "a"\ndest = "~/a"\nharnes = "claude"\n')
    with pytest.raises(ValueError, match="unknown key"):
        install.load_links(bad)


def test_links_reject_unknown_harness(tmp_path):
    bad = tmp_path / "links.toml"
    bad.write_text('[[link]]\nsrc = "a"\ndest = "~/a"\nharness = "nope"\n')
    with pytest.raises(ValueError, match="unknown harness"):
        install.load_links(bad)


def test_harness_gate(home, links):
    claude_only = make_ctx(home, harnesses=("claude",))
    dests = {s.dest for s in links if install.link_applies(s, claude_only)}
    assert "~/.claude/CLAUDE.md" in dests
    assert "~/.copilot/copilot-instructions.md" not in dests
    assert "~/.gemini/GEMINI.md" not in dests
    # Shared scripts stay linked no matter which harness was picked.
    assert "~/.claude/scripts/dev_status.py" in dests


def test_platform_and_profile_gates(home, links):
    linux_personal = make_ctx(home, system="Linux")
    dests = {s.dest for s in links if install.link_applies(s, linux_personal)}
    assert "~/.config/systemd/user/watchcommit.service" in dests
    assert "~/.claude/scripts/watchcommit_activity.py" in dests
    assert "~/.zprofile" not in dests
    assert "~/.config/Code/User/settings.json" in dests

    linux_work = make_ctx(home, system="Linux", profile="work")
    work_dests = {s.dest for s in links if install.link_applies(s, linux_work)}
    assert "~/.local/bin/watchcommit" not in work_dests
    assert "~/.config/systemd/user/watchcommit.service" not in work_dests
    assert "~/.claude/scripts/watchcommit_activity.py" not in work_dests

    mac = make_ctx(home, system="Darwin")
    mac_dests = {s.dest for s in links if install.link_applies(s, mac)}
    assert "~/.zprofile" in mac_dests
    assert "~/Library/LaunchAgents/com.user.watchcommit.plist" in mac_dests
    assert "~/.config/systemd/user/watchcommit.service" not in mac_dests

    wsl = make_ctx(home, system="Linux", is_wsl=True)
    wsl_dests = {s.dest for s in links if install.link_applies(s, wsl)}
    assert "~/.config/Code/User/settings.json" not in wsl_dests


def test_copilot_hook_platform_split(home, links):
    """The Copilot sessionStart hook is bash-only, so links.toml carries two
    same-destination entries gated mac/linux (the VS Code mutually-exclusive
    platform pattern) instead of one unqualified entry: on any given machine
    exactly one applies, and nothing installs the hook where the bash
    handler could not dispatch."""
    hook = "~/.copilot/hooks/session-start.json"
    entries = [s for s in links if s.dest == hook]
    assert len(entries) == 2
    assert {s.platform for s in entries} == {"mac", "linux"}
    assert all(s.harness == "copilot" for s in entries)

    for system in ("Linux", "Darwin"):
        ctx = make_ctx(home, harnesses=("copilot",), system=system)
        applying = [s for s in entries if install.link_applies(s, ctx)]
        assert len(applying) == 1, system
    # No copilot in --harness: neither entry links, on either platform.
    for system in ("Linux", "Darwin"):
        ctx = make_ctx(home, harnesses=("claude",), system=system)
        assert not any(install.link_applies(s, ctx) for s in entries)


def test_expand_dest(home):
    assert install.expand_dest("~/.vimrc", home) == home / ".vimrc"
    assert install.expand_dest("/etc/hosts", home) == Path("/etc/hosts")


# ── symlink engine ────────────────────────────────────────────────────────────


def test_symlink_creates_and_records(home):
    ctx = make_ctx(home)
    src = REPO_ROOT / "vim" / ".vimrc"
    dest = home / ".vimrc"

    assert install.symlink(ctx, src, dest)

    assert dest.is_symlink()
    assert Path(str(dest.resolve())) == src.resolve()
    assert kinds(ctx, "symlink-created") == [
        {"kind": "symlink-created", "dest": str(dest), "src": str(src)}
    ]


def test_symlink_backs_up_existing_file(home):
    ctx = make_ctx(home)
    src = REPO_ROOT / "vim" / ".vimrc"
    dest = home / ".vimrc"
    dest.write_text("sentinel-content\n")

    install.symlink(ctx, src, dest)

    assert dest.is_symlink()
    backup = home / ".vimrc.bak"
    assert backup.read_text() == "sentinel-content\n"
    assert kinds(ctx, "file-backed-up") == [
        {"kind": "file-backed-up", "dest": str(dest), "backup": str(backup)}
    ]
    assert len(kinds(ctx, "symlink-created")) == 1


def test_symlink_rerun_is_a_noop_and_not_rerecorded(home):
    ctx = make_ctx(home)
    src = REPO_ROOT / "vim" / ".vimrc"
    dest = home / ".vimrc"

    install.symlink(ctx, src, dest)
    install.symlink(ctx, src, dest)

    assert len(kinds(ctx, "symlink-created")) == 1


def test_symlink_replaces_a_link_pointing_elsewhere(home):
    ctx = make_ctx(home)
    src = REPO_ROOT / "vim" / ".vimrc"
    dest = home / ".vimrc"
    dest.symlink_to("/etc/hostname")

    install.symlink(ctx, src, dest)

    assert Path(str(dest.readlink())) == src
    # It was already a symlink, so the run that first created it owns the
    # history entry — this one must not add a second.
    assert kinds(ctx, "symlink-created") == []


def test_symlink_dry_run_changes_nothing(home, capsys):
    ctx = make_ctx(home, dry_run=True)
    src = REPO_ROOT / "vim" / ".vimrc"
    dest = home / ".vimrc"

    install.symlink(ctx, src, dest)

    assert not dest.exists()
    assert not ctx.manifest.path.exists()
    assert "would link" in capsys.readouterr().out


def test_symlink_directory_source(home):
    """A symlinked directory re-links in place, never nesting inside itself."""
    ctx = make_ctx(home)
    src = REPO_ROOT / "nvim"
    dest = home / ".config" / "nvim"

    install.symlink(ctx, src, dest)
    install.symlink(ctx, src, dest)

    assert dest.is_symlink()
    assert not (src / "nvim").exists()


# ── copy-once seeds and drift ─────────────────────────────────────────────────


def test_settings_seed_copied_once_then_reports_drift(home):
    ctx = make_ctx(home, harnesses=("claude",))
    seed_name, drift = install.seed_claude_settings(ctx)

    dest = home / ".claude" / "settings.json"
    assert seed_name == "settings.json"
    assert drift == ""
    assert dest.is_file() and not dest.is_symlink()
    assert dest.read_text() == (REPO_ROOT / "claude" / "settings.json").read_text()
    assert kinds(ctx, "file-copied") == [{"kind": "file-copied", "dest": str(dest)}]

    # Second run: the live file is edited, and must be reported, not replaced.
    live = json.loads(dest.read_text())
    live["model"] = "some-other-model"
    dest.write_text(json.dumps(live))

    ctx2 = make_ctx(home, harnesses=("claude",))
    _, drift2 = install.seed_claude_settings(ctx2)
    assert "model" in drift2
    assert json.loads(dest.read_text())["model"] == "some-other-model"
    # Still only run 1's copy on record — the second run reported, not rewrote.
    assert len(kinds(ctx2, "file-copied")) == 1


def test_work_profile_uses_the_work_seed(home):
    ctx = make_ctx(home, harnesses=("claude",), profile="work")
    seed_name, _ = install.seed_claude_settings(ctx)
    assert seed_name == "settings.work.json"
    assert (home / ".claude" / "settings.json").read_text() == (
        REPO_ROOT / "claude" / "settings.work.json"
    ).read_text()


def test_settings_seed_skipped_when_claude_not_selected(home):
    ctx = make_ctx(home, harnesses=("opencode",))
    assert install.seed_claude_settings(ctx) == ("", "")
    assert not (home / ".claude" / "settings.json").exists()


def test_opencode_seed_has_no_work_variant(home):
    """opencode is excluded from --profile=work at parse_args (see the CLI
    validation test) — this documents that seed_opencode_config itself has
    no separate work variant to accidentally seed either, even if a
    work-profile opencode Context were ever constructed directly."""
    personal = make_ctx(home, harnesses=("opencode",))
    name, drift = install.seed_opencode_config(personal)
    dest = home / ".config" / "opencode" / "opencode.jsonc"
    assert (name, drift) == ("opencode.jsonc", "")
    assert dest.read_text() == (REPO_ROOT / "opencode" / "opencode.jsonc").read_text()

    other_home = home.parent / "home2"
    other_home.mkdir()
    work = make_ctx(other_home, harnesses=("opencode",), profile="work")
    name, _ = install.seed_opencode_config(work)
    assert name == "opencode.jsonc"


def test_opencode_bypass_check_fires_specifically(home):
    """xargs/awk reappearing live is a SECURITY line, not generic drift."""
    ctx = make_ctx(home, harnesses=("opencode",))
    install.seed_opencode_config(ctx)

    dest = home / ".config" / "opencode" / "opencode.jsonc"
    live = json.loads(dest.read_text())
    live["permission"]["bash"]["xargs *"] = "allow"
    live["permission"]["bash"]["awk *"] = "allow"
    dest.write_text(json.dumps(live))

    _, drift = install.seed_opencode_config(make_ctx(home, harnesses=("opencode",)))
    assert drift.startswith("SECURITY: xargs *, awk *")
    assert "allowlist bypass" in drift


def test_opencode_generic_drift_when_no_bypass(home):
    ctx = make_ctx(home, harnesses=("opencode",))
    install.seed_opencode_config(ctx)

    dest = home / ".config" / "opencode" / "opencode.jsonc"
    live = json.loads(dest.read_text())
    live["theme"] = "custom"
    dest.write_text(json.dumps(live))

    _, drift = install.seed_opencode_config(make_ctx(home, harnesses=("opencode",)))
    assert drift == "theme"


def test_drift_helpers_on_raw_dicts():
    assert install.json_key_drift({"a": 1, "b": 2}, {"a": 1, "b": 3}) == ["b"]
    assert install.opencode_bypass_drift(
        {"permission": {"bash": {}}},
        {"permission": {"bash": {"awk *": "allow"}}},
    ) == ["awk *"]
    assert install.opencode_bypass_drift({}, {}) == []


# ── --reseed ─────────────────────────────────────────────────────────────────


def _raise_oserror(*args, **kwargs):
    raise OSError("simulated failure")


def test_manifest_entries_and_has_backup_with_no_history_file(home):
    """Eval criterion 13: a fresh machine has no history.jsonl yet."""
    ctx = make_ctx(home, harnesses=("claude",), reseed=True)
    assert not ctx.manifest.path.exists()
    assert ctx.manifest.entries() == []
    assert ctx.manifest.has_backup(home / ".claude" / "settings.json") is False


def test_reseed_backs_up_and_overwrites_drifted_file(home):
    """Eval criterion 1."""
    dest = home / ".claude" / "settings.json"
    dest.parent.mkdir(parents=True)
    live = json.loads((REPO_ROOT / "claude" / "settings.json").read_text())
    live["model"] = "drifted-value"
    dest.write_text(json.dumps(live))

    ctx = make_ctx(home, harnesses=("claude",), reseed=True)
    _, drift = install.seed_claude_settings(ctx)

    backup = home / ".claude" / "settings.json.bak"
    assert drift == ""
    assert json.loads(backup.read_text())["model"] == "drifted-value"
    assert dest.read_text() == (REPO_ROOT / "claude" / "settings.json").read_text()
    assert kinds(ctx, "file-backed-up") == [
        {"kind": "file-backed-up", "dest": str(dest), "backup": str(backup)}
    ]
    assert kinds(ctx, "file-copied") == [{"kind": "file-copied", "dest": str(dest)}]


def test_reseed_when_dest_missing_is_a_plain_copy(home):
    """Eval criterion 2: identical to today's behavior, no new code path."""
    ctx = make_ctx(home, harnesses=("claude",), reseed=True)
    _, drift = install.seed_claude_settings(ctx)

    dest = home / ".claude" / "settings.json"
    assert drift == ""
    assert dest.is_file()
    assert not (home / ".claude" / "settings.json.bak").exists()
    assert kinds(ctx, "file-backed-up") == []
    assert kinds(ctx, "file-copied") == [{"kind": "file-copied", "dest": str(dest)}]


def test_reseed_noop_when_drift_is_empty(home):
    """Eval criterion 3: real text drift but no *value* drift is still a no-op."""
    ctx = make_ctx(home, harnesses=("claude",))
    install.seed_claude_settings(ctx)

    dest = home / ".claude" / "settings.json"
    seed_path = REPO_ROOT / "claude" / "settings.json"
    values = json.loads(seed_path.read_text())
    reformatted = json.dumps(values, indent=4, sort_keys=True)
    dest.write_text(reformatted)
    assert dest.read_text() != seed_path.read_text()  # real text drift
    assert install.describe_settings_drift(seed_path, dest) == ""  # no value drift

    reseed_ctx = make_ctx(home, harnesses=("claude",), reseed=True)
    _, drift = install.seed_claude_settings(reseed_ctx)

    assert drift == ""
    assert not (home / ".claude" / "settings.json.bak").exists()
    assert dest.read_text() == reformatted
    assert kinds(reseed_ctx, "file-backed-up") == []
    assert len(kinds(reseed_ctx, "file-copied")) == 1  # only the initial seed


def test_reseed_dry_run_previews_fresh_backup_and_changes_nothing(home, capsys):
    """Eval criterion 4, fresh-backup variant."""
    dest = home / ".claude" / "settings.json"
    dest.parent.mkdir(parents=True)
    dest.write_text(json.dumps({"model": "drifted-value"}))
    before = dest.read_text()

    ctx = make_ctx(home, harnesses=("claude",), reseed=True, dry_run=True)
    _, drift = install.seed_claude_settings(ctx)
    out = capsys.readouterr().out

    assert drift == ""
    assert "would back up" in out
    assert "then reseed from settings.json" in out
    assert dest.read_text() == before
    assert not (home / ".claude" / "settings.json.bak").exists()
    assert not ctx.manifest.path.exists()


def test_reseed_dry_run_previews_already_backed_up(home, capsys):
    """Eval criterion 4, already-backed-up variant."""
    dest = home / ".claude" / "settings.json"
    dest.parent.mkdir(parents=True)
    backup = home / ".claude" / "settings.json.bak"
    dest.write_text(json.dumps({"model": "drifted-value"}))
    backup.write_text("true-original\n")
    make_ctx(home, harnesses=("claude",)).manifest.record_backup(dest, backup)

    ctx = make_ctx(home, harnesses=("claude",), reseed=True, dry_run=True)
    _, drift = install.seed_claude_settings(ctx)
    out = capsys.readouterr().out

    assert drift == ""
    assert "already backed up" in out
    assert backup.read_text() == "true-original\n"
    assert dest.read_text() == json.dumps({"model": "drifted-value"})


def test_reseed_dry_run_previews_foreign_backup_blocks_it(home, capsys):
    """Eval criterion 4, foreign-backup-blocks-it variant."""
    dest = home / ".claude" / "settings.json"
    dest.parent.mkdir(parents=True)
    backup = home / ".claude" / "settings.json.bak"
    dest.write_text(json.dumps({"model": "drifted-value"}))
    backup.write_text("foreign\n")

    ctx = make_ctx(home, harnesses=("claude",), reseed=True, dry_run=True)
    _, drift = install.seed_claude_settings(ctx)
    out = capsys.readouterr().out

    assert drift != ""
    assert "would skip reseeding" in out
    assert "resolve manually" in out
    assert backup.read_text() == "foreign\n"
    assert dest.read_text() == json.dumps({"model": "drifted-value"})


def test_reseed_foreign_unrecorded_backup_blocks_reseed(home, capsys):
    """Eval criterion 9 (real run, not dry-run)."""
    dest = home / ".claude" / "settings.json"
    dest.parent.mkdir(parents=True)
    backup = home / ".claude" / "settings.json.bak"
    dest.write_text(json.dumps({"model": "drifted-value"}))
    backup.write_text("foreign\n")

    ctx = make_ctx(home, harnesses=("claude",), reseed=True)
    _, drift = install.seed_claude_settings(ctx)
    out = capsys.readouterr().out

    assert drift != ""
    assert "SKIPPED" in out
    assert "resolve manually" in out
    assert backup.read_text() == "foreign\n"
    assert dest.read_text() == json.dumps({"model": "drifted-value"})
    assert kinds(ctx, "file-backed-up") == []
    assert kinds(ctx, "file-copied") == []


def test_reseed_backup_move_failure_is_skipped_not_raised(home, monkeypatch, capsys):
    """Eval criterion 12: a move failure never falls through to a copy attempt."""
    dest = home / ".claude" / "settings.json"
    dest.parent.mkdir(parents=True)
    live = json.loads((REPO_ROOT / "claude" / "settings.json").read_text())
    live["model"] = "drifted-value"
    dest.write_text(json.dumps(live))
    monkeypatch.setattr(install.shutil, "move", _raise_oserror)

    ctx = make_ctx(home, harnesses=("claude",), reseed=True)
    _, drift = install.seed_claude_settings(ctx)
    out = capsys.readouterr().out

    assert drift == ""
    assert "SKIPPED" in out
    assert "reseed backup failed" in out
    assert dest.read_text() == json.dumps(live)
    assert kinds(ctx, "file-backed-up") == []
    assert kinds(ctx, "file-copied") == []


def test_reseed_copy_failure_restores_dest_from_backup(home, monkeypatch, capsys):
    """Eval criterion 12: a copy failure after a successful move restores
    dest from backup, rather than leaving the live application with no
    config file at all."""
    dest = home / ".claude" / "settings.json"
    dest.parent.mkdir(parents=True)
    live = json.loads((REPO_ROOT / "claude" / "settings.json").read_text())
    live["model"] = "drifted-value"
    dest.write_text(json.dumps(live))
    monkeypatch.setattr(install.shutil, "copy", _raise_oserror)

    ctx = make_ctx(home, harnesses=("claude",), reseed=True)
    _, drift = install.seed_claude_settings(ctx)
    out = capsys.readouterr().out

    assert drift == ""
    assert "SKIPPED" in out
    assert "copy failed" in out
    assert dest.read_text() == json.dumps(live)
    assert not (home / ".claude" / "settings.json.bak").exists()
    # The backup entry is still recorded (the physical move did succeed);
    # only the copy step failed and its recovery moved the file back.
    assert len(kinds(ctx, "file-backed-up")) == 1
    assert kinds(ctx, "file-copied") == []


def test_reseed_recorded_backup_deleted_from_disk_takes_fresh_backup(home):
    """Eval criterion 15: a recorded-but-deleted .bak is treated as state 1,
    not silently skipped."""
    dest = home / ".claude" / "settings.json"
    dest.parent.mkdir(parents=True)
    stale_backup = home / ".claude" / "settings.json.bak"
    live = json.loads((REPO_ROOT / "claude" / "settings.json").read_text())
    live["model"] = "drifted-value"
    dest.write_text(json.dumps(live))
    # A backup was recorded once, but the .bak file itself no longer exists.
    make_ctx(home, harnesses=("claude",)).manifest.record_backup(dest, stale_backup)

    ctx = make_ctx(home, harnesses=("claude",), reseed=True)
    _, drift = install.seed_claude_settings(ctx)

    assert drift == ""
    assert json.loads(stale_backup.read_text())["model"] == "drifted-value"
    assert dest.read_text() == (REPO_ROOT / "claude" / "settings.json").read_text()
    assert len(kinds(ctx, "file-backed-up")) == 2
    assert len(kinds(ctx, "file-copied")) == 1


def test_two_consecutive_reseeds_then_rollback_restores_true_original(home):
    """Eval criterion 7."""
    dest = home / ".claude" / "settings.json"
    dest.parent.mkdir(parents=True)
    dest.write_text(json.dumps({"model": "true-original"}))

    ctx1 = make_ctx(home, harnesses=("claude",), reseed=True)
    install.seed_claude_settings(ctx1)
    assert dest.read_text() == (REPO_ROOT / "claude" / "settings.json").read_text()

    live = json.loads(dest.read_text())
    live["model"] = "intermediate-drift"
    dest.write_text(json.dumps(live))

    ctx2 = make_ctx(home, harnesses=("claude",), reseed=True)
    install.seed_claude_settings(ctx2)
    assert dest.read_text() == (REPO_ROOT / "claude" / "settings.json").read_text()

    assert len(kinds(ctx2, "file-backed-up")) == 1
    assert len(kinds(ctx2, "file-copied")) == 2

    assert install.do_rollback(make_ctx(home)) == 0
    assert json.loads(dest.read_text()) == {"model": "true-original"}


def test_three_consecutive_reseeds_then_rollback_restores_true_original(home):
    """Eval criterion 8: the invariant generalizes beyond the two-run case."""
    dest = home / ".claude" / "settings.json"
    dest.parent.mkdir(parents=True)
    dest.write_text(json.dumps({"model": "true-original"}))

    for i in range(3):
        ctx = make_ctx(home, harnesses=("claude",), reseed=True)
        install.seed_claude_settings(ctx)
        assert dest.read_text() == (REPO_ROOT / "claude" / "settings.json").read_text()
        live = json.loads(dest.read_text())
        live["model"] = f"intermediate-drift-{i}"
        dest.write_text(json.dumps(live))

    final_ctx = make_ctx(home, harnesses=("claude",), reseed=True)
    install.seed_claude_settings(final_ctx)
    assert len(kinds(final_ctx, "file-backed-up")) == 1
    assert len(kinds(final_ctx, "file-copied")) == 4

    assert install.do_rollback(make_ctx(home)) == 0
    assert json.loads(dest.read_text()) == {"model": "true-original"}


def test_bootstrap_then_single_reseed_then_rollback_restores_true_original(home):
    """Eval criterion 14: the realistic sequence every real seed goes
    through — a plain bootstrap copy, live edits, one --reseed, then
    --rollback — not just the back-to-back-reseeds cases above. Regression
    test for the do_rollback/_rollback_copy double-delete fix: without
    ``restored_dests``, the original bootstrap's file-copied entry deletes
    the just-restored true original a second time."""
    dest = home / ".claude" / "settings.json"

    bootstrap_ctx = make_ctx(home, harnesses=("claude",))
    install.seed_claude_settings(bootstrap_ctx)
    assert kinds(bootstrap_ctx, "file-copied") == [
        {"kind": "file-copied", "dest": str(dest)}
    ]

    live = json.loads(dest.read_text())
    live["model"] = "live-edited-value"
    dest.write_text(json.dumps(live))
    true_original_content = dest.read_text()

    reseed_ctx = make_ctx(home, harnesses=("claude",), reseed=True)
    install.seed_claude_settings(reseed_ctx)
    assert dest.read_text() == (REPO_ROOT / "claude" / "settings.json").read_text()
    assert len(kinds(reseed_ctx, "file-copied")) == 2
    assert len(kinds(reseed_ctx, "file-backed-up")) == 1

    assert install.do_rollback(make_ctx(home)) == 0
    assert dest.read_text() == true_original_content


# ── vscode WSL seeding ───────────────────────────────────────────────────────


def test_seed_vscode_settings_noop_off_wsl(home):
    ctx = make_ctx(home, is_wsl=False)
    assert install.seed_vscode_settings(ctx) == []
    assert not ctx.reporter.skipped


def test_seed_vscode_settings_skips_without_windows_code_cli(home, monkeypatch):
    monkeypatch.setattr(install, "_vscode_wsl_user_dir", lambda: None)
    ctx = make_ctx(home, is_wsl=True)
    assert install.seed_vscode_settings(ctx) == []
    assert ctx.reporter.skipped == [
        "VS Code settings — WSL detected but no Windows-side 'code' CLI found on PATH"
    ]


def test_seed_vscode_settings_fresh_copy_records_manifest(home, monkeypatch):
    vscode_dir = home / "winappdata"
    vscode_dir.mkdir()
    monkeypatch.setattr(install, "_vscode_wsl_user_dir", lambda: vscode_dir)
    ctx = make_ctx(home, is_wsl=True)

    results = install.seed_vscode_settings(ctx)

    names = {name for _, (name, _) in results}
    assert names == {"settings.json", "keybindings.json"}
    for name in ("settings.json", "keybindings.json"):
        dest = vscode_dir / name
        assert dest.is_file() and not dest.is_symlink()
        assert dest.read_text() == (REPO_ROOT / "vscode" / name).read_text()
    assert {e["dest"] for e in kinds(ctx, "file-copied")} == {
        str(vscode_dir / "settings.json"),
        str(vscode_dir / "keybindings.json"),
    }
    assert all(drift == "" for _, (_, drift) in results)


def test_seed_vscode_settings_migrates_stale_symlink(home, monkeypatch):
    vscode_dir = home / "winappdata"
    vscode_dir.mkdir()
    monkeypatch.setattr(install, "_vscode_wsl_user_dir", lambda: vscode_dir)
    ctx = make_ctx(home, is_wsl=True)

    old_target = home / "old_settings.json"
    old_target.write_text('{"old": true}')
    dest = vscode_dir / "settings.json"
    dest.symlink_to(old_target)
    assert dest.is_symlink()

    install.seed_vscode_settings(ctx)

    assert dest.is_file() and not dest.is_symlink()
    assert dest.read_text() == (REPO_ROOT / "vscode" / "settings.json").read_text()


def test_seed_vscode_settings_migration_dry_run_previews_and_changes_nothing(
    home, monkeypatch, capsys
):
    vscode_dir = home / "winappdata"
    vscode_dir.mkdir()
    monkeypatch.setattr(install, "_vscode_wsl_user_dir", lambda: vscode_dir)
    ctx = make_ctx(home, is_wsl=True, dry_run=True)

    old_target = home / "old_settings.json"
    old_target.write_text('{"old": true}')
    dest = vscode_dir / "settings.json"
    dest.symlink_to(old_target)
    # Keep keybindings.json out of the picture — a plain not-yet-seeded
    # file there would legitimately hit seed_file's own "would copy"
    # preview branch, which isn't what this test is about.
    (vscode_dir / "keybindings.json").write_text(
        (REPO_ROOT / "vscode" / "keybindings.json").read_text()
    )

    install.seed_vscode_settings(ctx)

    out = capsys.readouterr().out
    assert "would remove stale WSL symlink" in out
    assert "would copy" not in out
    assert dest.is_symlink()
    assert dest.resolve() == old_target.resolve()


def test_seed_vscode_settings_reports_drift_when_content_differs(home, monkeypatch):
    vscode_dir = home / "winappdata"
    vscode_dir.mkdir()
    monkeypatch.setattr(install, "_vscode_wsl_user_dir", lambda: vscode_dir)
    ctx = make_ctx(home, is_wsl=True)
    install.seed_vscode_settings(ctx)

    dest = vscode_dir / "settings.json"
    dest.write_text(dest.read_text() + "\n// live edit\n")

    results = install.seed_vscode_settings(make_ctx(home, is_wsl=True))
    drift_by_name = {name: drift for _, (name, drift) in results}
    assert drift_by_name["settings.json"] != ""
    assert drift_by_name["keybindings.json"] == ""


def test_describe_vscode_drift_identical_content_is_no_drift(tmp_path):
    seed = tmp_path / "seed.json"
    live = tmp_path / "live.json"
    seed.write_text('{"a": 1}')
    live.write_text('{"a": 1}')
    assert install.describe_vscode_drift(seed, live) == ""


def test_describe_vscode_drift_dict_key_diff(tmp_path):
    seed = tmp_path / "seed.json"
    live = tmp_path / "live.json"
    seed.write_text(json.dumps({"a": 1, "b": 2}))
    live.write_text(json.dumps({"a": 1, "b": 3}))
    assert install.describe_vscode_drift(seed, live) == "b"


def test_describe_vscode_drift_list_length_diff(tmp_path):
    seed = tmp_path / "seed.json"
    live = tmp_path / "live.json"
    seed.write_text(json.dumps([{"key": "a"}]))
    live.write_text(json.dumps([{"key": "a"}, {"key": "b"}]))
    assert install.describe_vscode_drift(seed, live) == "2 bindings live vs 1 in seed"


def test_describe_vscode_drift_list_same_length_content_differs(tmp_path):
    seed = tmp_path / "seed.json"
    live = tmp_path / "live.json"
    seed.write_text(json.dumps([{"key": "a"}]))
    live.write_text(json.dumps([{"key": "b"}]))
    assert (
        install.describe_vscode_drift(seed, live)
        == "binding definitions differ (1 bindings)"
    )


def test_describe_vscode_drift_jsonc_comment_still_reports_generic_fallback(tmp_path):
    seed = tmp_path / "seed.json"
    live = tmp_path / "live.json"
    seed.write_text(json.dumps({"a": 1}))
    live.write_text('// a comment\n{"a": 1, "b": 2}')
    drift = install.describe_vscode_drift(seed, live)
    assert drift == "content differs from the repo copy"


def test_describe_vscode_drift_missing_file_is_nothing_to_compare(tmp_path):
    seed = tmp_path / "seed.json"
    live = tmp_path / "live.json"
    seed.write_text('{"a": 1}')
    assert install.describe_vscode_drift(seed, live) == ""


# ── settings/opencode drift: text-first check + corrupted-JSON fix ─────────────


def test_describe_settings_drift_corrupted_json_now_reports_non_empty(tmp_path):
    """Eval criterion 10: unparseable live JSON used to be invisible to drift."""
    seed = tmp_path / "seed.json"
    live = tmp_path / "live.json"
    seed.write_text(json.dumps({"a": 1}))
    live.write_text("{not valid json")
    drift = install.describe_settings_drift(seed, live)
    assert drift != ""
    assert "unreadable or invalid JSON" in drift


def test_describe_opencode_drift_corrupted_json_now_reports_non_empty(tmp_path):
    seed = tmp_path / "seed.json"
    live = tmp_path / "live.json"
    seed.write_text(json.dumps({"a": 1}))
    live.write_text("{not valid json")
    drift = install.describe_opencode_drift(seed, live)
    assert drift != ""
    assert "unreadable or invalid JSON" in drift


def test_describe_opencode_drift_identical_jsonc_with_comment_is_no_drift(tmp_path):
    """Eval criterion 11: the text-equality check must fire before JSON
    parsing, so a byte-identical commented JSONC file is never misreported
    as corrupted/drifted just because json.loads can't parse the comment."""
    seed = tmp_path / "seed.jsonc"
    live = tmp_path / "live.jsonc"
    content = '// a comment\n{"a": 1}'
    seed.write_text(content)
    live.write_text(content)
    assert install.describe_opencode_drift(seed, live) == ""


def test_describe_settings_drift_identical_is_no_drift(tmp_path):
    seed = tmp_path / "seed.json"
    live = tmp_path / "live.json"
    seed.write_text(json.dumps({"a": 1}))
    live.write_text(json.dumps({"a": 1}))
    assert install.describe_settings_drift(seed, live) == ""


# ── history + rollback ────────────────────────────────────────────────────────


def test_history_accumulates_across_runs(home, links, offline_install):
    ctx_a = make_ctx(home, harnesses=("claude",))
    install.run_install(ctx_a, links)
    ctx_b = make_ctx(home, harnesses=("opencode",))
    install.run_install(ctx_b, links)

    entries = history(ctx_b)
    assert sum(1 for e in entries if e["kind"] == "run") == 2
    dests = {e.get("dest") for e in entries}
    assert str(home / ".claude" / "CLAUDE.md") in dests
    assert str(home / ".config" / "opencode" / "opencode.jsonc") in dests


def test_rollback_undoes_every_past_run(home, links, offline_install):
    install.run_install(make_ctx(home, harnesses=("claude",)), links)
    install.run_install(make_ctx(home, harnesses=("opencode",)), links)

    assert (home / ".claude" / "CLAUDE.md").is_symlink()
    assert (home / ".config" / "opencode" / "opencode.jsonc").is_file()

    rollback = make_ctx(home)
    assert install.do_rollback(rollback) == 0

    # Run A's files, not just run B's.
    assert not (home / ".claude" / "CLAUDE.md").exists()
    assert not (home / ".vimrc").exists()
    assert not (home / ".config" / "opencode" / "opencode.jsonc").exists()
    assert not rollback.manifest.path.exists()


def test_rollback_restores_backed_up_file(home, links, offline_install):
    (home / ".vimrc").write_text("sentinel-content\n")
    install.run_install(make_ctx(home, harnesses=("claude",)), links)
    assert (home / ".vimrc").is_symlink()

    assert install.do_rollback(make_ctx(home)) == 0

    assert (home / ".vimrc").read_text() == "sentinel-content\n"
    assert not (home / ".vimrc.bak").exists()


def test_rollback_leaves_a_reclaimed_symlink_alone(
    home, links, offline_install, capsys
):
    install.run_install(make_ctx(home, harnesses=("claude",)), links)

    claimed = home / ".claude" / "CLAUDE.md"
    claimed.unlink()
    claimed.symlink_to("/etc/hostname")

    code = install.do_rollback(make_ctx(home))
    out = capsys.readouterr().out

    assert code == 1
    assert claimed.readlink() == Path("/etc/hostname")
    assert "something else has claimed this path" in out
    assert "rollback step(s) did not apply cleanly" in out


def test_rollback_reports_a_missing_backup(home, links, offline_install, capsys):
    (home / ".vimrc").write_text("sentinel-content\n")
    install.run_install(make_ctx(home, harnesses=("claude",)), links)
    (home / ".vimrc.bak").unlink()

    code = install.do_rollback(make_ctx(home))
    out = capsys.readouterr().out

    assert code == 1
    assert "not found — already restored, or removed outside install.sh" in out


def test_duplicate_backup_entry_is_not_reported_twice(home, capsys):
    """A repeat backup/rollback/reinstall cycle leaves two entries for one path.

    The second (older) one is already satisfied by the first restore, so it
    must not be reported as a missing-backup anomaly.
    """
    ctx = make_ctx(home)
    dest = home / ".vimrc"
    backup = home / ".vimrc.bak"
    dest.write_text("original\n")
    dest.rename(backup)
    ctx.manifest.record_backup(dest, backup)
    ctx.manifest.record_backup(dest, backup)

    code = install.do_rollback(make_ctx(home))
    out = capsys.readouterr().out

    assert code == 0
    assert dest.read_text() == "original\n"
    assert "SKIPPED" not in out


def test_rollback_never_uninstalls_packages(home, capsys):
    ctx = make_ctx(home)
    ctx.manifest.init_run("personal")
    ctx.manifest.record_package("ripgrep")

    assert install.do_rollback(make_ctx(home)) == 0
    assert "package left installed (profile-independent): ripgrep" in (
        capsys.readouterr().out
    )


def test_rollback_dry_run_changes_nothing(home, links, offline_install, capsys):
    install.run_install(make_ctx(home, harnesses=("claude",)), links)

    preview = make_ctx(home, dry_run=True)
    assert install.do_rollback(preview) == 0
    out = capsys.readouterr().out

    assert "Dry run complete" in out
    assert (home / ".claude" / "CLAUDE.md").is_symlink()
    assert preview.manifest.path.is_file()


def test_rollback_without_history_exits_1(home, capsys):
    assert install.do_rollback(make_ctx(home)) == 1
    assert "nothing to roll back" in capsys.readouterr().err


# ── --rollback --wipe ────────────────────────────────────────────────────────


def _disable_systemctl(monkeypatch):
    """Make the watchcommit-wipe gate see systemd --user as unavailable.

    Only safe for tests with no unit symlink on disk at all (the no-manifest
    cases below, which never call ``run_install``): ``_wipe_watchcommit``
    checks ``unit_path.is_symlink()`` before ever calling ``have()``, so
    with no symlink this never fires and never touches ``run_command``.
    Tests that *do* run a real install first (``links.toml`` records a real
    watchcommit.service symlink for personal+linux, and ``offline_install``
    only stubs the install-time ``enable_watchcommit_service`` call, not the
    symlink itself) must use ``_watchcommit_available`` instead — forcing
    systemd "unavailable" against a real unit symlink is a genuine anomaly
    (SKIPPED, exit 1) by design, not a quiet no-op, so it would wrongly
    fail unrelated assertions about backup deletion or nvim-dir sweeping.
    """
    monkeypatch.setattr(install, "have", lambda exe: exe != "systemctl")


def _watchcommit_available(monkeypatch, *, probe_ok=True, disable_ok=True):
    """Stub systemd as available and record the calls _wipe_watchcommit makes."""
    calls: list[list[str]] = []

    def _stub(cmd, *, shell=False, capture=False):
        argv = list(cmd)
        calls.append(argv)
        if argv == ["systemctl", "--user", "show-environment"]:
            return install.CommandResult(probe_ok)
        if argv == ["systemctl", "--user", "disable", "--now", "watchcommit.service"]:
            return install.CommandResult(disable_ok)
        raise AssertionError(f"unexpected command: {argv!r}")

    monkeypatch.setattr(install, "have", lambda exe: exe == "systemctl")
    monkeypatch.setattr(install, "run_command", _stub)
    return calls


def test_wipe_deletes_backup_instead_of_restoring(
    home, links, offline_install, monkeypatch
):
    (home / ".vimrc").write_text("sentinel-content\n")
    install.run_install(make_ctx(home, harnesses=("claude",)), links)
    assert (home / ".vimrc").is_symlink()

    _watchcommit_available(monkeypatch)
    assert install.do_rollback(make_ctx(home, wipe=True)) == 0

    assert not (home / ".vimrc").exists()
    assert not (home / ".vimrc.bak").exists()


def test_wipe_dry_run_does_not_delete_backup(home, links, offline_install, monkeypatch):
    (home / ".vimrc").write_text("sentinel-content\n")
    install.run_install(make_ctx(home, harnesses=("claude",)), links)

    _watchcommit_available(monkeypatch)
    assert install.do_rollback(make_ctx(home, wipe=True, dry_run=True)) == 0

    assert (home / ".vimrc").is_symlink()
    assert (home / ".vimrc.bak").is_file()


def test_wipe_missing_backup_reports_wipe_wording(
    home, links, offline_install, capsys, monkeypatch
):
    (home / ".vimrc").write_text("sentinel-content\n")
    install.run_install(make_ctx(home, harnesses=("claude",)), links)
    (home / ".vimrc.bak").unlink()

    _watchcommit_available(monkeypatch)
    code = install.do_rollback(make_ctx(home, wipe=True))
    out = capsys.readouterr().out

    assert code == 1
    assert "not found — already wiped, or removed outside install.sh" in out
    assert "already restored" not in out


def test_wipe_sweeps_nvim_dirs(home, links, offline_install, monkeypatch):
    install.run_install(make_ctx(home, harnesses=("claude",)), links)
    nvim_dirs = [
        home / ".local" / "share" / "nvim",
        home / ".local" / "state" / "nvim",
        home / ".cache" / "nvim",
    ]
    for path in nvim_dirs:
        path.mkdir(parents=True)
        (path / "sentinel").write_text("x\n")

    _watchcommit_available(monkeypatch)
    assert install.do_rollback(make_ctx(home, wipe=True)) == 0

    for path in nvim_dirs:
        assert not path.exists()


def test_rollback_without_wipe_leaves_nvim_dirs_alone(home, links, offline_install):
    install.run_install(make_ctx(home, harnesses=("claude",)), links)
    nvim_dir = home / ".local" / "share" / "nvim"
    nvim_dir.mkdir(parents=True)
    (nvim_dir / "sentinel").write_text("x\n")

    assert install.do_rollback(make_ctx(home)) == 0

    assert nvim_dir.is_dir()


def test_wipe_dry_run_leaves_nvim_dirs_alone(home, links, offline_install, monkeypatch):
    install.run_install(make_ctx(home, harnesses=("claude",)), links)
    nvim_dir = home / ".local" / "share" / "nvim"
    nvim_dir.mkdir(parents=True)
    (nvim_dir / "sentinel").write_text("x\n")

    _watchcommit_available(monkeypatch)
    assert install.do_rollback(make_ctx(home, wipe=True, dry_run=True)) == 0

    assert nvim_dir.is_dir()
    assert (nvim_dir / "sentinel").is_file()


def test_wipe_disables_watchcommit_service(home, links, offline_install, monkeypatch):
    install.run_install(make_ctx(home, harnesses=("claude",)), links)
    unit = home / ".config" / "systemd" / "user" / "watchcommit.service"
    assert unit.is_symlink()

    calls = _watchcommit_available(monkeypatch)

    assert install.do_rollback(make_ctx(home, wipe=True)) == 0
    assert ["systemctl", "--user", "disable", "--now", "watchcommit.service"] in calls


def test_wipe_dry_run_previews_watchcommit_disable(
    home, links, offline_install, monkeypatch, capsys
):
    install.run_install(make_ctx(home, harnesses=("claude",)), links)

    calls = _watchcommit_available(monkeypatch)

    assert install.do_rollback(make_ctx(home, wipe=True, dry_run=True)) == 0
    out = capsys.readouterr().out

    assert "would disable+stop the watchcommit systemd user service" in out
    assert [
        "systemctl",
        "--user",
        "disable",
        "--now",
        "watchcommit.service",
    ] not in calls


def test_plain_rollback_does_not_touch_watchcommit(
    home, links, offline_install, monkeypatch
):
    install.run_install(make_ctx(home, harnesses=("claude",)), links)

    calls = _watchcommit_available(monkeypatch)

    assert install.do_rollback(make_ctx(home)) == 0
    assert calls == []


def test_wipe_watchcommit_noop_when_never_installed(
    home, links, offline_install, monkeypatch
):
    install.run_install(make_ctx(home, harnesses=("claude",), profile="work"), links)
    unit = home / ".config" / "systemd" / "user" / "watchcommit.service"
    assert not unit.exists()

    calls = _watchcommit_available(monkeypatch)

    assert install.do_rollback(make_ctx(home, wipe=True, profile="work")) == 0
    assert calls == []


def test_wipe_watchcommit_anomaly_when_systemd_unavailable(
    home, links, offline_install, monkeypatch, capsys
):
    install.run_install(make_ctx(home, harnesses=("claude",)), links)
    unit = home / ".config" / "systemd" / "user" / "watchcommit.service"
    assert unit.is_symlink()

    _watchcommit_available(monkeypatch, probe_ok=False)

    code = install.do_rollback(make_ctx(home, wipe=True))
    out = capsys.readouterr().out

    assert code == 1
    assert "SKIPPED" in out


def test_wipe_watchcommit_anomaly_counts_as_swept_with_no_manifest(
    home, monkeypatch, capsys
):
    """A probe failure is a real anomaly, not a silent no-op, even with
    nothing else to roll back — it must still push the exit code to 1
    instead of falling through to the plain "nothing to roll back" error."""
    unit = home / ".config" / "systemd" / "user" / "watchcommit.service"
    unit.parent.mkdir(parents=True)
    fake_src = home / ".fake-unit-source"
    fake_src.write_text("x\n")
    unit.symlink_to(fake_src)

    _watchcommit_available(monkeypatch, probe_ok=False)

    code = install.do_rollback(make_ctx(home, wipe=True))
    captured = capsys.readouterr()

    assert code == 1
    assert "SKIPPED" in captured.out
    assert "nothing to roll back" not in captured.out
    assert "nothing to roll back" not in captured.err


def test_wipe_no_manifest_sweeps_nvim_dirs(home, monkeypatch):
    nvim_dir = home / ".local" / "share" / "nvim"
    nvim_dir.mkdir(parents=True)
    (nvim_dir / "sentinel").write_text("x\n")

    _disable_systemctl(monkeypatch)
    code = install.do_rollback(make_ctx(home, wipe=True))

    assert code == 0
    assert not nvim_dir.exists()


def test_wipe_no_manifest_dry_run_sweeps_preview(home, monkeypatch, capsys):
    nvim_dir = home / ".local" / "share" / "nvim"
    nvim_dir.mkdir(parents=True)
    (nvim_dir / "sentinel").write_text("x\n")

    _disable_systemctl(monkeypatch)
    code = install.do_rollback(make_ctx(home, wipe=True, dry_run=True))
    out = capsys.readouterr().out

    assert code == 0
    assert nvim_dir.is_dir()
    assert "would remove" in out
    assert "nothing to roll back" not in out


def test_wipe_no_manifest_and_nothing_to_sweep_still_errors(home, monkeypatch, capsys):
    _disable_systemctl(monkeypatch)
    code = install.do_rollback(make_ctx(home, wipe=True))
    err = capsys.readouterr().err

    assert code == 1
    assert "nothing to roll back" in err


def test_wipe_no_manifest_dry_run_and_nothing_to_sweep_still_errors(
    home, monkeypatch, capsys
):
    _disable_systemctl(monkeypatch)
    code = install.do_rollback(make_ctx(home, wipe=True, dry_run=True))
    err = capsys.readouterr().err

    assert code == 1
    assert "nothing to roll back" in err


def test_wipe_removes_empty_state_dir(home, links, offline_install, monkeypatch):
    install.run_install(make_ctx(home, harnesses=("claude",)), links)
    state_dir = home / ".local" / "state" / "dotfiles"
    assert state_dir.is_dir()

    _watchcommit_available(monkeypatch)
    assert install.do_rollback(make_ctx(home, wipe=True)) == 0

    assert not state_dir.exists()


def test_wipe_dry_run_previews_empty_state_dir_removal(
    home, links, offline_install, monkeypatch, capsys
):
    install.run_install(make_ctx(home, harnesses=("claude",)), links)
    state_dir = home / ".local" / "state" / "dotfiles"

    _watchcommit_available(monkeypatch)
    assert install.do_rollback(make_ctx(home, wipe=True, dry_run=True)) == 0
    out = capsys.readouterr().out

    assert state_dir.is_dir()
    assert f"would remove empty state directory {state_dir}" in out


def test_plain_rollback_leaves_state_dir_even_if_empty(home, links, offline_install):
    install.run_install(make_ctx(home, harnesses=("claude",)), links)
    state_dir = home / ".local" / "state" / "dotfiles"

    assert install.do_rollback(make_ctx(home)) == 0

    assert state_dir.is_dir()


def test_history_survives_a_malformed_line(home):
    ctx = make_ctx(home)
    ctx.manifest.init_run("personal")
    with open(ctx.manifest.path, "a", encoding="utf-8") as handle:
        handle.write("not json at all\n")
    ctx.manifest.record_package("tmux")

    entries = ctx.manifest.entries()
    assert [e["kind"] for e in entries] == ["run", "package-installed"]


# ── work-profile guard and marker ─────────────────────────────────────────────


def test_work_run_writes_marker_and_rollback_resets_it(home, links, offline_install):
    ctx = make_ctx(home, harnesses=("claude",), profile="work")
    install.run_install(ctx, links)

    assert ctx.profile_marker.read_text().strip() == "work"
    assert install.work_guard_blocks(make_ctx(home, profile="personal"))
    assert not install.work_guard_blocks(make_ctx(home, profile="personal", force=True))
    assert not install.work_guard_blocks(make_ctx(home, profile="work"))

    install.do_rollback(make_ctx(home))
    assert not ctx.profile_marker.exists()
    assert not install.work_guard_blocks(make_ctx(home, profile="personal"))


def test_guard_is_inert_without_a_marker(home):
    assert not install.work_guard_blocks(make_ctx(home, profile="personal"))


def test_marker_not_written_on_personal_run(home, links, offline_install):
    ctx = make_ctx(home, harnesses=("claude",))
    install.run_install(ctx, links)
    assert not ctx.profile_marker.exists()


# ── whole-run behavior ────────────────────────────────────────────────────────


def test_full_run_wires_only_the_selected_harness(home, links, offline_install):
    ctx = make_ctx(home, harnesses=("claude",))
    assert install.run_install(ctx, links) == 0

    assert (home / ".claude" / "CLAUDE.md").is_symlink()
    assert (home / ".claude" / "scripts" / "dev_status.py").is_symlink()
    assert not (home / ".copilot" / "copilot-instructions.md").exists()
    assert not (home / ".config" / "opencode" / "opencode.jsonc").exists()
    assert not (home / ".gemini" / "GEMINI.md").exists()


def test_full_run_is_additive_across_narrowing_selections(home, links, offline_install):
    install.run_install(make_ctx(home, harnesses=("claude", "copilot")), links)
    install.run_install(make_ctx(home, harnesses=("claude",)), links)

    # Narrowing --harness on a later run never uninstalls what came before.
    assert (home / ".copilot" / "copilot-instructions.md").is_symlink()


def test_full_dry_run_touches_nothing(home, links, offline_install):
    ctx = make_ctx(home, harnesses=("claude", "opencode"), dry_run=True)
    install.run_install(ctx, links)

    assert not (home / ".vimrc").exists()
    assert not (home / ".claude").exists()
    assert not ctx.manifest.path.exists()


def test_exit_status_is_1_when_a_step_was_skipped(home, links, offline_install):
    ctx = make_ctx(home, harnesses=("claude",))
    ctx.reporter.skip("something", "because")
    assert install.run_install(ctx, links) == 1


# ── small helpers ─────────────────────────────────────────────────────────────


def test_parse_neovim_version():
    assert install.parse_neovim_version("NVIM v0.11.2\nBuild type: Release") == (0, 11)
    assert install.parse_neovim_version("NVIM v0.9.5") == (0, 9)
    assert install.parse_neovim_version("nvim: command not found") is None
    assert install.parse_neovim_version("") is None


def _stub_nvim(monkeypatch, *, version_output="NVIM v0.12.4", runtime_ok=True):
    """Stub nvim --version and the headless runtime probe."""

    def _stub(cmd, *, shell=False, capture=False):
        argv = list(cmd)
        if argv == ["nvim", "--version"]:
            return install.CommandResult(True, version_output)
        if argv[:2] == ["nvim", "--headless"]:
            return install.CommandResult(runtime_ok)
        raise AssertionError(f"unexpected command: {argv!r}")

    monkeypatch.setattr(install, "have", lambda exe: exe == "nvim")
    monkeypatch.setattr(install, "run_command", _stub)


def test_neovim_runtime_ok_reflects_probe(monkeypatch):
    _stub_nvim(monkeypatch, runtime_ok=True)
    assert install.neovim_runtime_ok() is True


def test_neovim_runtime_ok_reflects_probe_failure(monkeypatch):
    _stub_nvim(monkeypatch, runtime_ok=False)
    assert install.neovim_runtime_ok() is False


def test_neovim_status_missing_binary(monkeypatch):
    monkeypatch.setattr(install, "have", lambda exe: False)
    assert install._neovim_status() == (None, False)


def test_neovim_status_unparseable_version_skips_runtime_probe(monkeypatch):
    calls = []

    def _stub(cmd, *, shell=False, capture=False):
        calls.append(list(cmd))
        return install.CommandResult(True, "nvim: command not found")

    monkeypatch.setattr(install, "have", lambda exe: True)
    monkeypatch.setattr(install, "run_command", _stub)
    assert install._neovim_status() == (None, False)
    assert calls == [["nvim", "--version"]]


def test_neovim_status_ok(monkeypatch):
    _stub_nvim(monkeypatch, version_output="NVIM v0.12.4", runtime_ok=True)
    assert install._neovim_status() == ((0, 12), True)


def test_neovim_status_broken_runtime(monkeypatch):
    _stub_nvim(monkeypatch, version_output="NVIM v0.12.4", runtime_ok=False)
    assert install._neovim_status() == ((0, 12), False)


def test_bootstrap_neovim_skips_when_not_installed(home, monkeypatch, capsys):
    ctx = make_ctx(home)
    monkeypatch.setattr(install, "have", lambda exe: False)
    install.bootstrap_neovim(ctx)
    assert "Neovim not installed" in capsys.readouterr().out


def test_bootstrap_neovim_skips_when_version_too_old(home, monkeypatch, capsys):
    ctx = make_ctx(home)
    _stub_nvim(monkeypatch, version_output="NVIM v0.9.5")
    install.bootstrap_neovim(ctx)
    out = capsys.readouterr().out
    assert "Neovim 0.9 found, config needs >=0.11" in out


def test_bootstrap_neovim_old_version_includes_fallback_failure(
    home, monkeypatch, capsys
):
    ctx = make_ctx(home)
    ctx.neovim_fallback_failure = (
        "download/extract failed, or archive layout unexpected"
    )
    _stub_nvim(monkeypatch, version_output="NVIM v0.9.5")
    install.bootstrap_neovim(ctx)
    out = capsys.readouterr().out
    assert "fallback install also failed" in out
    assert "download/extract failed, or archive layout unexpected" in out


def test_bootstrap_neovim_skips_when_runtime_broken(home, monkeypatch, capsys):
    ctx = make_ctx(home)
    _stub_nvim(monkeypatch, version_output="NVIM v0.12.4", runtime_ok=False)
    install.bootstrap_neovim(ctx)
    out = capsys.readouterr().out
    assert "runtime doesn't resolve" in out
    assert "vim.uri" in out


def test_bootstrap_neovim_proceeds_when_runtime_ok(home, monkeypatch, capsys):
    ctx = make_ctx(home, dry_run=True)
    _stub_nvim(monkeypatch, version_output="NVIM v0.12.4", runtime_ok=True)
    install.bootstrap_neovim(ctx)
    out = capsys.readouterr().out
    assert "would run: nvim --headless" in out
    assert "SKIPPED" not in out


# ── neovim fallback install (Linux) ────────────────────────────────────────────


def _stub_neovim_fallback(
    monkeypatch,
    *,
    have_nvim=False,
    version_output="",
    runtime_ok=False,
    download_ok=True,
    extract_ok=True,
    asset="nvim-linux-x86_64.tar.gz",
    machine="x86_64",
):
    """Stub the whole external surface _install_neovim_fallback touches.

    The ``tar`` stub fabricates the extracted directory tree a real
    extraction would produce (bin/nvim, share/nvim/runtime), since nothing
    here actually shells out.
    """
    calls: list[list[str]] = []

    def _stub(cmd, *, shell=False, capture=False):
        argv = list(cmd)
        calls.append(argv)
        if argv == ["nvim", "--version"]:
            return install.CommandResult(True, version_output)
        if argv[:2] == ["nvim", "--headless"]:
            return install.CommandResult(runtime_ok)
        if argv[0] == "curl":
            return install.CommandResult(download_ok)
        if argv[0] == "tar":
            if extract_ok:
                tmp_dir = Path(argv[-1])
                extracted = tmp_dir / asset.removesuffix(".tar.gz")
                (extracted / "bin").mkdir(parents=True)
                (extracted / "bin" / "nvim").write_text("fake nvim binary\n")
                (extracted / "share" / "nvim" / "runtime").mkdir(parents=True)
            return install.CommandResult(extract_ok)
        raise AssertionError(f"unexpected command: {argv!r}")

    monkeypatch.setattr(install, "have", lambda exe: exe == "nvim" and have_nvim)
    monkeypatch.setattr(install, "run_command", _stub)
    monkeypatch.setattr(install.platform, "machine", lambda: machine)
    return calls


def test_install_neovim_fallback_no_pin_noop_when_already_good(home, monkeypatch):
    """--no-nvim-pin restores the old rescue-only behavior: a system nvim
    that's already good enough (>=0.11, working runtime) is left alone,
    even though it isn't our managed pin."""
    ctx = make_ctx(home, no_nvim_pin=True)
    calls = _stub_neovim_fallback(
        monkeypatch, have_nvim=True, version_output="NVIM v0.11.6", runtime_ok=True
    )
    install._install_neovim_fallback(ctx)
    assert kinds(ctx, "package-installed") == []
    assert kinds(ctx, "symlink-created") == []
    assert not any(argv[0] in ("curl", "tar") for argv in calls)


def test_install_neovim_fallback_pins_by_default_even_when_system_nvim_good(
    home, monkeypatch
):
    """Default behavior (no --no-nvim-pin): a system nvim that's already
    good enough still gets overridden by the pinned build, since it isn't
    our managed symlink into ~/.local/opt/neovim."""
    ctx = make_ctx(home)
    _stub_neovim_fallback(
        monkeypatch, have_nvim=True, version_output="NVIM v0.11.6", runtime_ok=True
    )
    install._install_neovim_fallback(ctx)

    prefix = home / ".local" / "opt" / "neovim"
    shim = home / ".local" / "bin" / "nvim"
    assert (prefix / "bin" / "nvim").read_text() == "fake nvim binary\n"
    assert shim.is_symlink()

    packages = kinds(ctx, "package-installed")
    assert len(packages) == 1
    assert install.NEOVIM_FALLBACK_VERSION in packages[0]["name"]


def test_install_neovim_fallback_noop_when_already_pinned(home, monkeypatch):
    """Idempotent: once ~/.local/bin/nvim already resolves to our own
    correctly-pinned install, re-running does nothing — no network calls,
    no reinstall — under default (pin-enforcing) behavior."""
    ctx = make_ctx(home)
    prefix = home / ".local" / "opt" / "neovim"
    shim = home / ".local" / "bin" / "nvim"
    nvim_bin = prefix / "bin" / "nvim"
    nvim_bin.parent.mkdir(parents=True)
    nvim_bin.write_text("real fallback binary\n")
    shim.parent.mkdir(parents=True)
    shim.symlink_to(nvim_bin)

    calls: list[list[str]] = []

    def _stub(cmd, *, shell=False, capture=False):
        argv = list(cmd)
        calls.append(argv)
        if argv == [str(shim), "--version"]:
            return install.CommandResult(
                True, f"NVIM v{install.NEOVIM_FALLBACK_VERSION}"
            )
        raise AssertionError(f"unexpected command: {argv!r}")

    monkeypatch.setattr(install, "run_command", _stub)
    install._install_neovim_fallback(ctx)

    assert calls == [[str(shim), "--version"]]
    assert kinds(ctx, "package-installed") == []
    assert kinds(ctx, "symlink-created") == []


def test_install_neovim_fallback_no_pin_still_rescues_when_broken(home, monkeypatch):
    """--no-nvim-pin doesn't suppress genuine rescues: a missing/broken
    system nvim still gets the fallback installed."""
    ctx = make_ctx(home, no_nvim_pin=True)
    _stub_neovim_fallback(
        monkeypatch, have_nvim=True, version_output="NVIM v0.9.5", runtime_ok=False
    )
    install._install_neovim_fallback(ctx)

    packages = kinds(ctx, "package-installed")
    assert len(packages) == 1
    assert install.NEOVIM_FALLBACK_VERSION in packages[0]["name"]


def test_install_neovim_fallback_installs_when_too_old(home, monkeypatch):
    ctx = make_ctx(home)
    _stub_neovim_fallback(
        monkeypatch, have_nvim=True, version_output="NVIM v0.9.5", runtime_ok=False
    )
    install._install_neovim_fallback(ctx)

    prefix = home / ".local" / "opt" / "neovim"
    shim = home / ".local" / "bin" / "nvim"
    assert (prefix / "bin" / "nvim").read_text() == "fake nvim binary\n"
    assert shim.is_symlink()
    assert shim.resolve() == (prefix / "bin" / "nvim").resolve()

    packages = kinds(ctx, "package-installed")
    assert len(packages) == 1
    assert install.NEOVIM_FALLBACK_VERSION in packages[0]["name"]

    symlinks = kinds(ctx, "symlink-created")
    assert len(symlinks) == 1
    assert symlinks[0]["dest"] == str(shim)


def test_install_neovim_fallback_download_failure_skips(home, monkeypatch, capsys):
    ctx = make_ctx(home)
    _stub_neovim_fallback(monkeypatch, have_nvim=False, download_ok=False)
    install._install_neovim_fallback(ctx)

    out = capsys.readouterr().out
    assert "download/extract failed" in out
    assert ctx.neovim_fallback_failure is not None
    assert kinds(ctx, "package-installed") == []
    assert kinds(ctx, "symlink-created") == []
    assert not (home / ".local" / "opt" / "neovim").exists()


def test_install_neovim_fallback_unsupported_arch_skips(home, monkeypatch, capsys):
    ctx = make_ctx(home)
    calls = _stub_neovim_fallback(monkeypatch, have_nvim=False, machine="riscv64")
    install._install_neovim_fallback(ctx)

    out = capsys.readouterr().out
    assert "unsupported architecture" in out
    assert "riscv64" in out
    assert ctx.neovim_fallback_failure is not None
    assert calls == []


def test_install_neovim_fallback_replaces_stale_prefix(home, monkeypatch):
    ctx = make_ctx(home)
    prefix = home / ".local" / "opt" / "neovim"
    (prefix / "bin").mkdir(parents=True)
    (prefix / "bin" / "nvim").write_text("stale old binary\n")
    (prefix / "STALE_MARKER").write_text("x\n")

    _stub_neovim_fallback(monkeypatch, have_nvim=False)
    install._install_neovim_fallback(ctx)

    assert (prefix / "bin" / "nvim").read_text() == "fake nvim binary\n"
    assert not (prefix / "STALE_MARKER").exists()


def test_install_neovim_fallback_replaces_incomplete_prefix(home, monkeypatch):
    ctx = make_ctx(home)
    prefix = home / ".local" / "opt" / "neovim"
    (prefix / "bin").mkdir(parents=True)  # interrupted install: no nvim binary

    _stub_neovim_fallback(monkeypatch, have_nvim=False)
    install._install_neovim_fallback(ctx)

    assert (prefix / "bin" / "nvim").read_text() == "fake nvim binary\n"


def test_install_neovim_fallback_dry_run(home, monkeypatch, capsys):
    ctx = make_ctx(home, dry_run=True)
    calls = _stub_neovim_fallback(monkeypatch, have_nvim=False)
    install._install_neovim_fallback(ctx)

    out = capsys.readouterr().out
    assert "[dry-run]" in out
    assert install.NEOVIM_FALLBACK_VERSION in out
    assert calls == []
    assert not (home / ".local" / "opt" / "neovim").exists()
    assert kinds(ctx, "package-installed") == []


def test_color_is_off_for_non_tty(monkeypatch):
    monkeypatch.delenv("NO_COLOR", raising=False)

    class _NotATty:
        def isatty(self):
            return False

    class _Tty:
        def isatty(self):
            return True

    monkeypatch.setenv("TERM", "xterm-256color")
    assert not install.color_enabled(_NotATty())
    assert install.color_enabled(_Tty())

    monkeypatch.setenv("NO_COLOR", "1")
    assert not install.color_enabled(_Tty())
    monkeypatch.delenv("NO_COLOR")
    monkeypatch.setenv("TERM", "dumb")
    assert not install.color_enabled(_Tty())


def test_palette_wraps_only_when_enabled():
    assert install.Palette(False).ok("hi") == "hi"
    assert install.Palette(True).ok("hi") == "\x1b[32mhi\x1b[0m"


def test_context_display_shortens_home(home):
    ctx = make_ctx(home)
    assert ctx.display(home / ".claude" / "settings.json") == "~/.claude/settings.json"
    assert ctx.display(Path("/etc/hosts")) == "/etc/hosts"


# ── departure baseline capture ──────────────────────────────────────────────


def test_capture_departure_baseline_noop_on_mac(home, links):
    ctx = make_ctx(home, system="Darwin")
    install.capture_departure_baseline(ctx, links)
    assert not depart.baseline_path(ctx.state_dir).exists()


def test_capture_departure_baseline_noop_on_dry_run(home, links):
    ctx = make_ctx(home, dry_run=True)
    install.capture_departure_baseline(ctx, links)
    assert not depart.baseline_path(ctx.state_dir).exists()


def test_capture_departure_baseline_captures_rc_files_with_blobs(home, links):
    (home / ".zshrc").write_text("original zshrc\n")
    ctx = make_ctx(home)
    install.capture_departure_baseline(ctx, links)

    baseline = depart.load_baseline(ctx.state_dir)
    assert baseline is not None
    record = baseline.value_for(depart.file_key(home / ".zshrc"))
    assert record["state"] == "present"
    assert depart.read_blob(ctx.state_dir, record["blob"]) == b"original zshrc\n"

    assert baseline.value_for(depart.file_key(home / ".bashrc")) == {"state": "absent"}


def test_capture_departure_baseline_captures_link_destination_and_bak(home, links):
    ctx = make_ctx(home, harnesses=("claude",))
    install.capture_departure_baseline(ctx, links)
    baseline = depart.load_baseline(ctx.state_dir)

    dest = home / ".vimrc"
    assert baseline.value_for(depart.file_key(dest)) == {"state": "absent"}
    assert baseline.value_for(depart.symlink_key(dest)) == {"state": "absent"}
    assert baseline.value_for(depart.file_key(dest.with_name(dest.name + ".bak"))) == {
        "state": "absent"
    }


def test_capture_departure_baseline_captures_ancestor_directories(home, links):
    ctx = make_ctx(home, harnesses=("claude",))
    install.capture_departure_baseline(ctx, links)
    baseline = depart.load_baseline(ctx.state_dir)
    # ~/.local/bin is an ancestor of the uv/oh-my-posh/shim destinations.
    assert baseline.value_for(depart.directory_key(home / ".local" / "bin")) == {
        "state": "absent"
    }


def test_capture_departure_baseline_captures_seed_destination_when_harness_selected(
    home, links
):
    ctx = make_ctx(home, harnesses=("claude",))
    install.capture_departure_baseline(ctx, links)
    baseline = depart.load_baseline(ctx.state_dir)
    dest = home / ".claude" / "settings.json"
    assert baseline.value_for(depart.file_key(dest)) is not None


def test_capture_departure_baseline_skips_seed_destination_when_harness_not_selected(
    home, links
):
    ctx = make_ctx(home, harnesses=("opencode",))
    install.capture_departure_baseline(ctx, links)
    baseline = depart.load_baseline(ctx.state_dir)
    dest = home / ".claude" / "settings.json"
    assert baseline.value_for(depart.file_key(dest)) is None


def test_capture_departure_baseline_tree_manifests_font_and_neovim_prefix(home, links):
    ctx = make_ctx(home)
    install.capture_departure_baseline(ctx, links)
    baseline = depart.load_baseline(ctx.state_dir)
    font_dir = home / ".local" / "share" / "fonts" / "JetBrainsMonoNerdFont"
    neovim_prefix = home / ".local" / "opt" / "neovim"
    assert baseline.value_for(depart.directory_key(font_dir)) == {"state": "absent"}
    assert baseline.value_for(depart.directory_key(neovim_prefix)) == {
        "state": "absent"
    }


def test_capture_departure_baseline_captures_shared_nvim_dirs(home, links):
    ctx = make_ctx(home)
    install.capture_departure_baseline(ctx, links)
    baseline = depart.load_baseline(ctx.state_dir)
    for parts in depart.SHARED_NEOVIM_DIRS:
        key = depart.directory_key(home.joinpath(*parts))
        assert baseline.value_for(key) == {"state": "absent"}


def test_capture_departure_baseline_captures_runtime_nvm(home, links):
    ctx = make_ctx(home)
    install.capture_departure_baseline(ctx, links)
    baseline = depart.load_baseline(ctx.state_dir)
    assert baseline.value_for(depart.runtime_key(home / ".nvm")) == {"state": "absent"}


def test_capture_departure_baseline_first_layer_is_immutable_across_runs(home, links):
    ctx = make_ctx(home)
    install.capture_departure_baseline(ctx, links)  # first run: rc absent

    (home / ".zshrc").write_text("added after baseline was captured")
    install.capture_departure_baseline(ctx, links)  # second run

    baseline = depart.load_baseline(ctx.state_dir)
    # Still recorded as absent from the first (immutable) layer — a
    # later-observed present value must never overwrite it.
    assert baseline.value_for(depart.file_key(home / ".zshrc")) == {"state": "absent"}
    assert len(baseline.layers) == 1  # nothing genuinely unrecorded to add


def test_capture_departure_baseline_runs_via_run_install(home, links, offline_install):
    ctx = make_ctx(home, harnesses=("claude",))
    install.run_install(ctx, links)
    assert depart.baseline_path(ctx.state_dir).is_file()


# ── rollback deletes departure state ────────────────────────────────────────


def test_real_rollback_deletes_baseline_and_blobs(home, links, offline_install):
    (home / ".zshrc").write_text("x")
    ctx = make_ctx(home, harnesses=("claude",))
    install.run_install(ctx, links)
    assert depart.baseline_path(ctx.state_dir).is_file()
    assert list(ctx.state_dir.glob("baseline-snapshot-*.blob"))

    install.do_rollback(make_ctx(home))

    assert not depart.baseline_path(ctx.state_dir).exists()
    assert not list(ctx.state_dir.glob("baseline-snapshot-*.blob"))


def test_rollback_wipe_dry_run_preview_excludes_departure_state(
    home, links, offline_install, monkeypatch, capsys
):
    (home / ".zshrc").write_text("x")
    ctx = make_ctx(home, harnesses=("claude",))
    install.run_install(ctx, links)

    monkeypatch.setattr(
        install,
        "run_command",
        lambda cmd, **k: install.CommandResult(True, ""),
    )
    code = install.do_rollback(make_ctx(home, wipe=True, dry_run=True))
    out = capsys.readouterr().out

    assert code == 0
    assert f"would remove empty state directory {ctx.state_dir}" in out
