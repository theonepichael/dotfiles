#!/usr/bin/env python3
"""Pristine-state departure mode: baseline capture and ownership tracking.

Kept separate from ``install.py`` — see that file's module docstring and the
execution plan this implements
(``~/.claude/data/grill/2026-08-13-dotfiles-depart-mode-v2-narrowed-plan.md``)
for the full design rationale. This module owns only the data model and
pure capture/comparison logic (Implementation Sequence step 1); CLI wiring,
package-transaction tracking, and the actual cleanup live alongside it in
``install.py`` and later modules as the feature grows.

Ownership keys are ``(type, path)`` pairs, rendered as ``"type:path"``
strings for storage (``file:/home/user/.zshrc``,
``package:uv-tool/ruff``, ...). Every key's baseline value is one of three
states — ``present``, ``absent``, or ``unknown`` — never conflated with
*unrecorded*, which means the key has no entry in any baseline layer at
all (see :func:`Baseline.is_unrecorded`).

Linux/WSL (apt) and Fedora (dnf) only — this feature does not apply on
macOS, matching Implementation Sequence step 6.
"""

import hashlib
import json
import os
from dataclasses import dataclass, field
from pathlib import Path

STATE_PRESENT = "present"
STATE_ABSENT = "absent"
STATE_UNKNOWN = "unknown"

RC_FILENAMES = (".bashrc", ".zshrc", ".profile")

# Shared Neovim XDG state/cache dirs the installer never owns the *contents*
# of, only whether the directory itself pre-existed — see
# ``_wipe_neovim_dirs`` in install.py for why these three specifically.
SHARED_NEOVIM_DIRS = (
    ("local", "share", "nvim"),
    ("local", "state", "nvim"),
    ("cache", "nvim"),
)


# ── ownership keys ────────────────────────────────────────────────────────


def file_key(path: Path) -> str:
    return f"file:{path}"


def symlink_key(path: Path) -> str:
    return f"symlink:{path}"


def directory_key(path: Path) -> str:
    return f"directory:{path}"


def package_key(manager: str, name: str) -> str:
    return f"package:{manager}/{name}"


def service_key(manager: str, name: str) -> str:
    return f"service:{manager}/{name}"


def runtime_key(root: Path) -> str:
    return f"runtime:{root}"


def key_type(key: str) -> str:
    """Return the ``type`` half of an ownership key string."""
    type_, _, _ = key.partition(":")
    return type_


# ── capturing individual keys ─────────────────────────────────────────────


def capture_file(path: Path, *, blob_dir: Path | None = None) -> dict[str, object]:
    """Capture a ``file:`` record: present (with size+sha256), absent, or unknown.

    A symlink at ``path`` is *not* a regular file for this key's purposes —
    it is captured separately as a ``symlink:`` key at the same path, since
    ``file:<path>`` and ``symlink:<path>`` are deliberately distinct keys
    for the same path (see the module docstring).

    Args:
        path: The file to capture.
        blob_dir: When given and the file is present, also write its
            content to a blob under this directory (normally the state
            directory) and record the digest as ``blob``. Callers only pass
            this for rc files and links.toml/seed destinations — the only
            categories that can ever need content restored, per
            Implementation Sequence step 1's blob-creation rule.
    """
    try:
        if path.is_symlink() or not path.is_file():
            return {"state": STATE_ABSENT}
        content = path.read_bytes()
    except OSError:
        return {"state": STATE_UNKNOWN}
    record: dict[str, object] = {
        "state": STATE_PRESENT,
        "size": len(content),
        "sha256": hashlib.sha256(content).hexdigest(),
    }
    if blob_dir is not None:
        record["blob"] = write_blob(blob_dir, content)
    return record


def capture_symlink(path: Path) -> dict[str, object]:
    """Capture a ``symlink:`` record: present (with target text), absent, unknown."""
    try:
        if not path.is_symlink():
            return {"state": STATE_ABSENT}
        target = os.readlink(path)
    except OSError:
        return {"state": STATE_UNKNOWN}
    return {"state": STATE_PRESENT, "target": target}


def capture_directory(path: Path) -> dict[str, object]:
    """Capture a ``directory:`` record: present, absent, or unknown.

    A symlink at ``path`` is not a directory for this key's purposes, same
    reasoning as :func:`capture_file`.
    """
    try:
        if path.is_symlink():
            return {"state": STATE_ABSENT}
        exists = path.is_dir()
    except OSError:
        return {"state": STATE_UNKNOWN}
    return {"state": STATE_PRESENT if exists else STATE_ABSENT}


def ancestor_directories(path: Path, home: Path) -> list[Path]:
    """Every directory strictly between ``path``'s parent and ``home``.

    Used to enumerate ``mkdir(parents=True)`` candidates for a destination
    the installer might create — ``~/.local/bin``, a symlink destination's
    parent, and so on — per Implementation Sequence step 1's parent-
    directory coverage bullet. ``home`` itself is never included (it always
    pre-exists and is never installer-owned), and a ``path`` outside
    ``home`` entirely yields an empty list rather than walking to the
    filesystem root.
    """
    ancestors: list[Path] = []
    current = path.parent
    while current != home and home in current.parents:
        ancestors.append(current)
        current = current.parent
    return ancestors


def capture_tree_manifest(root: Path) -> dict[str, object]:
    """Full recursive manifest for a small, fully installer-controlled tree.

    Every regular file's size+SHA-256, every symlink's target text, and
    every directory entry (including empty directories) — used only for
    the Neovim fallback prefix and the Nerd Font directory, per
    Implementation Sequence step 1's tree-manifest exception. Anything
    changed anywhere in the tree is meant to make the whole thing
    divergent, so this must not silently skip entries it can't read.
    """
    try:
        if root.is_symlink() or not root.is_dir():
            return {"state": STATE_ABSENT}
    except OSError:
        return {"state": STATE_UNKNOWN}

    entries: dict[str, object] = {}
    try:
        for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
            rel_dir = Path(dirpath).relative_to(root)
            for name in dirnames:
                child = Path(dirpath) / name
                rel = str(rel_dir / name) if str(rel_dir) != "." else name
                if child.is_symlink():
                    entries[f"symlink:{rel}"] = os.readlink(child)
                else:
                    entries[f"dir:{rel}"] = True
            for name in filenames:
                child = Path(dirpath) / name
                rel = str(rel_dir / name) if str(rel_dir) != "." else name
                if child.is_symlink():
                    entries[f"symlink:{rel}"] = os.readlink(child)
                else:
                    content = child.read_bytes()
                    entries[f"file:{rel}"] = {
                        "size": len(content),
                        "sha256": hashlib.sha256(content).hexdigest(),
                    }
    except OSError:
        return {"state": STATE_UNKNOWN}
    return {"state": STATE_PRESENT, "entries": entries}


def tree_manifest_matches(root: Path, recorded: dict[str, object]) -> bool:
    """Whether ``root``'s current tree still matches a recorded tree manifest.

    False for anything other than an exact match, including a recorded
    ``unknown``/``absent`` state or a current capture that errors — callers
    treat "doesn't match" as "don't remove wholesale," which is the safe
    default for a tree-manifest artifact.
    """
    if recorded.get("state") != STATE_PRESENT:
        return False
    current = capture_tree_manifest(root)
    return current.get("state") == STATE_PRESENT and current.get(
        "entries"
    ) == recorded.get("entries")


def capture_runtime_nvm(home: Path) -> dict[str, object]:
    """Capture the lightweight ``runtime:<root>`` record for NVM/Node.

    Deliberately *not* a full tree manifest (see the module-level docstring
    on ``runtime:`` divergence in the execution plan) — only the root's
    existence and the installed Node version directory names, since normal
    ``npm install -g`` usage constantly adds files underneath ``~/.nvm``
    and must not count as divergence.
    """
    root = home / ".nvm"
    try:
        if root.is_symlink() or not root.is_dir():
            return {"state": STATE_ABSENT}
        versions_dir = root / "versions" / "node"
        versions = (
            sorted(p.name for p in versions_dir.iterdir() if p.is_dir())
            if versions_dir.is_dir()
            else []
        )
    except OSError:
        return {"state": STATE_UNKNOWN}
    return {"state": STATE_PRESENT, "versions": versions}


def runtime_nvm_diverged(home: Path, recorded: dict[str, object]) -> bool:
    """Whether live NVM state has diverged from its recorded top-level markers.

    Only a change to what the installer itself introduced counts:
    disappearance of the root, or removal of a Node version baseline
    recorded as installed. Additional versions or npm packages added since
    are normal use, not divergence.
    """
    if recorded.get("state") != STATE_PRESENT:
        return False
    current = capture_runtime_nvm(home)
    if current.get("state") != STATE_PRESENT:
        return True
    recorded_versions = set(recorded.get("versions", []))  # type: ignore[arg-type]
    current_versions = set(current.get("versions", []))  # type: ignore[arg-type]
    return not recorded_versions.issubset(current_versions)


# ── content blobs ─────────────────────────────────────────────────────────


def blob_path(state_dir: Path, digest: str) -> Path:
    return state_dir / f"baseline-snapshot-{digest}.blob"


def write_blob(state_dir: Path, content: bytes) -> str:
    """Write a content-addressed blob, returning its SHA-256 hex digest.

    A no-op if a blob with this exact content already exists (the same rc
    file captured across two supplementary layers, for instance) — content
    addressing makes that safe rather than merely tolerable.
    """
    digest = hashlib.sha256(content).hexdigest()
    path = blob_path(state_dir, digest)
    if not path.is_file():
        state_dir.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    return digest


def read_blob(state_dir: Path, digest: str) -> bytes | None:
    """Read a blob's content back, or None if it's missing/unreadable.

    A missing referenced blob is a capture-time bug or on-disk corruption,
    never a signal to fabricate content — callers must treat this as
    ``unresolved``, not fall back to any other source.
    """
    try:
        return blob_path(state_dir, digest).read_bytes()
    except OSError:
        return None


# ── baseline layers ───────────────────────────────────────────────────────


@dataclass
class Layer:
    """One capture pass: a timestamp and the ownership-key records from it."""

    captured_at: str
    records: dict[str, dict[str, object]] = field(default_factory=dict)


@dataclass
class Baseline:
    """The full departure baseline: immutable first layer, later supplements.

    A key's authoritative value is whichever layer first recorded it —
    layers are appended, never rewritten (see ``is_unrecorded`` and
    ``add_layer``).
    """

    layers: list[Layer] = field(default_factory=list)
    transactions: list[dict[str, object]] = field(default_factory=list)

    def value_for(self, key: str) -> dict[str, object] | None:
        """The recorded record for ``key``, from the earliest layer that has it."""
        for layer in self.layers:
            if key in layer.records:
                return layer.records[key]
        return None

    def is_unrecorded(self, key: str) -> bool:
        """Whether ``key`` has no entry in *any* layer yet.

        Distinct from a recorded ``absent`` value — see the module
        docstring. A key already captured as ``absent`` in an earlier layer
        is not unrecorded, and must never be re-captured into a later layer
        just because its value happens to be ``absent``.
        """
        return self.value_for(key) is None

    def all_keys(self) -> set[str]:
        keys: set[str] = set()
        for layer in self.layers:
            keys.update(layer.records)
        return keys

    def add_layer(
        self, captured_at: str, candidate_records: dict[str, dict[str, object]]
    ) -> None:
        """Append a new layer containing only genuinely unrecorded keys.

        A no-op layer (nothing in ``candidate_records`` was unrecorded) is
        not appended at all, so ``layers`` never accumulates empty entries.
        The very first call — when ``layers`` is empty — makes every
        candidate key unrecorded by definition, so the whole set becomes
        the immutable first layer.
        """
        to_add = {
            key: record
            for key, record in candidate_records.items()
            if self.is_unrecorded(key)
        }
        if to_add:
            self.layers.append(Layer(captured_at=captured_at, records=to_add))


# ── serialization ─────────────────────────────────────────────────────────

BASELINE_VERSION = 1


def baseline_path(state_dir: Path) -> Path:
    return state_dir / "baseline.json"


def baseline_to_dict(baseline: Baseline) -> dict[str, object]:
    return {
        "version": BASELINE_VERSION,
        "layers": [
            {"captured_at": layer.captured_at, "records": layer.records}
            for layer in baseline.layers
        ],
        "transactions": baseline.transactions,
    }


def baseline_from_dict(data: dict[str, object]) -> Baseline:
    raw_layers = data.get("layers", [])
    layers = [
        Layer(
            captured_at=str(entry.get("captured_at", "")),
            records=dict(entry.get("records", {})),  # type: ignore[arg-type]
        )
        for entry in raw_layers  # type: ignore[union-attr]
    ]
    raw_transactions = data.get("transactions", [])
    return Baseline(layers=layers, transactions=list(raw_transactions))  # type: ignore[arg-type]


def save_baseline(state_dir: Path, baseline: Baseline) -> None:
    """Durably write ``baseline.json`` via a temp-file-then-replace swap."""

    path = baseline_path(state_dir)
    state_dir.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(
        json.dumps(baseline_to_dict(baseline), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(tmp, path)


def load_baseline(state_dir: Path) -> Baseline | None:
    """Load ``baseline.json``, or None if it's missing, empty, or unparseable."""

    path = baseline_path(state_dir)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    if not text.strip():
        return None
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    return baseline_from_dict(data)


# ── package managers: probe commands and output parsing ───────────────────
#
# Implementation Sequence step 2. These are pure functions — command argv
# builders and output parsers — so they're testable without a subprocess.
# Wiring them into the live install flow (running the probes through
# install.run_command at the right point in each package-installing
# function, and recording the resulting Transaction) is a separate,
# larger change to install.py's already heavily-tested package-install
# surface and is not done by this module.

PACKAGE_MANAGERS = ("apt", "dnf", "npm", "uv-tool")


def dpkg_query_command() -> list[str]:
    """List every installed apt package and its version, tab-separated."""
    return ["dpkg-query", "-W", "-f=${Package}\t${Version}\n"]


def parse_dpkg_query(output: str) -> dict[str, str]:
    """Parse :func:`dpkg_query_command` output into ``{name: version}``."""
    versions: dict[str, str] = {}
    for line in output.splitlines():
        name, sep, version = line.partition("\t")
        if sep and name:
            versions[name] = version.strip()
    return versions


def rpm_qa_command() -> list[str]:
    """List every installed dnf/rpm package and its version, tab-separated."""
    return ["rpm", "-qa", "--qf", "%{NAME}\t%{VERSION}-%{RELEASE}\n"]


def parse_rpm_qa(output: str) -> dict[str, str]:
    """Parse :func:`rpm_qa_command` output into ``{name: version}``."""
    return parse_dpkg_query(output)  # same tab-separated shape


def npm_ls_global_command() -> list[str]:
    """List globally installed npm packages as JSON."""
    return ["npm", "ls", "-g", "--depth=0", "--json"]


def parse_npm_ls_global(output: str) -> dict[str, str]:
    """Parse :func:`npm_ls_global_command` JSON output into ``{name: version}``.

    Unparseable or unexpectedly-shaped output yields ``{}`` rather than
    raising — a probe failure is signaled by the caller checking the
    command's own exit status, not by this parser guessing.
    """
    try:
        data = json.loads(output)
    except json.JSONDecodeError:
        return {}
    deps = data.get("dependencies") if isinstance(data, dict) else None
    if not isinstance(deps, dict):
        return {}
    versions: dict[str, str] = {}
    for name, info in deps.items():
        if isinstance(info, dict) and isinstance(info.get("version"), str):
            versions[name] = info["version"]
    return versions


def uv_tool_list_command() -> list[str]:
    """List installed ``uv tool`` packages and their versions."""
    return ["uv", "tool", "list"]


def parse_uv_tool_list(output: str) -> dict[str, str]:
    """Parse :func:`uv_tool_list_command` output into ``{name: version}``.

    Each installed tool is a non-indented ``<name> v<version>`` line,
    followed by indented lines naming the executables it provides — those
    are skipped by only matching lines with no leading whitespace.
    """
    versions: dict[str, str] = {}
    for line in output.splitlines():
        if not line or line[0].isspace():
            continue
        name, sep, version = line.strip().partition(" ")
        if sep and version.startswith("v"):
            versions[name] = version[1:]
    return versions


def apt_remove_command(package: str) -> list[str]:
    """Pinned apt removal command — never ``purge`` or ``autoremove``."""
    return ["sudo", "apt-get", "remove", "-y", package]


def dnf_remove_command(package: str) -> list[str]:
    """Pinned dnf removal command — never ``autoremove``."""
    return ["sudo", "dnf", "remove", "-y", package]


def npm_uninstall_command(package: str) -> list[str]:
    return ["npm", "uninstall", "-g", package]


def uv_tool_uninstall_command(name: str) -> list[str]:
    return ["uv", "tool", "uninstall", name]


def apt_downgrade_command(package: str, version: str) -> list[str]:
    return ["sudo", "apt-get", "install", "-y", f"{package}={version}"]


def dnf_downgrade_command(package: str, version: str) -> list[str]:
    return ["sudo", "dnf", "downgrade", "-y", f"{package}-{version}"]


def apt_rdepends_command(package: str) -> list[str]:
    return ["apt-cache", "rdepends", "--installed", package]


def dnf_whatrequires_command(package: str) -> list[str]:
    return ["dnf", "repoquery", "--whatrequires", "--installed", package]


def classify_rdepends_result(ok: bool, stdout: str) -> str:
    """Classify a reverse-dependency probe's outcome for removal eligibility.

    Returns:
        ``"removable"`` only when the probe succeeded and returned an
        explicitly empty result. ``"in-use"`` when the probe succeeded but
        named at least one installed reverse dependency. ``"unknown"`` for
        any failed/unavailable probe — never treated as proof of
        removability, exactly like an ``unknown`` baseline record blocks
        removal.
    """
    if not ok:
        return "unknown"
    return "removable" if not stdout.strip() else "in-use"


# ── package transactions ────────────────────────────────────────────────


@dataclass
class Transaction:
    """One package-manager operation's before/after state and provenance.

    ``requested`` is what this transaction asked for (usually a single
    package name); ``before``/``after`` are fresh ``{name: version}``
    snapshots of the whole manager taken immediately around the operation.
    ``comparand_source`` and ``interference`` record — purely for
    diagnostic purposes, never blocking anything, matching this installer's
    no-abort convention — whether anything unrelated changed between the
    previous same-manager transaction (or, for a manager's first
    transaction in a run, its epoch inventory) and this one's fresh
    ``before`` snapshot.
    """

    manager: str
    requested: list[str]
    before: dict[str, str]
    after: dict[str, str]
    comparand_source: str  # "epoch" | "previous" | "none"
    interference: list[str]
    captured_at: str

    def introduced(self) -> list[str]:
        """Packages that appeared as a side effect of this transaction.

        Excludes the requested package(s) themselves — those are removed
        via their own pinned top-level removal command, not the
        rdepends-gated introduced-dependency path.
        """
        return sorted(set(self.after) - set(self.before) - set(self.requested))


def transaction_to_dict(txn: Transaction) -> dict[str, object]:
    return {
        "manager": txn.manager,
        "requested": txn.requested,
        "before": txn.before,
        "after": txn.after,
        "comparand_source": txn.comparand_source,
        "interference": txn.interference,
        "captured_at": txn.captured_at,
    }


def transaction_from_dict(data: dict[str, object]) -> Transaction:
    return Transaction(
        manager=str(data.get("manager", "")),
        requested=list(data.get("requested", [])),  # type: ignore[arg-type]
        before=dict(data.get("before", {})),  # type: ignore[arg-type]
        after=dict(data.get("after", {})),  # type: ignore[arg-type]
        comparand_source=str(data.get("comparand_source", "none")),
        interference=list(data.get("interference", [])),  # type: ignore[arg-type]
        captured_at=str(data.get("captured_at", "")),
    )


def _manager_transactions(baseline: Baseline, manager: str) -> list[Transaction]:
    return [
        transaction_from_dict(t)
        for t in baseline.transactions
        if t.get("manager") == manager
    ]


def record_transaction(
    baseline: Baseline,
    *,
    manager: str,
    requested: list[str],
    before: dict[str, str],
    after: dict[str, str],
    captured_at: str,
    epoch: dict[str, str] | None = None,
) -> Transaction:
    """Build a :class:`Transaction`, append it to ``baseline``, and return it.

    The interference comparand is the immediately preceding transaction of
    the *same manager* (its ``after`` snapshot) if one exists in this
    baseline yet; otherwise ``epoch`` — that manager's install-epoch
    inventory, captured once before its first transaction ever runs — if
    given; otherwise there is nothing to compare against yet
    (``comparand_source="none"``, no interference computed).
    """
    prior = _manager_transactions(baseline, manager)
    if prior:
        comparand = prior[-1].after
        comparand_source = "previous"
    elif epoch is not None:
        comparand = epoch
        comparand_source = "epoch"
    else:
        comparand = {}
        comparand_source = "none"

    interference = (
        sorted(
            name
            for name in set(comparand) | set(before)
            if comparand.get(name) != before.get(name)
        )
        if comparand_source != "none"
        else []
    )

    txn = Transaction(
        manager=manager,
        requested=list(requested),
        before=before,
        after=after,
        comparand_source=comparand_source,
        interference=interference,
        captured_at=captured_at,
    )
    baseline.transactions.append(transaction_to_dict(txn))
    return txn


# ── downgrade ladder ─────────────────────────────────────────────────────


def earliest_recorded_version(
    baseline: Baseline, manager: str, name: str
) -> str | None:
    """``name``'s true pre-install version for ``manager``, if ever recorded.

    The first ``before`` value across that manager's transactions, in
    baseline order — i.e. the value captured immediately preceding the very
    first transaction that ever touched this package.
    """
    for txn in _manager_transactions(baseline, manager):
        if name in txn.before:
            return txn.before[name]
    return None


def downgrade_candidates(baseline: Baseline, manager: str, name: str) -> list[str]:
    """Versions to try, in order, when downgrading an installer-upgraded package.

    The earliest recorded (true pre-install) version first, then every
    other distinct version this package has been recorded ``after`` a
    transaction at, most-recently-observed first — matching the plan's
    downgrade ladder (direct-to-earliest, then intermediate versions in
    reverse chronological order).
    """
    earliest = earliest_recorded_version(baseline, manager, name)
    afters: list[str] = []
    for txn in _manager_transactions(baseline, manager):
        version = txn.after.get(name)
        if version is not None and version not in afters:
            afters.append(version)

    candidates: list[str] = []
    if earliest is not None:
        candidates.append(earliest)
    for version in reversed(afters):
        if version not in candidates:
            candidates.append(version)
    return candidates
