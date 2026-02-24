#!/bin/bash
# GOAT System Installer - Codex CLI
#
# WARNING: Only install on systems you own or have permission to modify.
# This script is for personal development environments only.
#
# Installs Codex CLI via npm or Homebrew.
# Run this script in Git Bash, WSL, or any Unix-like terminal.

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/_common.sh"

echo -e "${CYAN}Starting Codex CLI installation process...${NC}"
echo -e "${YELLOW}This will install Codex CLI from OpenAI${NC}"
print_platform

# macOS — prefer Homebrew
installed_via_brew="false"
if [[ "$GOAT_OS" == "macOS" ]] && command_exists brew; then
    echo -e "\n${YELLOW}Homebrew detected. Installing via Homebrew...${NC}"
    echo -e "${CYAN}========================================"
    echo -e "Installing Codex CLI via Homebrew"
    echo -e "========================================${NC}"

    if brew install codex; then
        echo -e "\n${GREEN}Codex CLI installed successfully via Homebrew!${NC}"
        installed_via_brew="true"
    else
        echo -e "\n${RED}Homebrew installation failed. Trying npm...${NC}"
    fi
fi

# Non-macOS or Homebrew fallback
if [[ "$installed_via_brew" != "true" ]]; then
    require_node_or_install || exit 1

    echo -e "\n${CYAN}========================================"
    echo -e "Installing Codex CLI via npm"
    echo -e "========================================${NC}"

    echo -e "\n${YELLOW}Installing Codex CLI via npm...${NC}"
    echo -e "${WHITE}This will install the latest version of the Codex CLI${NC}"

    if npm install -g @openai/codex --loglevel=error --no-audit --no-fund; then
        echo -e "\n${GREEN}npm install completed.${NC}"
    else
        if ! command_exists codex; then
            echo -e "\n${RED}Global installation failed.${NC}"
            echo -e "${YELLOW}This is likely a permission issue. Please try one of the following:${NC}"
            echo -e "${WHITE}1. Run the script again with 'sudo'."
            echo -e "${WHITE}2. Manually run: sudo npm install -g @openai/codex"
            echo -e "${WHITE}3. Configure npm to use a user-owned directory (see npm docs for 'prefix').${NC}"
            exit 1
        else
            echo -e "\n${YELLOW}npm install reported an error, but 'codex' seems to be installed.${NC}"
            echo -e "${YELLOW}This can happen with permission errors on global package updates. Continuing...${NC}"
        fi
    fi
fi

echo -e "\n${YELLOW}Verifying installation...${NC}"
if verify_native_binary codex "Codex CLI"; then
    echo -e "\n${GREEN}Codex CLI installed successfully!${NC}"
    codex --version 2>/dev/null || echo -e "${YELLOW}Version command not available yet${NC}"
    npm_prefix_warning

    echo -e "\n${CYAN}========================================"
    echo -e "Next Steps:"
    echo -e "========================================${NC}"
    echo -e "${WHITE}1. Start the CLI: ${GREEN}codex${NC}"
    echo -e "${WHITE}2. On first run, you'll be prompted to authenticate"
    echo -e "${WHITE}3. Sign in with your ChatGPT account (recommended)"
    echo -e "${WHITE}4. Alternative: Authenticate with OpenAI API key"
    echo -e "${WHITE}5. Use 'codex --help' to see available commands${NC}"
    echo -e "\n${CYAN}Platform Support:${NC}"
    echo -e "${WHITE}- macOS and Linux: Fully supported"
    echo -e "- Windows: Experimental (use WSL for best experience)${NC}"
fi

echo -e "\n${GREEN}========================================"
echo -e "Installation process completed!"
echo -e "========================================${NC}"
echo -e "\n${CYAN}For more information and documentation:"
echo -e "${WHITE}- Next step run codex login"
echo -e "- Codex CLI docs: https://developers.openai.com/codex/cli/"
echo -e "- GitHub repository: https://github.com/openai/openai-python${NC}"
