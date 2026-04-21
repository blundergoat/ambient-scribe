#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"

fail() {
    echo "FAIL: $1" >&2
    exit 1
}

warn() {
    echo "WARN: $1" >&2
}

require_file() {
    local path="$1"
    [[ -f "$path" ]] || fail "missing file: $path"
}

require_grep() {
    local pattern="$1"
    local path="$2"
    grep -Eq "$pattern" "$path" || fail "missing pattern '$pattern' in $path"
}

require_file "AGENTS.md"
require_file "CLAUDE.md"
require_file "docs/architecture.md"
require_file "docs/domain-reference.md"
require_file "docs/guidelines-ownership-split.md"
require_file "tasks/handoff-template.md"
require_file ".github/instructions/ai-agent-guidelines.instructions.md"

# goat-flow canonical learning-loop surfaces (directories, not flat files)
for dir in ".goat-flow/footguns" ".goat-flow/lessons" ".goat-flow/decisions"; do
    [[ -d "$dir" ]] || fail "missing directory: $dir"
done

agents_lines=$(wc -l < AGENTS.md | tr -d ' ')
if [[ "$agents_lines" -gt 150 ]]; then
    fail "AGENTS.md is ${agents_lines} lines; hard limit is 150"
elif [[ "$agents_lines" -gt 135 ]]; then
    warn "AGENTS.md is ${agents_lines} lines; target is under 135"
fi

for ref in \
    "docs/architecture.md" \
    "docs/domain-reference.md" \
    ".github/instructions/ai-agent-guidelines.instructions.md" \
    "docs/guidelines-ownership-split.md" \
    ".goat-flow/lessons" \
    ".goat-flow/footguns" \
    "tasks/handoff-template.md" \
    "scripts/context-validate.sh" \
    ".claude/hooks/deny-dangerous.sh" \
    "CLAUDE.md"
do
    require_grep "$ref" "AGENTS.md"
done

if [[ -x ".claude/hooks/deny-dangerous.sh" ]]; then
    ./.claude/hooks/deny-dangerous.sh --self-test >/dev/null
else
    fail ".claude/hooks/deny-dangerous.sh is not executable"
fi

# Footguns live in .goat-flow/footguns/ as category bucket files. Each entry MUST cite file:line evidence.
mapfile -t footgun_files < <(find .goat-flow/footguns -maxdepth 1 -type f -name '*.md' ! -name 'README.md' | sort)
[[ "${#footgun_files[@]}" -gt 0 ]] || fail ".goat-flow/footguns/ has no category bucket files"

total_refs=0
for fg_file in "${footgun_files[@]}"; do
    mapfile -t refs < <(grep -n '^- \*\*Files:\*\* ' "$fg_file" || true)
    for entry in "${refs[@]}"; do
        raw="${entry#*:}"
        raw="${raw#- **Files:** }"
        ref="$(echo "$raw" | sed 's/^[[:space:]]*//; s/[[:space:]]*$//; s/^`//; s/`$//')"
        [[ "$ref" =~ ^[^:]+:[0-9]+(-[0-9]+)?$ ]] || fail "footgun reference missing file:line evidence: $ref ($fg_file)"
        total_refs=$((total_refs + 1))
    done
done
[[ "$total_refs" -gt 0 ]] || fail ".goat-flow/footguns/ has no structured file:line entries"

while IFS= read -r path; do
    [[ -n "$path" ]] || continue
    line_count=$(wc -l < "$path" | tr -d ' ')
    if [[ "$line_count" -gt 20 ]]; then
        fail "local instruction file exceeds 20 lines: $path ($line_count)"
    fi
done < <(find . \( -name 'CLAUDE.md' -o -name 'AGENTS.md' \) \
    -not -path './CLAUDE.md' \
    -not -path './AGENTS.md' \
    -not -path './vendor/*' \
    -not -path './.git/*' \
    | sort)

echo "context-validate: OK"
