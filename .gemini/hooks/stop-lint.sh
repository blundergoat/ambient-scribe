#!/usr/bin/env bash
# Stop hook: .gemini/hooks/stop-lint.sh

# 1. Infinite loop guard
MAX_RUNS=3
RUN_COUNT_FILE="/tmp/stop-lint-runs-$GEMINI_SESSION_ID"
RUN_COUNT=$(cat "$RUN_COUNT_FILE" 2>/dev/null || echo 0)

if [[ $RUN_COUNT -ge $MAX_RUNS ]]; then
    echo "Infinite loop guard: stop-lint has run $MAX_RUNS times. Bailing out."
    exit 0
fi

echo $((RUN_COUNT + 1)) > "$RUN_COUNT_FILE"

# 2. Stack-adaptive linting
CHANGED_FILES=$(git diff --name-only HEAD)

# PHP check
if echo "$CHANGED_FILES" | grep -q "\.php$"; then
    echo "Running PHPStan on changed files..."
    composer analyse || echo "PHPStan found issues, but continuing to avoid blocking session exit."
fi

# Python check
if echo "$CHANGED_FILES" | grep -q "\.py$"; then
    echo "Running Ruff on changed files..."
    ruff check strands_agents/ || echo "Ruff not found or failed, continuing."
fi

exit 0
