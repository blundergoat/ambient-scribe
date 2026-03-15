#!/bin/bash
# GOAT System Installer - Claude CLI
#
# WARNING: Only install on systems you own or have permission to modify.
# This script is for personal development environments only.
#
# Installs Claude CLI via npm package @anthropic-ai/claude-code.
# Run this script in Git Bash, WSL, or any Unix-like terminal.

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/_common.sh"

CLAUDE_NPM_PACKAGE=${CLAUDE_NPM_PACKAGE:-@anthropic-ai/claude-code}

echo -e "${CYAN}Starting Claude CLI installation process...${NC}"
echo -e "${YELLOW}This will install Claude CLI using npm package ${CLAUDE_NPM_PACKAGE}${NC}"
print_platform

require_node_or_install || exit 1

echo -e "\n${CYAN}========================================"
echo -e "Installing Claude CLI via npm"
echo -e "========================================${NC}"

if ! npm install -g "${CLAUDE_NPM_PACKAGE}" --loglevel=error --no-audit --no-fund; then
    echo -e "\n${RED}Error installing Claude CLI${NC}"
    echo -e "\n${YELLOW}Troubleshooting steps:${NC}"
    echo -e "${WHITE}1. Check internet connection"
    echo -e "2. npm config list"
    echo -e "3. Try: npm install -g ${CLAUDE_NPM_PACKAGE}${NC}"
    exit 1
fi

echo -e "\n${YELLOW}Verifying installation...${NC}"
if verify_native_binary claude "Claude CLI"; then
    echo -e "${GREEN}Claude CLI installed successfully!${NC}"
    claude --version 2>/dev/null || echo -e "${YELLOW}Version command not available yet${NC}"
    npm_prefix_warning

    echo -e "\n${CYAN}========================================"
    echo -e "Next Steps:"
    echo -e "========================================${NC}"
    echo -e "${WHITE}1. Start the CLI: claude"
    echo -e "2. Set ANTHROPIC_API_KEY for authentication"
    echo -e "3. Run claude --help for commands${NC}"
fi

echo -e "\n${GREEN}Installation process completed!${NC}"
