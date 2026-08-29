#!/bin/bash
# =============================================================================
# Update Dependencies - Updates PHP and Python packages to latest versions
# =============================================================================
# Usage: ./scripts/dependencies-update.sh [--php] [--python]
#
# Updates packages to the latest versions allowed by version constraints:
#   - PHP: runs composer update (rewrites composer.lock)
#   - Python: runs pip install --upgrade (pulls latest within constraints)
#
# After updating, runs a security audit and quick smoke test to catch breakage.
#
# Options:
#   --php       Update PHP dependencies only
#   --python    Update Python dependencies only
#   (no flags)  Update both
# =============================================================================

set -euo pipefail

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

ERRORS=0
WARNINGS=0

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

warn() {
    local msg="$1"
    WARNINGS=$((WARNINGS + 1))
    echo -e "${WARN}  ${YELLOW}${msg}${RESET}"
}

# ── Header ──────────────────────────────────────────────────────────
echo ""
echo -e "${BOLD}  Ambient Scribe - Update Dependencies${RESET}"
echo -e "  ${DIM}$(printf '─%.0s' {1..44})${RESET}"

# ── PHP ─────────────────────────────────────────────────────────────
if [[ "$DO_PHP" == true ]]; then
    echo ""
    echo -e "  ${BOLD}PHP (Composer)${RESET}"
    echo ""

    if ! command -v composer &>/dev/null; then
        step "composer"
        fail "not found"
    else
        step "composer update"
        # Capture the status with `|| var=$?`. A bare `out=$(cmd)` followed by
        # `rc=$?` aborts under set -e the moment the command fails, which made
        # every failure branch below it unreachable and hid failed installs.
        update_exit=0
        update_output=$(cd "$REPO_ROOT" && composer update 2>&1) || update_exit=$?
        if [[ $update_exit -eq 0 ]]; then
            changed_lines=$(echo "$update_output" | grep -E "^\s+- (Upgrading|Installing|Removing)" || true)
            updated=$(echo "$changed_lines" | grep -c "." || true)
            updated="${updated//[^0-9]/}"
            if [[ "${updated:-0}" -gt 0 ]]; then
                pass "${updated} package(s) changed"
                echo "$changed_lines" | while IFS= read -r line; do
                    echo -e "       ${DIM}${line}${RESET}"
                done
            else
                pass "already up to date"
            fi
        else
            fail "composer update failed"
            echo "$update_output" | tail -5 | while IFS= read -r line; do
                echo -e "    ${DIM}${line}${RESET}"
            done
        fi

        # Security audit
        step "Security audit"
        audit_exit=0
        audit_output=$(cd "$REPO_ROOT" && composer audit 2>&1) || audit_exit=$?
        if [[ $audit_exit -eq 0 ]]; then
            pass "no vulnerabilities"
        else
            warn "$(echo "$audit_output" | grep -c "Advisory" || echo "?") advisory(s) found"
        fi

        # Quick test
        if [[ -x "$REPO_ROOT/vendor/bin/phpunit" ]]; then
            step "PHPUnit smoke test"
            test_exit=0
            test_output=$("$REPO_ROOT/vendor/bin/phpunit" --configuration "$REPO_ROOT/phpunit.xml.dist" 2>&1) || test_exit=$?
            if [[ $test_exit -eq 0 ]]; then
                test_summary=$(echo "$test_output" | grep -oE '[0-9]+ tests, [0-9]+ assertions' || echo "ok")
                pass "$test_summary"
            else
                fail "tests broken after update"
                echo "$test_output" | grep -E "(FAIL|Error)" | head -5 | while IFS= read -r line; do
                    echo -e "    ${DIM}${line}${RESET}"
                done
            fi
        fi
    fi
fi

# ── Python ──────────────────────────────────────────────────────────
if [[ "$DO_PYTHON" == true ]]; then
    echo ""
    echo -e "  ${BOLD}Python (pip)${RESET}"
    echo ""

    if [[ ! -f "$VENV_DIR/bin/pip" ]]; then
        step "Python venv"
        fail "not found - run ./scripts/setup-initial.sh first"
    else
        # Upgrade pip itself
        step "pip self-update"
        pip_self_exit=0
        pip_self=$("$VENV_DIR/bin/pip" install --upgrade pip 2>&1) || pip_self_exit=$?
        if [[ $pip_self_exit -eq 0 ]]; then
            pip_ver=$("$VENV_DIR/bin/pip" --version 2>/dev/null | grep -oE '[0-9]+\.[0-9]+' | head -1)
            pass "v${pip_ver}"
        else
            warn "pip self-update failed"
        fi

        # Capture before versions
        before=$("$VENV_DIR/bin/pip" freeze 2>/dev/null | sort)

        # Upgrade packages
        step "pip install --upgrade -r requirements.txt"
        pip_exit=0
        pip_output=$("$VENV_DIR/bin/pip" install --upgrade -r "$PYTHON_AGENT_DIR/requirements.txt" 2>&1) || pip_exit=$?
        if [[ $pip_exit -eq 0 ]]; then
            after=$("$VENV_DIR/bin/pip" freeze 2>/dev/null | sort)
            diff_output=$(diff <(echo "$before") <(echo "$after") || true)
            changed=$(echo "$diff_output" | grep -c "^[<>]" || true)
            changed="${changed:-0}"
            changed="${changed//[^0-9]/}"
            # Each change shows as two diff lines (< old, > new), so divide by 2
            pkg_changed=$(( ${changed:-0} / 2 ))
            if [[ "$pkg_changed" -gt 0 ]]; then
                pass "${pkg_changed} package(s) changed"
                # Show old→new version for each changed package
                echo "$diff_output" | grep "^[<>]" | sed 's/^< //' | sed 's/^> //' | \
                    awk -F'==' '{packages[$1] = packages[$1] ? packages[$1] " → " $2 : $2} END {for (p in packages) print p "  " packages[p]}' | sort | \
                    while IFS= read -r line; do
                        echo -e "       ${DIM}${line}${RESET}"
                    done
            else
                pass "already up to date"
            fi
        else
            fail "pip upgrade failed"
            echo "$pip_output" | tail -5 | while IFS= read -r line; do
                echo -e "    ${DIM}${line}${RESET}"
            done
        fi

        # Audit
        step "pip audit"
        if "$VENV_DIR/bin/pip" check 2>&1 | grep -q "No broken requirements"; then
            pass "no broken requirements"
        else
            check_output=$("$VENV_DIR/bin/pip" check 2>&1)
            issues=$(echo "$check_output" | grep -c "has requirement" || echo "0")
            if [[ "$issues" -gt 0 ]]; then
                warn "${issues} compatibility issue(s)"
                echo "$check_output" | head -5 | while IFS= read -r line; do
                    echo -e "    ${DIM}${line}${RESET}"
                done
            else
                pass "all compatible"
            fi
        fi

        # Ruff check
        step "Python agent lint"
        py_errors=0
        if [[ ! -x "$VENV_DIR/bin/ruff" ]]; then
            py_errors=1
        elif ! "$VENV_DIR/bin/ruff" check "$PYTHON_AGENT_DIR" >/dev/null 2>&1; then
            py_errors=1
        fi
        if [[ $py_errors -eq 0 ]]; then
            pass
        else
            fail "${py_errors} lint check(s) failed"
        fi
    fi
fi

# ── Summary ─────────────────────────────────────────────────────────
echo ""
echo -e "  ${DIM}$(printf '─%.0s' {1..44})${RESET}"
echo ""

if [[ $ERRORS -eq 0 ]]; then
    msg="Dependencies updated"
    if [[ $WARNINGS -gt 0 ]]; then
        msg="${msg} with ${WARNINGS} warning(s)"
    fi
    echo -e "  ${GREEN}${BOLD}${msg}${RESET}"
    echo ""
    if [[ "$DO_PHP" == true ]]; then
        echo -e "  ${DIM}Review changes:${RESET}"
        echo -e "    ${ARROW} ${BOLD}git diff composer.lock${RESET}"
        echo ""
    fi
else
    echo -e "  ${RED}${BOLD}Update finished with ${ERRORS} error(s)${RESET}"
    echo ""
    exit 1
fi
