#!/bin/bash
# GOAT System Installer - Kilo CLI
# Installs the Kilo CLI and configures it for LM Studio (http://127.0.0.1:1234).
# WARNING: Only install on systems you own or have permission to modify.
# Run this script in Git Bash, WSL, or any Unix-like terminal.

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/_common.sh"

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
print_platform

require_node_or_install || exit 1

echo -e "\n${CYAN}========================================"
echo -e "Installing Kilo CLI via npm"
echo -e "========================================${NC}"

if ! npm install -g "${KILO_NPM_PACKAGE}"; then
    echo -e "\n${RED}Error installing ${KILO_NPM_PACKAGE}.${NC}"
    echo -e "${YELLOW}Check the package name or set KILO_NPM_PACKAGE to the correct npm package and rerun.${NC}"
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
chmod 700 "${KILO_CONFIG_DIR}" 2>/dev/null || true
chmod 600 "${KILO_CONFIG_FILE}" 2>/dev/null || true
echo -e "${GREEN}Saved configuration to ${KILO_CONFIG_FILE}${NC}"

echo -e "\n${YELLOW}Verifying installation...${NC}"
if verify_native_binary kilo "Kilo CLI"; then
    echo -e "${GREEN}Kilo CLI installed successfully!${NC}"
    kilo --version 2>/dev/null || echo -e "${YELLOW}Version command not available yet${NC}"
fi

echo -e "\n${CYAN}========================================"
echo -e "Next Steps:"
echo -e "========================================${NC}"
echo -e "${WHITE}1. Start the CLI: kilo${NC}"
echo -e "${WHITE}2. LM Studio endpoint is set to ${KILO_BASE_URL}${NC}"
echo -e "${WHITE}3. Update config via KILO_BASE_URL env var or by editing ${KILO_CONFIG_FILE}${NC}"
echo -e "${WHITE}4. Run 'kilo --help' to see available commands${NC}"

echo -e "\n${GREEN}Installation process completed!${NC}"
