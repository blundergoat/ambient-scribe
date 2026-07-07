#!/bin/bash
# _common.sh - shared helper library for GOAT installer/uninstaller scripts
#
# Source this file from every script:
#   SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
#   source "${SCRIPT_DIR}/_common.sh"

# ── Colors ────────────────────────────────────────────────────────────────────

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
WHITE='\033[1;37m'
NC='\033[0m'

# ── Platform detection (runs automatically on source) ─────────────────────────

detect_platform() {
    GOAT_IS_WSL="false"
    GOAT_IS_GITBASH="false"

    if [[ "$OSTYPE" == "darwin"* ]]; then
        GOAT_OS="macOS"
    elif [[ "$OSTYPE" == "linux-gnu"* ]]; then
        GOAT_OS="Linux"
        if [[ -f /proc/version ]] && grep -qi microsoft /proc/version 2>/dev/null; then
            GOAT_IS_WSL="true"
        fi
    elif [[ "$OSTYPE" == "msys" ]] || [[ "$OSTYPE" == "mingw"* ]] || [[ "$OSTYPE" == "cygwin"* ]]; then
        GOAT_OS="Windows"
        GOAT_IS_GITBASH="true"
    else
        GOAT_OS="Unknown"
    fi

    export GOAT_OS GOAT_IS_WSL GOAT_IS_GITBASH
}

detect_platform

print_platform() {
    local suffix=""
    if [[ "$GOAT_IS_WSL" == "true" ]]; then
        suffix=" (WSL)"
    elif [[ "$GOAT_IS_GITBASH" == "true" ]]; then
        suffix=" (Git Bash)"
    fi
    echo -e "\n${CYAN}Detected platform: ${WHITE}${GOAT_OS}${suffix}${NC}"
}

# ── Command helpers ───────────────────────────────────────────────────────────

# WSL-aware command check: rejects commands resolving under /mnt/* in WSL.
command_exists() {
    local cmd_path
    cmd_path="$(command -v "$1" 2>/dev/null)" || return 1
    if [[ "$GOAT_IS_WSL" == "true" && "$cmd_path" == /mnt/* ]]; then
        return 1
    fi
    return 0
}

# Strip /mnt/* entries from PATH (WSL only). Call before npm operations.
sanitize_path_for_wsl() {
    if [[ "$GOAT_IS_WSL" != "true" ]]; then
        return 0
    fi
    local new_path="" entry
    IFS=':' read -r -a _path_entries <<< "$PATH"
    for entry in "${_path_entries[@]}"; do
        if [[ "$entry" != /mnt/* ]]; then
            new_path="${new_path:+${new_path}:}${entry}"
        fi
    done
    export PATH="$new_path"
}

# Ensure native npm is available. Calls sanitize_path_for_wsl.
require_npm() {
    sanitize_path_for_wsl
    if ! command_exists npm; then
        echo -e "${RED}npm is required but not found.${NC}"
        echo -e "${YELLOW}Please install Node.js/npm or remove the global package manually.${NC}"
        return 1
    fi
    return 0
}

# Check for Node.js and offer auto-install per platform.
require_node_or_install() {
    sanitize_path_for_wsl
    echo -e "\n${YELLOW}Checking for Node.js installation...${NC}"

    if command_exists node; then
        local node_ver npm_ver
        node_ver=$(node --version)
        echo -e "${GREEN}Node.js is already installed (version ${node_ver})${NC}"
        if command_exists npm; then
            npm_ver=$(npm --version)
            echo -e "${GREEN}npm is already installed (version ${npm_ver})${NC}"
        else
            echo -e "${RED}npm is not found. Please reinstall Node.js.${NC}"
            return 1
        fi
        return 0
    fi

    echo -e "${RED}Node.js is required.${NC}"
    if ! confirm_or_auto "Would you like to install Node.js?"; then
        echo -e "${RED}Node.js is required. Exiting.${NC}"
        return 1
    fi

    case "$GOAT_OS" in
        Windows)
            echo -e "${CYAN}Installing Node.js for Windows via winget...${NC}"
            winget install -e --id OpenJS.NodeJS.LTS
            ;;
        Linux)
            echo -e "${CYAN}Installing Node.js for Linux...${NC}"
            curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash -
            sudo apt-get install -y nodejs
            ;;
        macOS)
            echo -e "${CYAN}Installing Node.js for macOS...${NC}"
            if command_exists brew; then
                brew install node
            else
                echo -e "${YELLOW}Homebrew not found. Please install it first or use the Node.js installer.${NC}"
                return 1
            fi
            ;;
        *)
            echo -e "${RED}Cannot auto-install Node.js on ${GOAT_OS}.${NC}"
            return 1
            ;;
    esac

    export PATH="$PATH:/usr/local/bin"
    hash -r
    if ! command_exists node; then
        echo -e "${RED}Node.js installation failed.${NC}"
        return 1
    fi
    return 0
}

# Warn if multiple npm-related paths exist in PATH.
npm_prefix_warning() {
    local prefix
    prefix=$(npm config get prefix 2>/dev/null || true)
    local paths=()
    IFS=':' read -r -a paths <<< "$PATH"
    local uniq_paths=()
    mapfile -t uniq_paths < <(printf "%s\n" "${paths[@]}" | grep -i npm | sort -u)
    if [[ "${#uniq_paths[@]}" -gt 1 ]]; then
        echo -e "${YELLOW}\nWarning: multiple npm-related paths detected in PATH. This can cause version drift between shells.${NC}"
        printf ' - %s\n' "${uniq_paths[@]}"
        [[ -n "$prefix" ]] && echo -e "npm prefix: $prefix"
        echo -e "Prefer a single global prefix (Windows: %APPDATA%/npm; Unix: ~/.npm or /usr/local) and remove extra npm/global bin paths."
    fi
}

# Post-install check: rejects Windows shims in WSL.
verify_native_binary() {
    local cmd="$1"
    local name="${2:-$1}"
    local cmd_path
    cmd_path="$(command -v "$cmd" 2>/dev/null)" || {
        echo -e "\n${YELLOW}${name} installed but command not found in PATH.${NC}"
        echo -e "${YELLOW}You may need to:${NC}"
        echo -e "${WHITE}1. Restart your terminal or run: source ~/.bashrc"
        echo -e "2. Or add the npm global bin directory to your PATH"
        echo -e "3. Check npm global directory: npm config get prefix${NC}"
        return 1
    }

    if [[ "$GOAT_IS_WSL" == "true" && "$cmd_path" == /mnt/* ]]; then
        echo -e "\n${RED}${name} resolved to a Windows shim: ${cmd_path}${NC}"
        echo -e "${YELLOW}This is a Windows binary and won't work correctly in WSL.${NC}"
        echo -e "${YELLOW}Ensure the native Linux npm prefix is on your PATH and retry.${NC}"
        return 1
    fi
    return 0
}

# Exit with error if running in Git Bash/MSYS.
block_gitbash() {
    local name="${1:-This tool}"
    if [[ "$GOAT_IS_GITBASH" == "true" ]]; then
        echo -e "${RED}${name} is not supported in Git Bash/MSYS/Cygwin.${NC}"
        echo -e "${YELLOW}Use WSL (Ubuntu/etc) and run this script inside WSL.${NC}"
        exit 1
    fi
}

# ── Interactive helpers ───────────────────────────────────────────────────────

# Prompt user in interactive mode, auto-proceed in non-interactive.
# Returns 0 (yes) or 1 (no).
confirm_or_auto() {
    local msg="${1:-Continue?}"
    if [[ -t 0 ]]; then
        read -r -p "${msg} (y/n): " _confirm
        if [[ "$_confirm" != "y" ]]; then
            return 1
        fi
    else
        echo -e "${CYAN}Non-interactive mode: auto-proceeding...${NC}"
    fi
    return 0
}

# Prompt before rm -rf a directory. Skips non-existent dirs.
# In non-interactive mode, removes without prompt.
remove_dir_prompt() {
    local dir="$1"
    if [[ ! -d "$dir" ]]; then
        echo -e "${YELLOW}Not found: ${dir}${NC}"
        return 0
    fi
    if [[ -t 0 ]]; then
        read -r -p "Remove ${dir}? (y/n): " _confirm
        if [[ "$_confirm" != "y" ]]; then
            echo -e "${YELLOW}Skipped: ${dir}${NC}"
            return 0
        fi
    else
        echo -e "${CYAN}Non-interactive mode: removing ${dir}...${NC}"
    fi
    if rm -rf "$dir"; then
        echo -e "${GREEN}Removed: ${dir}${NC}"
    else
        echo -e "${RED}Failed to remove: ${dir}${NC}"
    fi
    return 0
}
