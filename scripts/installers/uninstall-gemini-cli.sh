#!/bin/bash
# GOAT System Uninstaller - Gemini CLI
# Run this script in Git Bash, WSL, or any Unix-like terminal.

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/_common.sh"

echo -e "${CYAN}Starting Gemini CLI uninstallation process...${NC}"
print_platform

require_npm || exit 1

echo -e "\n${CYAN}========================================"
echo -e "Uninstalling Gemini CLI via npm"
echo -e "========================================${NC}"

if npm uninstall -g @google/gemini-cli; then
    echo -e "\n${GREEN}Gemini CLI uninstalled via npm.${NC}"
else
    echo -e "\n${YELLOW}npm uninstall reported an issue. The package may not have been installed globally.${NC}"
    echo -e "${YELLOW}You can check with: npm list -g @google/gemini-cli${NC}"
fi

echo -e "\n${CYAN}========================================"
echo -e "Cleaning up Gemini CLI data"
echo -e "========================================${NC}"

for dir in "$HOME/.config/gemini" "$HOME/.config/google-gemini" "$HOME/.cache/gemini"; do
    remove_dir_prompt "$dir"
done

echo -e "\n${CYAN}========================================"
echo -e "Verifying uninstall"
echo -e "========================================${NC}"

if command_exists gemini; then
    GEMINI_PATH=$(command -v gemini)
    echo -e "${YELLOW}gemini command still present at: ${GEMINI_PATH}${NC}"
    echo -e "${YELLOW}You may need to remove it from your PATH or restart your shell.${NC}"
else
    echo -e "${GREEN}Gemini CLI command not found. Uninstall appears complete.${NC}"
fi

echo -e "\n${GREEN}========================================"
echo -e "Uninstallation process completed!"
echo -e "========================================${NC}"
