#!/bin/bash
# GOAT System Installer - Claude CLI
#
# WARNING: Only install on systems you own or have permission to modify.
# This script is for personal development environments only.
#
# Bash script to install Claude CLI via npm
# Run this script in Git Bash, WSL, or any Unix-like terminal

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
WHITE='\033[1;37m'
NC='\033[0m' # No Color

CLAUDE_NPM_PACKAGE=${CLAUDE_NPM_PACKAGE:-@anthropic-ai/claude-code}

echo -e "${CYAN}Starting Claude CLI installation process...${NC}"
echo -e "${YELLOW}This will install Claude CLI using npm package ${CLAUDE_NPM_PACKAGE}${NC}"

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
    echo -e "${RED}Node.js is required for Claude CLI installation.${NC}"
    if [[ -t 0 ]]; then
        read -p "Would you like to install Node.js? (y/n): " install_node
        if [[ "$install_node" != "y" ]]; then
            echo -e "${RED}Node.js is required for Claude CLI. Exiting.${NC}"
            exit 1
        fi
    else
        echo -e "${CYAN}Non-interactive mode: auto-installing Node.js...${NC}"
    fi

    if [[ "$OSTYPE" == "msys" ]] || [[ "$OSTYPE" == "mingw"* ]]; then
        echo -e "${CYAN}Installing Node.js for Windows via winget...${NC}"
        winget install -e --id OpenJS.NodeJS.LTS
    elif [[ "$OSTYPE" == "linux-gnu"* ]]; then
        echo -e "${CYAN}Installing Node.js for Linux...${NC}"
        curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash -
        sudo apt-get install -y nodejs
    elif [[ "$OSTYPE" == "darwin"* ]]; then
        echo -e "${CYAN}Installing Node.js for macOS...${NC}"
        if command_exists brew; then
            brew install node
        else
            echo -e "${YELLOW}Homebrew not found. Please install it first or use the Node.js installer.${NC}"
            exit 1
        fi
    fi

    export PATH=$PATH:/usr/local/bin
    hash -r
    if ! command_exists node; then
        echo -e "${RED}Node.js installation failed. Exiting.${NC}"
        exit 1
    fi
fi

echo -e "\n${CYAN}========================================"
echo -e "Installing Claude CLI via npm"
echo -e "========================================${NC}"

if command_exists npm; then
    npm install -g "${CLAUDE_NPM_PACKAGE}" --loglevel=error --no-audit --no-fund
else
    echo -e "${RED}Error: npm is not installed.${NC}"
    echo -e "${YELLOW}Please install Node.js and npm first.${NC}"
    exit 1
fi

if [ $? -ne 0 ]; then
    echo -e "\n${RED}Error installing Claude CLI${NC}"
    echo -e "\n${YELLOW}Troubleshooting steps:${NC}"
    echo -e "${WHITE}1. Check internet connection"
    echo -e "2. npm config list"
    echo -e "3. Try: npm install -g ${CLAUDE_NPM_PACKAGE}${NC}"
    exit 1
fi

echo -e "\n${YELLOW}Verifying installation...${NC}"
if command_exists claude; then
    echo -e "${GREEN}Claude CLI installed successfully!${NC}"
    claude --version 2>/dev/null || echo -e "${YELLOW}Version command not available yet${NC}"
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
    echo -e "${WHITE}1. Start the CLI: claude"
    echo -e "2. Set ANTHROPIC_API_KEY for authentication"
    echo -e "3. Run claude --help for commands${NC}"
else
    echo -e "\n${YELLOW}Claude CLI installed but command not found in PATH.${NC}"
    echo -e "${YELLOW}You may need to:${NC}"
    echo -e "${WHITE}1. Restart your terminal or run: source ~/.bashrc"
    echo -e "2. Or add the npm global bin directory to your PATH"
    echo -e "3. Check npm global directory: npm config get prefix${NC}"
fi

echo -e "\n${GREEN}Installation process completed!${NC}"