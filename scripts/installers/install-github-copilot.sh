#!/bin/bash
# GOAT System Installer - GitHub Copilot CLI
#
# WARNING: Only install on systems you own or have permission to modify.
# This script is for personal development environments only.
#
# Installs the standalone GitHub Copilot CLI (copilot) via npm.
# Auth happens on first run via /login - no pre-auth required.
# Run this script in Git Bash, WSL, or any Unix-like terminal.

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
WHITE='\033[1;37m'
NC='\033[0m' # No Color

echo -e "${CYAN}Starting GitHub Copilot CLI installation process...${NC}"
echo -e "${YELLOW}This will install the standalone Copilot CLI from GitHub${NC}"

# Function to check if a command exists
command_exists() {
    command -v "$1" >/dev/null 2>&1
}

# Detect OS
if [[ "$OSTYPE" == "darwin"* ]]; then
    OS="macOS"
elif [[ "$OSTYPE" == "linux-gnu"* ]]; then
    OS="Linux"
elif [[ "$OSTYPE" == "msys" ]] || [[ "$OSTYPE" == "mingw"* ]]; then
    OS="Windows"
else
    OS="Unknown"
fi

echo -e "\n${CYAN}Detected OS: ${WHITE}$OS${NC}"

# Check if Node.js is installed (required for npm)
echo -e "\n${YELLOW}Checking for Node.js installation...${NC}"

if command_exists node; then
    NODE_VERSION=$(node --version)
    echo -e "${GREEN}Node.js is already installed (version $NODE_VERSION)${NC}"

    if command_exists npm; then
        NPM_VERSION=$(npm --version)
        echo -e "${GREEN}npm is already installed (version $NPM_VERSION)${NC}"
    else
        echo -e "${RED}npm is not found. Please reinstall Node.js.${NC}"
        exit 1
    fi
else
    echo -e "${RED}Node.js is required for GitHub Copilot CLI installation.${NC}"
    echo -e "${RED}Please install Node.js first (or enable it in your Forge config).${NC}"
    exit 1
fi

echo -e "\n${CYAN}========================================"
echo -e "Installing GitHub Copilot CLI via npm"
echo -e "========================================${NC}"

if command_exists npm; then
    npm install -g @github/copilot --loglevel=error --no-audit --no-fund
else
    echo -e "${RED}Error: npm is not installed.${NC}"
    echo -e "${YELLOW}Please install Node.js and npm first.${NC}"
    exit 1
fi

if [ $? -ne 0 ]; then
    echo -e "\n${RED}Error installing GitHub Copilot CLI${NC}"
    echo -e "\n${YELLOW}Troubleshooting steps:${NC}"
    echo -e "${WHITE}1. Check internet connection"
    echo -e "2. npm config list"
    echo -e "3. Try: npm install -g @github/copilot${NC}"
    exit 1
fi

echo -e "\n${YELLOW}Verifying installation...${NC}"
if command_exists copilot; then
    echo -e "${GREEN}GitHub Copilot CLI installed successfully!${NC}"
    copilot --version 2>/dev/null || echo -e "${YELLOW}Version command not available yet${NC}"

    npm_prefix_warning_sh() {
        local prefix paths uniq_paths
        prefix=$(npm config get prefix 2>/dev/null || true)
        IFS=':' read -r -a paths <<<"$PATH"
        mapfile -t uniq_paths < <(printf "%s\n" "${paths[@]}" | grep -i npm | sort -u)
        if [ "${#uniq_paths[@]}" -gt 1 ]; then
            echo -e "${YELLOW}\nWarning: multiple npm-related paths detected in PATH. This can cause version drift between shells.${NC}"
            printf ' - %s\n' "${uniq_paths[@]}"
            [ -n "$prefix" ] && echo -e "npm prefix: $prefix"
            echo -e "Prefer a single global prefix (Windows: %APPDATA%/npm; Unix: ~/.npm or /usr/local) and remove extra npm/global bin paths."
        fi
    }
    npm_prefix_warning_sh

    echo -e "\n${CYAN}========================================"
    echo -e "Next Steps:"
    echo -e "========================================${NC}"
    echo -e "${WHITE}1. Start the CLI: ${GREEN}copilot${NC}"
    echo -e "${WHITE}2. On first run, use ${GREEN}/login${WHITE} to authenticate with GitHub${NC}"
    echo -e "${WHITE}3. Use ${GREEN}/model${WHITE} to select an AI model${NC}"
    echo -e "${WHITE}4. Run copilot --help for commands${NC}"
else
    echo -e "\n${YELLOW}GitHub Copilot CLI installed but command not found in PATH.${NC}"
    echo -e "${YELLOW}You may need to:${NC}"
    echo -e "${WHITE}1. Restart your terminal or run: source ~/.bashrc"
    echo -e "2. Or add the npm global bin directory to your PATH"
    echo -e "3. Check npm global directory: npm config get prefix${NC}"
fi

echo -e "\n${GREEN}Installation process completed!${NC}"
