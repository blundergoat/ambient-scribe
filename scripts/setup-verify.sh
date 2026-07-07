#!/bin/bash
# =============================================================================
# Verify Setup - Checks that all dependencies and config are correctly installed
# =============================================================================
# Usage: ./scripts/setup-verify.sh
#
# Run this after setup-initial.sh (or any time) to confirm the development
# environment is complete and ready. Checks system tools, installed packages,
# config files, and PHP/Python dev tooling.
# =============================================================================

set -uo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PYTHON_AGENT_DIR="$REPO_ROOT/strands_agents"
VENV_DIR="$PYTHON_AGENT_DIR/.venv"

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
WARN="${YELLOW}○${RESET}"
ARROW="${BLUE}▸${RESET}"

TOTAL=0
PASSED=0
FAILED=0
WARNED=0
FAILURES=()

# ── Helpers ─────────────────────────────────────────────────────────
step() {
    TOTAL=$((TOTAL + 1))
    printf "  ${ARROW} %-44s" "$1"
}

pass() {
    local detail="${1:-}"
    PASSED=$((PASSED + 1))
    if [[ -n "$detail" ]]; then
        echo -e "${PASS}  ${DIM}${detail}${RESET}"
    else
        echo -e "${PASS}"
    fi
}

fail() {
    local msg="$1"
    FAILED=$((FAILED + 1))
    FAILURES+=("$msg")
    echo -e "${FAIL}  ${RED}${msg}${RESET}"
}

warn() {
    local msg="$1"
    WARNED=$((WARNED + 1))
    echo -e "${WARN}  ${YELLOW}${msg}${RESET}"
}

section() {
    echo ""
    echo -e "  ${BOLD}$1${RESET}"
    echo ""
}

header() {
    echo ""
    echo -e "${BOLD}  Ambient Scribe - Setup Verification${RESET}"
    echo -e "  ${DIM}$(printf '─%.0s' {1..44})${RESET}"
}

# ═════════════════════════════════════════════════════════════════════
header

# ── System Tools ────────────────────────────────────────────────────
section "System tools"

step "PHP 8.3-8.4"
if command -v php &>/dev/null; then
    php_version=$(php -r 'echo PHP_MAJOR_VERSION . "." . PHP_MINOR_VERSION . "." . PHP_RELEASE_VERSION;')
    php_major=$(php -r 'echo PHP_MAJOR_VERSION;')
    php_minor=$(php -r 'echo PHP_MINOR_VERSION;')
    # composer.json pins ">=8.3 <8.5"; this gate must agree or setup-verify
    # passes on hosts where composer install then refuses to run.
    if [[ "$php_major" -eq 8 ]] && [[ "$php_minor" -ge 3 ]] && [[ "$php_minor" -lt 5 ]]; then
        pass "v${php_version}"
    else
        fail "v${php_version} - composer.json requires >=8.3 <8.5"
    fi
else
    fail "not found"
fi

step "Composer"
if command -v composer &>/dev/null; then
    composer_version=$(composer --version 2>/dev/null | grep -oE '[0-9]+\.[0-9]+\.[0-9]+' | head -1)
    pass "v${composer_version}"
else
    fail "not found"
fi

step "Python 3.12+"
if command -v python3 &>/dev/null; then
    py_version=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}")')
    py_major=$(python3 -c 'import sys; print(sys.version_info.major)')
    py_minor=$(python3 -c 'import sys; print(sys.version_info.minor)')
    if [[ "$py_major" -ge 3 ]] && [[ "$py_minor" -ge 12 ]]; then
        pass "v${py_version}"
    else
        fail "v${py_version} - need 3.12+"
    fi
else
    fail "not found"
fi

step "ffmpeg"
if command -v ffmpeg &>/dev/null; then
    ffmpeg_version=$(ffmpeg -version 2>/dev/null | head -1 | grep -oE '[0-9]+\.[0-9]+(\.[0-9]+)?' | head -1)
    pass "v${ffmpeg_version}"
else
    fail "not found - run: sudo apt-get install ffmpeg"
fi

step "Docker"
if command -v docker &>/dev/null; then
    docker_version=$(docker --version 2>/dev/null | grep -oE '[0-9]+\.[0-9]+\.[0-9]+' | head -1)
    pass "v${docker_version}"
else
    warn "not found - needed for docker compose up"
fi

step "Docker Compose"
if docker compose version &>/dev/null 2>&1; then
    compose_version=$(docker compose version 2>/dev/null | grep -oE '[0-9]+\.[0-9]+\.[0-9]+' | head -1)
    pass "v${compose_version}"
else
    warn "not found - needed for docker compose up"
fi

# ── Project Files ───────────────────────────────────────────────────
section "Project files"

step ".env"
if [[ -f "$REPO_ROOT/.env" ]]; then
    pass
else
    fail "missing - run: cp .env.example .env"
fi

step ".env has APP_SECRET"
if [[ -f "$REPO_ROOT/.env" ]] && grep -q '^APP_SECRET=' "$REPO_ROOT/.env"; then
    pass
else
    fail "APP_SECRET not set in .env"
fi

step ".env has AGENT_ENDPOINT"
if [[ -f "$REPO_ROOT/.env" ]] && grep -q '^AGENT_ENDPOINT=' "$REPO_ROOT/.env"; then
    pass
else
    fail "AGENT_ENDPOINT not set in .env"
fi

step "docker-compose.yml"
if [[ -f "$REPO_ROOT/docker-compose.yml" ]]; then
    pass
else
    fail "missing"
fi

# ── PHP Dependencies ───────────────────────────────────────────────
section "PHP dependencies (Composer)"

step "vendor/ directory"
if [[ -d "$REPO_ROOT/vendor" ]]; then
    pass
else
    fail "missing - run: composer install"
fi

step "vendor/autoload.php"
if [[ -f "$REPO_ROOT/vendor/autoload.php" ]]; then
    pass
else
    fail "missing - run: composer install"
fi

step "composer.json valid"
if cd "$REPO_ROOT" && composer validate 2>&1 | grep -q "is valid"; then
    pass
else
    fail "composer.json invalid - run: composer validate"
fi

step "phpunit"
if [[ -x "$REPO_ROOT/vendor/bin/phpunit" ]]; then
    phpunit_version=$("$REPO_ROOT/vendor/bin/phpunit" --version 2>/dev/null | grep -oE '[0-9]+\.[0-9]+' | head -1)
    pass "v${phpunit_version}"
else
    fail "not installed"
fi

step "phpstan"
if [[ -x "$REPO_ROOT/vendor/bin/phpstan" ]]; then
    phpstan_version=$("$REPO_ROOT/vendor/bin/phpstan" --version 2>/dev/null | grep -oE '[0-9]+\.[0-9]+' | head -1)
    pass "v${phpstan_version}"
else
    fail "not installed"
fi

step "php-cs-fixer"
if [[ -x "$REPO_ROOT/vendor/bin/php-cs-fixer" ]]; then
    csfixer_version=$("$REPO_ROOT/vendor/bin/php-cs-fixer" --version 2>/dev/null | grep -oE '[0-9]+\.[0-9]+' | head -1)
    pass "v${csfixer_version}"
else
    fail "not installed"
fi

step "phpmd"
if [[ -x "$REPO_ROOT/vendor/bin/phpmd" ]]; then
    pass
else
    fail "not installed"
fi

# ── Python Dependencies ────────────────────────────────────────────
section "Python dependencies (venv)"

step "Python venv"
if [[ -f "$VENV_DIR/bin/python" ]]; then
    venv_py=$("$VENV_DIR/bin/python" --version 2>/dev/null | grep -oE '[0-9]+\.[0-9]+\.[0-9]+')
    pass "v${venv_py}"
else
    fail "missing - run: ./scripts/setup-initial.sh"
fi

step "strands-agents"
if [[ -f "$VENV_DIR/bin/python" ]] && "$VENV_DIR/bin/python" -c "import strands" 2>/dev/null; then
    strands_ver=$("$VENV_DIR/bin/python" -c "import importlib.metadata; print(importlib.metadata.version('strands-agents'))" 2>/dev/null)
    pass "v${strands_ver}"
else
    fail "not importable"
fi

step "fastapi"
if [[ -f "$VENV_DIR/bin/python" ]] && "$VENV_DIR/bin/python" -c "import fastapi" 2>/dev/null; then
    fastapi_ver=$("$VENV_DIR/bin/python" -c "import importlib.metadata; print(importlib.metadata.version('fastapi'))" 2>/dev/null)
    pass "v${fastapi_ver}"
else
    fail "not importable"
fi

step "uvicorn"
if [[ -f "$VENV_DIR/bin/uvicorn" ]]; then
    uvicorn_ver=$("$VENV_DIR/bin/python" -c "import importlib.metadata; print(importlib.metadata.version('uvicorn'))" 2>/dev/null)
    pass "v${uvicorn_ver}"
else
    fail "not installed"
fi

step "yt-dlp"
if [[ -f "$VENV_DIR/bin/yt-dlp" ]]; then
    ytdlp_ver=$("$VENV_DIR/bin/yt-dlp" --version 2>/dev/null)
    pass "v${ytdlp_ver}"
else
    fail "not installed"
fi

step "pydantic"
if [[ -f "$VENV_DIR/bin/python" ]] && "$VENV_DIR/bin/python" -c "import pydantic" 2>/dev/null; then
    pydantic_ver=$("$VENV_DIR/bin/python" -c "import importlib.metadata; print(importlib.metadata.version('pydantic'))" 2>/dev/null)
    pass "v${pydantic_ver}"
else
    fail "not importable"
fi

step "pytest"
if [[ -f "$VENV_DIR/bin/pytest" ]]; then
    pytest_ver=$("$VENV_DIR/bin/python" -c "import importlib.metadata; print(importlib.metadata.version('pytest'))" 2>/dev/null)
    pass "v${pytest_ver}"
else
    fail "not installed - run: pip install -r tests/python/requirements-dev.txt"
fi

step "numpy"
if [[ -f "$VENV_DIR/bin/python" ]] && "$VENV_DIR/bin/python" -c "import numpy" 2>/dev/null; then
    numpy_ver=$("$VENV_DIR/bin/python" -c "import importlib.metadata; print(importlib.metadata.version('numpy'))" 2>/dev/null)
    pass "v${numpy_ver}"
else
    fail "not importable"
fi

step "soundfile"
if [[ -f "$VENV_DIR/bin/python" ]] && "$VENV_DIR/bin/python" -c "import soundfile" 2>/dev/null; then
    sf_ver=$("$VENV_DIR/bin/python" -c "import importlib.metadata; print(importlib.metadata.version('soundfile'))" 2>/dev/null)
    pass "v${sf_ver}"
else
    fail "not importable"
fi

# ── Python Lint ────────────────────────────────────────────────────
section "Python agent lint"

py_errors=0
py_files=0
if [[ ! -x "$VENV_DIR/bin/ruff" ]]; then
    step "ruff availability"
    fail "ruff not installed"
    py_errors=$((py_errors + 1))
else
    for f in "$PYTHON_AGENT_DIR"/*.py "$PYTHON_AGENT_DIR"/api/*.py; do
        if [[ -f "$f" ]]; then
            py_files=$((py_files + 1))
            rel_path="${f#$REPO_ROOT/}"
            step "$rel_path"
            if "$VENV_DIR/bin/ruff" check "$f" >/dev/null 2>&1; then
                pass
            else
                fail "lint error"
                py_errors=$((py_errors + 1))
            fi
        fi
    done
fi

# ── Quick Smoke Tests ──────────────────────────────────────────────
section "Smoke tests"

step "PHPUnit runs"
if [[ -x "$REPO_ROOT/vendor/bin/phpunit" ]]; then
    test_output=$("$REPO_ROOT/vendor/bin/phpunit" --configuration "$REPO_ROOT/phpunit.xml.dist" 2>&1)
    test_exit=$?
    if [[ $test_exit -eq 0 ]]; then
        test_summary=$(echo "$test_output" | grep -oE '[0-9]+ tests, [0-9]+ assertions' || echo "ok")
        pass "$test_summary"
    else
        fail "tests failing"
    fi
else
    fail "phpunit not available"
fi

step "PHPStan clean"
if [[ -x "$REPO_ROOT/vendor/bin/phpstan" ]]; then
    stan_output=$("$REPO_ROOT/vendor/bin/phpstan" analyse --no-progress --configuration "$REPO_ROOT/phpstan.neon" 2>&1)
    stan_exit=$?
    if [[ $stan_exit -eq 0 ]]; then
        pass "level 10"
    else
        err_count=$(echo "$stan_output" | grep -cE "^/" || echo "?")
        fail "${err_count} error(s)"
    fi
else
    fail "phpstan not available"
fi

step "pytest runs"
if [[ -f "$VENV_DIR/bin/pytest" ]]; then
    pytest_output=$(NEMO_MODEL_PROVIDER=mock PYTHONPATH="$PYTHON_AGENT_DIR" "$VENV_DIR/bin/pytest" "$REPO_ROOT/tests/python/" -q 2>&1)
    pytest_exit=$?
    if [[ $pytest_exit -eq 0 ]]; then
        pytest_summary=$(echo "$pytest_output" | tail -1)
        pass "$pytest_summary"
    else
        fail "tests failing"
    fi
else
    fail "pytest not available"
fi

step "Code style clean"
if [[ -x "$REPO_ROOT/vendor/bin/php-cs-fixer" ]]; then
    cs_output=$("$REPO_ROOT/vendor/bin/php-cs-fixer" fix --dry-run --diff --config="$REPO_ROOT/.php-cs-fixer.php" 2>&1)
    cs_exit=$?
    if [[ $cs_exit -eq 0 ]]; then
        pass
    else
        fix_count=$(echo "$cs_output" | grep -c "^   [0-9]*)" || echo "?")
        fail "${fix_count} file(s) need fixing - run: composer cs:fix"
    fi
else
    fail "php-cs-fixer not available"
fi

# ── Summary ────────────────────────────────────────────────────────
echo ""
echo -e "  ${DIM}$(printf '─%.0s' {1..44})${RESET}"
echo ""

if [[ $FAILED -eq 0 ]]; then
    msg="${PASSED}/${TOTAL} checks passed"
    if [[ $WARNED -gt 0 ]]; then
        msg="${msg}, ${WARNED} warning(s)"
    fi
    echo -e "  ${GREEN}${BOLD}${msg}${RESET}"
    echo ""
else
    echo -e "  ${RED}${BOLD}${FAILED}/${TOTAL} checks failed${RESET}"
    echo ""
    for f in "${FAILURES[@]}"; do
        echo -e "    ${FAIL}  ${f}"
    done
    echo ""
    echo -e "  ${DIM}Run ./scripts/setup-initial.sh to fix most issues${RESET}"
    echo ""
    exit 1
fi
