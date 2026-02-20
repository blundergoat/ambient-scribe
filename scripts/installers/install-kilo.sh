#!/bin/bash
# GOAT System Installer - Kilo CLI
# Installs the Kilo CLI and configures it for LM Studio (http://127.0.0.1:1234).
# WARNING: Only install on systems you own or have permission to modify.
# Run this script in Git Bash, WSL, or any Unix-like terminal.

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
WHITE='\033[1;37m'
NC='\033[0m' # No Color

# Allow overrides via environment variables
KILO_NPM_PACKAGE=${KILO_NPM_PACKAGE:-@kilocode/cli}
KILO_BASE_URL=${KILO_BASE_URL:-http://127.0.0.1:1234}
KILO_CONFIG_DIR="${HOME}/.kilocode/cli"
KILO_CONFIG_FILE="${KILO_CONFIG_DIR}/config.json"
KILO_TOKEN=${KILO_TOKEN:-local-dev-token}
KILO_PROFILE_ID=${KILO_PROFILE_ID:-default}
KILO_MODEL=${KILO_MODEL:-lmstudio}
KILO_OPENAI_API_KEY=${KILO_OPENAI_API_KEY:-local-dev-api-key}

echo -e "${CYAN}Starting Kilo CLI installation...${NC}"
echo -e "${YELLOW}npm package: ${WHITE}${KILO_NPM_PACKAGE}${NC}"
echo -e "${YELLOW}LM Studio endpoint: ${WHITE}${KILO_BASE_URL}${NC}"

command_exists() {
    command -v "$1" >/dev/null 2>&1
}

# Detect OS (used for Node.js guidance)
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

echo -e "\n${YELLOW}Checking for Node.js installation...${NC}"
if command_exists node; then
    NODE_VERSION=$(node --version)
    echo -e "${GREEN}Node.js is already installed (version ${NODE_VERSION})${NC}"
    if command_exists npm; then
        NPM_VERSION=$(npm --version)
        echo -e "${GREEN}npm is already installed (version ${NPM_VERSION})${NC}"
    else
        echo -e "${RED}npm not found. Please reinstall Node.js.${NC}"
        exit 1
    fi
else
    echo -e "${RED}Node.js is required for Kilo CLI installation.${NC}"
    # In non-interactive mode, auto-install; otherwise prompt
    if [[ -t 0 ]]; then
        read -p "Would you like to install Node.js? (y/n): " install_node
        if [[ "$install_node" != "y" ]]; then
            echo -e "${RED}Node.js is required. Exiting.${NC}"
            exit 1
        fi
    else
        echo -e "${CYAN}Non-interactive mode: auto-installing Node.js...${NC}"
    fi

    if [[ "$OS" == "Windows" ]]; then
        echo -e "${CYAN}Installing Node.js via winget...${NC}"
        winget install -e --id OpenJS.NodeJS.LTS
    elif [[ "$OS" == "Linux" ]]; then
        echo -e "${CYAN}Installing Node.js for Linux...${NC}"
        curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash -
        sudo apt-get install -y nodejs
    elif [[ "$OS" == "macOS" ]]; then
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
echo -e "Installing Kilo CLI via npm"
echo -e "========================================${NC}"

if command_exists npm; then
    npm install -g "${KILO_NPM_PACKAGE}"
    if [ $? -ne 0 ]; then
        echo -e "\n${RED}Error installing ${KILO_NPM_PACKAGE}.${NC}"
        echo -e "${YELLOW}Check the package name or set KILO_NPM_PACKAGE to the correct npm package and rerun.${NC}"
        exit 1
    fi
else
    echo -e "${RED}npm is not installed.${NC}"
    exit 1
fi

echo -e "\n${YELLOW}Configuring Kilo CLI for LM Studio...${NC}"
mkdir -p "${KILO_CONFIG_DIR}"
cat > "${KILO_CONFIG_FILE}" <<EOF
{
  "provider": "lm-studio",
  "providers": [
    {
      "id": "lm-studio",
      "provider": "openai",
      "type": "openai-compatible",
      "baseUrl": "${KILO_BASE_URL}",
      "kilocodeToken": "${KILO_TOKEN}",
      "openAiApiKey": "${KILO_OPENAI_API_KEY}",
      "profiles": [
        {
          "id": "${KILO_PROFILE_ID}",
          "model": "${KILO_MODEL}"
        }
      ]
    }
  ]
}
EOF
chmod 700 "${KILO_CONFIG_DIR}" 2>/dev/null
chmod 600 "${KILO_CONFIG_FILE}" 2>/dev/null
echo -e "${GREEN}Saved configuration to ${KILO_CONFIG_FILE}${NC}"

echo -e "\n${YELLOW}Verifying installation...${NC}"
if command_exists kilo; then
    echo -e "${GREEN}Kilo CLI installed successfully!${NC}"
    kilo --version 2>/dev/null || echo -e "${YELLOW}Version command not available yet${NC}"
else
    echo -e "${YELLOW}Kilo command not found in PATH. You may need to restart your shell or add npm's global bin to PATH.${NC}"
fi

echo -e "\n${CYAN}========================================"
echo -e "Next Steps:"
echo -e "========================================${NC}"
echo -e "${WHITE}1. Start the CLI: kilo${NC}"
echo -e "${WHITE}2. LM Studio endpoint is set to ${KILO_BASE_URL}${NC}"
echo -e "${WHITE}3. Update config via KILO_BASE_URL env var or by editing ${KILO_CONFIG_FILE}${NC}"
echo -e "${WHITE}4. Run 'kilo --help' to see available commands${NC}"

echo -e "\n${GREEN}Installation process completed!${NC}"
