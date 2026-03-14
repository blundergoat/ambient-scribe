#!/usr/bin/env bash
# Stop hook: stack-adaptive lint check after each Claude turn.
# MUST exit 0 even when errors found — non-zero causes infinite fix loops.
# Errors go to stderr (informational, not imperative).

# Infinite loop guard
if [ "${STOP_HOOK_ACTIVE:-}" = "1" ]; then exit 0; fi
export STOP_HOOK_ACTIVE=1

ROOT=$(git rev-parse --show-toplevel 2>/dev/null)
if [ -z "$ROOT" ]; then exit 0; fi

cd "$ROOT" || exit 0

# Check git diff for modified file types
CHANGED_FILES=$(git diff --name-only HEAD 2>/dev/null || git diff --name-only 2>/dev/null || echo "")
if [ -z "$CHANGED_FILES" ]; then exit 0; fi

HAS_PHP=false
HAS_PYTHON=false

if echo "$CHANGED_FILES" | grep -qE '\.php$'; then HAS_PHP=true; fi
if echo "$CHANGED_FILES" | grep -qE '\.py$'; then HAS_PYTHON=true; fi

# PHP: syntax check on changed files (fast, <2s)
if [ "$HAS_PHP" = true ]; then
  if command -v php &>/dev/null; then
    PHP_FILES=$(echo "$CHANGED_FILES" | grep -E '\.php$' || true)
    for f in $PHP_FILES; do
      if [ -f "$f" ]; then
        output=$(php -l "$f" 2>&1) || {
          echo "PHP syntax error in $f:" >&2
          echo "$output" >&2
        }
      fi
    done
  fi
fi

# Python: syntax check on changed files (fast, <2s)
if [ "$HAS_PYTHON" = true ]; then
  if command -v python3 &>/dev/null; then
    PY_FILES=$(echo "$CHANGED_FILES" | grep -E '\.py$' || true)
    for f in $PY_FILES; do
      if [ -f "$f" ]; then
        python3 -m py_compile "$f" 2>&1 || {
          echo "Python syntax error in $f" >&2
        }
      fi
    done
  fi
fi

# Always exit 0 — errors are informational only
exit 0
