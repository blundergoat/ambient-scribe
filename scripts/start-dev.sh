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
<<<<<<< Updated upstream
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
=======
#   ollama (default):
#     1. Ollama (auto-starts via binary or Docker if needed)
#     2. NeMo FastAPI agent via Docker Compose on the first free port in 48101-48110
#     3. Mercure hub via Docker Compose on port 48137
#     4. PHP Symfony dev server on port 48082
#
#   bedrock:
#     1. NeMo FastAPI agent via Docker Compose on the first free port in 48101-48110
#     2. Mercure hub via Docker Compose on port 48137
#     3. PHP Symfony dev server on port 48082
#     (Requires AWS credentials - no local LLM needed)
#
# Prerequisites:
#   - .env is copied from .env.example if missing
#   - Local PHP dependencies are installed automatically if vendor/ is missing
#   - Docker + docker compose
#   - Ollama (ROLE_AGENT_MODEL_PROVIDER=ollama): binary, Docker, or running instance
#   - Bedrock (ROLE_AGENT_MODEL_PROVIDER=bedrock): AWS credentials configured
#   - NVIDIA Container Toolkit for GPU NeMo inference
#
# Environment:
#   ROLE_AGENT_MODEL_PROVIDER - Role inference backend: 'ollama' or 'bedrock'
#   MODEL_PROVIDER            - Legacy alias for ROLE_AGENT_MODEL_PROVIDER
#   AGENT_PORT     - NeMo agent starting port (default: 48101)
#   AGENT_PORT_MAX - Highest NeMo agent port to try (default: 48110)
#   APP_PORT       - PHP app starting port (default: 48082)
#   APP_PORT_MAX   - Highest PHP app port to try (default: 48090)
#   MERCURE_PORT   - Mercure hub port (default: 48137)
#   OLLAMA_HOST    - Ollama URL (default: http://localhost:11434)
#   OLLAMA_MODEL   - Model name (default: from .env or qwen2.5:14b)
#
# Press Ctrl+C to stop all services and containers started by this script.
>>>>>>> Stashed changes
# =============================================================================

set -uo pipefail

source "$(dirname "${BASH_SOURCE[0]}")/env-detect.sh"

<<<<<<< Updated upstream
ERRORS=0
=======
# ── Configurable ports ──────────────────────────────────────────────
AGENT_PORT="${AGENT_PORT:-48101}"
AGENT_PORT_MAX="${AGENT_PORT_MAX:-48110}"
APP_PORT="${APP_PORT:-48082}"
APP_PORT_MAX="${APP_PORT_MAX:-48090}"
MERCURE_PORT="${MERCURE_PORT:-48137}"
>>>>>>> Stashed changes

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

<<<<<<< Updated upstream
step ".env file"
=======
OLLAMA_HOST_LOCAL="$OLLAMA_HOST"
if [[ "$MODEL_PROVIDER" == "ollama" ]] && ! curl -sf "${OLLAMA_HOST_LOCAL}/api/tags" >/dev/null 2>&1; then
    DETECTED_OLLAMA_HOST="$(detect_running_ollama_host || true)"
    if [[ -n "$DETECTED_OLLAMA_HOST" ]] && curl -sf "${DETECTED_OLLAMA_HOST}/api/tags" >/dev/null 2>&1; then
        echo -e "  ${YELLOW}${BOLD}Using detected Ollama endpoint${RESET} ${DIM}${DETECTED_OLLAMA_HOST}${RESET}"
        OLLAMA_HOST_LOCAL="$DETECTED_OLLAMA_HOST"
    fi
fi
OLLAMA_HOST_DOCKER="$(dockerize_ollama_host "$OLLAMA_HOST_LOCAL")"

# ── Validate environment ─────────────────────────────────────────
ENV_ERRORS=0

# MERCURE_JWT_SECRET - must be ≥ 32 chars (256 bits) for HS256 if set
if [[ -n "${MERCURE_JWT_SECRET:-}" && ${#MERCURE_JWT_SECRET} -lt 32 ]]; then
    echo -e "  ${FAIL} ${RED}MERCURE_JWT_SECRET is too short${RESET} ${DIM}(${#MERCURE_JWT_SECRET} chars, need ≥ 32 for HS256)${RESET}"
    echo -e "     ${DIM}Update to at least 32 characters, or unset it for sync-only mode${RESET}"
    ENV_ERRORS=$((ENV_ERRORS + 1))
fi

# MERCURE triad - if any Mercure var is set, all three should be
MERCURE_VARS_SET=0
for var in MERCURE_JWT_SECRET MERCURE_URL MERCURE_PUBLIC_URL; do
    [[ -n "${!var:-}" ]] && MERCURE_VARS_SET=$((MERCURE_VARS_SET + 1))
done
if [[ $MERCURE_VARS_SET -gt 0 && $MERCURE_VARS_SET -lt 3 ]]; then
    echo -e "  ${YELLOW}${BOLD}Warning:${RESET} ${DIM}Partial Mercure config - set all three: MERCURE_JWT_SECRET, MERCURE_URL, MERCURE_PUBLIC_URL${RESET}"
    echo -e "     ${DIM}Streaming won't work without all three. Sync mode will still work.${RESET}"
fi

if [[ $ENV_ERRORS -gt 0 ]]; then
    echo ""
    echo -e "  ${RED}${BOLD}Fix the above error(s) before continuing.${RESET}"
    exit 1
fi

# ── 1. LLM Provider ────────────────────────────────────────────────
if [[ "$MODEL_PROVIDER" == "ollama" ]]; then

    echo -e "  ${BOLD}Checking Ollama${RESET}"
    echo ""

    OLLAMA_TAGS_CACHE=""
    if OLLAMA_TAGS_CACHE="$(curl -sf "${OLLAMA_HOST_LOCAL}/api/tags" 2>/dev/null)"; then
        echo -e "  ${ARROW} Ollama                 ${PASS}  ${DIM}running at ${OLLAMA_HOST_LOCAL}${RESET}"
    else
        # Try to start ollama serve if the binary exists
        if command -v ollama &>/dev/null; then
            # Extract port from OLLAMA_HOST for the serve command
            OLLAMA_SERVE_PORT=$(echo "$OLLAMA_HOST_LOCAL" | grep -oE '[0-9]+$' || echo "11434")
            echo -e "  ${ARROW} Starting Ollama...     ${DIM}${OLLAMA_HOST_LOCAL}${RESET}"
            OLLAMA_HOST="0.0.0.0:${OLLAMA_SERVE_PORT}" ollama serve >/dev/null 2>&1 &
            PIDS+=($!)

            # Wait for Ollama to be ready
            for i in $(seq 1 15); do
                if OLLAMA_TAGS_CACHE="$(curl -sf "${OLLAMA_HOST_LOCAL}/api/tags" 2>/dev/null)"; then
                    echo -e "  ${ARROW} Ollama                 ${PASS}  ${DIM}started${RESET}"
                    break
                fi
                if [[ $i -eq 15 ]]; then
                    echo -e "  ${ARROW} Ollama                 ${FAIL}  ${RED}failed to start${RESET}"
                    echo -e "     ${DIM}Check that port 11434 is free${RESET}"
                    cleanup 1
                fi
                sleep 1
            done
        elif command -v docker &>/dev/null; then
            # No ollama binary - try Docker instead
            echo -e "  ${ARROW} Starting Ollama via Docker..."
            OLLAMA_SERVE_PORT=$(echo "$OLLAMA_HOST_LOCAL" | grep -oE '[0-9]+$' || echo "11434")

            # Check if an ollama container already exists (stopped)
            if docker ps -a --filter "name=^ollama$" --format "{{.Names}}" 2>/dev/null | grep -q "^ollama$"; then
                docker start ollama >/dev/null 2>&1
            else
                docker run -d --name ollama -p "${OLLAMA_SERVE_PORT}:11434" ollama/ollama >/dev/null 2>&1
            fi
            OLLAMA_DOCKER=true

            # Wait for Ollama to be ready
            for i in $(seq 1 20); do
                if OLLAMA_TAGS_CACHE="$(curl -sf "${OLLAMA_HOST_LOCAL}/api/tags" 2>/dev/null)"; then
                    echo -e "  ${ARROW} Ollama                 ${PASS}  ${DIM}running via Docker${RESET}"
                    break
                fi
                if [[ $i -eq 20 ]]; then
                    echo -e "  ${ARROW} Ollama                 ${FAIL}  ${RED}Docker container failed to start${RESET}"
                    echo -e "     ${DIM}Check: docker logs ollama${RESET}"
                    cleanup 1
                fi
                sleep 1
            done
        else
            echo -e "  ${ARROW} Ollama                 ${FAIL}  ${RED}not running${RESET}"
            echo -e "     ${DIM}Install from https://ollama.com or install Docker${RESET}"
            cleanup 1
        fi
    fi

    # Check if the model is pulled (reuse cached /api/tags response)
    echo -ne "  ${ARROW} Model ${OLLAMA_MODEL}     "
    model_name=$(echo "$OLLAMA_TAGS_CACHE" | python3 -c "
import json, sys
try:
    data = json.load(sys.stdin)
    for m in data.get('models', []):
        name = m.get('name', '')
        if name == '${OLLAMA_MODEL}' or name.startswith('${OLLAMA_MODEL}:'):
            print(name)
            break
except: pass
" 2>/dev/null)
    if [[ -n "$model_name" ]]; then
        echo -e "${PASS}  ${DIM}available (${model_name})${RESET}"
    else
        echo -e "${YELLOW}pulling...${RESET}"
        echo -e "     ${DIM}This may take a while on first run${RESET}"
        # Use docker exec if ollama was started via Docker, otherwise use the binary
        if [[ "${OLLAMA_DOCKER:-false}" == "true" ]]; then
            if docker exec ollama ollama pull "$OLLAMA_MODEL" 2>&1; then
                echo -e "  ${ARROW} Model ${OLLAMA_MODEL}     ${PASS}  ${DIM}ready${RESET}"
            else
                echo -e "  ${ARROW} Model ${OLLAMA_MODEL}     ${FAIL}  ${RED}pull failed${RESET}"
                cleanup 1
            fi
        elif OLLAMA_HOST="$OLLAMA_HOST_LOCAL" ollama pull "$OLLAMA_MODEL" 2>&1; then
            echo -e "  ${ARROW} Model ${OLLAMA_MODEL}     ${PASS}  ${DIM}ready${RESET}"
        else
            echo -e "  ${ARROW} Model ${OLLAMA_MODEL}     ${FAIL}  ${RED}pull failed${RESET}"
            cleanup 1
        fi
    fi

elif [[ "$MODEL_PROVIDER" == "bedrock" ]]; then

    echo -e "  ${BOLD}Checking AWS Bedrock${RESET}"
    echo ""

    # Validate AWS credentials - either explicit keys or a named profile
    if [[ -z "${AWS_ACCESS_KEY_ID:-}" && -z "${AWS_PROFILE:-}" ]]; then
        echo -e "  ${ARROW} AWS credentials        ${FAIL}  ${RED}not configured${RESET}"
        echo -e "     ${DIM}Set AWS_ACCESS_KEY_ID + AWS_SECRET_ACCESS_KEY, or AWS_PROFILE${RESET}"
        echo -e "     ${DIM}See .env.example for details${RESET}"
        cleanup 1
    fi

    if [[ -n "${AWS_PROFILE:-}" ]]; then
        echo -e "  ${ARROW} AWS credentials        ${PASS}  ${DIM}profile: ${AWS_PROFILE}${RESET}"
    else
        echo -e "  ${ARROW} AWS credentials        ${PASS}  ${DIM}access key configured${RESET}"
    fi
    echo -e "  ${ARROW} Region                 ${DIM}${AWS_DEFAULT_REGION:-us-east-1}${RESET}"

else

    echo -e "  ${FAIL} ${RED}Unknown role agent provider: ${MODEL_PROVIDER}${RESET}"
    echo -e "     ${DIM}Set ROLE_AGENT_MODEL_PROVIDER (or legacy MODEL_PROVIDER) to 'ollama' or 'bedrock' in .env${RESET}"
    exit 1

fi

# ── 2. NeMo Agent + Mercure ────────────────────────────────────────
echo ""
echo -e "  ${BOLD}Starting services${RESET}"
echo ""

export MODEL_PROVIDER
export NEMO_MODEL_PROVIDER
export AGENT_PORT
export APP_PORT
export MERCURE_PORT
[[ -n "${MERCURE_JWT_SECRET:-}" ]] && export MERCURE_JWT_SECRET
[[ -n "${NEMO_STREAM_INPUT_FORMAT:-}" ]] && export NEMO_STREAM_INPUT_FORMAT
export ROLE_AGENT_MODEL_PROVIDER="$MODEL_PROVIDER"

if [[ "$MODEL_PROVIDER" == "ollama" ]]; then
    export OLLAMA_HOST="$OLLAMA_HOST_DOCKER"
    export ROLE_AGENT_OLLAMA_MODEL="${ROLE_AGENT_OLLAMA_MODEL:-$OLLAMA_MODEL}"
else
    # Pass through AWS credentials for Bedrock
    if ! export_aws_profile_credentials; then
        cleanup 1
    fi
    [[ -n "${AWS_ACCESS_KEY_ID:-}" ]]     && export AWS_ACCESS_KEY_ID
    [[ -n "${AWS_SECRET_ACCESS_KEY:-}" ]] && export AWS_SECRET_ACCESS_KEY
    [[ -n "${AWS_SESSION_TOKEN:-}" ]]     && export AWS_SESSION_TOKEN
    [[ -n "${AWS_DEFAULT_REGION:-}" ]]    && export AWS_DEFAULT_REGION
    [[ -n "${AWS_PROFILE:-}" ]]           && export AWS_PROFILE
    if [[ -n "${ROLE_AGENT_MODEL_ID:-}" ]]; then
        export ROLE_AGENT_MODEL_ID
    elif [[ -n "${MODEL_ID:-}" ]]; then
        export ROLE_AGENT_MODEL_ID="$MODEL_ID"
    fi
fi

if ! select_available_port AGENT_PORT "NeMo agent" "$AGENT_PORT_MAX"; then
    echo -e "  ${FAIL} ${RED}Could not reserve a port for the NeMo agent${RESET}"
    exit 1
fi

echo -e "  ${ARROW} NeMo agent             ${DIM}http://localhost:${AGENT_PORT}${RESET}"
echo -e "  ${ARROW} Mercure                ${DIM}http://localhost:${MERCURE_PORT}${RESET}"
echo -e "     ${DIM}Building/starting containers. First run can take a while.${RESET}"
echo -e "     ${DIM}Detailed Docker log: ${COMPOSE_LOG}${RESET}"

DOCKER_STACK_STARTED=true
if start_compose_stack; then
    MERCURE_DOCKER=true
    follow_agent_logs
else
    show_compose_failure_details
    cleanup 1
fi

# Interleaved health-check loop: poll agent and Mercure concurrently
AGENT_READY=false
MERCURE_READY=false
for i in $(seq 1 90); do
    if [[ "$AGENT_READY" != "true" ]]; then
        if curl -sf "http://localhost:${AGENT_PORT}/health" >/dev/null 2>&1; then
            echo -e "  ${ARROW} NeMo agent             ${PASS}  ${DIM}ready${RESET}"
            AGENT_READY=true
        fi
    fi
    if [[ "$MERCURE_READY" != "true" ]]; then
        mercure_status=$(curl -so /dev/null -w "%{http_code}" \
            --connect-timeout 2 "http://localhost:${MERCURE_PORT}/.well-known/mercure" 2>/dev/null) || true
        if [[ "$mercure_status" =~ ^(200|401|400)$ ]]; then
            echo -e "  ${ARROW} Mercure                ${PASS}  ${DIM}ready (streaming enabled)${RESET}"
            MERCURE_READY=true
        fi
    fi
    # Both ready - break early
    [[ "$AGENT_READY" == "true" && "$MERCURE_READY" == "true" ]] && break
    # Timeout handling
    if [[ $i -eq 90 ]]; then
        if [[ "$AGENT_READY" != "true" ]]; then
            echo -e "  ${ARROW} NeMo agent             ${FAIL}  ${RED}failed to start${RESET}"
            echo -e "     ${DIM}See log: ${AGENT_LOG}${RESET}"
            cleanup 1
        fi
        if [[ "$MERCURE_READY" != "true" ]]; then
            echo -e "  ${ARROW} Mercure                ${FAIL}  ${RED}failed to start${RESET}"
            echo -e "     ${DIM}Check: docker compose logs mercure${RESET}"
            cleanup 1
        fi
    fi
    sleep 1
done

# ── 4. PHP Symfony ──────────────────────────────────────────────────
# IMPORTANT: Do NOT export environment variables for Symfony here.
#
# The PHP built-in server does NOT pass shell environment variables into
# PHP's $_SERVER or $_ENV superglobals (variables_order=GPCS, no 'E').
# Symfony's DotEnv component reads .env directly and ignores shell exports.
#
# All Symfony configuration lives in .env (local dev defaults).
# Docker Compose overrides specific values via its environment: block.
#
# Verify AGENT_ENDPOINT / NEMO_WEBSOCKET_URL / MERCURE URLs / stream format in .env match local dev:
>>>>>>> Stashed changes
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
