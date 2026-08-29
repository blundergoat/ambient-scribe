#!/bin/bash
# =============================================================================
# Install Dependencies - Installs PHP and Python packages from lock/requirements
# =============================================================================
# Usage: ./scripts/dependencies-install.sh [--php] [--python]
#
# Installs exact versions from composer.lock and requirements.txt.
# Use this after cloning, switching branches, or when lock files change.
#
# Options:
#   --php       Install PHP dependencies only
#   --python    Install Python dependencies only
#   (no flags)  Install both
# =============================================================================

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PYTHON_AGENT_DIR="$REPO_ROOT/strands_agents"
VENV_DIR="$PYTHON_AGENT_DIR/.venv"

# ── Colors ──────────────────────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
BLUE='\033[0;34m'
DIM='\033[2m'
BOLD='\033[1m'
RESET='\033[0m'

PASS="${GREEN}✔${RESET}"
FAIL="${RED}✘${RESET}"
ARROW="${BLUE}▸${RESET}"

ERRORS=0

# ── Parse flags ─────────────────────────────────────────────────────
DO_PHP=false
DO_PYTHON=false

for arg in "$@"; do
    case "$arg" in
        --php)    DO_PHP=true ;;
        --python) DO_PYTHON=true ;;
        *)
            echo "Unknown option: $arg"
            echo "Usage: $0 [--php] [--python]"
            exit 1
            ;;
    esac
done

# No flags = both
if [[ "$DO_PHP" == false && "$DO_PYTHON" == false ]]; then
    DO_PHP=true
    DO_PYTHON=true
fi

# ── Helpers ─────────────────────────────────────────────────────────
step() {
    printf "  ${ARROW} %-44s" "$1"
}

pass() {
    local detail="${1:-}"
    if [[ -n "$detail" ]]; then
        echo -e "${PASS}  ${DIM}${detail}${RESET}"
    else
        echo -e "${PASS}"
    fi
}

fail() {
    local msg="$1"
    ERRORS=$((ERRORS + 1))
    echo -e "${FAIL}  ${RED}${msg}${RESET}"
}

# ── Header ──────────────────────────────────────────────────────────
echo ""
echo -e "${BOLD}  Ambient Scribe - Install Dependencies${RESET}"
echo -e "  ${DIM}$(printf '─%.0s' {1..44})${RESET}"

# ── PHP ─────────────────────────────────────────────────────────────
if [[ "$DO_PHP" == true ]]; then
    echo ""
    echo -e "  ${BOLD}PHP (Composer)${RESET}"
    echo ""

    if ! command -v composer &>/dev/null; then
        step "composer"
        fail "not found - install from https://getcomposer.org"
    else
        step "composer install"
        # Capture the status with `|| var=$?`. A bare `out=$(cmd)` followed by
        # `rc=$?` aborts under set -e the moment the command fails, which made
        # every failure branch below it unreachable and hid failed installs.
        install_exit=0
        install_output=$(cd "$REPO_ROOT" && composer install 2>&1) || install_exit=$?
        if [[ $install_exit -eq 0 ]]; then
            pkg_count=$(cd "$REPO_ROOT" && composer show 2>/dev/null | wc -l)
            pass "${pkg_count} packages"
        else
            fail "composer install failed"
            echo "$install_output" | tail -5 | while IFS= read -r line; do
                echo -e "    ${DIM}${line}${RESET}"
            done
        fi
    fi
fi

# ── Python ──────────────────────────────────────────────────────────
if [[ "$DO_PYTHON" == true ]]; then
    echo ""
    echo -e "  ${BOLD}Python (pip)${RESET}"
    echo ""

    if ! command -v python3 &>/dev/null; then
        step "python3"
        fail "not found"
    else
        # Create venv if missing
        step "Python venv"
        if [[ -f "$VENV_DIR/bin/python" ]]; then
            pass "exists"
        else
            if python3 -m venv "$VENV_DIR" 2>&1; then
                pass "created"
            else
                fail "failed to create venv"
            fi
        fi

        # Install from requirements.txt
        if [[ -f "$VENV_DIR/bin/pip" ]]; then
            step "pip install -r requirements.txt"
            pip_exit=0
            pip_output=$("$VENV_DIR/bin/pip" install -r "$PYTHON_AGENT_DIR/requirements.txt" 2>&1) || pip_exit=$?
            if [[ $pip_exit -eq 0 ]]; then
                pkg_count=$("$VENV_DIR/bin/pip" list --format=columns 2>/dev/null | tail -n +3 | wc -l)
                pass "${pkg_count} packages"
            else
                fail "pip install failed"
                echo "$pip_output" | tail -5 | while IFS= read -r line; do
                    echo -e "    ${DIM}${line}${RESET}"
                done
            fi
        fi
    fi
fi

# ── Summary ─────────────────────────────────────────────────────────
echo ""
echo -e "  ${DIM}$(printf '─%.0s' {1..44})${RESET}"
echo ""

if [[ $ERRORS -eq 0 ]]; then
    echo -e "  ${GREEN}${BOLD}All dependencies installed${RESET}"
    if [[ "$DO_PYTHON" == true ]]; then
        echo ""
        echo -e "  ${YELLOW}${BOLD}Note:${RESET} ${DIM}the local Python venv excludes torch and NVIDIA NeMo.${RESET}"
        echo -e "     ${DIM}Use docker compose up --build for full transcription support.${RESET}"
    fi
    echo ""
else
    echo -e "  ${RED}${BOLD}${ERRORS} error(s) during install${RESET}"
    echo ""
    exit 1
fi
