#!/bin/bash
# Uninstall Codex CLI for macOS/Linux/Git Bash - Fixed
# Run this script with: bash uninstall-codex.sh

echo "Uninstalling Codex CLI..."

# Function to check if a command exists
command_exists() {
    command -v "$1" >/dev/null 2>&1
}

# First, let's see where codex is coming from
if command_exists codex; then
    echo "Found 'codex' command at: $(which codex)"
else
    echo "'codex' command not found in PATH."
fi

# Detect if installed via Homebrew (macOS)
echo -e "\nChecking for Homebrew installation..."
if command_exists brew && brew list codex &> /dev/null; then
    echo "Found Codex installed via Homebrew"
    brew uninstall codex
    echo "Homebrew uninstall completed"
else
    echo "Codex not found in Homebrew"
fi

# Try npm uninstall for all related packages
echo -e "\nAttempting to uninstall all related npm packages..."
if command_exists npm; then
    echo "Removing 'openai' package..."
    npm uninstall -g openai >/dev/null 2>&1
    echo "Removing '@openai/codex' package..."
    npm uninstall -g @openai/codex >/dev/null 2>&1
    echo "NPM uninstall process completed"
else
    echo "NPM not found, skipping npm uninstall"
fi

# Remove potential config/cache directories
echo -e "\nRemoving configuration and cache directories..."

# Check for .openai directory
if [ -d "$HOME/.openai" ]; then
    # Bypassing interactive prompt for non-interactive execution
    echo "Found OpenAI config directory ($HOME/.openai). Removing it."
    rm -rf "$HOME/.openai"
    echo "Removed: $HOME/.openai"
else
    echo "Directory not found: $HOME/.openai"
fi

# Check for .config/codex directory
if [ -d "$HOME/.config/codex" ]; then
    rm -rf "$HOME/.config/codex"
    echo "Removed: $HOME/.config/codex"
else
    echo "Config directory not found: $HOME/.config/codex"
fi

# Check for .codex directory
if [ -d "$HOME/.codex" ]; then
    rm -rf "$HOME/.codex"
    echo "Removed: $HOME/.codex"
else
    echo "Directory not found: $HOME/.codex"
fi

# Verify uninstall
echo -e "\nVerifying uninstall..."
if command_exists codex; then
    echo "WARNING: Codex command still found at: $(which codex)"
    echo "You may need to manually remove it or restart your terminal"
else
    echo "SUCCESS: Codex CLI has been uninstalled"
fi

echo -e "\nUninstall complete!"
