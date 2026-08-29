#!/bin/bash
# =============================================================================
# gruff-php.sh - Run the PHP quality analyzer from anywhere in the repo
# =============================================================================
#
# Usage:
#   ./scripts/gruff-php.sh                        # whole-project digest
#   ./scripts/gruff-php.sh analyse src/Service    # findings for one path
#   ./scripts/gruff-php.sh analyse --diff         # findings on working-tree changes
#   ./scripts/gruff-php.sh analyse --format json  # machine-readable, JSON on stdout
#   ./scripts/gruff-php.sh list-rules             # any other gruff-php command
#
# Arguments pass straight through, so `./scripts/gruff-php.sh analyse --help`
# stays the flag reference. The wrapper only saves you the retyping: it finds
# the Composer binary, runs from the repo root so .gruff-php.yaml is picked up,
# and adds --baseline=gruff-php-baseline.json to analyse and report the way
# `composer analyse:complexity` and preflight-checks.sh already do. Pass any
# baseline flag yourself to opt out of that.
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

BASELINE="gruff-php-baseline.json"

# Composer's shim first; the package path covers an install whose shim is missing.
BIN=""
for candidate in \
    "vendor/bin/gruff-php" \
    "vendor/blundergoat/gruff-php/bin/gruff-php"
do
    if [[ -x "$candidate" ]]; then
        BIN="$candidate"
        break
    fi
done

if [[ -z "$BIN" ]]; then
    BIN="$(command -v gruff-php 2>/dev/null)" || true
fi

if [[ -z "$BIN" ]]; then
    echo "gruff-php not found in vendor/bin or on PATH. Run: composer install" >&2
    exit 127
fi

args=("$@")

# No arguments means orientation, so give the compact digest rather than an error.
if [[ ${#args[@]} -eq 0 ]]; then
    args=(summary)
fi

# A caller who names any baseline flag owns the decision; the wrapper stays out.
has_baseline_flag=0
for arg in "${args[@]}"; do
    case "$arg" in
        --baseline | --baseline=* | --no-baseline | --generate-baseline | --generate-baseline=*)
            has_baseline_flag=1
            break
            ;;
    esac
done

# The accepted PHP debt lives in gruff-php-baseline.json, not the gruff-baseline.json
# name gruff-php auto-applies, so an unqualified run would re-report all of it.
# Only analyse and report take the flag; summary rejects it.
case "${args[0]}" in
    analyse | report)
        if [[ -f "$BASELINE" && $has_baseline_flag -eq 0 ]]; then
            args=("${args[0]}" "--baseline=$BASELINE" "${args[@]:1}")
        fi
        ;;
esac

# Echo the real command so a run can be repeated or tweaked without the wrapper.
printf '+ %s %s\n' "$BIN" "${args[*]}" >&2

exec "$BIN" "${args[@]}"
