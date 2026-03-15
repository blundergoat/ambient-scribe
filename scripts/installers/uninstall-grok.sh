#!/bin/bash
# GOAT System Uninstaller - Grok CLI
# Run this script in Git Bash, WSL, or any Unix-like terminal.

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/_common.sh"

echo -e "${CYAN}Starting Grok CLI uninstallation process...${NC}"
print_platform

require_npm || exit 1

echo -e "\n${CYAN}========================================"
echo -e "Uninstalling Grok CLI via npm"
echo -e "========================================${NC}"

if npm uninstall -g @vibe-kit/grok-cli; then
    echo -e "\n${GREEN}Grok CLI uninstalled successfully!${NC}"
else
    echo -e "\n${RED}Error uninstalling Grok CLI.${NC}"
    echo -e "${YELLOW}It might not be installed globally. Try checking with 'npm list -g @vibe-kit/grok-cli'${NC}"
fi

echo -e "\n${CYAN}========================================"
echo -e "Cleaning up Grok CLI user settings"
echo -e "========================================${NC}"

remove_dir_prompt "$HOME/.grok"

echo -e "\n${GREEN}========================================"
echo -e "Uninstallation process completed!"
echo -e "========================================${NC}"
