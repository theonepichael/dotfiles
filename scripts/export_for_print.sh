#!/usr/bin/env bash
# Format files as print-friendly markdown (full bodies, not diffs) for
# retyping from paper on the other side of an air gap.
#
# Usage:
#   export_for_print.sh <output.md> <file1> [file2 ...]
#   export_for_print.sh <output.md> --range <base>..<head>
#
# --range reads committed content via `git show <head>:<file>` for every
# file that changed in the range, so it reflects the commit exactly
# regardless of current working-tree state.
set -euo pipefail

if [ "$#" -lt 2 ]; then
    echo "usage: $0 <output.md> <file1> [file2 ...]" >&2
    echo "       $0 <output.md> --range <base>..<head>" >&2
    exit 1
fi

out="$1"
shift

: > "$out"

emit() {
    local label="$1" content="$2" ext="$3"
    {
        echo "## \`$label\`"
        echo
        echo "\`\`\`${ext}"
        printf '%s\n' "$content"
        echo "\`\`\`"
        echo
        echo '***'
        echo
    } >> "$out"
}

if [ "$1" = "--range" ]; then
    range="$2"
    head_ref="${range##*..}"
    while IFS= read -r f; do
        [ -z "$f" ] && continue
        ext="${f##*.}"
        if content=$(git show "${head_ref}:${f}" 2>/dev/null); then
            emit "$f" "$content" "$ext"
        else
            echo "skipping (deleted or unreadable at ${head_ref}): $f" >&2
        fi
    done < <(git diff --name-only "$range")
else
    for f in "$@"; do
        if [ ! -f "$f" ]; then
            echo "skipping missing file: $f" >&2
            continue
        fi
        emit "$f" "$(cat "$f")" "${f##*.}"
    done
fi

echo "wrote $out ($(wc -l < "$out") lines)" >&2
if command -v pandoc >/dev/null 2>&1; then
    pdf="${out%.md}.pdf"
    # plain black text, no syntax-highlight color -- better scan contrast
    if pandoc "$out" -o "$pdf" --no-highlight; then
        echo "also wrote $pdf" >&2
    else
        echo "pandoc PDF conversion failed; $out is still available" >&2
    fi
fi
