#!/bin/bash
# GOAT System Uninstaller - Cursor Agent (cursor-agent)
#
# WARNING: Only uninstall on systems you own or have permission to modify.
# This script removes the Cursor Agent CLI (`cursor-agent`) and its install directory.
# It does NOT uninstall the Cursor desktop app.
# See https://cursor.com/cli for details.
#
# Run this script in Git Bash, WSL, or any Unix-like terminal.

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/_common.sh"

block_gitbash "Cursor Agent uninstaller"

echo -e "${CYAN}Starting Cursor Agent uninstallation...${NC}"
print_platform

# Helper to prompt+remove a single file (not a dir).
remove_file_prompt() {
    local p="$1"
    if [[ ! -e "$p" ]]; then
        return 0
    fi
    if [[ -t 0 ]]; then
        read -r -p "Remove ${p}? (y/n): " _confirm
        if [[ "$_confirm" != "y" ]]; then
            echo -e "${YELLOW}Skipped: ${p}${NC}"
            return 0
        fi
    else
        echo -e "${CYAN}Non-interactive mode: removing ${p}...${NC}"
    fi
    if rm -f "$p" 2>/dev/null; then
        echo -e "${GREEN}Removed: ${p}${NC}"
    else
        echo -e "${YELLOW}Failed to remove: ${p}${NC}"
        echo -e "${YELLOW}If this is a system location, rerun with elevated permissions (e.g. sudo) or remove manually.${NC}"
    fi
    return 0
}

agent_cmd_path=""
if command_exists cursor-agent; then
    agent_cmd_path="$(command -v cursor-agent)"
    echo -e "${YELLOW}Found 'cursor-agent' on PATH:${NC} ${WHITE}${agent_cmd_path}${NC}"
else
    echo -e "${YELLOW}'cursor-agent' command not found on PATH.${NC}"
fi

echo -e "\n${CYAN}========================================"
echo -e "Removing Cursor Agent CLI"
echo -e "========================================${NC}"

# Prefer removing exactly what PATH resolves to (this is typically a symlink/shim).
if [[ -n "${agent_cmd_path}" ]]; then
    remove_file_prompt "${agent_cmd_path}"
fi

# Also offer removal from common user/system install locations.
common_candidates=(
    "${HOME}/.local/bin/cursor-agent"
    "${HOME}/bin/cursor-agent"
    "/usr/local/bin/cursor-agent"
    "/opt/homebrew/bin/cursor-agent"
)

for candidate in "${common_candidates[@]}"; do
    if [[ -n "${agent_cmd_path}" && "${candidate}" == "${agent_cmd_path}" ]]; then
        continue
    fi
    if [[ -e "${candidate}" ]]; then
        remove_file_prompt "${candidate}"
    fi
done

install_root="${HOME}/.local/share/cursor-agent"
if [[ -d "${install_root}" ]]; then
    echo -e "\n${CYAN}========================================"
    echo -e "Removing Cursor Agent files"
    echo -e "========================================${NC}"
    remove_dir_prompt "${install_root}"
fi

echo -e "\n${CYAN}========================================"
echo -e "Verifying uninstall"
echo -e "========================================${NC}"
if command_exists cursor-agent; then
    echo -e "${YELLOW}cursor-agent command still present at:${NC} ${WHITE}$(command -v cursor-agent)${NC}"
    echo -e "${YELLOW}There may be another Cursor Agent shim on your PATH, or you may need to restart your shell.${NC}"
else
    echo -e "${GREEN}Cursor Agent command not found. Uninstall appears complete.${NC}"
fi

echo -e "\n${YELLOW}Note:${NC} This script does not remove the Cursor desktop app."
echo -e "${YELLOW}If you installed the Cursor desktop app, uninstall it via your OS package manager or system settings.${NC}"

echo -e "\n${GREEN}Uninstallation process completed!${NC}"
