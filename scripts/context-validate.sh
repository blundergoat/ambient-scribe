#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT" || exit

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
for dir in \
    ".goat-flow/learning-loop/footguns" \
    ".goat-flow/learning-loop/lessons" \
    ".goat-flow/learning-loop/decisions" \
    ".goat-flow/learning-loop/patterns" \
    ".goat-flow/skill-docs/playbooks" \
    ".goat-flow/hooks"
do
    [[ -d "$dir" ]] || fail "missing directory: $dir"
done

agents_lines=$(wc -l < AGENTS.md | tr -d ' ')
if [[ "$agents_lines" -gt 150 ]]; then
    fail "AGENTS.md is ${agents_lines} lines; hard limit is 150"
elif [[ "$agents_lines" -gt 135 ]]; then
    warn "AGENTS.md is ${agents_lines} lines; target is under 135"
fi

for ref in \
    ".goat-flow/architecture.md" \
    ".goat-flow/code-map.md" \
    ".goat-flow/glossary.md" \
    "docs/domain-reference.md" \
    ".github/instructions/" \
    "docs/guidelines-ownership-split.md" \
    ".goat-flow/learning-loop/lessons" \
    ".goat-flow/learning-loop/footguns" \
    ".goat-flow/skill-docs/playbooks" \
    "scripts/context-validate.sh" \
    ".goat-flow/hooks" \
    "CLAUDE.md"
do
    require_grep "$ref" "AGENTS.md"
done

if [[ -x ".goat-flow/hooks/deny-dangerous/deny-dangerous-self-test.sh" ]]; then
    ./.goat-flow/hooks/deny-dangerous/deny-dangerous-self-test.sh >/dev/null
else
    fail ".goat-flow/hooks/deny-dangerous/deny-dangerous-self-test.sh is not executable"
fi

# Footguns live in .goat-flow/learning-loop/footguns/ as category bucket files. Each
# entry MUST cite file evidence with a grep-friendly semantic anchor.
mapfile -t footgun_files < <(find .goat-flow/learning-loop/footguns -maxdepth 1 -type f -name '*.md' ! -name 'README.md' ! -name 'INDEX.md' | sort)
[[ "${#footgun_files[@]}" -gt 0 ]] || fail ".goat-flow/learning-loop/footguns/ has no category bucket files"

total_refs=0
for fg_file in "${footgun_files[@]}"; do
    mapfile -t refs < <(grep -n '^- \*\*Files:\*\* ' "$fg_file" || true)
    for entry in "${refs[@]}"; do
        raw="${entry#*:}"
        raw="${raw#- **Files:** }"
        ref_path=""
        if [[ "$raw" == \`* ]]; then
            ref_path="${raw#\`}"
            if [[ "$ref_path" == *\`* ]]; then
                ref_path="${ref_path%%\`*}"
            else
                ref_path=""
            fi
        fi
        [[ -n "$ref_path" ]] || fail "footgun reference missing backticked path: $raw ($fg_file)"
        [[ -e "$ref_path" ]] || fail "footgun reference path missing: $ref_path ($fg_file)"
        [[ "$raw" =~ \(search:\ \"[^\"]+\"\) ]] || fail "footgun reference missing semantic search anchor: $raw ($fg_file)"
        total_refs=$((total_refs + 1))
    done
done
[[ "$total_refs" -gt 0 ]] || fail ".goat-flow/learning-loop/footguns/ has no structured file evidence entries"

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
