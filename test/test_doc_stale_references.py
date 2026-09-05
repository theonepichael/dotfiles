import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

DOC_FILES = [
    REPO_ROOT / "README.md",
    REPO_ROOT / "STYLE.md",
    REPO_ROOT / "AGENTS.md",
    REPO_ROOT / "test" / "AGENTS.md",
]


def test_no_references_to_pruned_harness_docs() -> None:
    """Docs must not reference deleted harness parity or deleted test files."""
    forbidden = [
        "CLAUDE_CODE_PARITY.md",
        "test_pi_ts_checks.py",
    ]
    failures: list[str] = []
    for doc in DOC_FILES:
        if not doc.is_file():
            continue
        text = doc.read_text(encoding="utf-8")
        for bad in forbidden:
            if bad in text:
                failures.append(f"{doc.relative_to(REPO_ROOT)}: references pruned {bad}")
    assert not failures, "\n".join(failures)


def test_relative_markdown_file_links_exist() -> None:
    """Relative markdown links in key docs must point to existing files."""
    link_pattern = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")
    failures: list[str] = []

    for doc in DOC_FILES:
        if not doc.is_file():
            continue
        content = doc.read_text(encoding="utf-8")
        for match in link_pattern.finditer(content):
            target = match.group(2).strip()
            # Ignore external URLs and anchors
            if target.startswith(("http://", "https://", "mailto:", "#")):
                continue
            # Strip anchors
            file_part = target.split("#")[0]
            if not file_part:
                continue
            resolved = (doc.parent / file_part).resolve()
            if not resolved.exists():
                failures.append(f"{doc.relative_to(REPO_ROOT)}: dead link {target} -> {resolved}")

    assert not failures, "\n".join(failures)
