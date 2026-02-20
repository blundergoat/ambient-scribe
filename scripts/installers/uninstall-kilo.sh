#!/bin/bash
# GOAT System Uninstaller - Kilo CLI
# Removes the Kilo CLI and its LM Studio configuration.
# Run this script in Git Bash, WSL, or any Unix-like terminal.

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
WHITE='\033[1;37m'
NC='\033[0m' # No Color

KILO_NPM_PACKAGE=${KILO_NPM_PACKAGE:-kilo-cli}
KILO_CONFIG_DIR="${HOME}/.kilocode/cli"

echo -e "${CYAN}Starting Kilo CLI uninstallation...${NC}"
echo -e "${YELLOW}npm package: ${WHITE}${KILO_NPM_PACKAGE}${NC}"

command_exists() {
    command -v "$1" >/dev/null 2>&1
}

if ! command_exists npm; then
    echo -e "${RED}npm is required to uninstall the Kilo CLI package.${NC}"
    echo -e "${YELLOW}Install Node.js/npm or remove the package manually.${NC}"
    exit 1
fi

echo -e "\n${CYAN}========================================"
echo -e "Uninstalling Kilo CLI via npm"
echo -e "========================================${NC}"
npm uninstall -g "${KILO_NPM_PACKAGE}"
if [ $? -eq 0 ]; then
    echo -e "${GREEN}npm uninstall completed.${NC}"
else
    echo -e "${YELLOW}npm uninstall reported an issue. Check with: npm list -g ${KILO_NPM_PACKAGE}${NC}"
fi

echo -e "\n${CYAN}========================================"
echo -e "Cleaning Kilo CLI configuration"
echo -e "========================================${NC}"
if [ -d "${KILO_CONFIG_DIR}" ]; then
    read -p "Remove ${KILO_CONFIG_DIR} and its contents? (y/n): " confirm_remove
    if [[ "$confirm_remove" == "y" ]]; then
        rm -rf "${KILO_CONFIG_DIR}"
        if [ $? -eq 0 ]; then
            echo -e "${GREEN}Removed ${KILO_CONFIG_DIR}${NC}"
        else
            echo -e "${RED}Failed to remove ${KILO_CONFIG_DIR}${NC}"
        fi
    else
        echo -e "${YELLOW}Skipped removing ${KILO_CONFIG_DIR}${NC}"
    fi
else
    echo -e "${YELLOW}Config directory not found: ${KILO_CONFIG_DIR}${NC}"
fi

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
