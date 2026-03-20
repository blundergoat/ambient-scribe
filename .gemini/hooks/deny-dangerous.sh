#!/usr/bin/env bash
# PreToolUse hook: .gemini/hooks/deny-dangerous.sh

# 1. Block dangerous shell commands
if [[ "$GEMINI_TOOL_NAME" == "run_shell_command" ]]; then
    COMMAND=$(echo "$GEMINI_TOOL_ARGS_JSON" | jq -r '.command' 2>/dev/null || echo "$1")
    
    # Block unscoped rm -rf, git push main, etc.
    ./scripts/deny-dangerous.sh --command "$COMMAND" || exit 1
fi

# 2. Block sensitive file edits (Write/Edit)
if [[ "$GEMINI_TOOL_NAME" == "write_file" || "$GEMINI_TOOL_NAME" == "replace" ]]; then
    FILE_PATH=$(echo "$GEMINI_TOOL_ARGS_JSON" | jq -r '.file_path' 2>/dev/null || echo "$1")
    
    case "$FILE_PATH" in
        *.env|.env.*)
            echo "DENY: Direct .env edits are blocked. Use .env.example or ask user." >&2
            exit 1
            ;;
        composer.lock|package-lock.json)
            echo "DENY: Lockfile edits should be handled by package managers." >&2
            exit 1
            ;;
    esac
fi

exit 0
