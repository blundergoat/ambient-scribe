#!/bin/bash
# =============================================================================
# Initial Setup - Installs all dependencies for local development
# =============================================================================
# Usage: ./scripts/setup-initial.sh
#
# This script sets up the project for local development (outside Docker).
# It installs PHP (Composer) and Python (pip) dependencies, creates the
# .env file if missing, and validates the environment.
#
# Prerequisites:
#   - PHP 8.2+
#   - Composer
#   - Python 3.12+
#   - pip3
# =============================================================================

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"

# ── Colors ──────────────────────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
BLUE='\033[0;34m'
DIM='\033[2m'
BOLD='\033[1m'
RESET='\033[0m'

PASS="${GREEN}✔${RESET}"
FAIL="${RED}✘${RESET}"
ARROW="${BLUE}▸${RESET}"

ERRORS=0

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

warn() {
    local msg="$1"
    echo -e "${YELLOW}⚠${RESET}  ${DIM}${msg}${RESET}"
}

header() {
    echo ""
    echo -e "${BOLD}  Ambient Scribe - Initial Setup${RESET}"
    echo -e "  ${DIM}$(printf '─%.0s' {1..44})${RESET}"
    echo ""
}

# ── Prerequisite checks ────────────────────────────────────────────
header

echo -e "  ${BOLD}Checking prerequisites${RESET}"
echo ""

step "PHP 8.2+"
if command -v php &>/dev/null; then
    php_version=$(php -r 'echo PHP_MAJOR_VERSION . "." . PHP_MINOR_VERSION;')
    php_major=$(php -r 'echo PHP_MAJOR_VERSION;')
    php_minor=$(php -r 'echo PHP_MINOR_VERSION;')
    if [[ "$php_major" -gt 8 ]] || { [[ "$php_major" -eq 8 ]] && [[ "$php_minor" -ge 2 ]]; }; then
        pass "v${php_version}"
    else
        fail "found v${php_version}, need 8.2+"
    fi
else
    fail "not found"
fi

step "Composer"
if command -v composer &>/dev/null; then
    composer_version=$(composer --version 2>/dev/null | grep -oE '[0-9]+\.[0-9]+\.[0-9]+' | head -1)
    pass "v${composer_version}"
else
    fail "not found - install from https://getcomposer.org"
fi

step "Python 3.12+"
if command -v python3 &>/dev/null; then
    py_version=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
    py_major=$(python3 -c 'import sys; print(sys.version_info.major)')
    py_minor=$(python3 -c 'import sys; print(sys.version_info.minor)')
    if [[ "$py_major" -ge 3 ]] && [[ "$py_minor" -ge 12 ]]; then
        pass "v${py_version}"
    else
        fail "found v${py_version}, need 3.12+"
    fi
else
    fail "not found"
fi

step "pip3"
if command -v pip3 &>/dev/null; then
    pip_version=$(pip3 --version 2>/dev/null | grep -oE '[0-9]+\.[0-9]+' | head -1)
    pass "v${pip_version}"
else
    fail "not found"
fi

if [[ $ERRORS -gt 0 ]]; then
    echo ""
    echo -e "  ${RED}${BOLD}Cannot continue - ${ERRORS} prerequisite(s) missing${RESET}"
    echo ""
    exit 1
fi

# ── Environment file ───────────────────────────────────────────────
echo ""
echo -e "  ${BOLD}Setting up environment${RESET}"
echo ""

step "Copy .env.example → .env"
if [[ -f "$REPO_ROOT/.env" ]]; then
    pass "already exists"
else
    cp "$REPO_ROOT/.env.example" "$REPO_ROOT/.env"
    pass "created"
fi

# ── PHP dependencies ───────────────────────────────────────────────
echo ""
echo -e "  ${BOLD}Installing PHP dependencies${RESET}"
echo ""

step "composer install"
cd "$REPO_ROOT"
if composer install 2>&1 | tail -1; then
    pass
else
    fail "composer install failed"
fi

# ── Python dependencies ────────────────────────────────────────────
echo ""
echo -e "  ${BOLD}Installing Python dependencies${RESET}"
echo ""

PYTHON_AGENT_DIR="$REPO_ROOT/strands_agents"

step "Create Python venv"
if [[ -d "$PYTHON_AGENT_DIR/.venv" ]]; then
    pass "already exists"
else
    python3 -m venv "$PYTHON_AGENT_DIR/.venv"
    pass "created"
fi

step "pip install -r requirements.txt"
if "$PYTHON_AGENT_DIR/.venv/bin/pip" install -r "$PYTHON_AGENT_DIR/requirements.txt" 2>&1 | tail -1; then
    pass
else
    fail "pip install failed"
fi

step "pip install yt-dlp"
if "$PYTHON_AGENT_DIR/.venv/bin/pip" install yt-dlp 2>&1 | tail -1; then
    pass
else
    fail "yt-dlp install failed"
fi

step "pip install test deps (pytest)"
if "$PYTHON_AGENT_DIR/.venv/bin/pip" install -r "$REPO_ROOT/tests/python/requirements-dev.txt" 2>&1 | tail -1; then
    pass
else
    fail "test deps install failed"
fi

# ── System packages ──────────────────────────────────────────────
echo ""
echo -e "  ${BOLD}Installing system packages${RESET}"
echo ""

step "ffmpeg"
if command -v ffmpeg &>/dev/null; then
    ffmpeg_version=$(ffmpeg -version 2>/dev/null | head -1 | grep -oE '[0-9]+\.[0-9]+(\.[0-9]+)?' | head -1)
    pass "already installed v${ffmpeg_version}"
else
    if sudo apt-get install -y -qq ffmpeg 2>&1 | tail -1; then
        pass "installed"
    else
        fail "ffmpeg install failed - install manually: sudo apt-get install ffmpeg"
    fi
fi

# ── Summary ────────────────────────────────────────────────────────
echo ""
echo -e "  ${DIM}$(printf '─%.0s' {1..44})${RESET}"

if [[ $ERRORS -eq 0 ]]; then
    echo ""
    echo -e "  ${GREEN}${BOLD}Setup complete!${RESET}"
    echo ""
    echo -e "  ${DIM}Next steps:${RESET}"
    echo -e "    ${ARROW} Run quality checks:     ${BOLD}composer preflight${RESET}"
    echo -e "    ${ARROW} Start full stack:        ${BOLD}docker compose up --build${RESET}"
    echo -e "    ${ARROW} Python venv is at:       ${DIM}strands_agents/.venv${RESET}"
    echo ""
else
    echo ""
    echo -e "  ${RED}${BOLD}Setup finished with ${ERRORS} error(s)${RESET}"
    echo ""
    exit 1
fi
