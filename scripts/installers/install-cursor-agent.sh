#!/bin/bash
# GOAT System Installer - Cursor Agent (cursor-agent)
#
# WARNING: Only install on systems you own or have permission to modify.
# This script downloads and runs Cursor's installer from https://cursor.com/install.
# Note: this installs the Cursor Agent CLI (`cursor-agent`) for macOS/Linux (and WSL),
# not the Cursor desktop app.
# See https://cursor.com/cli for details (if available in your region).
#
# Run this script in Git Bash, WSL, or any Unix-like terminal.

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/_common.sh"

block_gitbash "Cursor Agent installer"

URL="https://cursor.com/install"

echo -e "${CYAN}Starting Cursor Agent installation...${NC}"
echo -e "${YELLOW}Installer URL: ${WHITE}${URL}${NC}"
echo -e "${YELLOW}Docs: ${WHITE}https://cursor.com/cli${NC}"
print_platform

if ! command_exists curl; then
    echo -e "${RED}curl is required to download the installer.${NC}"
    echo -e "${YELLOW}Install curl and rerun.${NC}"
    exit 1
fi

echo -e "\n${YELLOW}This will download and run a remote script from cursor.com.${NC}"
if ! confirm_or_auto "Continue?"; then
    echo -e "${YELLOW}Cancelled.${NC}"
    exit 1
fi

# Create a secure temp file for the downloaded installer script.
# Avoid predictable fallbacks (e.g. /tmp/...$$) which can be abused via symlink attacks.
tmp_script="$(mktemp -t cursor-install.XXXXXX 2>/dev/null || mktemp "${TMPDIR:-/tmp}/cursor-install.XXXXXX" 2>/dev/null)" || {
    echo -e "${RED}Failed to create temporary file (mktemp).${NC}"
    exit 1
}
if [[ -z "${tmp_script}" ]]; then
    echo -e "${RED}Failed to create temporary file (mktemp returned empty path).${NC}"
    exit 1
fi

cleanup() {
    rm -f "$tmp_script" 2>/dev/null || true
}
trap cleanup EXIT

echo -e "\n${CYAN}Downloading installer...${NC}"
curl -fsSL "$URL" -o "$tmp_script"
chmod +x "$tmp_script" 2>/dev/null || true

echo -e "\n${CYAN}Running installer...${NC}"
bash "$tmp_script"

echo -e "\n${CYAN}Verifying installation...${NC}"
if verify_native_binary cursor-agent "Cursor Agent"; then
    CURSOR_AGENT_PATH="$(command -v cursor-agent)"
    echo -e "${GREEN}Cursor Agent installed:${NC} ${WHITE}${CURSOR_AGENT_PATH}${NC}"
    cursor-agent --help 2>/dev/null | head -n 2 || true
    echo -e "\n${CYAN}Next steps:${NC}"
    echo -e "${WHITE}1) Run: cursor-agent${NC}"
    echo -e "${WHITE}2) See: https://cursor.com/cli${NC}"
fi

echo -e "\n${GREEN}Installation process completed!${NC}"
