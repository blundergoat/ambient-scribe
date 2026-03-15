#!/usr/bin/env bash
# PreToolUse hook: block dangerous commands before execution.
# Exit 2 = block with error message. Exit 0 = allow.

set -euo pipefail

# The command being executed is passed via stdin for Bash tool
INPUT=$(cat)

# Extract the command from the tool input
COMMAND=$(echo "$INPUT" | jq -r '.command // empty' 2>/dev/null)

if [ -z "$COMMAND" ]; then
  exit 0
fi

# --- Universal deny rules ---

# rm -rf without explicit path scoping
if echo "$COMMAND" | grep -qE 'rm\s+-[a-zA-Z]*r[a-zA-Z]*f|rm\s+-[a-zA-Z]*f[a-zA-Z]*r'; then
  if ! echo "$COMMAND" | grep -qE 'rm\s+-rf\s+\./|rm\s+-rf\s+[a-zA-Z_/]+[a-zA-Z]'; then
    echo "BLOCKED: rm -rf without explicit path. Use a specific path like 'rm -rf ./dir_name'" >&2
    exit 2
  fi
fi

# git push to main/master/production
if echo "$COMMAND" | grep -qE 'git\s+push\s+.*\b(main|master|production)\b'; then
  echo "BLOCKED: Direct push to protected branch. Create a feature branch and open a PR instead." >&2
  exit 2
fi

# git push --force (suggest --force-with-lease)
if echo "$COMMAND" | grep -qE 'git\s+push\s+.*--force\b'; then
  if ! echo "$COMMAND" | grep -qE 'git\s+push\s+.*--force-with-lease'; then
    echo "BLOCKED: git push --force is dangerous. Use --force-with-lease instead." >&2
    exit 2
  fi
fi

# chmod 777
if echo "$COMMAND" | grep -qE 'chmod\s+777'; then
  echo "BLOCKED: chmod 777 is overly permissive. Use specific permissions (e.g., 755 for dirs, 644 for files)." >&2
  exit 2
fi

# Pipe-to-shell patterns
if echo "$COMMAND" | grep -qE 'curl\s+.*\|\s*(ba)?sh|wget\s+.*\|\s*(ba)?sh'; then
  echo "BLOCKED: Pipe-to-shell is dangerous. Download the script first, review it, then execute." >&2
  exit 2
fi

# .env file modifications
if echo "$COMMAND" | grep -qE '(>|>>|tee|sed\s+-i|mv\s+.*)\s*\.env\b'; then
  echo "BLOCKED: Direct .env modification. Edit .env.example instead and document the change." >&2
  exit 2
fi

# git commit --no-verify / -n (hook bypass)
if echo "$COMMAND" | grep -qE 'git\s+commit\s+.*(-n\b|--no-verify)'; then
  echo "BLOCKED: Skipping git hooks (--no-verify / -n). Fix the hook issue instead of bypassing." >&2
  exit 2
fi

# --- Project-specific deny rules ---

# Direct edits to .nemo model files (too large, must be managed externally)
if echo "$COMMAND" | grep -qE '\.(nemo)\b.*(>|>>|cp|mv|sed|awk)|(>|>>|cp|mv|sed|awk).*\.(nemo)\b'; then
  echo "BLOCKED: Direct modification of .nemo model files. Models are managed externally." >&2
  exit 2
fi

# terraform apply without plan
if echo "$COMMAND" | grep -qE 'terraform\s+apply\b'; then
  if ! echo "$COMMAND" | grep -qE 'terraform\s+apply\s+.*\.tfplan|terraform\s+apply\s+-auto-approve'; then
    echo "BLOCKED: Run 'terraform plan -out=plan.tfplan' first, review, then 'terraform apply plan.tfplan'." >&2
    exit 2
  fi
fi

# All checks passed
exit 0
