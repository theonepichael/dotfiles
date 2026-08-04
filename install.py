#!/usr/bin/env python3
"""install.py — dotfiles + AI-harness provisioner for macOS and Linux/WSL.

Ported from the zsh ``install.sh`` this repo used through mid-2026; the
shell script is now a thin bootstrap that locates a Python 3.12+ and execs
this file.

Two properties from the shell version are load-bearing and preserved here:

* **Nothing aborts the run.** There is no ``set -e`` equivalent and no
  bare ``raise`` in the install path — a blocked installer or an offline
  package mirror must not stop the steps after it. Every failure is
  collected by :class:`Reporter` and printed loudly in the end-of-run
  summary; the exit code is 1 if anything was skipped, 0 otherwise.
* **Every file mutation is recorded** to an append-only history log
  (``~/.local/state/dotfiles/history.jsonl``) that never gets truncated, so
  ``--rollback`` reverses *every* run ever recorded, not just the most
  recent one. Packages are reported but never uninstalled.

The dotfile symlink table itself lives in ``links.toml`` next to this file,
not in code — see that file's header for the per-entry schema.

Requires Python 3.12+.
"""

import argparse
import json
import os
import platform
import plistlib
import shutil
import subprocess
import sys
import tempfile
import tomllib
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import NoReturn

VALID_HARNESSES = ("claude", "copilot", "opencode", "agy")
VALID_PROFILES = ("personal", "work")

# Pinned rather than "latest" so every machine ends up with byte-identical
# fonts; bump manually to upgrade. A version-marker file next to the fonts
# keeps re-runs from re-downloading and re-extracting 90+ files every time.
NERD_FONT_VERSION = "3.4.0"
NERD_FONT_URL = (
    "https://github.com/ryanoasis/nerd-fonts/releases/download/"
    f"v{NERD_FONT_VERSION}/JetBrainsMono.zip"
)

BREW_FORMULAE = (
    "python@3.13",
    "uv",
    "ruff",
    "tmux",
    "zoxide",
    "eza",
    "bat",
    "ripgrep",
    "lsd",
    "ncdu",
    "tldr",
    "oh-my-posh",
    "neovim",
    "fd",
)
BREW_CASKS = (
    "karabiner-elements",
    "rectangle",
    "ghostty",
    "visual-studio-code",
    "alt-tab",
    "font-jetbrains-mono-nerd-font",
)
LINUX_PACKAGES = (
    "tmux",
    "zoxide",
    "eza",
    "bat",
    "lsd",
    "ncdu",
    "tldr",
    "ripgrep",
    "unzip",
    "lsof",
    "xclip",
    "fontconfig",
    "neovim",
    "fd-find",
)

# Caps Lock → Escape, in the numeric form macOS stores keyboard modifier
# remaps in (HID usage page << 32 | usage).
CAPS_LOCK_TO_ESCAPE = [
    {
        "HIDKeyboardModifierMappingSrc": 30064771129,  # Caps Lock
        "HIDKeyboardModifierMappingDst": 30064771113,  # Escape
    }
]

USAGE = """\
usage: ./install.sh --harness=<claude,copilot,opencode,agy>[,...] [--profile=personal|work] [--rollback] [--wipe] [--force] [--dry-run]

  --harness   required unless --rollback. Comma-separated, at least one of:
              claude, copilot, opencode, agy. No default — every run must
              state its intent explicitly. Purely additive: omitting a harness
              you previously selected does NOT uninstall or clean it up,
              it just skips re-provisioning it this run. Removal is a
              --rollback concern (reverses every run recorded in the
              history file, not just the most recent one) or manual cleanup.
  --profile   personal (default) or work. Controls machine-level concerns:
              excludes watchcommit, excludes personal API-key setup, seeds
              tightened settings where a profile-specific variant exists
              (settings.work.json), and excludes opencode entirely — it is
              never installed on a work machine, regardless of --harness.
              Otherwise never restricts which harness(es) you can choose —
              --profile=work --harness=claude is honored as stated.
  --rollback  reverse every file mutation (symlinks, copies, backups) ever
              recorded across all past runs, using the history file, then
              exit. Not limited to the most recent run — running install.sh
              several times over weeks and then rolling back undoes all of
              it in one shot, oldest run included. Packages are reported
              but never uninstalled. Must be used alone (no --harness,
              --profile, or --force) — except --dry-run and --wipe, see
              below.
  --wipe      modifier for --rollback: instead of restoring the original
              pre-dotfiles files from their .bak backups, deletes the
              backups outright, so nothing dotfiles-related is left behind.
              Also sweeps untracked state the installer creates but never
              records in its history — nvim runtime dirs
              (~/.local/share/nvim, ~/.local/state/nvim, ~/.cache/nvim) and,
              on Linux, the watchcommit systemd --user service (disabled
              and stopped). Packages are still never touched. Excludes the
              macOS watchcommit launchd agent, Rectangle preferences, and
              the Caps Lock→Escape remap — no clean filesystem-delete
              equivalent for those. Requires --rollback.
  --force     override the work-profile guard on a machine previously
              provisioned with --profile=work
  --dry-run   print what every step would do without doing it: no packages
              installed, no files written/symlinked/removed, no history
              written. Detection (what's already installed, which
              profile/harness branches apply) still runs for real, so the
              preview reflects actual machine state. The one flag allowed
              alongside --rollback, to preview an undo before running it.

Examples:
  ./install.sh --harness=claude
  ./install.sh --profile=work --harness=copilot
  ./install.sh --harness=claude,opencode
  ./install.sh --harness=claude,agy
  ./install.sh --dry-run --harness=claude
  ./install.sh --dry-run --rollback
  ./install.sh --rollback --wipe        # full rollback to a blank slate

Exits 0 if every step ran, 1 if any step was skipped (see summary)."""


# ── terminal colors ───────────────────────────────────────────────────────────


class Palette:
    """ANSI colorizer that no-ops when color isn't appropriate.

    Raw escape codes rather than a third-party library: this script runs on
    a machine that by definition hasn't been provisioned yet, so it can only
    depend on the standard library.
    """

    RESET = "\x1b[0m"

    def __init__(self, enabled: bool) -> None:
        self.enabled = enabled

    def _wrap(self, text: str, code: str) -> str:
        return f"{code}{text}{self.RESET}" if self.enabled else text

    def header(self, text: str) -> str:
        """Section header (``==>`` lines) and the summary banner."""
        return self._wrap(text, "\x1b[1;36m")

    def ok(self, text: str) -> str:
        """A mutation that succeeded."""
        return self._wrap(text, "\x1b[32m")

    def warn(self, text: str) -> str:
        """A skipped step or a drift report — not fatal, but read it."""
        return self._wrap(text, "\x1b[33m")

    def error(self, text: str) -> str:
        """A hard error (argument errors, blocked run)."""
        return self._wrap(text, "\x1b[31m")

    def dim(self, text: str) -> str:
        """Dry-run previews and other informational asides."""
        return self._wrap(text, "\x1b[2m")


def color_enabled(stream: object) -> bool:
    """Return whether ANSI codes should be emitted to ``stream``.

    Honors the ``NO_COLOR`` convention (any non-empty value disables color)
    and ``TERM=dumb``, and otherwise only colorizes an interactive terminal
    so piped/redirected output stays clean for grep.
    """
    if os.environ.get("NO_COLOR"):
        return False
    if os.environ.get("TERM") == "dumb":
        return False
    isatty = getattr(stream, "isatty", None)
    return bool(isatty and isatty())


# Rebound in main() once the output stream is known. Default-off so any
# import-time or test-time use is plain text.
PALETTE = Palette(False)


# ── skip-and-report plumbing ──────────────────────────────────────────────────


@dataclass
class Reporter:
    """Collects every step that didn't run, for the end-of-run summary.

    Install steps and rollback steps each get their own reporter so their
    tallies (and exit codes) stay separate, mirroring the shell version's
    two arrays.
    """

    skipped: list[str] = field(default_factory=list)

    def skip(self, step: str, reason: str) -> None:
        """Record and print a skipped install step.

        Args:
            step: What was being attempted, e.g. ``"apt package: eza"``.
            reason: Why it didn't happen, in user-facing prose.
        """
        self.note(f"{step} — {reason}")

    def note(self, message: str) -> None:
        """Record and print an already-formatted skip message."""
        self.skipped.append(message)
        print(PALETTE.warn(f"  !! SKIPPED: {message}"))

    def __len__(self) -> int:
        return len(self.skipped)


# ── run history (drives --rollback) ───────────────────────────────────────────


@dataclass
class Manifest:
    """Append-only JSON Lines history of every file mutation, across all runs.

    One JSON object per line, never truncated at the start of a run — that
    is what makes ``--rollback`` a full-history undo rather than a
    last-run-only one. A completed (non-dry-run) rollback deletes the file,
    which is correct: at that point every recorded mutation really has been
    undone, so the next run starts from a genuinely fresh history instead of
    one padded with already-reversed entries.

    Entry kinds:
        ``run``               ``timestamp``, ``profile``
        ``symlink-created``   ``dest``, ``src`` (the link's recorded target)
        ``file-copied``       ``dest``
        ``file-backed-up``    ``dest``, ``backup``
        ``package-installed`` ``name``
    """

    path: Path
    dry_run: bool = False

    def init_run(self, profile: str) -> None:
        """Open a new run in the history (or preview doing so)."""
        if self.dry_run:
            print(PALETTE.dim(f"  [dry-run] would record a new run in {self.path}"))
            return
        stamp = datetime.now().astimezone().isoformat(timespec="seconds")
        self._append({"kind": "run", "timestamp": stamp, "profile": profile})

    def record_symlink(self, dest: Path, src: Path) -> None:
        """Record a symlink this run created, along with what it points at."""
        self._record({"kind": "symlink-created", "dest": str(dest), "src": str(src)})

    def record_copy(self, dest: Path) -> None:
        """Record a file this run created by copying or downloading."""
        self._record({"kind": "file-copied", "dest": str(dest)})

    def record_backup(self, dest: Path, backup: Path) -> None:
        """Record a pre-existing file this run moved aside."""
        self._record(
            {"kind": "file-backed-up", "dest": str(dest), "backup": str(backup)}
        )

    def record_package(self, name: str) -> None:
        """Record an installed package (reported, never uninstalled, on undo)."""
        self._record({"kind": "package-installed", "name": name})

    def entries(self) -> list[dict[str, object]]:
        """Read every recorded entry, oldest first.

        Unparseable lines are dropped rather than raising: a truncated last
        line (power loss mid-append) must not make the whole history
        unrollbackable.
        """
        entries: list[dict[str, object]] = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                parsed = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, dict):
                entries.append(parsed)
        return entries

    def _record(self, entry: dict[str, object]) -> None:
        if self.dry_run:
            return
        self._append(entry)

    def _append(self, entry: dict[str, object]) -> None:
        """Durably append one JSON object as a line.

        Appends (rather than the temp-file + ``os.replace`` dance the repo's
        other Python scripts use for whole-file writes) because a single
        ``write`` of one short line under ``O_APPEND`` is already atomic
        with respect to other appenders; flush + fsync is what makes it
        survive a crash. The containing directory is fsynced the first time
        the file is created so the new directory entry is durable too.
        """
        self.path.parent.mkdir(parents=True, exist_ok=True)
        is_new = not self.path.exists()
        with open(self.path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        if is_new:
            _fsync_dir(self.path.parent)


def _fsync_dir(directory: Path) -> None:
    """fsync a directory so a newly created entry within it is durable."""
    fd = os.open(str(directory), os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


# ── options and run context ───────────────────────────────────────────────────


@dataclass(frozen=True)
class Options:
    """Validated command-line options for one invocation."""

    harnesses: tuple[str, ...] = ()
    profile: str = "personal"
    rollback: bool = False
    force: bool = False
    dry_run: bool = False
    wipe: bool = False


@dataclass
class Context:
    """Everything a step needs: paths, options, history, and the skip tally."""

    dotfiles: Path
    home: Path
    opts: Options
    manifest: Manifest
    reporter: Reporter
    system: str
    is_wsl: bool

    @property
    def state_dir(self) -> Path:
        """Where the history log and profile marker live."""
        return self.home / ".local" / "state" / "dotfiles"

    @property
    def profile_marker(self) -> Path:
        """Marker file recording that this machine was provisioned as work."""
        return self.state_dir / "profile"

    @property
    def is_mac(self) -> bool:
        return self.system == "Darwin"

    @property
    def is_linux(self) -> bool:
        return self.system == "Linux"

    def has_harness(self, name: str) -> bool:
        """Return whether ``name`` was named in ``--harness`` this run."""
        return name in self.opts.harnesses

    def display(self, path: Path) -> str:
        """Render ``path`` with the home directory shortened back to ``~``."""
        try:
            return f"~/{path.relative_to(self.home)}"
        except ValueError:
            return str(path)


def detect_wsl(system: str) -> bool:
    """Return whether this is a WSL kernel (as opposed to native Linux)."""
    if system != "Linux":
        return False
    if os.environ.get("WSL_DISTRO_NAME"):
        return True
    try:
        return "microsoft" in Path("/proc/version").read_text().lower()
    except OSError:
        return False


def build_context(opts: Options, dotfiles: Path | None = None) -> Context:
    """Assemble a :class:`Context` for a real run on this machine."""
    root = dotfiles or Path(__file__).resolve().parent
    home = Path.home()
    system = platform.system()
    state_dir = home / ".local" / "state" / "dotfiles"
    return Context(
        dotfiles=root,
        home=home,
        opts=opts,
        manifest=Manifest(state_dir / "history.jsonl", dry_run=opts.dry_run),
        reporter=Reporter(),
        system=system,
        is_wsl=detect_wsl(system),
    )


# ── argument parsing ──────────────────────────────────────────────────────────


class _Parser(argparse.ArgumentParser):
    """ArgumentParser whose own errors exit 2 with the usage text, like the
    hand-rolled shell parser this replaces."""

    def error(self, message: str) -> NoReturn:
        """Route argparse's own parse failures through the shared exit path."""
        _fail(message, show_usage=True)


def _fail(message: str, *, show_usage: bool = False) -> NoReturn:
    """Print an argument error and exit 2, matching the shell version."""
    print(PALETTE.error(message), file=sys.stderr)
    if show_usage:
        print(USAGE, file=sys.stderr)
    raise SystemExit(2)


def parse_args(argv: Sequence[str]) -> Options:
    """Parse and validate the command line.

    argparse handles the raw flag shapes; every semantic rule below is
    checked explicitly, in the same order and with the same messages the
    shell version used, because those messages are the documented contract
    (``test/scenarios.sh`` greps for them).

    Args:
        argv: Arguments without the program name.

    Returns:
        The validated options.

    Raises:
        SystemExit: 0 for ``--help``, 2 for any argument error.
    """
    parser = _Parser(add_help=False, allow_abbrev=False)
    parser.add_argument("--profile", default="personal")
    # append, not store: --harness=claude --harness=copilot must accumulate
    # both, not silently drop the first on the second flag.
    parser.add_argument("--harness", action="append", default=[])
    parser.add_argument("--rollback", action="store_true")
    parser.add_argument("--wipe", action="store_true")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--dry-run", dest="dry_run", action="store_true")
    parser.add_argument("-h", "--help", dest="help", action="store_true")

    args, extras = parser.parse_known_args(list(argv))

    if args.help:
        print(USAGE)
        raise SystemExit(0)

    # Unknown flags are reported before anything else, so an invocation with
    # both a typo'd flag and a bad value names the typo first — the same
    # order the shell version's parse loop produced.
    if extras:
        _fail(f"unknown argument: {extras[0]}", show_usage=True)

    if args.profile not in VALID_PROFILES:
        _fail(f"invalid --profile: {args.profile} (must be personal or work)")

    harness_set = bool(args.harness)
    harnesses: list[str] = []
    for flag_value in args.harness:
        harnesses.extend(flag_value.split(","))

    # An empty --harness= (stray trailing comma, or a copy-paste mistake)
    # gets its own message, checked ahead of the unknown-harness loop below —
    # otherwise it would be reported as an "unknown harness" with a blank name.
    for harness in harnesses:
        if not harness:
            _fail("--harness has an empty value — check for a stray comma")

    for harness in harnesses:
        if harness not in VALID_HARNESSES:
            _fail(
                f"unknown harness: {harness} "
                "(must be claude, copilot, opencode, and/or agy)"
            )

    # opencode never belongs on a work machine, full stop — not "tightened
    # settings," excluded entirely, the same way watchcommit is.
    if args.profile == "work" and "opencode" in harnesses:
        _fail("--harness=opencode is not allowed with --profile=work")

    # --rollback is an undo-only action. Rejecting --profile/--force
    # alongside it (not just --harness) keeps them from being silently
    # ignored, which would mislead someone into thinking they rolled back
    # "as work" or similar.
    if args.rollback and (harness_set or args.profile != "personal" or args.force):
        _fail("--rollback must be used alone, with no other flags")

    if args.wipe and not args.rollback:
        _fail("--wipe can only be used with --rollback")

    if not args.rollback and not harness_set:
        _fail(
            "no --harness specified — pass at least one of: "
            "claude, copilot, opencode, agy",
            show_usage=True,
        )

    # Deduplicate while preserving first-seen order: --harness=claude,claude
    # is a typo, not a request to wire claude twice.
    seen: list[str] = []
    for harness in harnesses:
        if harness not in seen:
            seen.append(harness)

    return Options(
        harnesses=tuple(seen),
        profile=args.profile,
        rollback=args.rollback,
        force=args.force,
        dry_run=args.dry_run,
        wipe=args.wipe,
    )


# ── subprocess plumbing ───────────────────────────────────────────────────────


@dataclass(frozen=True)
class CommandResult:
    """Outcome of one external command: whether it succeeded, and its stdout."""

    ok: bool
    stdout: str = ""


def run_command(
    cmd: Sequence[str] | str, *, shell: bool = False, capture: bool = False
) -> CommandResult:
    """Run an external command, returning success rather than raising.

    Every package manager, installer, and service call in this file goes
    through this one wrapper — both so a missing binary degrades to a skip
    instead of a traceback, and so tests can stub the whole external world
    with a single monkeypatch.

    Args:
        cmd: Argument list, or a shell command string when ``shell`` is set.
        shell: Run through ``/bin/sh`` (needed for the ``curl … | sh``
            installers upstream projects publish).
        capture: Capture stdout instead of letting it stream to the terminal,
            and discard stderr. Every capture=True call site here is a
            detection probe (``brew shellenv``, ``systemctl --user
            show-environment``, ``nvim --version``), where a diagnostic on
            stderr is noise the caller already turns into a clean skip
            message — the same reason the shell version redirected these
            with ``&>/dev/null``.

    Returns:
        A :class:`CommandResult`; ``ok`` is False for a non-zero exit, a
        missing executable, or a permission error.
    """
    try:
        proc = subprocess.run(
            cmd,
            shell=shell,
            check=False,
            text=True,
            stdout=subprocess.PIPE if capture else None,
            stderr=subprocess.DEVNULL if capture else None,
        )
    except OSError:
        return CommandResult(False)
    return CommandResult(proc.returncode == 0, proc.stdout or "")


def have(executable: str) -> bool:
    """Return whether ``executable`` is on PATH."""
    return shutil.which(executable) is not None


def _preview(message: str) -> None:
    """Print a dry-run preview line."""
    print(PALETTE.dim(f"  [dry-run] {message}"))


def _header(message: str) -> None:
    """Print a section header line."""
    print(PALETTE.header(message))


# ── packages: macOS ───────────────────────────────────────────────────────────


def _apply_brew_shellenv(brew: Path) -> None:
    """Put Homebrew on PATH for the rest of this process.

    The shell version could ``eval "$(brew shellenv)"``; a Python process
    can't source shell output, so the ``export KEY="value"`` lines are
    parsed back into ``os.environ`` instead.
    """
    result = run_command([str(brew), "shellenv"], capture=True)
    if not result.ok:
        return
    for line in result.stdout.splitlines():
        line = line.strip()
        if not line.startswith("export "):
            continue
        assignment = line[len("export ") :].rstrip(";")
        key, _, value = assignment.partition("=")
        if key:
            os.environ[key] = value.strip().strip('"')


def install_mac_packages(ctx: Context) -> None:
    """Bootstrap Homebrew if needed, then install the formulae and casks."""
    if not have("brew"):
        if ctx.opts.dry_run:
            # Homebrew itself is a real mutation, so it's previewed rather
            # than installed — which means the "brew unavailable" branch
            # below fires in dry-run on a brew-less machine just like it
            # would for real. A preview can only see one install-time
            # dependency deep.
            _preview(
                "would install Homebrew "
                "(curl raw.githubusercontent.com/Homebrew/install | bash)"
            )
        else:
            _header("==> Installing Homebrew...")
            installed = run_command(
                '/bin/bash -c "$(curl -fsSL '
                'https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"',
                shell=True,
            )
            if not installed.ok:
                ctx.reporter.skip("Homebrew", "installer failed (network blocked?)")

    for candidate in (Path("/opt/homebrew/bin/brew"), Path("/usr/local/bin/brew")):
        if os.access(candidate, os.X_OK):
            _apply_brew_shellenv(candidate)
            break

    if not have("brew"):
        ctx.reporter.skip("brew formulae + casks", "brew unavailable")
        return

    if ctx.opts.dry_run:
        _preview(f"would install formulae: {' '.join(BREW_FORMULAE)}")
        _preview(f"would install casks: {' '.join(BREW_CASKS)}")
        return

    _header("==> Installing formulae...")
    if run_command(["brew", "install", *BREW_FORMULAE]).ok:
        ctx.manifest.record_package("brew formulae")
    else:
        ctx.reporter.skip("brew formulae", "brew install failed")

    _header("==> Installing casks...")
    if run_command(["brew", "install", "--cask", *BREW_CASKS]).ok:
        ctx.manifest.record_package("brew casks")
    else:
        ctx.reporter.skip("brew casks", "brew install --cask failed")


# ── packages: Linux ───────────────────────────────────────────────────────────


def _install_linux_packages_one_by_one(ctx: Context, manager: str) -> None:
    """Install each package with its own package-manager invocation.

    Deliberately not one batched install command: ``apt-get install`` fails
    atomically on the first unresolvable name, which would block every
    package after it — e.g. eza/lsd don't exist before Ubuntu 24.04, which
    used to silently take tmux/bat/ncdu/tldr/ripgrep/unzip down with them on
    22.04 machines. Same reasoning applies to dnf.
    """
    base = (
        ["sudo", "dnf", "install", "-y"]
        if manager == "dnf"
        else ["sudo", "apt-get", "install", "-y"]
    )
    _header(f"==> Installing packages ({manager})...")
    for pkg in LINUX_PACKAGES:
        if ctx.opts.dry_run:
            _preview(f"would run: {' '.join(base)} {pkg}")
        elif run_command([*base, pkg]).ok:
            ctx.manifest.record_package(pkg)
        else:
            ctx.reporter.skip(
                f"{manager} package: {pkg}",
                "not available in this release's repos, or install failed",
            )


def _shim(ctx: Context, shim_name: str, real_name: str) -> None:
    """Point ``~/.local/bin/<shim_name>`` at a differently-named binary.

    Debian/Ubuntu rename two of these packages' binaries to avoid conflicts
    (bat → batcat, fd → fdfind); Fedora installs them under their real
    names, so this is a no-op there.
    """
    if not have(real_name) or have(shim_name):
        return
    target = ctx.home / ".local" / "bin" / shim_name
    if ctx.opts.dry_run:
        _preview(f"would shim {shim_name} → {real_name} ({ctx.display(target)})")
        return
    real_path = Path(shutil.which(real_name) or real_name)
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.is_symlink() or target.exists():
        target.unlink()
    target.symlink_to(real_path)
    ctx.manifest.record_symlink(target, real_path)
    print(PALETTE.ok(f"  shimmed {shim_name} → {real_name}"))


def _install_uv(ctx: Context) -> None:
    """Install uv (not packaged in apt/dnf) via the official installer."""
    if have("uv"):
        return
    if ctx.opts.dry_run:
        _preview("would install uv (curl astral.sh/uv/install.sh | sh)")
        return
    _header("==> Installing uv...")
    if run_command("curl -LsSf https://astral.sh/uv/install.sh | sh", shell=True).ok:
        ctx.manifest.record_package("uv")
        # The installer writes ~/.local/bin/env for shells to source; this
        # process can't source it, so put the same directory on PATH
        # directly, otherwise the `uv tool install ruff` step below can't
        # see the uv that was just installed.
        _prepend_path(ctx.home / ".local" / "bin")
    else:
        ctx.reporter.skip("uv", "installer failed (network blocked?)")


def _prepend_path(directory: Path) -> None:
    """Put ``directory`` at the front of this process's PATH."""
    os.environ["PATH"] = f"{directory}{os.pathsep}{os.environ.get('PATH', '')}"


def _install_nerd_font(ctx: Context) -> None:
    """Install the pinned JetBrainsMono Nerd Font, unless already at version.

    The patched (icon-glyph) variant isn't in apt/dnf at all, so the release
    zip is pulled directly.
    """
    font_dir = ctx.home / ".local" / "share" / "fonts" / "JetBrainsMonoNerdFont"
    marker = font_dir / ".nerd-fonts-version"
    try:
        current = marker.read_text().strip()
    except OSError:
        current = ""
    if current == NERD_FONT_VERSION:
        return

    if ctx.opts.dry_run:
        _preview(
            f"would install JetBrainsMono Nerd Font v{NERD_FONT_VERSION} to {font_dir}"
        )
        return

    _header(f"==> Installing JetBrainsMono Nerd Font v{NERD_FONT_VERSION}...")
    tmp_dir = Path(tempfile.mkdtemp())
    try:
        archive = tmp_dir / "JetBrainsMono.zip"
        downloaded = run_command(["curl", "-fLo", str(archive), NERD_FONT_URL]).ok
        extracted = False
        if downloaded:
            font_dir.mkdir(parents=True, exist_ok=True)
            extracted = run_command(
                ["unzip", "-oq", str(archive), "-d", str(font_dir)]
            ).ok
        if downloaded and extracted:
            marker.write_text(f"{NERD_FONT_VERSION}\n")
            ctx.manifest.record_package(f"JetBrainsMono Nerd Font v{NERD_FONT_VERSION}")
            run_command(["fc-cache", "-f", str(font_dir)], capture=True)
            print(PALETTE.ok(f"  installed to {font_dir}"))
        else:
            ctx.reporter.skip(
                "JetBrainsMono Nerd Font",
                "download/extract failed (network blocked, or unzip missing?)",
            )
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def install_linux_packages(ctx: Context) -> None:
    """Install everything the Linux/WSL branch owns: distro packages and extras."""
    if have("dnf"):
        if ctx.opts.dry_run:
            _preview("would run: sudo dnf makecache")
        else:
            _header("==> Refreshing dnf package metadata...")
            if not run_command(["sudo", "dnf", "makecache"]).ok:
                ctx.reporter.skip(
                    "dnf makecache", "dnf makecache failed (offline or blocked?)"
                )
        _install_linux_packages_one_by_one(ctx, "dnf")
    else:
        if ctx.opts.dry_run:
            _preview("would run: sudo apt-get update")
        else:
            _header("==> Updating apt package lists...")
            if not run_command(["sudo", "apt-get", "update"]).ok:
                ctx.reporter.skip(
                    "apt update", "apt-get update failed (offline or blocked?)"
                )
        _install_linux_packages_one_by_one(ctx, "apt")

    _shim(ctx, "bat", "batcat")
    _shim(ctx, "fd", "fdfind")
    _install_uv(ctx)

    if have("uv"):
        if ctx.opts.dry_run:
            _preview("would run: uv tool install ruff")
        elif run_command(["uv", "tool", "install", "ruff"]).ok:
            ctx.manifest.record_package("ruff")
        else:
            ctx.reporter.skip("ruff", "uv tool install failed")
    else:
        ctx.reporter.skip("ruff", "uv unavailable")

    if not have("oh-my-posh"):
        if ctx.opts.dry_run:
            _preview(
                "would install oh-my-posh "
                "(curl ohmyposh.dev/install.sh | bash -s -- -d ~/.local/bin)"
            )
        else:
            _header("==> Installing oh-my-posh...")
            bin_dir = ctx.home / ".local" / "bin"
            if run_command(
                f"curl -s https://ohmyposh.dev/install.sh | bash -s -- -d {bin_dir}",
                shell=True,
            ).ok:
                ctx.manifest.record_package("oh-my-posh")
            else:
                ctx.reporter.skip(
                    "oh-my-posh",
                    "installer failed (network blocked, or unzip missing?)",
                )

    _install_nerd_font(ctx)


# ── packages: Node-based harnesses ────────────────────────────────────────────


def _activate_nvm_node(ctx: Context) -> None:
    """Put the newest nvm-installed node on PATH for the rest of this process.

    ``nvm`` is a shell function, so unlike the shell version this process
    can't ``source nvm.sh`` and have ``npm`` appear on PATH — the installed
    node's bin directory is added explicitly instead.
    """
    versions = ctx.home / ".nvm" / "versions" / "node"
    if not versions.is_dir():
        return
    candidates = sorted(p for p in versions.iterdir() if (p / "bin").is_dir())
    if candidates:
        _prepend_path(candidates[-1] / "bin")


def install_node(ctx: Context) -> None:
    """Install NVM and a Node LTS — only for the harnesses that need npm.

    opencode and agy both manage their own runtime externally (agy is a
    standalone Go binary, not an npm package), so nothing here runs when
    they're the only selection.
    """
    if not (ctx.has_harness("claude") or ctx.has_harness("copilot")):
        return

    if not (ctx.home / ".nvm").is_dir():
        if ctx.opts.dry_run:
            _preview("would install NVM (curl nvm-sh/nvm install.sh | bash)")
        else:
            _header("==> Installing NVM...")
            if not run_command(
                "curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/HEAD/"
                "install.sh | bash",
                shell=True,
            ).ok:
                ctx.reporter.skip("NVM", "installer failed (network blocked?)")

    if have("npm"):
        return

    _activate_nvm_node(ctx)
    if have("npm"):
        return

    nvm_sh = ctx.home / ".nvm" / "nvm.sh"
    if not nvm_sh.is_file():
        return
    if ctx.opts.dry_run:
        _preview("would run: nvm install --lts")
        return
    if run_command(f'. "{nvm_sh}" && nvm install --lts', shell=True).ok:
        _activate_nvm_node(ctx)
    else:
        ctx.reporter.skip("node", "nvm install --lts failed")


def install_npm_harness(ctx: Context, harness: str, label: str, package: str) -> None:
    """Install one npm-distributed harness CLI, if it was selected."""
    if not ctx.has_harness(harness):
        print(f"  {label}: skipped (not in --harness)")
        return
    if not have("npm"):
        ctx.reporter.skip(label, "npm unavailable (NVM install failed or skipped)")
        return
    if ctx.opts.dry_run:
        _preview(f"would run: npm install -g {package}")
        return
    _header(f"==> Installing {label}...")
    if run_command(["npm", "install", "-g", package]).ok:
        ctx.manifest.record_package(package)
    else:
        ctx.reporter.skip(label, "npm install failed (registry blocked?)")


# ── symlink engine ────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class LinkSpec:
    """One row of ``links.toml``: a repo file and where it gets linked."""

    src: str
    dest: str
    harness: str | None = None
    platform: str | None = None
    wsl: str | None = None
    profile_exclude: tuple[str, ...] = ()


_LINK_FIELDS = {"src", "dest", "harness", "platform", "wsl", "profile_exclude"}


def load_links(path: Path) -> list[LinkSpec]:
    """Parse ``links.toml`` into an ordered list of link specs.

    Unknown keys and bad values are rejected loudly rather than ignored — a
    typo'd gate (``harnes = "claude"``) would otherwise silently widen a
    link to every run.

    Args:
        path: Path to the TOML table.

    Returns:
        The specs, in file order.

    Raises:
        ValueError: If the file is malformed or an entry is invalid.
    """
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ValueError(f"{path}: {exc}") from exc

    rows = data.get("link", [])
    if not isinstance(rows, list):
        raise ValueError(f"{path}: expected a [[link]] array")

    specs: list[LinkSpec] = []
    for index, row in enumerate(rows, start=1):
        if not isinstance(row, dict):
            raise ValueError(f"{path}: entry {index} is not a table")
        unknown = sorted(set(row) - _LINK_FIELDS)
        if unknown:
            raise ValueError(f"{path}: entry {index} has unknown key(s): {unknown}")
        for required in ("src", "dest"):
            if not isinstance(row.get(required), str) or not row[required]:
                raise ValueError(f"{path}: entry {index} is missing '{required}'")
        harness = row.get("harness")
        if harness is not None and harness not in VALID_HARNESSES:
            raise ValueError(f"{path}: entry {index} has unknown harness {harness!r}")
        os_gate = row.get("platform")
        if os_gate is not None and os_gate not in ("mac", "linux"):
            raise ValueError(f"{path}: entry {index} has unknown platform {os_gate!r}")
        wsl = row.get("wsl")
        if wsl is not None and wsl not in ("only", "exclude"):
            raise ValueError(f"{path}: entry {index} has unknown wsl value {wsl!r}")
        excluded = row.get("profile_exclude", [])
        if not isinstance(excluded, list) or any(
            profile not in VALID_PROFILES for profile in excluded
        ):
            raise ValueError(f"{path}: entry {index} has invalid profile_exclude")
        specs.append(
            LinkSpec(
                src=row["src"],
                dest=row["dest"],
                harness=harness,
                platform=os_gate,
                wsl=wsl,
                profile_exclude=tuple(excluded),
            )
        )
    return specs


def link_applies(spec: LinkSpec, ctx: Context) -> bool:
    """Return whether ``spec`` should be linked for this run's machine/options."""
    if spec.harness is not None and not ctx.has_harness(spec.harness):
        return False
    if spec.platform == "mac" and not ctx.is_mac:
        return False
    if spec.platform == "linux" and not ctx.is_linux:
        return False
    if spec.wsl == "exclude" and ctx.is_wsl:
        return False
    if spec.wsl == "only" and not ctx.is_wsl:
        return False
    if ctx.opts.profile in spec.profile_exclude:
        return False
    return True


def expand_dest(dest: str, home: Path) -> Path:
    """Expand a ``links.toml`` destination against ``home``.

    Explicit rather than :func:`os.path.expanduser` so the destination
    tracks the context's home directory (which tests point at a temporary
    one) instead of the process environment.
    """
    if dest == "~":
        return home
    if dest.startswith("~/"):
        return home / dest[2:]
    return Path(dest)


def symlink(ctx: Context, src: Path, dest: Path) -> bool:
    """Link ``dest`` → ``src``, backing up whatever non-symlink is in the way.

    An already-correct symlink is a no-op and is deliberately *not*
    re-recorded in the history: recording it again on every re-run would
    make a later rollback try to remove a link an earlier run created and
    already accounted for.

    Args:
        ctx: The run context.
        src: Absolute path inside the repo.
        dest: Absolute destination path.

    Returns:
        True if the link is in place (or would be, in dry-run), False if the
        step was skipped.
    """
    if ctx.opts.dry_run:
        if dest.is_symlink():
            current = os.readlink(dest)
            if current == str(src):
                _preview(f"{dest} already correctly linked → {src}, no-op")
            else:
                _preview(f"would relink {dest} → {src} (currently → {current})")
        elif dest.exists():
            _preview(f"would back up {dest} → {dest}.bak, then link {dest} → {src}")
        else:
            _preview(f"would link {dest} → {src}")
        return True

    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
    except OSError:
        ctx.reporter.skip(f"symlink {dest}", "could not create parent directory")
        return False

    was_link = dest.is_symlink()
    if dest.exists() and not was_link:
        backup = dest.with_name(dest.name + ".bak")
        try:
            shutil.move(str(dest), str(backup))
        except (OSError, shutil.Error):
            ctx.reporter.skip(f"symlink {dest}", "could not back up existing file")
            return False
        ctx.manifest.record_backup(dest, backup)
        print(f"  Backing up {dest} → {backup}")

    try:
        # Unlink first rather than relying on an atomic replace: `ln -sf`
        # onto an existing symlink-to-a-directory would create the new link
        # *inside* that directory instead of replacing it.
        if dest.is_symlink() or dest.exists():
            dest.unlink()
        dest.symlink_to(src)
    except OSError:
        ctx.reporter.skip(f"symlink {dest}", "ln failed")
        return False

    if not was_link:
        ctx.manifest.record_symlink(dest, src)
    print(PALETTE.ok(f"  linked {dest}"))
    return True


def _vscode_wsl_user_dir() -> Path | None:
    """Locate the Windows-side VS Code user directory from WSL.

    Under WSL, VS Code is normally driven from the Windows GUI via the
    Remote-WSL extension, so the real user settings.json lives in the
    Windows user profile, not the WSL filesystem. The profile directory is
    derived from the Windows-side ``code`` shim's own path (inherited onto
    PATH via WSL interop) rather than hardcoding a username.

    Returns:
        The ``.../AppData/Roaming/Code/User`` directory, or None if no
        Windows-side ``code`` CLI is on PATH.
    """
    code_bin = shutil.which("code")
    if not code_bin:
        return None
    parts = Path(code_bin).parts
    if "AppData" not in parts or not code_bin.startswith("/mnt/"):
        return None
    win_user_dir = Path(*parts[: parts.index("AppData")])
    if "Users" not in parts:
        return None
    return win_user_dir / "AppData" / "Roaming" / "Code" / "User"


def install_symlinks(ctx: Context, specs: Sequence[LinkSpec]) -> None:
    """Link every applicable ``links.toml`` entry, plus the WSL VS Code case."""
    _header("==> Symlinking dotfiles...")

    if ctx.opts.profile == "work":
        print("  watchcommit: excluded (work profile)")

    for spec in specs:
        if not link_applies(spec, ctx):
            continue
        symlink(ctx, ctx.dotfiles / spec.src, expand_dest(spec.dest, ctx.home))

    if ctx.is_wsl:
        user_dir = _vscode_wsl_user_dir()
        if user_dir is None:
            ctx.reporter.skip(
                "VS Code settings",
                "WSL detected but no Windows-side 'code' CLI found on PATH",
            )
        else:
            for name in ("settings.json", "keybindings.json"):
                symlink(ctx, ctx.dotfiles / "vscode" / name, user_dir / name)


# ── copy-once seeds and drift detection ───────────────────────────────────────


def json_key_drift(seed: dict[str, object], live: dict[str, object]) -> list[str]:
    """Return the top-level keys whose values differ between seed and live."""
    return sorted(k for k in set(seed) | set(live) if seed.get(k) != live.get(k))


def opencode_bypass_drift(
    seed: dict[str, object], live: dict[str, object]
) -> list[str]:
    """Return allowlist-bypass bash patterns present live but not in the seed.

    ``xargs`` and ``awk`` each invoke an arbitrary other command as their
    own argument (awk via ``system()``), so their presence isn't
    "individually risky command a profile could allow" — it defeats the
    allowlist entirely. They're called out separately from the generic
    top-level key diff because they live nested under ``permission.bash``,
    where a top-level diff would only say "permission" changed without
    saying which pattern came back.
    """
    seed_bash = _bash_permissions(seed)
    live_bash = _bash_permissions(live)
    return [k for k in ("xargs *", "awk *") if k in live_bash and k not in seed_bash]


def _bash_permissions(config: dict[str, object]) -> dict[str, object]:
    """Return ``permission.bash`` from an opencode config, or ``{}``."""
    permission = config.get("permission")
    if not isinstance(permission, dict):
        return {}
    bash = permission.get("bash")
    return bash if isinstance(bash, dict) else {}


def describe_settings_drift(seed: Path, live: Path) -> str:
    """Describe how a live settings.json diverged from its seed."""
    pair = _load_json_pair(seed, live)
    if pair is None:
        return ""
    return ", ".join(json_key_drift(*pair))


def describe_opencode_drift(seed: Path, live: Path) -> str:
    """Describe how a live opencode.jsonc diverged from its seed.

    A returned allowlist bypass outranks (and replaces) the generic key
    list: it's a security regression, not config drift to skim past.
    """
    pair = _load_json_pair(seed, live)
    if pair is None:
        return ""
    bypasses = opencode_bypass_drift(*pair)
    if bypasses:
        return (
            f"SECURITY: {', '.join(bypasses)} still allowed in your live "
            "opencode.jsonc (allowlist bypass) — delete the file and re-run to fix"
        )
    return ", ".join(json_key_drift(*pair))


def _load_json_pair(
    seed: Path, live: Path
) -> tuple[dict[str, object], dict[str, object]] | None:
    """Load a (seed, live) JSON pair, or None if either can't be read."""
    try:
        seed_data = json.loads(seed.read_text(encoding="utf-8"))
        live_data = json.loads(live.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return None
    if not isinstance(seed_data, dict) or not isinstance(live_data, dict):
        return None
    return seed_data, live_data


def seed_file(
    ctx: Context,
    seed: Path,
    dest: Path,
    *,
    skip_label: str,
    drift: Callable[[Path, Path], str],
) -> str:
    """Copy ``seed`` to ``dest`` once, or report drift if it's already there.

    These two files (Claude Code's settings.json, opencode's opencode.jsonc)
    are copied rather than symlinked because both tools rewrite them in
    place as permissions get approved live, which would replace a symlink
    with a plain file and silently detach it from the repo. So the repo copy
    is a *seed*: written once, never overwritten, with divergence reported
    instead.

    Args:
        ctx: The run context.
        seed: Repo-side seed file (already profile-resolved by the caller).
        dest: Live destination path.
        skip_label: Step name used if the copy fails.
        drift: Callback describing divergence when ``dest`` already exists.

    Returns:
        A drift description for the end-of-run summary, or ``""``.
    """
    if not dest.is_file():
        if ctx.opts.dry_run:
            _preview(f"would copy {ctx.display(dest)} (from {seed.name})")
            return ""
        try:
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy(seed, dest)
        except OSError:
            ctx.reporter.skip(skip_label, "copy failed")
            return ""
        ctx.manifest.record_copy(dest)
        print(PALETTE.ok(f"  copied {ctx.display(dest)} (from {seed.name})"))
        return ""
    return drift(seed, dest)


def seed_claude_settings(ctx: Context) -> tuple[str, str]:
    """Seed ~/.claude/settings.json, if Claude Code was selected.

    Returns:
        ``(seed filename, drift description)``; both empty when the harness
        wasn't selected.
    """
    if not ctx.has_harness("claude"):
        return "", ""
    name = "settings.work.json" if ctx.opts.profile == "work" else "settings.json"
    seed = ctx.dotfiles / "claude" / name
    dest = ctx.home / ".claude" / "settings.json"
    return name, seed_file(
        ctx,
        seed,
        dest,
        skip_label="settings.json seed",
        drift=describe_settings_drift,
    )


def seed_opencode_config(ctx: Context) -> tuple[str, str]:
    """Seed ~/.config/opencode/opencode.jsonc, if opencode was selected.

    opencode is never installed on a work machine at all — parse_args
    rejects --profile=work combined with --harness=opencode outright — so
    there is only one variant of this seed file.

    Returns:
        ``(seed filename, drift description)``; both empty when the harness
        wasn't selected.
    """
    if not ctx.has_harness("opencode"):
        return "", ""
    name = "opencode.jsonc"
    seed = ctx.dotfiles / "opencode" / name
    dest = ctx.home / ".config" / "opencode" / "opencode.jsonc"
    return name, seed_file(
        ctx,
        seed,
        dest,
        skip_label="opencode.jsonc seed",
        drift=describe_opencode_drift,
    )


# ── services ──────────────────────────────────────────────────────────────────


def enable_watchcommit_service(ctx: Context) -> None:
    """Enable and start watchcommit's systemd --user unit (Linux, non-work)."""
    if not ctx.is_linux or ctx.opts.profile == "work":
        return
    if (
        not have("systemctl")
        or not run_command(["systemctl", "--user", "show-environment"], capture=True).ok
    ):
        ctx.reporter.skip(
            "watchcommit service",
            "systemd --user unavailable (enable systemd in /etc/wsl.conf?)",
        )
        return
    if ctx.opts.dry_run:
        _preview(
            "would enable+start watchcommit systemd user service, "
            f"enable-linger for {_current_user()}"
        )
        return
    _header("==> Enabling watchcommit systemd user service...")
    run_command(["systemctl", "--user", "daemon-reload"])
    if run_command(
        ["systemctl", "--user", "enable", "--now", "watchcommit.service"]
    ).ok:
        # Without lingering, the service dies when the last WSL/SSH session
        # closes — enable-linger keeps the user manager (and this unit) up.
        if not run_command(
            ["loginctl", "enable-linger", _current_user()], capture=True
        ).ok:
            print(
                "  note: loginctl enable-linger failed — "
                "service won't survive full logout"
            )
    else:
        ctx.reporter.skip("watchcommit service", "systemctl --user enable --now failed")


def _current_user() -> str:
    """Return the invoking user's name, for loginctl."""
    return os.environ.get("USER") or os.environ.get("LOGNAME") or ""


def load_watchcommit_agent(ctx: Context) -> None:
    """(Re)load watchcommit's launchd agent (macOS, non-work)."""
    if ctx.opts.profile == "work":
        return
    plist = ctx.home / "Library" / "LaunchAgents" / "com.user.watchcommit.plist"
    if ctx.opts.dry_run:
        _preview("would (re)load watchcommit launchd agent")
        return
    _header("==> Loading watchcommit launchd agent...")
    run_command(["launchctl", "unload", str(plist)], capture=True)
    if not run_command(["launchctl", "load", str(plist)]).ok:
        ctx.reporter.skip("watchcommit agent", "launchctl load failed")


# ── macOS extras ──────────────────────────────────────────────────────────────


def import_rectangle_prefs(ctx: Context) -> None:
    """Import the repo's Rectangle window-manager preferences."""
    plist = ctx.dotfiles / "rectangle" / "com.knollsoft.Rectangle.plist"
    if ctx.opts.dry_run:
        _preview(f"would import Rectangle preferences from {plist}")
        return
    _header("==> Importing Rectangle preferences...")
    if not run_command(
        ["defaults", "import", "com.knollsoft.Rectangle", str(plist)]
    ).ok:
        ctx.reporter.skip("Rectangle preferences", "defaults import failed")


def set_caps_lock_to_escape(ctx: Context) -> None:
    """Remap Caps Lock to Escape by rewriting the ByHost GlobalPreferences plist.

    macOS stores the keyboard modifier mapping under a per-host preferences
    domain, keyed by a name that varies by OS version — hence the substring
    match on ``modifiermapping`` rather than a fixed key.
    """
    if ctx.opts.dry_run:
        _preview(
            "would set Caps Lock → Escape "
            "(rewrite ~/Library/Preferences/ByHost/.GlobalPreferences.*.plist)"
        )
        return

    _header("==> Setting Caps Lock → Escape...")
    byhost = ctx.home / "Library" / "Preferences" / "ByHost"
    plists = sorted(byhost.glob(".GlobalPreferences.*.plist"))
    if not plists:
        print("  No ByHost GlobalPreferences plist found — skipping")
        return

    for path in plists:
        try:
            with open(path, "rb") as handle:
                prefs = plistlib.load(handle)
            for key in list(prefs):
                if "modifiermapping" in key:
                    prefs[key] = CAPS_LOCK_TO_ESCAPE
            with open(path, "wb") as handle:
                plistlib.dump(prefs, handle)
        except (OSError, ValueError, plistlib.InvalidFileException):
            ctx.reporter.skip("Caps Lock → Escape", "plist rewrite failed")
            return
        print(f"  Updated {path.name}")


# ── editors ───────────────────────────────────────────────────────────────────


def install_vim_plug(ctx: Context) -> None:
    """Download vim-plug into ~/.vim/autoload, if it isn't there already."""
    target = ctx.home / ".vim" / "autoload" / "plug.vim"
    if target.is_file():
        return
    if ctx.opts.dry_run:
        _preview(f"would install vim-plug to {ctx.display(target)}")
        return
    _header("==> Installing vim-plug...")
    target.parent.mkdir(parents=True, exist_ok=True)
    url = "https://raw.githubusercontent.com/junegunn/vim-plug/master/plug.vim"
    if run_command(["curl", "-fLo", str(target), "--create-dirs", url]).ok:
        ctx.manifest.record_copy(target)
        print("  Run :PlugInstall inside vim to install plugins")
    else:
        ctx.reporter.skip("vim-plug", "download failed (network blocked?)")


def parse_nvim_version(output: str) -> tuple[int, int] | None:
    """Extract ``(major, minor)`` from ``nvim --version`` output.

    Returns:
        The parsed version, or None if the first line isn't recognizable.
    """
    first = output.splitlines()[0] if output.strip() else ""
    if not first.startswith("NVIM v"):
        return None
    parts = first[len("NVIM v") :].split(".")
    try:
        return int(parts[0]), int(parts[1])
    except (IndexError, ValueError):
        return None


def bootstrap_neovim(ctx: Context) -> None:
    """Sync the vendored Neovim config's plugins with lazy.nvim.

    The vendored config targets Neovim 0.11+; older distro repos still ship
    older builds (same class of version gap as eza/lsd on Ubuntu 22.04), so
    this degrades to a skip rather than the upstream config's own hard
    ``exit 1`` — consistent with this script never aborting a run.
    """
    if not have("nvim"):
        ctx.reporter.skip("Neovim plugin bootstrap", "nvim not installed")
        return

    result = run_command(["nvim", "--version"], capture=True)
    version = parse_nvim_version(result.stdout) if result.ok else None
    if version is None:
        ctx.reporter.skip("Neovim plugin bootstrap", "could not determine nvim version")
        return

    major, minor = version
    pretty = f"{major}.{minor}"
    if major == 0 and minor < 11:
        ctx.reporter.skip(
            "Neovim plugin bootstrap", f"nvim {pretty} found, config needs >=0.11"
        )
        return
    if ctx.opts.dry_run:
        _preview(f'would run: nvim --headless "+Lazy! sync" +qa (nvim {pretty})')
        return

    _header(f"==> Bootstrapping Neovim plugins (lazy.nvim sync, nvim {pretty})...")
    if run_command(["nvim", "--headless", "+Lazy! sync", "+qa"]).ok:
        print(PALETTE.ok("  plugins synced"))
    else:
        ctx.reporter.skip(
            "Neovim plugin sync",
            "'Lazy! sync' reported errors — run :Lazy sync manually inside nvim",
        )


# ── profile marker ────────────────────────────────────────────────────────────


def write_profile_marker(ctx: Context) -> None:
    """Mark this machine as work-provisioned, so later plain runs are guarded.

    Recorded as a copied file so a rollback removes it, resetting the guard
    along with everything else that run put in place.
    """
    if ctx.opts.profile != "work" or ctx.profile_marker.is_file():
        return
    if ctx.opts.dry_run:
        _preview(f"would write profile marker: {ctx.profile_marker}")
        return
    ctx.profile_marker.parent.mkdir(parents=True, exist_ok=True)
    ctx.profile_marker.write_text("work\n")
    ctx.manifest.record_copy(ctx.profile_marker)


def work_guard_blocks(ctx: Context) -> bool:
    """Return whether a plain personal run must be refused on this machine."""
    if ctx.opts.profile != "personal" or ctx.opts.force:
        return False
    try:
        return ctx.profile_marker.read_text().strip() == "work"
    except OSError:
        return False


# ── rollback ──────────────────────────────────────────────────────────────────


def do_rollback(ctx: Context) -> int:
    """Reverse every file mutation recorded across every past run.

    Walks the history newest-to-oldest so a path mutated by several runs
    ends up back at its oldest recorded state (the original pre-dotfiles
    file, not an intermediate one). Nothing here aborts: anything that
    doesn't match what was recorded is reported and the walk continues.

    Under ``--wipe``, backups are deleted instead of restored, and untracked
    state the installer creates but never records in the manifest (nvim
    runtime dirs, the Linux watchcommit service) is swept too — even when
    no manifest exists at all, e.g. a second ``--wipe`` run after the first
    already consumed it.

    Returns:
        Exit status — 1 if any step was skipped, else 0.
    """
    skips = Reporter()
    swept = False

    if ctx.opts.wipe:
        swept = _wipe_watchcommit(ctx, skips) or swept
        swept = _wipe_nvim_dirs(ctx, skips) or swept

    manifest = ctx.manifest
    if not manifest.path.is_file():
        if swept:
            print(
                PALETTE.header(
                    "Wipe swept untracked state — no recorded history to reverse."
                )
            )
            return _report_skips_and_exit(skips)
        print(
            PALETTE.error(f"no manifest at {manifest.path} — nothing to roll back"),
            file=sys.stderr,
        )
        return 1

    entries = manifest.entries()
    run_count = sum(1 for entry in entries if entry.get("kind") == "run")
    verb = "Would roll back" if ctx.opts.dry_run else "Rolling back"
    header_msg = f"==> {verb} {run_count} recorded run(s) from {manifest.path}"
    if ctx.opts.wipe:
        header_msg += (
            " — wipe mode: original configs discarded, not restored; "
            "untracked nvim/watchcommit state swept"
        )
    _header(header_msg)

    # Which backup paths this pass has already restored (or, under --wipe,
    # deleted). An older duplicate file-backed-up entry for the same path
    # (recorded across an earlier backup/rollback/reinstall cycle) is then
    # recognized as already handled rather than misreported as a missing
    # backup.
    restored: set[Path] = set()

    for entry in reversed(entries):
        match entry.get("kind"):
            case "symlink-created":
                _rollback_symlink(ctx, entry, skips)
            case "file-copied":
                _rollback_copy(ctx, entry)
            case "file-backed-up":
                _rollback_backup(ctx, entry, skips, restored)
            case "package-installed":
                print(
                    f"  package left installed (profile-independent): "
                    f"{entry.get('name', '')}"
                )
            case "run":
                print(
                    f"  (run was: {entry.get('timestamp', '')}, "
                    f"profile: {entry.get('profile', '')})"
                )

    if ctx.opts.dry_run:
        if ctx.opts.wipe:
            remaining = (
                [p for p in ctx.state_dir.iterdir() if p != manifest.path]
                if ctx.state_dir.is_dir()
                else []
            )
            if not remaining:
                _preview(f"would remove empty state directory {ctx.state_dir}")
        print(
            "Dry run complete — nothing was changed. "
            "Re-run without --dry-run to roll back for real."
        )
    else:
        manifest.path.unlink(missing_ok=True)
        if (
            ctx.opts.wipe
            and ctx.state_dir.is_dir()
            and not any(ctx.state_dir.iterdir())
        ):
            ctx.state_dir.rmdir()
        msg = (
            "Rollback complete — configuration wiped to a blank slate."
            if ctx.opts.wipe
            else "Rollback complete. Re-run ./install.sh with the intended profile."
        )
        print(PALETTE.header(msg))

    return _report_skips_and_exit(skips)


def _report_skips_and_exit(skips: Reporter) -> int:
    """Print the rollback skip tally, if any, and return the matching exit code."""
    if skips.skipped:
        print(
            PALETTE.warn(
                f"⚠ {len(skips.skipped)} rollback step(s) did not apply cleanly "
                "(see SKIPPED lines above)"
            )
        )
        return 1
    return 0


def _wipe_watchcommit(ctx: Context, skips: Reporter) -> bool:
    """Disable+stop the Linux watchcommit systemd --user unit, under --wipe.

    Gated on live filesystem state, not manifest entries — simpler than
    scanning history, and it's what makes this work even when no manifest
    exists at all.

    Returns:
        Whether the unit symlink existed at the start — that's what
        "swept something" means here, true regardless of whether the probe
        or the disable call then succeed.
    """
    if not (ctx.opts.wipe and ctx.is_linux):
        return False
    unit_path = ctx.home / ".config" / "systemd" / "user" / "watchcommit.service"
    if not unit_path.is_symlink():
        return False

    if (
        not have("systemctl")
        or not run_command(["systemctl", "--user", "show-environment"], capture=True).ok
    ):
        skips.note(
            f"{unit_path} exists but systemd --user is unavailable — "
            "could not disable the watchcommit service"
        )
        return True

    if ctx.opts.dry_run:
        _preview("would disable+stop the watchcommit systemd user service (wipe)")
        return True

    if run_command(
        ["systemctl", "--user", "disable", "--now", "watchcommit.service"]
    ).ok:
        print("  disabled+stopped watchcommit systemd user service")
    else:
        skips.note("could not disable+stop the watchcommit systemd user service")
    return True


def _wipe_nvim_dirs(ctx: Context, skips: Reporter) -> bool:
    """Sweep neovim's untracked runtime directories, under --wipe.

    Returns:
        Whether any of the three currently exist — "swept something" is
        based on pre-sweep state, not on whether removal (or its preview)
        then succeeds.
    """
    if not ctx.opts.wipe:
        return False
    dirs = (
        ctx.home / ".local" / "share" / "nvim",
        ctx.home / ".local" / "state" / "nvim",
        ctx.home / ".cache" / "nvim",
    )
    found = False
    for path in dirs:
        if not path.exists():
            continue
        found = True
        if ctx.opts.dry_run:
            _preview(f"would remove {path} (wipe)")
            continue
        try:
            shutil.rmtree(path)
        except OSError as exc:
            skips.note(f"could not remove {path}: {exc}")
            continue
        print(f"  removed {path}")
    return found


def _rollback_symlink(ctx: Context, entry: dict[str, object], skips: Reporter) -> None:
    """Undo one ``symlink-created`` entry, unless something else claimed the path."""
    dest = Path(str(entry.get("dest", "")))
    recorded_src = str(entry.get("src", ""))
    if not dest.is_symlink():
        return
    current = os.readlink(dest)
    if recorded_src and current != recorded_src:
        skips.note(
            f"symlink {dest} now points to {current}, not {recorded_src} — "
            "something else has claimed this path, leaving it alone"
        )
        return
    if ctx.opts.dry_run:
        _preview(f"would remove symlink {dest}")
        return
    try:
        dest.unlink()
    except OSError as exc:
        skips.note(f"could not remove symlink {dest}: {exc}")
        return
    print(f"  removed symlink {dest}")


def _rollback_copy(ctx: Context, entry: dict[str, object]) -> None:
    """Undo one ``file-copied`` entry."""
    dest = Path(str(entry.get("dest", "")))
    if not dest.is_file():
        return
    if ctx.opts.dry_run:
        _preview(f"would remove {dest}")
        return
    dest.unlink()
    print(f"  removed {dest}")


def _rollback_backup(
    ctx: Context, entry: dict[str, object], skips: Reporter, restored: set[Path]
) -> None:
    """Restore one ``file-backed-up`` entry from its ``.bak`` path.

    Under --wipe, the backup is deleted instead of restored — the original
    pre-dotfiles file is discarded, not brought back.
    """
    dest = Path(str(entry.get("dest", "")))
    backup = Path(str(entry.get("backup", "")))
    if backup.exists():
        if ctx.opts.dry_run:
            if ctx.opts.wipe:
                _preview(
                    f"would delete backup {backup} (wipe — {dest} will not be restored)"
                )
            else:
                _preview(f"would restore {dest} from {backup}")
            return
        if ctx.opts.wipe:
            try:
                backup.unlink()
            except OSError as exc:
                skips.note(f"could not delete backup {backup}: {exc}")
                return
            print(f"  deleted backup {backup} — original {dest} not restored (wipe)")
            restored.add(backup)
            return
        try:
            shutil.move(str(backup), str(dest))
        except (OSError, shutil.Error) as exc:
            skips.note(f"could not restore {dest} from {backup}: {exc}")
            return
        print(f"  restored {dest} from {backup}")
        restored.add(backup)
        return
    if backup not in restored:
        wording = (
            "already wiped, or removed outside install.sh"
            if ctx.opts.wipe
            else "already restored, or removed outside install.sh"
        )
        skips.note(f"backup {backup} for {dest} not found — {wording}")


# ── summary ───────────────────────────────────────────────────────────────────


def print_summary(
    ctx: Context, settings: tuple[str, str], opencode: tuple[str, str]
) -> None:
    """Print the loud end-of-run summary: skips, drift, and next steps."""
    dry = ctx.opts.dry_run
    print()
    label = "Dry run summary" if dry else "Install summary"
    print(PALETTE.header(f"════════ {label} — profile: {ctx.opts.profile} ════════"))

    if ctx.reporter.skipped:
        print(PALETTE.warn(f"⚠ {len(ctx.reporter.skipped)} step(s) DID NOT run:"))
        for item in ctx.reporter.skipped:
            print(PALETTE.error(f"  ✗ {item}"))
    elif dry:
        print(PALETTE.ok("✓ all steps previewed cleanly (no real detection failures)"))
    else:
        print(PALETTE.ok("✓ all steps completed"))

    for path, (seed_name, drift) in (
        ("~/.claude/settings.json", settings),
        ("~/.config/opencode/opencode.jsonc", opencode),
    ):
        if drift:
            print(PALETTE.warn(f"⚠ {path} drifted from {seed_name}: {drift}"))
            print("  (copy-once by design — port changes manually if wanted)")

    if dry:
        print("  dry run — nothing was changed; re-run without --dry-run to apply")
    else:
        print(
            f"  rollback available: ./install.sh --rollback "
            f"(history: {ctx.manifest.path})"
        )

    if dry:
        return

    print()
    print("Manual steps:")
    if ctx.is_mac:
        print("  - Log out and back in for Caps Lock → Escape to take effect")
        print("  - Open Karabiner-Elements → grant Input Monitoring + Accessibility")
        print("  - Open Rectangle → grant Accessibility permission")
    if ctx.opts.profile == "work":
        print("  - ~/.secrets is sourced if present — for work-issued tokens only;")
        print("    do NOT put a personal ANTHROPIC_API_KEY on this machine")
    elif ctx.has_harness("claude"):
        print(
            "  - Run 'claude login' if you haven't, so watchcommit can "
            "generate commit messages"
        )
    if ctx.is_linux:
        print("  - Restart your shell to pick up the new config")


# ── entry point ───────────────────────────────────────────────────────────────


def run_install(ctx: Context, specs: Sequence[LinkSpec]) -> int:
    """Run every install step in order and return the process exit status."""
    ctx.manifest.init_run(ctx.opts.profile)
    if ctx.opts.dry_run:
        _header(f"==> DRY RUN — no changes will be made. Profile: {ctx.opts.profile}")
    else:
        _header(f"==> Installing with profile: {ctx.opts.profile}")

    if ctx.is_mac:
        install_mac_packages(ctx)
    elif ctx.is_linux:
        install_linux_packages(ctx)

    install_node(ctx)
    install_npm_harness(ctx, "claude", "Claude Code", "@anthropic-ai/claude-code")
    install_npm_harness(ctx, "copilot", "Copilot CLI", "@github/copilot")

    install_symlinks(ctx, specs)
    opencode_drift = seed_opencode_config(ctx)
    settings_drift = seed_claude_settings(ctx)

    if ctx.is_mac:
        import_rectangle_prefs(ctx)
        set_caps_lock_to_escape(ctx)
        load_watchcommit_agent(ctx)
    enable_watchcommit_service(ctx)

    install_vim_plug(ctx)
    bootstrap_neovim(ctx)
    write_profile_marker(ctx)

    print_summary(ctx, settings_drift, opencode_drift)
    return 1 if ctx.reporter.skipped else 0


def main(argv: Sequence[str] | None = None) -> int:
    """Parse arguments, then either roll back or install.

    Returns:
        Process exit status: 0 clean, 1 something was skipped, 2 refused
        (bad arguments, or the work-profile guard).
    """
    global PALETTE
    PALETTE = Palette(color_enabled(sys.stdout))

    opts = parse_args(sys.argv[1:] if argv is None else argv)
    ctx = build_context(opts)

    if opts.rollback:
        return do_rollback(ctx)

    if work_guard_blocks(ctx):
        print(
            PALETTE.error(
                f"This machine is provisioned as WORK (marker: {ctx.profile_marker})."
            ),
            file=sys.stderr,
        )
        print(
            PALETTE.error(
                "Pass --profile=work, or --force to provision as personal anyway."
            ),
            file=sys.stderr,
        )
        return 2

    try:
        specs = load_links(ctx.dotfiles / "links.toml")
    except ValueError as exc:
        print(
            PALETTE.error(f"could not read the symlink table: {exc}"), file=sys.stderr
        )
        return 2

    return run_install(ctx, specs)


if __name__ == "__main__":
    raise SystemExit(main())
