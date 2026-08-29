#!/bin/bash
# =============================================================================
# gruff-ts.sh - Run the JS/TS quality analyzer from anywhere in the repo
# =============================================================================
#
# Usage:
#   ./scripts/gruff-ts.sh                          # whole-project digest
#   ./scripts/gruff-ts.sh analyse public/js        # findings for one path
#   ./scripts/gruff-ts.sh analyse --diff           # findings on working-tree changes
#   ./scripts/gruff-ts.sh analyse --format json    # machine-readable, JSON on stdout
#   ./scripts/gruff-ts.sh list-rules               # any other gruff-ts command
#
# Arguments pass straight through, so `./scripts/gruff-ts.sh analyse --help`
# stays the flag reference. The wrapper only saves you the retyping: it finds
# the npm binary and runs from the repo root so .gruff-ts.yaml is picked up.
# That config ignores public/js/tailwind.js, the vendored Tailwind runtime, so
# expect no findings there.
#
# With no arguments you get `summary`: per-pillar counts, top rules and top file
# offenders, with no per-finding spam.
#
# Exit 1 means findings exist, not that the tool failed. Exit 2 is a real
# diagnostic such as a parse error, a missing path, or a rejected config.
# Triage guidance lives in .goat-flow/skill-docs/playbooks/gruff-code-quality.md
# =============================================================================

set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT" || exit 1

# npm's shim first; the package path covers an install whose shim is missing.
BIN=""
for candidate in \
    "node_modules/.bin/gruff-ts" \
    "node_modules/@blundergoat/gruff-ts/bin/gruff-ts"
do
    if [[ -x "$candidate" ]]; then
        BIN="$candidate"
        break
    fi
done

if [[ -z "$BIN" ]]; then
    BIN="$(command -v gruff-ts 2>/dev/null)" || true
fi

if [[ -z "$BIN" ]]; then
    echo "gruff-ts not found in node_modules or on PATH. Run: npm install" >&2
    exit 127
fi

args=("$@")

# No arguments means orientation, so give the compact digest rather than an error.
if [[ ${#args[@]} -eq 0 ]]; then
    args=(summary)
fi

# Echo the real command so a run can be repeated or tweaked without the wrapper.
printf '+ %s %s\n' "$BIN" "${args[*]}" >&2

exec "$BIN" "${args[@]}"
