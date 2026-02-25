#!/bin/bash
# =============================================================================
# Start Dev — Daily lightweight startup for Ambient Scribe
# =============================================================================
# Usage: ./scripts/start-dev.sh [OPTIONS]
#
# Options:
#   --build, -b     Force Docker image rebuild
#   --no-logs       Skip log streaming (exit after health checks)
#   --help, -h      Show this help
#
# What it does:
#   1. Quick preflight (Docker, .env, nvidia-smi)
#   2. Check current container state
#   3. Start containers (rebuild only with --build or missing images)
#   4. Health-check all services
#   5. Stream nemo-agent logs
#
# Ctrl+C stops containers (preserves them for fast restart).
#
# For first-time setup:     scripts/setup-initial.sh
# To diagnose issues:       scripts/health-checks.sh
# =============================================================================

set -uo pipefail

source "$(dirname "${BASH_SOURCE[0]}")/env-detect.sh"

ERRORS=0

# ── Parse flags ─────────────────────────────────────────────────────
FORCE_BUILD=false
NO_LOGS=false

for arg in "$@"; do
    case "$arg" in
        --build|-b) FORCE_BUILD=true ;;
        --no-logs)  NO_LOGS=true ;;
        --help|-h)
            echo "Usage: $0 [--build|-b] [--no-logs]"
            echo ""
            echo "  --build, -b     Force Docker image rebuild"
            echo "  --no-logs       Skip log streaming"
            exit 0
            ;;
    esac
done

# ── Cleanup on Ctrl+C ──────────────────────────────────────────────
cleanup() {
    echo ""
    echo -e "${DIM}  Stopping containers...${RESET}"
    dc stop >/dev/null 2>&1 || true
    echo -e "  ${PASS} Containers stopped (preserved for fast restart)"
    echo -e "  ${DIM}Remove with: dc down${RESET}"
    exit 0
}
trap cleanup SIGINT SIGTERM

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

header() {
    echo ""
    echo -e "${BOLD}  Ambient Scribe — Dev Server${RESET}"
    echo -e "  ${DIM}$(printf '─%.0s' {1..44})${RESET}"
    echo ""
}

# Poll a container's Docker health status with a countdown timer.
# Usage: wait_healthy <container_name> <label> <timeout_secs>
wait_healthy() {
    local container="$1" label="$2" timeout="$3"
    local padded
    padded=$(printf '%-28s' "$label")
    local elapsed=0

    while [[ $elapsed -lt $timeout ]]; do
        local remaining=$(( timeout - elapsed ))
        local status
        status=$(docker inspect "$container" --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}no-healthcheck{{end}}' 2>/dev/null || echo "missing")

        if [[ "$status" == "healthy" ]]; then
            echo -ne "\r\033[K"
            echo -e "  ${ARROW} ${padded} ${PASS}  ${DIM}healthy${RESET}"
            return 0
        fi

        # Containers without a HEALTHCHECK (e.g., Mercure) — treat "running" as ready
        if [[ "$status" == "no-healthcheck" || "$status" == "none" ]]; then
            echo -ne "\r\033[K"
            echo -e "  ${ARROW} ${padded} ${PASS}  ${DIM}running (no healthcheck)${RESET}"
            return 0
        fi

        if [[ "$status" == "missing" ]]; then
            echo -ne "\r\033[K"
            echo -e "  ${ARROW} ${padded} ${FAIL}  ${RED}container not found${RESET}"
            ERRORS=$((ERRORS + 1))
            return 1
        fi

        echo -ne "\r\033[K  ${ARROW} ${padded} ${DIM}${status} (${remaining}s)${RESET}"
        sleep 2
        elapsed=$(( elapsed + 2 ))
    done

    echo -ne "\r\033[K"
    echo -e "  ${ARROW} ${padded} ${FAIL}  ${RED}timeout after ${timeout}s${RESET}"
    echo -e "     ${DIM}Check: dc logs ${container}${RESET}"
    ERRORS=$((ERRORS + 1))
    return 1
}

# =============================================================================
# STEP 1: Preflight (fail-fast)
# =============================================================================
header
STARTUP_START=$SECONDS

step "Docker daemon"
if ! command -v docker &>/dev/null || ! docker ps &>/dev/null 2>&1; then
    fail "not running — start Docker Desktop or dockerd"
    echo ""
    exit 1
else
    pass
fi

step ".env file"
if [[ -f "$REPO_ROOT/.env" ]]; then
    pass
else
    fail "not found — run ./scripts/setup-initial.sh first"
    echo ""
    exit 1
fi

step "nvidia-smi"
if [[ "$HAS_NVIDIA_SMI" == "true" ]]; then
    pass "${GPU_NAME}"
else
    fail "not found — GPU required for NeMo"
    echo ""
    exit 1
fi

echo ""

# =============================================================================
# STEP 2: State Check
# =============================================================================
echo -e "  ${BOLD}Checking state${RESET}"
echo ""

RUNNING_COUNT=$(dc ps --status running -q 2>/dev/null | wc -l)
EXPECTED_COUNT=$(dc config --services 2>/dev/null | wc -l)
ALL_RUNNING=false

step "Containers"
if [[ "$RUNNING_COUNT" -eq "$EXPECTED_COUNT" && "$EXPECTED_COUNT" -gt 0 && "$FORCE_BUILD" == "false" ]]; then
    ALL_RUNNING=true
    pass "all ${RUNNING_COUNT}/${EXPECTED_COUNT} running — skipping to health checks"
else
    pass "${RUNNING_COUNT}/${EXPECTED_COUNT} running"
fi

echo ""

# =============================================================================
# STEP 3: Build Strategy + Start
# =============================================================================
if [[ "$ALL_RUNNING" == "false" ]]; then
    echo -e "  ${BOLD}Starting containers${RESET}"
    echo ""

    BUILD_FLAG=""
    if [[ "$FORCE_BUILD" == "true" ]]; then
        BUILD_FLAG="--build"
        step "Build strategy"
        pass "--build flag"
    else
        # Check if required images exist
        MISSING=false
        while IFS= read -r img; do
            if [[ -z "$(docker images -q "$img" 2>/dev/null)" ]]; then
                MISSING=true
                break
            fi
        done < <(dc config --images 2>/dev/null | sort -u)

        if [[ "$MISSING" == "true" ]]; then
            step "Build strategy"
            fail "images missing — run ./scripts/setup-initial.sh for first-time setup"
            echo ""
            exit 1
        fi
    fi

    # shellcheck disable=SC2086
    if dc up -d $BUILD_FLAG 2>&1 | tail -3; then
        echo -e "  ${ARROW} Docker Compose             ${PASS}  ${DIM}containers started${RESET}"
    else
        echo -e "  ${ARROW} Docker Compose             ${FAIL}  ${RED}failed to start${RESET}"
        echo -e "     ${DIM}Check: dc logs${RESET}"
        exit 1
    fi

    echo ""
fi

# =============================================================================
# STEP 4: Health Checks
# =============================================================================
echo -e "  ${BOLD}Health checks${RESET}"
echo ""

# Refresh container names after start
_RUNNING_CONTAINERS="$(docker ps --format '{{.Names}}' 2>/dev/null || true)"
NEMO_CONTAINER=$(_resolve_container "${COMPOSE_PROJECT}-nemo-agent-1" "nemo-agent")
APP_CONTAINER=$(_resolve_container "${COMPOSE_PROJECT}-app-1" "app")
MERCURE_CONTAINER=$(_resolve_container "${COMPOSE_PROJECT}-mercure-1" "mercure")

wait_healthy "$MERCURE_CONTAINER"  "Mercure"    15
wait_healthy "$NEMO_CONTAINER"     "nemo-agent" 120
wait_healthy "$APP_CONTAINER"      "app"        30

echo ""

# =============================================================================
# STEP 5: Ready Banner
# =============================================================================
echo -e "  ${DIM}$(printf '─%.0s' {1..44})${RESET}"
echo ""
STARTUP_ELAPSED=$(( SECONDS - STARTUP_START ))
echo -e "  ${GREEN}${BOLD}Ready!${RESET} ${DIM}(${STARTUP_ELAPSED}s)${RESET}"
echo ""
echo -e "  ${DIM}Services:${RESET}"
echo -e "    ${ARROW} Scribe UI:     ${BOLD}http://localhost:8082/scribe${RESET}"
echo -e "    ${ARROW} NeMo agent:    ${BOLD}http://localhost:8001${RESET}"
echo -e "    ${ARROW} Mercure:       ${BOLD}http://localhost:3701${RESET}"
echo ""
echo -e "  ${DIM}Useful commands:${RESET}"
echo -e "    ${ARROW} Health check:  ${DIM}./scripts/health-checks.sh${RESET}"
echo -e "    ${ARROW} Rebuild:       ${DIM}./scripts/start-dev.sh --build${RESET}"
echo -e "    ${ARROW} Service logs:  ${DIM}dc logs -f <service>${RESET}"
echo -e "    ${ARROW} Stop:          ${DIM}dc stop${RESET}"
echo -e "    ${ARROW} Remove:        ${DIM}dc down${RESET}"
echo ""

# =============================================================================
# STEP 6: Stream Logs + Wait
# =============================================================================
if [[ "$NO_LOGS" == "true" ]]; then
    exit 0
fi

echo -e "  ${DIM}Streaming nemo-agent logs (Ctrl+C to stop)...${RESET}"
echo ""

dc logs -f nemo-agent 2>&1 | while IFS= read -r line; do
    case "$line" in
        *WARNING*|*ERROR*|*Traceback*|*"POST "*|*"GET "*|*WebSocket*|*"model"*|*"loaded"*|*"health"*)
            echo -e "    ${CYAN}[nemo]${RESET} $line" ;;
    esac
done &
TAIL_PID=$!

# Wait — Ctrl+C triggers cleanup
wait $TAIL_PID 2>/dev/null
