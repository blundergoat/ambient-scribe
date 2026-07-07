#!/bin/bash
# GOAT System Installer - Grok CLI
#
# WARNING: Only install on systems you own or have permission to modify.
# This script is for personal development environments only.
#
# Installs Grok CLI via npm package @vibe-kit/grok-cli.
# Run this script in Git Bash, WSL, or any Unix-like terminal.

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/_common.sh"

echo -e "${CYAN}Starting Grok CLI installation process...${NC}"
echo -e "${YELLOW}This will install Grok CLI from Vibe Kit${NC}"
print_platform

require_node_or_install || exit 1

echo -e "\n${CYAN}========================================"
echo -e "Installing Grok CLI via npm"
echo -e "========================================${NC}"

echo -e "\n${YELLOW}Installing Grok CLI via npm...${NC}"
echo -e "${WHITE}This will install the latest version of Grok CLI${NC}"

if ! npm install -g @vibe-kit/grok-cli 2>/dev/null; then
    echo -e "\n${YELLOW}Permission denied for global installation.${NC}"
    echo -e "${YELLOW}Use the default user-level npm prefix and rerun:${NC}"
    if [[ "$GOAT_OS" == "Windows" ]]; then
        echo -e "${WHITE}npm config set prefix \"\$APPDATA/npm\"${NC}"
    else
        echo -e "${WHITE}npm config set prefix \"\$HOME/.npm\"${NC}"
    fi
    echo -e "${WHITE}npm install -g @vibe-kit/grok-cli${NC}"
    echo -e "${YELLOW}Ensure that prefix/bin is on your PATH.${NC}"
    exit 1
fi

echo -e "\n${YELLOW}Verifying installation...${NC}"
if verify_native_binary grok "Grok CLI"; then
    echo -e "\n${GREEN}Grok CLI installed successfully!${NC}"
    grok --version 2>/dev/null || echo -e "${YELLOW}Version command not available yet${NC}"
    npm_prefix_warning

    # API Key Setup - skip in non-interactive mode
    echo -e "\n${CYAN}========================================"
    echo -e "Setting up Grok API Key"
    echo -e "========================================${NC}"

    if [[ -t 0 ]]; then
        read -r -sp "Please enter your Grok API key: " grok_api_key
        echo

        if [[ -z "$grok_api_key" ]]; then
            echo -e "${YELLOW}No API key provided. You can set it up later by creating the file ~/.grok/user-settings.json${NC}"
        else
            mkdir -p ~/.grok
            echo "{\"apiKey\": \"$grok_api_key\"}" > ~/.grok/user-settings.json
            echo -e "${GREEN}Grok API key saved successfully to ~/.grok/user-settings.json${NC}"
        fi
    else
        echo -e "${YELLOW}Non-interactive mode: skipping API key setup.${NC}"
        echo -e "${YELLOW}Set up your API key later by creating ~/.grok/user-settings.json${NC}"
    fi

    echo -e "\n${CYAN}========================================"
    echo -e "Next Steps:"
    echo -e "========================================${NC}"
    echo -e "${WHITE}1. Start the CLI: ${GREEN}grok${NC}"
    echo -e "${WHITE}2. Use 'grok --help' to see available commands${NC}"
    echo -e "${WHITE}3. Your API key is saved in ~/.grok/user-settings.json${NC}"
fi

echo -e "\n${GREEN}========================================"
echo -e "Installation process completed!"
echo -e "========================================${NC}"
echo -e "\n${CYAN}For more information and documentation, visit the Vibe Kit repository.${NC}"
