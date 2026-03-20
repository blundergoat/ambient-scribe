#!/usr/bin/env bash
# PostToolUse hook: .gemini/hooks/format-file.sh

# 1. Check if tool wrote or edited a file
if [[ "$GEMINI_TOOL_NAME" == "write_file" || "$GEMINI_TOOL_NAME" == "replace" ]]; then
    FILE_PATH=$(echo "$GEMINI_TOOL_ARGS_JSON" | jq -r '.file_path' 2>/dev/null || echo "$1")
    
    # Format by file extension
    case "$FILE_PATH" in
        *.php)
            echo "Formatting PHP file: $FILE_PATH"
            composer cs:fix "$FILE_PATH" || echo "PHP formatter failed."
            ;;
        *.py)
            echo "Formatting Python file: $FILE_PATH"
            ruff format "$FILE_PATH" || echo "Python formatter failed."
            ;;
    esac
fi

exit 0
