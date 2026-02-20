#!/bin/bash
# GOAT System Uninstaller - GitHub Copilot CLI
# Run this script in Git Bash, WSL, or any Unix-like terminal

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
WHITE='\033[1;37m'
NC='\033[0m' # No Color

echo -e "${CYAN}Starting GitHub Copilot CLI uninstallation process...${NC}"

# Function to check if a command exists
command_exists() {
    command -v "$1" >/dev/null 2>&1
}

# Check if npm is installed
if ! command_exists npm; then
    echo -e "${RED}npm is required to uninstall the GitHub Copilot CLI package.${NC}"
    echo -e "${YELLOW}Please install Node.js/npm or remove the global package manually.${NC}"
    exit 1
fi

echo -e "\n${CYAN}========================================"
echo -e "Uninstalling GitHub Copilot CLI via npm"
echo -e "========================================${NC}"

npm uninstall -g @github/copilot

if [ $? -eq 0 ]; then
    echo -e "\n${GREEN}GitHub Copilot CLI uninstalled via npm.${NC}"
else
    echo -e "\n${YELLOW}npm uninstall reported an issue. The package may not have been installed globally.${NC}"
    echo -e "${YELLOW}You can check with: npm list -g @github/copilot${NC}"
fi

echo -e "\n${CYAN}========================================"
echo -e "Cleaning up GitHub Copilot CLI data"
echo -e "========================================${NC}"

POSSIBLE_DIRS=(
    "$HOME/.copilot"
    "$HOME/.config/copilot"
    "$HOME/.config/github-copilot"
    "$HOME/.cache/copilot"
)

for dir in "${POSSIBLE_DIRS[@]}"; do
    if [ -d "$dir" ]; then
        read -p "Remove $dir ? (y/n): " confirm_remove
        if [[ "$confirm_remove" == "y" ]]; then
            rm -rf "$dir"
            if [ $? -eq 0 ]; then
                echo -e "${GREEN}Removed: $dir${NC}"
            else
                echo -e "${RED}Failed to remove: $dir${NC}"
            fi
        else
            echo -e "${YELLOW}Skipped: $dir${NC}"
        fi
    else
        echo -e "${YELLOW}Not found: $dir${NC}"
    fi
done

echo -e "\n${CYAN}========================================"
echo -e "Verifying uninstall"
echo -e "========================================${NC}"

if command_exists copilot; then
    COPILOT_PATH=$(command -v copilot)
    echo -e "${YELLOW}copilot command still present at: ${COPILOT_PATH}${NC}"
    echo -e "${YELLOW}You may need to remove it from your PATH or restart your shell.${NC}"
else
    echo -e "${GREEN}GitHub Copilot CLI command not found. Uninstall appears complete.${NC}"
fi

echo -e "\n${GREEN}========================================"
echo -e "Uninstallation process completed!"
echo -e "========================================${NC}"
