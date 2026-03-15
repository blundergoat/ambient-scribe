#!/usr/bin/env bash
# PostToolUse hook: auto-format files after Edit/Write.
# Formats based on file extension. Silences failures.

FILE_PATH="${CLAUDE_FILE_PATH:-}"
if [ -z "$FILE_PATH" ]; then exit 0; fi

ROOT=$(git rev-parse --show-toplevel 2>/dev/null)
if [ -z "$ROOT" ]; then exit 0; fi

cd "$ROOT" || exit 0

case "$FILE_PATH" in
  *.php)
    if command -v vendor/bin/php-cs-fixer &>/dev/null; then
      vendor/bin/php-cs-fixer fix --quiet "$FILE_PATH" 2>/dev/null || true
    fi
    ;;
  *.py)
    if command -v ruff &>/dev/null; then
      ruff format --quiet "$FILE_PATH" 2>/dev/null || true
    fi
    ;;
  # Add more formatters as needed:
  # *.ts|*.tsx|*.js|*.jsx)
  #   npx prettier --write "$FILE_PATH" 2>/dev/null || true
  #   ;;
esac

exit 0
