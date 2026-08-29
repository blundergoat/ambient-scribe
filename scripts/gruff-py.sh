#!/bin/bash
# =============================================================================
# gruff-py.sh - Run the Python quality analyzer from anywhere in the repo
# =============================================================================
#
# Usage:
#   ./scripts/gruff-py.sh                             # whole-project digest
#   ./scripts/gruff-py.sh analyse strands_agents/api  # findings for one path
#   ./scripts/gruff-py.sh analyse --diff working-tree # findings on uncommitted changes
#   ./scripts/gruff-py.sh analyse --format json       # machine-readable, JSON on stdout
#   ./scripts/gruff-py.sh check-ignore <path>         # any other gruff-py command
#
# Arguments pass straight through, so `./scripts/gruff-py.sh analyse --help`
# stays the flag reference. The wrapper only saves you the retyping: it finds
# the binary inside the strands_agents virtualenv and runs from the repo root
# so .gruff-py.yaml is picked up.
#
# That config scopes gruff-py to maintained runtime Python. tests/python,
# tests/e2e and tests/fixtures are ignored on purpose, so a clean run says
# nothing about the tests; `strands_agents/.venv/bin/pytest tests/python/ -q`
# is their gate. Reasoning is in
# .goat-flow/learning-loop/decisions/ADR-004-gruff-py-runtime-scope.md
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

# The agent virtualenv owns the install; a repo-root venv is the fallback layout.
BIN=""
for candidate in \
    "strands_agents/.venv/bin/gruff-py" \
    ".venv/bin/gruff-py"
do
    if [[ -x "$candidate" ]]; then
        BIN="$candidate"
        break
    fi
done

if [[ -z "$BIN" ]]; then
    BIN="$(command -v gruff-py 2>/dev/null)" || true
fi

if [[ -z "$BIN" ]]; then
    echo "gruff-py not found in strands_agents/.venv or on PATH." >&2
    echo "Run: strands_agents/.venv/bin/pip install -r tests/python/requirements-dev.txt" >&2
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
