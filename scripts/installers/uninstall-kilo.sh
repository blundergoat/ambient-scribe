#!/bin/bash
# GOAT System Uninstaller - Kilo CLI
# Removes the Kilo CLI and its LM Studio configuration.
# Run this script in Git Bash, WSL, or any Unix-like terminal.

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/_common.sh"

KILO_NPM_PACKAGE=${KILO_NPM_PACKAGE:-kilo-cli}
KILO_CONFIG_DIR="${HOME}/.kilocode/cli"

echo -e "${CYAN}Starting Kilo CLI uninstallation...${NC}"
echo -e "${YELLOW}npm package: ${WHITE}${KILO_NPM_PACKAGE}${NC}"
print_platform

require_npm || exit 1

echo -e "\n${CYAN}========================================"
echo -e "Uninstalling Kilo CLI via npm"
echo -e "========================================${NC}"

if npm uninstall -g "${KILO_NPM_PACKAGE}"; then
    echo -e "${GREEN}npm uninstall completed.${NC}"
else
    echo -e "${YELLOW}npm uninstall reported an issue. Check with: npm list -g ${KILO_NPM_PACKAGE}${NC}"
fi

echo -e "\n${CYAN}========================================"
echo -e "Cleaning Kilo CLI configuration"
echo -e "========================================${NC}"

remove_dir_prompt "${KILO_CONFIG_DIR}"

echo -e "\n${CYAN}========================================"
echo -e "Verifying uninstall"
echo -e "========================================${NC}"

if command_exists kilo; then
    KILO_PATH=$(command -v kilo)
    echo -e "${YELLOW}kilo command still present at: ${KILO_PATH}${NC}"
    echo -e "${YELLOW}You may need to remove it from PATH or restart your shell.${NC}"
else
    echo -e "${GREEN}Kilo CLI command not found. Uninstall appears complete.${NC}"
fi

echo -e "\n${GREEN}Uninstallation process completed!${NC}"
