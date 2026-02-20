#!/bin/bash
# GOAT System Installer - Grok CLI
# 
# ⚠️ WARNING: Only install on systems you own or have permission to modify.
# This script is for personal development environments only.
#
# Bash script to install Grok CLI via npm
# Run this script in Git Bash, WSL, or any Unix-like terminal

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
WHITE='\033[1;37m'
NC='\033[0m' # No Color

echo -e "${CYAN}Starting Grok CLI installation process...${NC}"
echo -e "${YELLOW}This will install Grok CLI from Vibe Kit${NC}"

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

    # Check npm version
    if command_exists npm; then
        NPM_VERSION=$(npm --version)
        echo -e "${GREEN}npm is already installed (version $NPM_VERSION)${NC}"
    else
        echo -e "${RED}npm is not found. Please reinstall Node.js.${NC}"
        exit 1
    fi
else
    echo -e "${RED}Node.js is required for Grok CLI installation.${NC}"
    # In non-interactive mode, auto-install; otherwise prompt
    if [[ -t 0 ]]; then
        read -p "Would you like to install Node.js? (y/n): " install_node
        if [[ "$install_node" != "y" ]]; then
            echo -e "${RED}Node.js is required for Grok CLI. Exiting...${NC}"
            exit 1
        fi
    else
        echo -e "${CYAN}Non-interactive mode: auto-installing Node.js...${NC}"
    fi

    # Detect OS and install Node.js
    if [[ "$OSTYPE" == "msys" ]] || [[ "$OSTYPE" == "mingw"* ]]; then
        # Git Bash on Windows
        echo -e "${CYAN}Installing Node.js for Windows via winget...${NC}"
        winget install -e --id OpenJS.NodeJS.LTS
    elif [[ "$OSTYPE" == "linux-gnu"* ]]; then
        # Linux
        echo -e "${CYAN}Installing Node.js for Linux...${NC}"
        curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash -
        sudo apt-get install -y nodejs
    elif [[ "$OSTYPE" == "darwin"* ]]; then
        # macOS
        echo -e "${CYAN}Installing Node.js for macOS...${NC}"
        if command_exists brew;
            then
            brew install node
        else
            echo -e "${YELLOW}Homebrew not found. Please install it first or use the Node.js installer.${NC}"
            exit 1
        fi
    fi

    # Refresh PATH and check Node.js installation again
    export PATH=$PATH:/usr/local/bin
    hash -r
    if ! command_exists node; then
        echo -e "${RED}Node.js installation failed. Exiting.${NC}"
        exit 1
    fi
fi

echo -e "\n${CYAN}========================================"
echo -e "Installing Grok CLI via npm"
echo -e "========================================${NC}"

# Install Grok CLI using npm
echo -e "\n${YELLOW}Installing Grok CLI via npm...${NC}"
echo -e "${WHITE}This will install the latest version of Grok CLI${NC}"

if command_exists npm; then
    npm install -g @vibe-kit/grok-cli 2>/dev/null

    if [ $? -ne 0 ]; then
        echo -e "\n${YELLOW}Permission denied for global installation.${NC}"
        echo -e "${YELLOW}Use the default user-level npm prefix and rerun:${NC}"
        if [[ "$OSTYPE" == "msys" ]] || [[ "$OSTYPE" == "mingw"* ]]; then
            echo -e "${WHITE}npm config set prefix \"$APPDATA/npm\"${NC}"
        else
            echo -e "${WHITE}npm config set prefix \"$HOME/.npm\"${NC}"
        fi
        echo -e "${WHITE}npm install -g @vibe-kit/grok-cli${NC}"
        echo -e "${YELLOW}Ensure that prefix/bin is on your PATH.${NC}"
        exit 1
    fi
else
    echo -e "${RED}Error: npm is not installed.${NC}"
    echo -e "${YELLOW}Please install Node.js and npm first.${NC}"
    exit 1
fi

# Check installation status
if [ $? -ne 0 ]; then
    echo -e "\n${RED}Error installing Grok CLI${NC}"
    echo -e "\n${YELLOW}Troubleshooting steps:${NC}"
    echo -e "${WHITE}1. Make sure you have an internet connection"
    echo -e "2. Check if the package exists: npm search @vibe-kit/grok-cli"
    echo -e "3. Check npm configuration: npm config list"
    echo -e "4. Try manual configuration:"
    echo -e "   mkdir -p ~/.npm-global"
    echo -e "   npm config set prefix ~/.npm-global"
    echo -e "   export PATH=~/.npm-global/bin:$PATH"
    echo -e "   npm install -g @vibe-kit/grok-cli${NC}"
    echo -e "\n${CYAN}Or install locally in a project directory:"
    echo -e "${GREEN}npm install @vibe-kit/grok-cli${NC}"
    exit 1
fi

# Verify installation
echo -e "\n${YELLOW}Verifying installation...${NC}"
if command_exists grok; then
    echo -e "\n${GREEN}Grok CLI installed successfully!${NC}"
    grok --version 2>/dev/null || echo -e "${YELLOW}Version command not available yet${NC}"
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

    # API Key Setup - skip in non-interactive mode
    echo -e "\n${CYAN}========================================"
    echo -e "Setting up Grok API Key"
    echo -e "========================================${NC}"

    if [[ -t 0 ]]; then
        read -sp "Please enter your Grok API key: " grok_api_key
        echo

        if [ -z "$grok_api_key" ]; then
            echo -e "${YELLOW}No API key provided. You can set it up later by creating the file ~/.grok/user-settings.json${NC}"
        else
            mkdir -p ~/.grok
            echo "{\"apiKey\": \"$grok_api_key\"}" > ~/.grok/user-settings.json
            if [ $? -eq 0 ]; then
                echo -e "${GREEN}Grok API key saved successfully to ~/.grok/user-settings.json${NC}"
            else
                echo -e "${RED}Failed to save Grok API key.${NC}"
            fi
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
else
    echo -e "\n${YELLOW}Grok CLI installed but command not found in PATH.${NC}"
    echo -e "${YELLOW}You may need to:${NC}"
    echo -e "${WHITE}1. Restart your terminal or run: source ~/.bashrc"
    echo -e "2. Or add the npm global bin directory to your PATH"
    echo -e "3. Check npm global directory: npm config get prefix${NC}"
fi

echo -e "\n${GREEN}========================================"
echo -e "Installation process completed!"
echo -e "========================================${NC}"
echo -e "\n${CYAN}For more information and documentation, visit the Vibe Kit repository.${NC}"
