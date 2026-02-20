#!/bin/bash
# GOAT System Installer - Codex CLI
# 
# ⚠️ WARNING: Only install on systems you own or have permission to modify.
# This script is for personal development environments only.
#
# Bash script to install Codex CLI via npm or Homebrew
# Run this script in Git Bash, WSL, or any Unix-like terminal

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
WHITE='\033[1;37m'
NC='\033[0m' # No Color

echo -e "${CYAN}Starting Codex CLI installation process...${NC}"
echo -e "${YELLOW}This will install Codex CLI from OpenAI${NC}"

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

# macOS - prefer Homebrew
if [[ "$OS" == "macOS" ]]; then
    if command_exists brew; then
        echo -e "\n${YELLOW}Homebrew detected. Installing via Homebrew...${NC}"
        echo -e "${CYAN}========================================"
        echo -e "Installing Codex CLI via Homebrew"
        echo -e "========================================${NC}"

        brew install codex

        if [ $? -eq 0 ]; then
            echo -e "\n${GREEN}Codex CLI installed successfully via Homebrew!${NC}"
        else
            echo -e "\n${RED}Homebrew installation failed. Trying npm...${NC}"
            OS="fallback_to_npm"
        fi
    else
        echo -e "\n${YELLOW}Homebrew not found. Will use npm installation.${NC}"
        OS="fallback_to_npm"
    fi
fi

# Non-macOS or fallback to npm
if [[ "$OS" != "macOS" ]] || [[ "$OS" == "fallback_to_npm" ]]; then
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
        echo -e "${RED}Node.js is required for Codex CLI installation.${NC}"
        echo -e "${RED}Please install Node.js first (or enable it in your Forge config).${NC}"
        exit 1
    fi

    echo -e "\n${CYAN}========================================"
    echo -e "Installing Codex CLI via npm"
    echo -e "========================================${NC}"

    # Install Codex CLI using npm



    echo -e "\n${YELLOW}Installing Codex CLI via npm...${NC}"
    echo -e "${WHITE}This will install the latest version of the Codex CLI${NC}"

    if command_exists npm; then
        # Try installing globally
        npm install -g @openai/codex --loglevel=error --no-audit --no-fund

        # If that failed, check for common issues
        if [ $? -ne 0 ]; then
            # If the command doesn't exist after a failed install, it's a real failure.
            if ! command_exists codex; then
                echo -e "\n${RED}Global installation failed.${NC}"
                echo -e "${YELLOW}This is likely a permission issue. Please try one of the following:${NC}"
                echo -e "${WHITE}1. Run the script again with 'sudo'."
                echo -e "${WHITE}2. Manually run: sudo npm install -g @openai/codex"
                echo -e "${WHITE}3. Configure npm to use a user-owned directory (see npm docs for 'prefix').${NC}"
                exit 1
            else
                # If the command *does* exist, the error was likely a failed update due to permissions.
                # We can warn the user but continue, as a version of the tool is present.
                echo -e "\n${YELLOW}npm install reported an error, but 'codex' seems to be installed.${NC}"
                echo -e "${YELLOW}This can happen with permission errors on global package updates. Continuing...${NC}"
            fi
        fi
    else
        echo -e "${RED}Error: npm is not installed.${NC}"
        echo -e "${YELLOW}Please install Node.js and npm first.${NC}"
        exit 1
    fi

    # Check installation status
    if ! command_exists codex; then
        echo -e "\n${RED}Error installing Codex CLI${NC}"
        echo -e "\n${YELLOW}Troubleshooting steps:${NC}"
        echo -e "${WHITE}1. Make sure you have an internet connection"
        echo -e "2. Check npm configuration: npm config list"
        echo -e "3. Try installing manually: npm install -g @openai/codex${NC}"
        exit 1
    fi
fi

# Verify installation
echo -e "\n${YELLOW}Verifying installation...${NC}"
if command_exists codex; then
    echo -e "\n${GREEN}Codex CLI installed successfully!${NC}"
    codex --version 2>/dev/null || echo -e "${YELLOW}Version command not available yet${NC}"

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
    echo -e "${WHITE}1. Start the CLI: ${GREEN}codex${NC}"
    echo -e "${WHITE}2. On first run, you'll be prompted to authenticate"
    echo -e "${WHITE}3. Sign in with your ChatGPT account (recommended)"
    echo -e "${WHITE}4. Alternative: Authenticate with OpenAI API key"
    echo -e "${WHITE}5. Use 'codex --help' to see available commands${NC}"
    echo -e "\n${CYAN}Platform Support:${NC}"
    echo -e "${WHITE}- macOS and Linux: Fully supported"
    echo -e "- Windows: Experimental (use WSL for best experience)${NC}"
else
    echo -e "\n${YELLOW}Codex CLI installed but command not found in PATH.${NC}"
    echo -e "${YELLOW}You may need to:${NC}"
    echo -e "${WHITE}1. Restart your terminal or run: source ~/.bashrc"
    echo -e "2. Or add the npm global bin directory to your PATH"
    echo -e "3. Check npm global directory: npm config get prefix${NC}"
fi

echo -e "\n${GREEN}========================================"
echo -e "Installation process completed!"
echo -e "========================================${NC}"
echo -e "\n${CYAN}For more information and documentation:"
echo -e "${WHITE}- Next step run codex login"
echo -e "- Codex CLI docs: https://developers.openai.com/codex/cli/"
echo -e "- GitHub repository: https://github.com/openai/openai-python${NC}"
