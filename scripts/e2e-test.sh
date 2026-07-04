#!/bin/bash
# =============================================================================
# E2E Test Runner — Starts services, runs contract tests, cleans up
# =============================================================================
#
# Usage:
#   ./scripts/e2e-test.sh              # Full run: start services, test, stop
#   ./scripts/e2e-test.sh --no-start   # Tests only (services already running)
#   ./scripts/e2e-test.sh --agent-only # Skip PHP app (test agent directly)
#
# Starts services WITHOUT GPU (NEMO_MODEL_PROVIDER=mock) so tests run on any machine.
# Tests validate API contracts, proxy chains, and service integration.
#
# Exit codes:
#   0 — All tests passed
#   1 — Tests failed
#   2 — Service startup failed
# =============================================================================

set -uo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
VENV="${REPO_ROOT}/strands_agents/.venv"
LOG_DIR="${REPO_ROOT}/var/log/e2e"

# Ports — use high range to avoid conflicts with dev stack
export AGENT_PORT="${AGENT_PORT:-48201}"
export APP_PORT="${APP_PORT:-48202}"
export MERCURE_PORT="${MERCURE_PORT:-48203}"

# Flags
START_SERVICES=true
INCLUDE_PHP=true
INCLUDE_BROWSER=false
PIDS=()

for arg in "$@"; do
    case "$arg" in
        --no-start)   START_SERVICES=false ;;
        --agent-only) INCLUDE_PHP=false ;;
        --browser)    INCLUDE_BROWSER=true ;;
        --help|-h)
            head -17 "$0" | tail -15
            echo "  --browser     Also run Playwright browser tests (requires Node.js + Chromium)"
            exit 0
            ;;
    esac
done

# ── Colors ────────────────────────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
DIM='\033[2m'
BOLD='\033[1m'
RESET='\033[0m'

log()  { echo -e "${DIM}[e2e]${RESET} $*"; }
ok()   { echo -e "${GREEN}[OK]${RESET} $*"; }
fail() { echo -e "${RED}[FAIL]${RESET} $*"; }
warn() { echo -e "${YELLOW}[WARN]${RESET} $*"; }

# ── Cleanup ───────────────────────────────────────────────────────────
cleanup() {
    log "Cleaning up..."
    for pid in "${PIDS[@]}"; do
        if kill -0 "$pid" 2>/dev/null; then
            kill "$pid" 2>/dev/null
            wait "$pid" 2>/dev/null || true
        fi
    done

    # Stop Mercure container if we started it
    if [[ "$START_SERVICES" == "true" ]]; then
        docker rm -f ambient-scribe-e2e-mercure 2>/dev/null || true
    fi
}
trap cleanup EXIT INT TERM

# ── Preflight ─────────────────────────────────────────────────────────
if [[ ! -f "${VENV}/bin/python3" ]]; then
    fail "Python venv not found at ${VENV}"
    echo "  Run: cd strands_agents && python3 -m venv .venv && pip install -r requirements.txt"
    exit 2
fi

mkdir -p "$LOG_DIR"

# ── Wait for port ─────────────────────────────────────────────────────
wait_for_port() {
    local port="$1"
    local label="$2"
    local timeout="${3:-30}"
    local health_path="${4:-/}"
    local elapsed=0

    while true; do
        local status
        status=$(curl -sf -o /dev/null -w "%{http_code}" \
            --connect-timeout 2 --max-time 3 \
            "http://localhost:${port}${health_path}" 2>/dev/null) || true
        if [[ "$status" =~ ^[2-4] ]]; then
            break
        fi
        sleep 1
        elapsed=$((elapsed + 1))
        if [[ $elapsed -ge $timeout ]]; then
            fail "${label} did not start within ${timeout}s on port ${port}"
            return 1
        fi
    done
    ok "${label} ready on port ${port} (${elapsed}s)"
}

# ═══════════════════════════════════════════════════════════════════════
# START SERVICES
# ═══════════════════════════════════════════════════════════════════════
if [[ "$START_SERVICES" == "true" ]]; then
    log "Starting services (no GPU, no model loading)..."

    # ── Mercure ───────────────────────────────────────────────────────
    log "Starting Mercure on port ${MERCURE_PORT}..."
    docker rm -f ambient-scribe-e2e-mercure 2>/dev/null || true
    docker run -d \
        --name ambient-scribe-e2e-mercure \
        -p "${MERCURE_PORT}:3701" \
        -e MERCURE_PUBLISHER_JWT_KEY="e2e-test-secret-key-minimum-32-chars" \
        -e MERCURE_SUBSCRIBER_JWT_KEY="e2e-test-secret-key-minimum-32-chars" \
        -e SERVER_NAME=":3701" \
        -e "MERCURE_EXTRA_DIRECTIVES=anonymous
cors_origins http://localhost:${APP_PORT}" \
        dunglas/mercure >"${LOG_DIR}/mercure.log" 2>&1

    if [[ $? -ne 0 ]]; then
        fail "Mercure container failed to start"
        cat "${LOG_DIR}/mercure.log"
        exit 2
    fi

    # ── Python Agent ──────────────────────────────────────────────────
    log "Starting Python agent on port ${AGENT_PORT} (NEMO_MODEL_PROVIDER=mock)..."
    NEMO_MODEL_PROVIDER=mock \
    NEMO_STREAM_INPUT_FORMAT=pcm \
    MERCURE_HUB_URL="http://localhost:${MERCURE_PORT}/.well-known/mercure" \
    MERCURE_JWT_SECRET="e2e-test-secret-key-minimum-32-chars" \
    ROLE_AGENT_MODEL_PROVIDER=ollama \
    OLLAMA_HOST="http://localhost:11434" \
    "${VENV}/bin/python3" -m uvicorn api.server:app \
        --host 0.0.0.0 --port "$AGENT_PORT" \
        --app-dir "${REPO_ROOT}/strands_agents" \
        >"${LOG_DIR}/agent.log" 2>&1 &
    PIDS+=($!)

    wait_for_port "$AGENT_PORT" "Agent" 15 "/health" || exit 2

    # ── PHP App ───────────────────────────────────────────────────────
    if [[ "$INCLUDE_PHP" == "true" ]]; then
        log "Starting PHP app on port ${APP_PORT}..."
        APP_ENV=dev \
        APP_DEBUG=1 \
        APP_SECRET=e2e-test-secret \
        AGENT_ENDPOINT="http://localhost:${AGENT_PORT}" \
        NEMO_WEBSOCKET_URL="ws://localhost:${AGENT_PORT}" \
        MERCURE_URL="http://localhost:${MERCURE_PORT}/.well-known/mercure" \
        MERCURE_PUBLIC_URL="http://localhost:${MERCURE_PORT}/.well-known/mercure" \
        MERCURE_JWT_SECRET="e2e-test-secret-key-minimum-32-chars" \
        php -d variables_order=EGPCS \
            -d upload_max_filesize=128M \
            -d post_max_size=128M \
            -d memory_limit=512M \
            -S "0.0.0.0:${APP_PORT}" -t "${REPO_ROOT}/public" \
            "${REPO_ROOT}/public/index.php" \
            >"${LOG_DIR}/php.log" 2>&1 &
        PIDS+=($!)

        wait_for_port "$APP_PORT" "PHP app" 10 || exit 2
    fi

    echo ""
    ok "All services running"
    echo ""
fi

# ═══════════════════════════════════════════════════════════════════════
# RUN TESTS
# ═══════════════════════════════════════════════════════════════════════
log "Running e2e contract tests..."
echo ""

AGENT_PORT="$AGENT_PORT" \
APP_PORT="$APP_PORT" \
MERCURE_PORT="$MERCURE_PORT" \
"${VENV}/bin/python3" -m pytest "${REPO_ROOT}/tests/e2e/" \
    -v --tb=short --no-header \
    -x  # Stop on first failure

TEST_EXIT=$?

echo ""
if [[ $TEST_EXIT -eq 0 ]]; then
    ok "All e2e contract tests passed"
else
    fail "E2E contract tests failed (exit code: ${TEST_EXIT})"
    echo ""
    echo -e "${DIM}Logs:${RESET}"
    echo "  Agent:   ${LOG_DIR}/agent.log"
    echo "  PHP:     ${LOG_DIR}/php.log"
    echo "  Mercure: ${LOG_DIR}/mercure.log"
    exit $TEST_EXIT
fi

# ═══════════════════════════════════════════════════════════════════════
# BROWSER TESTS (Playwright)
# ═══════════════════════════════════════════════════════════════════════
if [[ "$INCLUDE_BROWSER" == "true" ]]; then
    if ! command -v npx &>/dev/null; then
        warn "Node.js not found — skipping browser tests"
        warn "Install Node.js and run: npm install && npx playwright install --with-deps chromium"
    elif [[ ! -f "${REPO_ROOT}/node_modules/.package-lock.json" ]]; then
        warn "node_modules not found — skipping browser tests"
        warn "Run: npm install && npx playwright install --with-deps chromium"
    else
        log "Running Playwright browser tests..."
        echo ""

        AGENT_PORT="$AGENT_PORT" \
        APP_PORT="$APP_PORT" \
        MERCURE_PORT="$MERCURE_PORT" \
        npx playwright test "${REPO_ROOT}/tests/e2e/browser.spec.js" \
            --reporter=list

        BROWSER_EXIT=$?

        echo ""
        if [[ $BROWSER_EXIT -eq 0 ]]; then
            ok "All browser tests passed"
        else
            fail "Browser tests failed (exit code: ${BROWSER_EXIT})"
            TEST_EXIT=$BROWSER_EXIT
        fi
    fi
else
    log "Skipping browser tests (use --browser to include)"
fi

exit $TEST_EXIT
