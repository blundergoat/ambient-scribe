#!/bin/bash
# GOAT System Uninstaller - Codex CLI
# Removes Codex CLI (Homebrew and/or npm) and configuration directories.
# Run this script in Git Bash, WSL, or any Unix-like terminal.

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/_common.sh"

echo -e "${CYAN}Starting Codex CLI uninstallation process...${NC}"
print_platform

if command_exists codex; then
    echo -e "${YELLOW}Found 'codex' command at: $(command -v codex)${NC}"
else
    echo -e "${YELLOW}'codex' command not found in PATH.${NC}"
fi

# Detect if installed via Homebrew (macOS)
echo -e "\n${CYAN}========================================"
echo -e "Checking Homebrew installation"
echo -e "========================================${NC}"

if command_exists brew && brew list codex &>/dev/null; then
    echo -e "${YELLOW}Found Codex installed via Homebrew${NC}"
    if brew uninstall codex; then
        echo -e "${GREEN}Homebrew uninstall completed.${NC}"
    else
        echo -e "${YELLOW}Homebrew uninstall reported an issue.${NC}"
    fi
else
    echo -e "${YELLOW}Codex not found in Homebrew.${NC}"
fi

# npm uninstall
echo -e "\n${CYAN}========================================"
echo -e "Uninstalling Codex CLI via npm"
echo -e "========================================${NC}"

if command_exists npm; then
    sanitize_path_for_wsl
    echo -e "${YELLOW}Removing 'openai' package...${NC}"
    npm uninstall -g openai >/dev/null 2>&1 || true
    echo -e "${YELLOW}Removing '@openai/codex' package...${NC}"
    npm uninstall -g @openai/codex >/dev/null 2>&1 || true
    echo -e "${GREEN}npm uninstall process completed.${NC}"
else
    echo -e "${YELLOW}npm not found, skipping npm uninstall.${NC}"
fi

echo -e "\n${CYAN}========================================"
echo -e "Cleaning up Codex CLI data"
echo -e "========================================${NC}"

for dir in "$HOME/.openai" "$HOME/.config/codex" "$HOME/.codex"; do
    remove_dir_prompt "$dir"
done

echo -e "\n${CYAN}========================================"
echo -e "Verifying uninstall"
echo -e "========================================${NC}"

if command_exists codex; then
    echo -e "${YELLOW}codex command still present at: $(command -v codex)${NC}"
    echo -e "${YELLOW}You may need to manually remove it or restart your terminal.${NC}"
else
    echo -e "${GREEN}Codex CLI command not found. Uninstall appears complete.${NC}"
fi

echo -e "\n${GREEN}========================================"
echo -e "Uninstallation process completed!"
echo -e "========================================${NC}"
