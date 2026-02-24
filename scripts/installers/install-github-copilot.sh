#!/bin/bash
# GOAT System Installer - GitHub Copilot CLI
#
# WARNING: Only install on systems you own or have permission to modify.
# This script is for personal development environments only.
#
# Installs the standalone GitHub Copilot CLI (copilot) via npm.
# Auth happens on first run via /login - no pre-auth required.
# Run this script in Git Bash, WSL, or any Unix-like terminal.

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/_common.sh"

echo -e "${CYAN}Starting GitHub Copilot CLI installation process...${NC}"
echo -e "${YELLOW}This will install the standalone Copilot CLI from GitHub${NC}"
print_platform

require_node_or_install || exit 1

echo -e "\n${CYAN}========================================"
echo -e "Installing GitHub Copilot CLI via npm"
echo -e "========================================${NC}"

if ! npm install -g @github/copilot --loglevel=error --no-audit --no-fund; then
    echo -e "\n${RED}Error installing GitHub Copilot CLI${NC}"
    echo -e "\n${YELLOW}Troubleshooting steps:${NC}"
    echo -e "${WHITE}1. Check internet connection"
    echo -e "2. npm config list"
    echo -e "3. Try: npm install -g @github/copilot${NC}"
    exit 1
fi

echo -e "\n${YELLOW}Verifying installation...${NC}"
if verify_native_binary copilot "GitHub Copilot CLI"; then
    echo -e "${GREEN}GitHub Copilot CLI installed successfully!${NC}"
    copilot --version 2>/dev/null || echo -e "${YELLOW}Version command not available yet${NC}"
    npm_prefix_warning

    echo -e "\n${CYAN}========================================"
    echo -e "Next Steps:"
    echo -e "========================================${NC}"
    echo -e "${WHITE}1. Start the CLI: ${GREEN}copilot${NC}"
    echo -e "${WHITE}2. On first run, use ${GREEN}/login${WHITE} to authenticate with GitHub${NC}"
    echo -e "${WHITE}3. Use ${GREEN}/model${WHITE} to select an AI model${NC}"
    echo -e "${WHITE}4. Run copilot --help for commands${NC}"
fi

echo -e "\n${GREEN}Installation process completed!${NC}"
