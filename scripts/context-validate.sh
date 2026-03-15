#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"

fail() {
    echo "FAIL: $1" >&2
    exit 1
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
require_file "docs/lessons.md"
require_file "docs/footguns.md"
require_file "docs/architecture.md"
require_file "docs/domain-reference.md"
require_file "docs/guidelines-ownership-split.md"
require_file "tasks/todo.md"
require_file "tasks/handoff.md"
require_file "codex-evals/README.md"

for ref in \
    "docs/architecture.md" \
    "docs/domain-reference.md" \
    "docs/footguns.md" \
    "docs/lessons.md" \
    "docs/guidelines-ownership-split.md" \
    "docs/codex-playbooks/preflight.md" \
    "docs/codex-playbooks/research.md" \
    "docs/codex-playbooks/debug-investigate.md" \
    "docs/codex-playbooks/audit.md" \
    "docs/codex-playbooks/code-review.md" \
    "codex-evals/README.md" \
    "scripts/context-validate.sh" \
    "scripts/deny-dangerous.sh"
do
    require_grep "$ref" "AGENTS.md"
done

require_file "docs/codex-playbooks/preflight.md"
require_grep '^## MUST$' "docs/codex-playbooks/preflight.md"
require_grep '^## SHOULD$' "docs/codex-playbooks/preflight.md"
require_grep 'Dependency Audit' "docs/codex-playbooks/preflight.md"

require_file "docs/codex-playbooks/research.md"
require_grep '^## Files Involved$' "docs/codex-playbooks/research.md"
require_grep '^## Request Flow$' "docs/codex-playbooks/research.md"
require_grep '^## Boundaries Touched$' "docs/codex-playbooks/research.md"
require_grep '^## Risks/Gotchas$' "docs/codex-playbooks/research.md"
require_grep 'Hard gate:' "docs/codex-playbooks/research.md"

require_file "docs/codex-playbooks/debug-investigate.md"
require_grep 'STOP\.' "docs/codex-playbooks/debug-investigate.md"
require_grep '^## Diagnosis Output$' "docs/codex-playbooks/debug-investigate.md"
require_grep 'Hard gate:' "docs/codex-playbooks/debug-investigate.md"

require_file "docs/codex-playbooks/audit.md"
require_grep '^## Discovery$' "docs/codex-playbooks/audit.md"
require_grep '^## Verification$' "docs/codex-playbooks/audit.md"
require_grep '^## Prioritisation$' "docs/codex-playbooks/audit.md"
require_grep '^## Self-Check$' "docs/codex-playbooks/audit.md"
require_grep 'MUST NOT propose fixes' "docs/codex-playbooks/audit.md"

require_file "docs/codex-playbooks/code-review.md"
require_grep '^## Priority Markers$' "docs/codex-playbooks/code-review.md"
require_grep 'autonomy tiers' "docs/codex-playbooks/code-review.md"

if grep -Fq 'none confirmed yet' "docs/footguns.md"; then
    :
else
    require_grep '^### [0-9]+\.' "docs/footguns.md"
    require_grep ':[0-9]+' "docs/footguns.md"
fi

eval_count=$(find codex-evals -maxdepth 1 -type f -name '*.md' ! -name 'README.md' | wc -l | tr -d ' ')
[[ "$eval_count" -ge 5 ]] || fail "expected at least 5 eval files, found $eval_count"

echo "context-validate: OK"
