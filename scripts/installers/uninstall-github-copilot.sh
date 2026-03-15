#!/bin/bash
# GOAT System Uninstaller - GitHub Copilot CLI
# Run this script in Git Bash, WSL, or any Unix-like terminal.

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/_common.sh"

echo -e "${CYAN}Starting GitHub Copilot CLI uninstallation process...${NC}"
print_platform

require_npm || exit 1

echo -e "\n${CYAN}========================================"
echo -e "Uninstalling GitHub Copilot CLI via npm"
echo -e "========================================${NC}"

if npm uninstall -g @github/copilot; then
    echo -e "\n${GREEN}GitHub Copilot CLI uninstalled via npm.${NC}"
else
    echo -e "\n${YELLOW}npm uninstall reported an issue. The package may not have been installed globally.${NC}"
    echo -e "${YELLOW}You can check with: npm list -g @github/copilot${NC}"
fi

echo -e "\n${CYAN}========================================"
echo -e "Cleaning up GitHub Copilot CLI data"
echo -e "========================================${NC}"

for dir in "$HOME/.copilot" "$HOME/.config/copilot" "$HOME/.config/github-copilot" "$HOME/.cache/copilot"; do
    remove_dir_prompt "$dir"
done

echo -e "\n${CYAN}========================================"
echo -e "Verifying uninstall"
echo -e "========================================${NC}"

if command_exists copilot; then
    COPILOT_PATH=$(command -v copilot)
    echo -e "${YELLOW}copilot command still present at: ${COPILOT_PATH}${NC}"
    echo -e "${YELLOW}You may need to remove it from your PATH or restart your shell.${NC}"
else
    echo -e "${GREEN}GitHub Copilot CLI command not found. Uninstall appears complete.${NC}"
fi

echo -e "\n${GREEN}========================================"
echo -e "Uninstallation process completed!"
echo -e "========================================${NC}"
