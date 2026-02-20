#!/bin/bash
# Bash script to uninstall Grok CLI via npm
# Run this script in Git Bash, WSL, or any Unix-like terminal

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
WHITE='\033[1;37m'
NC='\033[0m' # No Color

echo -e "${CYAN}Starting Grok CLI uninstallation process...${NC}"

# Function to check if a command exists
command_exists() {
    command -v "$1" >/dev/null 2>&1
}

# Check if npm is installed
if ! command_exists npm; then
    echo -e "${RED}npm is not found. Uninstallation requires npm.${NC}"
    exit 1
fi

echo -e "\n${CYAN}========================================"
echo -e "Uninstalling Grok CLI via npm"
echo -e "========================================${NC}"

npm uninstall -g @vibe-kit/grok-cli

if [ $? -eq 0 ]; then
    echo -e "\n${GREEN}Grok CLI uninstalled successfully!${NC}"
else
    echo -e "\n${RED}Error uninstalling Grok CLI.${NC}"
    echo -e "${YELLOW}It might not be installed globally. Try checking with 'npm list -g @vibe-kit/grok-cli'${NC}"
    # Do not exit here, proceed with cleanup even if npm uninstall failed
fi

echo -e "\n${CYAN}========================================"
echo -e "Cleaning up Grok CLI user settings"
echo -e "========================================${NC}"

if [ -d ~/.grok ]; then
    read -p "Remove ~/.grok directory and all its contents? (y/n): " confirm_remove
    if [[ "$confirm_remove" == "y" ]]; then
        rm -rf ~/.grok
        if [ $? -eq 0 ]; then
            echo -e "${GREEN}Removed: ~/.grok${NC}"
        else
            echo -e "${RED}Failed to remove: ~/.grok${NC}"
        fi
    else
        echo -e "${YELLOW}Skipped: ~/.grok${NC}"
    fi
else
    echo -e "${YELLOW}Not found: ~/.grok${NC}"
fi

echo -e "\n${GREEN}========================================"
echo -e "Uninstallation process completed!"
echo -e "========================================${NC}"
