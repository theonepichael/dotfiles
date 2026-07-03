#!/usr/bin/env python3
"""Recursively walk `claude --help` and emit a bash completion script.

Usage:
    python3 ~/.claude/scripts/gen_claude_completion.py
    python3 ~/.claude/scripts/gen_claude_completion.py --out /path/to/completion

Writes to ~/.local/share/bash-completion/completions/claude by default, where
bash-completion will auto-load it on the first `claude <TAB>`.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

CLAUDE_BIN = "claude"
DEFAULT_OUT = Path.home() / ".local/share/bash-completion/completions/claude"

SECTION_RE = re.compile(r"^(Arguments|Options|Commands):\s*$")
OPT_START_RE = re.compile(r"^ {2}(-\S)")  # option line begins with "  -"
CMD_START_RE = re.compile(r"^ {2}(\S)")  # command line begins with "  <nonspace>"
DEF_DESC_SPLIT = re.compile(r"^(.*?)(\s{2,})(.*)$")
CHOICES_RE = re.compile(r"\(choices:\s*([^)]+)\)")
# Commander command/alias tokens are lowercase words, optionally with dashes/digits
# and pipe-separated aliases. "Examples:" and other spurious tokens must be rejected.
CMD_NAME_RE = re.compile(r"^[a-z][a-z0-9-]*(\|[a-z][a-z0-9-]*)*$")
USAGE_RE = re.compile(r"^Usage:\s+(.+?)(?:\s+\[options\]|\s+\[command\]|\s*$)")

MAX_DEPTH = 6


@dataclass
class Option:
    names: list[str]
    takes_arg: bool
    arg_placeholder: str | None
    choices: list[str] | None
    desc: str


@dataclass
class Node:
    path: tuple[str, ...]
    options: list[Option] = field(default_factory=list)
    subcommands: dict[str, "Node"] = field(default_factory=dict)
    aliases: list[str] = field(default_factory=list)


def run_help(path: list[str]) -> str:
    try:
        r = subprocess.run(
            [CLAUDE_BIN, *path, "--help"],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
        return r.stdout or ""
    except Exception as e:
        print(
            f"warn: help failed for {' '.join(path) or '<root>'}: {e}", file=sys.stderr
        )
        return ""


def collect_sections(text: str) -> dict[str, list[str]]:
    sections: dict[str, list[str]] = {}
    current: str | None = None
    for line in text.splitlines():
        m = SECTION_RE.match(line)
        if m:
            current = m.group(1)
            sections.setdefault(current, [])
            continue
        if current is None:
            continue
        if line.strip() == "":
            # Blank lines don't end a section; skip.
            continue
        if line.startswith("  "):
            sections[current].append(line)
        else:
            current = None
    return sections


def _group_indented(lines: list[str], is_new_entry) -> list[str]:
    """Join wrapped continuation lines into single entries."""
    entries: list[str] = []
    cur: str | None = None
    for line in lines:
        if is_new_entry(line):
            if cur is not None:
                entries.append(cur)
            cur = line
        else:
            if cur is not None:
                cur = cur + " " + line.strip()
    if cur is not None:
        entries.append(cur)
    return entries


def parse_options(lines: list[str]) -> list[Option]:
    entries = _group_indented(lines, lambda ln: bool(OPT_START_RE.match(ln)))
    out: list[Option] = []
    for entry in entries:
        m = DEF_DESC_SPLIT.match(entry.lstrip().rstrip())
        if not m:
            continue
        defn, _, desc = m.groups()
        parts = [p.strip() for p in defn.split(",")]
        names: list[str] = []
        placeholder: str | None = None
        for p in parts:
            toks = p.split(None, 1)
            if not toks or not toks[0].startswith("-"):
                continue
            names.append(toks[0])
            if len(toks) > 1 and placeholder is None:
                placeholder = toks[1].strip()
        if not names:
            continue
        takes_arg = placeholder is not None and placeholder.startswith(("<", "["))
        choices: list[str] | None = None
        cm = CHOICES_RE.search(desc)
        if cm:
            choices = [c.strip().strip('"').strip("'") for c in cm.group(1).split(",")]
            choices = [c for c in choices if c]
        out.append(
            Option(
                names=names,
                takes_arg=takes_arg,
                arg_placeholder=placeholder,
                choices=choices,
                desc=desc.strip(),
            )
        )
    return out


def parse_commands(lines: list[str]) -> list[tuple[str, list[str]]]:
    """Return list of (primary_name, aliases)."""
    entries = _group_indented(lines, lambda ln: bool(CMD_START_RE.match(ln)))
    out: list[tuple[str, list[str]]] = []
    for entry in entries:
        m = DEF_DESC_SPLIT.match(entry.lstrip().rstrip())
        if not m:
            tokens = entry.strip().split()
            if not tokens:
                continue
            head = tokens[0]
        else:
            defn, _, _ = m.groups()
            head = defn.strip().split()[0]
        # Reject anything that isn't a valid commander command token.
        # This filters out "Examples:" and similar spurious tokens that arise
        # when `--help` intersperses example blocks at column 2.
        if not CMD_NAME_RE.match(head):
            continue
        parts = head.split("|")
        name = parts[0]
        aliases = parts[1:]
        if name == "help":
            continue
        out.append((name, aliases))
    return out


def help_matches_path(text: str, path: tuple[str, ...]) -> bool:
    """Check that the help output's `Usage:` line reflects the path we asked for.

    Commander re-prints the parent's help when given an unknown subcommand, so
    a mismatch here means the path is bogus and we must not recurse further.
    """
    for line in text.splitlines():
        m = USAGE_RE.match(line)
        if not m:
            continue
        usage_cmd = m.group(1).strip()
        tokens = usage_cmd.split()
        # tokens[0] is "claude"; remainder should be the path (commander may
        # drop trailing positionals like "[command]" which USAGE_RE already
        # trims, but placeholders like "<name>" may still appear).
        if not tokens or tokens[0] != "claude":
            return True  # Unrecognized format — don't block.
        usage_tokens = [
            t for t in tokens[1:] if not (t.startswith("<") or t.startswith("["))
        ]
        if len(usage_tokens) != len(path):
            return False
        # Each token may carry commander aliases ("plugin|plugins"); any match counts.
        for want, got in zip(path, usage_tokens):
            if want not in got.split("|"):
                return False
        return True
    return True


def build_tree(path: tuple[str, ...], seen: set[tuple[str, ...]]) -> Node:
    if path in seen:
        return Node(path=path)
    seen.add(path)
    if len(path) > MAX_DEPTH:
        print(f"warn: max depth exceeded at {' '.join(path)}", file=sys.stderr)
        return Node(path=path)
    text = run_help(list(path))
    if path and not help_matches_path(text, path):
        # Commander fell back to a parent's help — this path isn't real.
        print(
            f"warn: {' '.join(path)} is not a real subcommand; skipping",
            file=sys.stderr,
        )
        return Node(path=path)
    sections = collect_sections(text)
    node = Node(path=path)
    node.options = parse_options(sections.get("Options", []))
    for name, aliases in parse_commands(sections.get("Commands", [])):
        child = build_tree(path + (name,), seen)
        child.aliases = aliases
        node.subcommands[name] = child
    return node


# -- Bash emission --------------------------------------------------------


def path_key(path: tuple[str, ...]) -> str:
    return "ROOT" if not path else "ROOT__" + "__".join(path)


def is_dir_option(opt: Option) -> bool:
    text = (opt.arg_placeholder or "") + " " + " ".join(opt.names)
    text = text.lower()
    return any(
        kw in text for kw in ("<dir", "<directory", "<directories", "--add-dir", "-dir")
    )


def is_file_option(opt: Option) -> bool:
    if is_dir_option(opt):
        return False
    text = (opt.arg_placeholder or "") + " " + " ".join(opt.names)
    text = text.lower()
    return any(
        kw in text
        for kw in (
            "<file",
            "<path",
            "<configs",
            "<config",
            "--settings",
            "--mcp-config",
            "--debug-file",
            "--file",
            "--json-schema",
        )
    )


def collect_all_nodes(root: Node) -> list[Node]:
    out: list[Node] = []

    def walk(n: Node) -> None:
        out.append(n)
        for c in n.subcommands.values():
            walk(c)

    walk(root)
    return out


def shell_quote(s: str) -> str:
    return "'" + s.replace("'", "'\\''") + "'"


def emit_bash(root: Node) -> str:
    nodes = collect_all_nodes(root)

    lines: list[str] = []
    lines.append("# bash completion for the `claude` CLI — AUTO-GENERATED.")
    lines.append(
        "# Regenerate with: python3 ~/.claude/scripts/gen_claude_completion.py"
    )
    lines.append("# Do not edit by hand.")
    lines.append("")
    lines.append("declare -gA _CLAUDE_OPTS _CLAUDE_OPTARGS _CLAUDE_SUBS \\")
    lines.append(
        "              _CLAUDE_CHOICES _CLAUDE_FILE_FLAGS _CLAUDE_DIR_FLAGS \\"
    )
    lines.append("              _CLAUDE_ALIASES")
    lines.append("")

    for node in nodes:
        key = path_key(node.path)
        opt_names: list[str] = []
        optarg_names: list[str] = []
        file_flags: list[str] = []
        dir_flags: list[str] = []
        for opt in node.options:
            for n in opt.names:
                opt_names.append(n)
                if opt.takes_arg:
                    optarg_names.append(n)
                    if opt.choices:
                        choice_key = f"{key} {n}"
                        lines.append(
                            f"_CLAUDE_CHOICES[{shell_quote(choice_key)}]={shell_quote(' '.join(opt.choices))}"
                        )
                    if is_dir_option(opt):
                        dir_flags.append(n)
                    elif is_file_option(opt):
                        file_flags.append(n)
        sub_names = list(node.subcommands.keys())
        # Include aliases so completion matches them too.
        alias_names: list[str] = []
        for sub in node.subcommands.values():
            alias_names.extend(sub.aliases)
        all_sub_names = sub_names + alias_names

        lines.append(f"_CLAUDE_OPTS[{key}]={shell_quote(' '.join(opt_names))}")
        lines.append(f"_CLAUDE_OPTARGS[{key}]={shell_quote(' '.join(optarg_names))}")
        lines.append(f"_CLAUDE_SUBS[{key}]={shell_quote(' '.join(all_sub_names))}")
        lines.append(f"_CLAUDE_FILE_FLAGS[{key}]={shell_quote(' '.join(file_flags))}")
        lines.append(f"_CLAUDE_DIR_FLAGS[{key}]={shell_quote(' '.join(dir_flags))}")
        # Alias map: for each alias at this level, record canonical name.
        for sub_name, sub_node in node.subcommands.items():
            for a in sub_node.aliases:
                lines.append(
                    f"_CLAUDE_ALIASES[{shell_quote(f'{key} {a}')}]={shell_quote(sub_name)}"
                )
        lines.append("")

    lines.append(_COMPLETION_FUNC)
    return "\n".join(lines) + "\n"


_COMPLETION_FUNC = r"""
_claude() {
    local cur prev words cword
    _init_completion -n = 2>/dev/null || {
        # Fallback if bash-completion helpers are unavailable.
        COMPREPLY=()
        cur="${COMP_WORDS[COMP_CWORD]}"
        prev="${COMP_WORDS[COMP_CWORD-1]}"
        words=("${COMP_WORDS[@]}")
        cword=$COMP_CWORD
    }

    local path="ROOT"
    local i w canonical
    for ((i=1; i<cword; i++)); do
        w="${words[i]}"
        [[ "$w" == -* ]] && continue
        # Skip values consumed by an option that takes an argument
        local prev_w="${words[i-1]}"
        if [[ "$prev_w" == -* ]]; then
            local inherited="${_CLAUDE_OPTARGS[ROOT]:-}"
            local local_optargs="${_CLAUDE_OPTARGS[$path]:-}"
            if [[ " $inherited $local_optargs " == *" $prev_w "* ]]; then
                continue
            fi
        fi
        # Is w a subcommand or alias at current path?
        local subs=" ${_CLAUDE_SUBS[$path]:-} "
        if [[ "$subs" == *" $w "* ]]; then
            canonical="${_CLAUDE_ALIASES["$path $w"]:-$w}"
            path="${path}__${canonical}"
        fi
    done

    # Value completion: is prev an option that takes an argument?
    if [[ "$prev" == -* ]]; then
        local all_optargs=" ${_CLAUDE_OPTARGS[$path]:-} ${_CLAUDE_OPTARGS[ROOT]:-} "
        if [[ "$all_optargs" == *" $prev "* ]]; then
            local choices="${_CLAUDE_CHOICES["$path $prev"]:-}"
            if [[ -z "$choices" ]]; then
                choices="${_CLAUDE_CHOICES["ROOT $prev"]:-}"
            fi
            if [[ -n "$choices" ]]; then
                COMPREPLY=( $(compgen -W "$choices" -- "$cur") )
                return 0
            fi
            local dir_flags=" ${_CLAUDE_DIR_FLAGS[$path]:-} ${_CLAUDE_DIR_FLAGS[ROOT]:-} "
            if [[ "$dir_flags" == *" $prev "* ]]; then
                if declare -F _filedir >/dev/null; then _filedir -d; else
                    COMPREPLY=( $(compgen -d -- "$cur") )
                fi
                return 0
            fi
            local file_flags=" ${_CLAUDE_FILE_FLAGS[$path]:-} ${_CLAUDE_FILE_FLAGS[ROOT]:-} "
            if [[ "$file_flags" == *" $prev "* ]]; then
                if declare -F _filedir >/dev/null; then _filedir; else
                    COMPREPLY=( $(compgen -f -- "$cur") )
                fi
                return 0
            fi
            # Unknown value type — no completion.
            return 0
        fi
    fi

    if [[ "$cur" == -* ]]; then
        local all_opts="${_CLAUDE_OPTS[$path]:-}"
        if [[ "$path" != "ROOT" ]]; then
            all_opts="$all_opts ${_CLAUDE_OPTS[ROOT]:-}"
        fi
        COMPREPLY=( $(compgen -W "$all_opts" -- "$cur") )
        return 0
    fi

    # Positional: prefer subcommands at current path, otherwise files (for [prompt]).
    local subs="${_CLAUDE_SUBS[$path]:-}"
    if [[ -n "$subs" ]]; then
        COMPREPLY=( $(compgen -W "$subs" -- "$cur") )
    fi
    if [[ ${#COMPREPLY[@]} -eq 0 ]]; then
        if declare -F _filedir >/dev/null; then _filedir; else
            COMPREPLY=( $(compgen -f -- "$cur") )
        fi
    fi
    return 0
}

complete -F _claude claude
"""


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    ap.add_argument(
        "--stdout", action="store_true", help="Print to stdout instead of writing"
    )
    args = ap.parse_args()

    # Sanity check the CLI is reachable.
    probe = run_help([])
    if not probe:
        print(
            "error: `claude --help` produced no output; is the CLI installed?",
            file=sys.stderr,
        )
        return 1

    root = build_tree((), set())
    script = emit_bash(root)

    if args.stdout:
        sys.stdout.write(script)
        return 0

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(script)
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
