#!/bin/bash
# GOAT System Installer - Gemini CLI
# 
# ⚠️ WARNING: Only install on systems you own or have permission to modify.
# This script is for personal development environments only.
#
# Bash script to install Gemini CLI via npm (for Git Bash on Windows or Linux/macOS)
# Run this script in Git Bash, WSL, or any Unix-like terminal

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
WHITE='\033[1;37m'
NC='\033[0m' # No Color

echo -e "${CYAN}Starting Gemini CLI installation process...${NC}"
echo -e "${YELLOW}This will install Gemini CLI using npm package @google/gemini-cli${NC}"

# Function to check if a command exists
command_exists() {
    command -v "$1" >/dev/null 2>&1
}

# Check if Node.js is installed (required for npm)
echo -e "\n${YELLOW}Checking for Node.js installation...${NC}"

if command_exists node; then
    NODE_VERSION=$(node --version)
    echo -e "${GREEN}Node.js is already installed (version $NODE_VERSION)${NC}"

    # Check npm version
    if command_exists npm; then
        NPM_VERSION=$(npm --version)
        echo -e "${GREEN}npm is already installed (version $NPM_VERSION)${NC}"
    else
        echo -e "${RED}npm is not found. Please reinstall Node.js.${NC}"
        exit 1
    fi
else
    echo -e "${RED}Node.js is required for Gemini CLI installation.${NC}"
    echo -e "${RED}Please install Node.js first (or enable it in your Forge config).${NC}"
    exit 1
fi

echo -e "\n${CYAN}========================================"
echo -e "Installing Gemini CLI via npm"
echo -e "========================================${NC}"

# Install Gemini CLI using npm (correct package name)
echo -e "\n${YELLOW}Installing Gemini CLI via npm...${NC}"
echo -e "${WHITE}This will install the latest version of Gemini CLI${NC}"

# Check if npm is available
if command_exists npm; then
    npm install -g @google/gemini-cli --loglevel=error --no-audit --no-fund
else
    echo -e "${RED}Error: npm is not installed.${NC}"
    echo -e "${YELLOW}Please install Node.js and npm first.${NC}"
    exit 1
fi

# Check installation status
if [ $? -eq 0 ]; then
    echo -e "\n${GREEN}Gemini CLI installation completed!${NC}"

    # Verify installation
    if command_exists gemini; then
        echo -e "\n${YELLOW}Verifying installation...${NC}"
        gemini --version
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
        echo -e "\n${GREEN}Gemini CLI installed successfully!${NC}"
        echo -e "\n${CYAN}========================================"
        echo -e "Next Steps:"
        echo -e "========================================${NC}"
        echo -e "${WHITE}1. Start the CLI: gemini"
        echo -e "2. On first run, complete the OAuth authentication with your Google account."
        echo -e "3. For higher limits, set your API key: export GEMINI_API_KEY=\"YOUR_API_KEY\""
        echo -e "4. Use 'gemini doctor' to verify your setup."
        echo -e "5. Use 'gemini --help' to see available commands.${NC}"
    else
        echo -e "\n${YELLOW}Gemini CLI installed but command not found in PATH.${NC}"
        echo -e "${YELLOW}You may need to:${NC}"
        echo -e "${WHITE}1. Restart your terminal or run: source ~/.bashrc"
        echo -e "2. Or add the npm global bin directory to your PATH"
        echo -e "3. Check npm global directory: npm config get prefix${NC}"
    fi
else
    echo -e "\n${RED}Error installing Gemini CLI${NC}"
    echo -e "\n${YELLOW}Troubleshooting steps:${NC}"
    echo -e "${WHITE}1. Make sure you have an internet connection"
    echo -e "2. Try installing without the -g flag in a local project"
    echo -e "3. Check npm configuration: npm config list${NC}"
    echo -e "\n${CYAN}Try running directly:"
    echo -e "${GREEN}npm install -g @google/gemini-cli${NC}"
fi

echo -e "\n${GREEN}========================================"
echo -e "Installation process completed!"
echo -e "========================================${NC}"
echo -e "\n${CYAN}For more information and documentation:"
echo -e "${WHITE}- Gemini CLI docs: https://github.com/google-gemini/gemini-cli${NC}"
