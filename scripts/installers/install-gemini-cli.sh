#!/bin/bash
# GOAT System Installer - Gemini CLI
#
# WARNING: Only install on systems you own or have permission to modify.
# This script is for personal development environments only.
#
# Installs Gemini CLI via npm package @google/gemini-cli.
# Run this script in Git Bash, WSL, or any Unix-like terminal.

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/_common.sh"

echo -e "${CYAN}Starting Gemini CLI installation process...${NC}"
echo -e "${YELLOW}This will install Gemini CLI using npm package @google/gemini-cli${NC}"
print_platform

require_node_or_install || exit 1

echo -e "\n${CYAN}========================================"
echo -e "Installing Gemini CLI via npm"
echo -e "========================================${NC}"

echo -e "\n${YELLOW}Installing Gemini CLI via npm...${NC}"
echo -e "${WHITE}This will install the latest version of Gemini CLI${NC}"

if ! npm install -g @google/gemini-cli --loglevel=error --no-audit --no-fund; then
    echo -e "\n${RED}Error installing Gemini CLI${NC}"
    echo -e "\n${YELLOW}Troubleshooting steps:${NC}"
    echo -e "${WHITE}1. Make sure you have an internet connection"
    echo -e "2. Try installing without the -g flag in a local project"
    echo -e "3. Check npm configuration: npm config list${NC}"
    echo -e "\n${CYAN}Try running directly:"
    echo -e "${GREEN}npm install -g @google/gemini-cli${NC}"
    exit 1
fi

echo -e "\n${GREEN}Gemini CLI installation completed!${NC}"

echo -e "\n${YELLOW}Verifying installation...${NC}"
if verify_native_binary gemini "Gemini CLI"; then
    gemini --version 2>/dev/null || echo -e "${YELLOW}Version command not available yet${NC}"
    npm_prefix_warning
    echo -e "\n${GREEN}Gemini CLI installed successfully!${NC}"
    echo -e "\n${CYAN}========================================"
    echo -e "Next Steps:"
    echo -e "========================================${NC}"
    echo -e "${WHITE}1. Start the CLI: gemini"
    echo -e "2. On first run, complete the OAuth authentication with your Google account."
    echo -e "3. For higher limits, set your API key: export GEMINI_API_KEY=\"YOUR_API_KEY\""
    echo -e "4. Use 'gemini doctor' to verify your setup."
    echo -e "5. Use 'gemini --help' to see available commands.${NC}"
fi

echo -e "\n${GREEN}========================================"
echo -e "Installation process completed!"
echo -e "========================================${NC}"
echo -e "\n${CYAN}For more information and documentation:"
echo -e "${WHITE}- Gemini CLI docs: https://github.com/google-gemini/gemini-cli${NC}"
