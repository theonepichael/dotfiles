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
