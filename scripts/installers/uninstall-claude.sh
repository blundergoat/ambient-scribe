#!/bin/bash
# GOAT System Uninstaller - Claude CLI
# Run this script in Git Bash, WSL, or any Unix-like terminal.

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/_common.sh"

CLAUDE_NPM_PACKAGE=${CLAUDE_NPM_PACKAGE:-@anthropic-ai/claude-code}

echo -e "${CYAN}Starting Claude CLI uninstallation process...${NC}"
print_platform

require_npm || exit 1

echo -e "\n${CYAN}========================================"
echo -e "Uninstalling Claude CLI via npm"
echo -e "========================================${NC}"

if npm uninstall -g "${CLAUDE_NPM_PACKAGE}"; then
    echo -e "\n${GREEN}Claude CLI uninstalled via npm.${NC}"
else
    echo -e "\n${YELLOW}npm uninstall reported an issue. The package may not have been installed globally.${NC}"
    echo -e "${YELLOW}You can check with: npm list -g ${CLAUDE_NPM_PACKAGE}${NC}"
fi

echo -e "\n${CYAN}========================================"
echo -e "Cleaning up Claude CLI data"
echo -e "========================================${NC}"

for dir in "$HOME/.claude" "$HOME/.config/claude" "$HOME/.config/anthropic" "$HOME/.cache/claude"; do
    remove_dir_prompt "$dir"
done

echo -e "\n${CYAN}========================================"
echo -e "Verifying uninstall"
echo -e "========================================${NC}"

if command_exists claude; then
    CLAUDE_PATH=$(command -v claude)
    echo -e "${YELLOW}claude command still present at: ${CLAUDE_PATH}${NC}"
    echo -e "${YELLOW}You may need to remove it from your PATH or restart your shell.${NC}"
else
    echo -e "${GREEN}Claude CLI command not found. Uninstall appears complete.${NC}"
fi

echo -e "\n${GREEN}========================================"
echo -e "Uninstallation process completed!"
echo -e "========================================${NC}"
