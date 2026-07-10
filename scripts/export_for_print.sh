#!/usr/bin/env bash
# Format a list of files as print-friendly markdown (full bodies, not diffs)
# for retyping from paper on the other side of an air gap.
#
# Usage: export_for_print.sh <output.md> <file1> [file2 ...]
set -euo pipefail

if [ "$#" -lt 2 ]; then
    echo "usage: $0 <output.md> <file1> [file2 ...]" >&2
    exit 1
fi

out="$1"
shift

: > "$out"
for f in "$@"; do
    if [ ! -f "$f" ]; then
        echo "skipping missing file: $f" >&2
        continue
    fi
    ext="${f##*.}"
    {
        echo "## \`$f\`"
        echo
        echo "\`\`\`${ext}"
        cat "$f"
        echo "\`\`\`"
        echo
        echo '---'
        echo
    } >> "$out"
done

echo "wrote $out ($(wc -l < "$out") lines)" >&2
if command -v pandoc >/dev/null 2>&1; then
    pdf="${out%.md}.pdf"
    pandoc "$out" -o "$pdf" && echo "also wrote $pdf" >&2
fi
