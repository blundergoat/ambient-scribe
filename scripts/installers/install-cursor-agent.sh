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

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
WHITE='\033[1;37m'
NC='\033[0m' # No Color

command_exists() {
    command -v "$1" >/dev/null 2>&1
}

URL="https://cursor.com/install"

os="$(uname -s 2>/dev/null || echo "")"
case "$os" in
  MINGW*|MSYS*|CYGWIN*)
    echo -e "${RED}This installer is not supported in Git Bash/MSYS/Cygwin.${NC}"
    echo -e "${YELLOW}Use WSL (Ubuntu/etc) and run this script inside WSL.${NC}"
    exit 1
    ;;
esac

echo -e "${CYAN}Starting Cursor Agent installation...${NC}"
echo -e "${YELLOW}Installer URL: ${WHITE}${URL}${NC}"
echo -e "${YELLOW}Docs: ${WHITE}https://cursor.com/cli${NC}"

if ! command_exists bash; then
    echo -e "${RED}bash is required to run the installer.${NC}"
    echo -e "${YELLOW}If you're on Windows, run this inside WSL.${NC}"
    exit 1
fi

if ! command_exists curl; then
    echo -e "${RED}curl is required to download the installer.${NC}"
    echo -e "${YELLOW}Install curl and rerun.${NC}"
    exit 1
fi

echo -e "\n${YELLOW}This will download and run a remote script from cursor.com.${NC}"
# Skip confirmation in non-interactive mode (e.g., when run from Forge with </dev/null)
if [[ -t 0 ]]; then
    read -r -p "Continue? (y/n): " confirm_run
    if [[ "$confirm_run" != "y" ]]; then
        echo -e "${YELLOW}Cancelled.${NC}"
        exit 1
    fi
else
    echo -e "${CYAN}Running in non-interactive mode, proceeding automatically...${NC}"
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
if command_exists cursor-agent; then
    CURSOR_AGENT_PATH="$(command -v cursor-agent)"
    echo -e "${GREEN}Cursor Agent installed:${NC} ${WHITE}${CURSOR_AGENT_PATH}${NC}"
    cursor-agent --help 2>/dev/null | head -n 2 || true
    echo -e "\n${CYAN}Next steps:${NC}"
    echo -e "${WHITE}1) Run: cursor-agent${NC}"
    echo -e "${WHITE}2) See: https://cursor.com/cli${NC}"
else
    echo -e "${YELLOW}Installer completed, but 'cursor-agent' was not found on PATH.${NC}"
    echo -e "${YELLOW}Restart your shell and try: cursor-agent${NC}"
    echo -e "${YELLOW}If it still isn't found, check https://cursor.com/cli for PATH setup instructions.${NC}"
fi

echo -e "\n${GREEN}Installation process completed!${NC}"
