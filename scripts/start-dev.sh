#!/bin/bash
# =============================================================================
# Start Dev - Daily lightweight startup for Ambient Scribe
# =============================================================================
# Usage: ./scripts/start-dev.sh
#
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
#   NEMO_SESSION_ENGINE - Transcription engine (default here: 'streaming', the
#                         M22 session-long identity engine; set 'windowed' to
#                         compare against the legacy per-window engine)
#   AGENT_PORT     - NeMo agent starting port (default: 48101)
#   AGENT_PORT_MAX - Highest NeMo agent port to try (default: 48110)
#   APP_PORT       - PHP app starting port (default: 48082)
#   APP_PORT_MAX   - Highest PHP app port to try (default: 48090)
#   MERCURE_PORT   - Mercure hub port (default: 48137)
#   OLLAMA_HOST    - Ollama URL (default: http://localhost:11434)
#   OLLAMA_MODEL   - Model name (default: from .env or qwen3.5:9b)
#   START_DEV_LOG_TAIL - Historical nemo-agent log lines to show before
#                        following new logs (default: 0; use 'all' for full)
#
# Press Ctrl+C to stop all services and containers started by this script.
# =============================================================================

set -uo pipefail

source "$(dirname "${BASH_SOURCE[0]}")/env-detect.sh"

# ── Configurable ports ──────────────────────────────────────────────
AGENT_PORT="${AGENT_PORT:-48101}"
AGENT_PORT_MAX="${AGENT_PORT_MAX:-48110}"
APP_PORT="${APP_PORT:-48082}"
APP_PORT_MAX="${APP_PORT_MAX:-48090}"
MERCURE_PORT="${MERCURE_PORT:-48137}"

# ── Role inference provider & Ollama defaults ──────────────────────
MODEL_PROVIDER="${ROLE_AGENT_MODEL_PROVIDER:-${MODEL_PROVIDER:-ollama}}"
OLLAMA_MODEL="${ROLE_AGENT_OLLAMA_MODEL:-${OLLAMA_MODEL:-qwen3.5:9b}}"

# The ollama container sits behind a compose profile; only the ollama
# provider needs it. Bedrock setups start the stack without it. The agent is
# pinned to the compose service (OLLAMA_HOST=http://ollama:11434 in
# docker-compose.yml), so model checks must target that service - a host
# Ollama at localhost:11434 is invisible to the agent.
if [[ "$MODEL_PROVIDER" == "ollama" ]]; then
    export COMPOSE_PROFILES="${COMPOSE_PROFILES:-ollama}"
fi

# ── Cleanup on Ctrl+C ──────────────────────────────────────────────
cleanup() {
    # Failure paths pass a non-zero code; exiting 0 there would make a broken
    # start look successful to setup scripts and CI wrappers.
    local exit_code="${1:-0}"
    echo ""
    echo -e "${DIM}  Stopping containers...${RESET}"
    dc stop >/dev/null 2>&1 || true
    echo -e "  ${PASS} Containers stopped (preserved for fast restart)"
    echo -e "  ${DIM}Remove with: dc down${RESET}"
    exit "$exit_code"
}
trap cleanup SIGINT SIGTERM

# ── Helpers ─────────────────────────────────────────────────────────
ERRORS=0

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
    echo -e "${BOLD}  Ambient Scribe - Dev Server${RESET}"
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

        # Containers without a HEALTHCHECK (e.g., Mercure) - treat "running" as ready
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
    fail "not running - start Docker Desktop or dockerd"
    echo ""
    exit 1
else
    pass
fi

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

    # The agent only reaches the compose ollama service, so the model check
    # runs after `dc up` against that service (see "Model in compose ollama"
    # below). A host Ollama at localhost:11434 is not used by the stack.
    echo -e "  ${BOLD}Ollama provider${RESET} ${DIM}compose service; model verified after start${RESET}"

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
NEMO_MODEL_PROVIDER="${NEMO_MODEL_PROVIDER:-local}"

# GPU is required - NeMo transcription is the core feature
if [[ "$HAS_NVIDIA_SMI" != "true" && "$NEMO_MODEL_PROVIDER" == "local" ]]; then
    # The tooling exists but no adapter answered: WSL2 dropped the GPU, and the
    # nemo-agent container would fail with "no adapters were found".
    if [[ "$NVIDIA_SMI_PRESENT" == "true" ]]; then
        echo -e "  ${FAIL} ${RED}nvidia-smi reports NO adapter - WSL2 lost the GPU${RESET}"
        echo -e "     ${DIM}Fix from Windows (not inside WSL): quit Docker Desktop, run 'wsl --shutdown',${RESET}"
        echo -e "     ${DIM}start Docker Desktop again, then re-run this script. Reboot Windows if it persists.${RESET}"
        exit 1
    fi
    echo -e "  ${FAIL} ${RED}NVIDIA GPU required for NeMo transcription${RESET}"
    echo -e "     ${DIM}Install NVIDIA Container Toolkit: https://docs.nvidia.com/datacenter/cloud-native/${RESET}"
    echo -e "     ${DIM}Or set NEMO_MODEL_PROVIDER=mock in .env for test/development only${RESET}"
    exit 1
fi

export NEMO_MODEL_PROVIDER
# Daily dev runs the session-long streaming engine (M22) so speaker identity
# cannot swap mid-visit; compose/CI keep the windowed default until Phase 4
# flips it. Override with NEMO_SESSION_ENGINE=windowed for A/B comparisons.
# The mock pipeline cannot construct a streaming engine (it raises at the
# first WebSocket connect), so mock runs default to the windowed path.
if [[ "$NEMO_MODEL_PROVIDER" == "mock" ]]; then
    export NEMO_SESSION_ENGINE="${NEMO_SESSION_ENGINE:-windowed}"
else
    export NEMO_SESSION_ENGINE="${NEMO_SESSION_ENGINE:-streaming}"
fi
export AGENT_PORT
export APP_PORT
export MERCURE_PORT
[[ -n "${MERCURE_JWT_SECRET:-}" ]] && export MERCURE_JWT_SECRET
[[ -n "${NEMO_STREAM_INPUT_FORMAT:-}" ]] && export NEMO_STREAM_INPUT_FORMAT
export ROLE_AGENT_MODEL_PROVIDER="$MODEL_PROVIDER"

if [[ "$MODEL_PROVIDER" == "ollama" ]]; then
    # OLLAMA_HOST is pinned to the compose service in docker-compose.yml.
    export ROLE_AGENT_OLLAMA_MODEL="${ROLE_AGENT_OLLAMA_MODEL:-$OLLAMA_MODEL}"
else
    # Pass through AWS credentials for Bedrock
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
step ".env file"
if [[ -f "$REPO_ROOT/.env" ]]; then
    pass
else
    fail "not found - run: cp .env.example .env"
    echo ""
    exit 1
fi

step "nvidia-smi"
# A named adapter means live transcription can run on the GPU.
if [[ "$HAS_NVIDIA_SMI" == "true" ]]; then
    pass "${GPU_NAME}"
# Mock mode never touches the GPU, so a missing adapter is only a warning.
elif [[ "$NEMO_MODEL_PROVIDER" == "mock" ]]; then
    echo -e "${WARN}  ${DIM}no GPU - using mock NeMo pipeline (scenarios will work, live transcription won't)${RESET}"
# The driver tooling exists but no adapter answered: the WSL2 GPU has dropped
# and the nemo-agent container would fail with "no adapters were found".
elif [[ "$NVIDIA_SMI_PRESENT" == "true" ]]; then
    fail "nvidia-smi reports NO adapter - WSL2 lost the GPU"
    echo -e "     ${DIM}Fix from Windows (not inside WSL): quit Docker Desktop, run 'wsl --shutdown',${RESET}"
    echo -e "     ${DIM}start Docker Desktop again, then re-run this script. Reboot Windows if it persists.${RESET}"
    echo ""
    exit 1
else
    fail "not found - GPU required for NeMo (set NEMO_MODEL_PROVIDER=mock for UI-only testing)"
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
if [[ "$RUNNING_COUNT" -eq "$EXPECTED_COUNT" && "$EXPECTED_COUNT" -gt 0 ]]; then
    ALL_RUNNING=true
    pass "all ${RUNNING_COUNT}/${EXPECTED_COUNT} running - skipping to health checks"
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
        fail "images missing - run ./scripts/setup-initial.sh for first-time setup"
        echo ""
        exit 1
    fi

    echo -e "     ${DIM}Running: dc up -d${RESET}"
    if dc up -d; then
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

# The agent talks only to the compose ollama service, so the model must exist
# in that service's volume; a fresh ollama_data volume starts empty.
if [[ "$MODEL_PROVIDER" == "ollama" ]]; then
    step "Model ${OLLAMA_MODEL}"
    OLLAMA_EXPECTED_TAG="$OLLAMA_MODEL"
    [[ "$OLLAMA_MODEL" != *:* ]] && OLLAMA_EXPECTED_TAG="${OLLAMA_MODEL}:latest"
    if dc exec -T ollama ollama list 2>/dev/null | awk 'NR>1 {print $1}' \
        | grep -Fxq -e "$OLLAMA_MODEL" -e "$OLLAMA_EXPECTED_TAG"; then
        pass "available in compose ollama"
    else
        echo -e "${YELLOW}pulling into compose ollama...${RESET}"
        echo -e "     ${DIM}This may take a while on first run${RESET}"
        if dc exec -T ollama ollama pull "$OLLAMA_MODEL"; then
            echo -e "  ${ARROW} Model ${OLLAMA_MODEL}     ${PASS}  ${DIM}ready${RESET}"
        else
            echo -e "  ${ARROW} Model ${OLLAMA_MODEL}     ${FAIL}  ${RED}pull failed - check the model name, network, and disk space${RESET}"
            cleanup 1
        fi
    fi
fi

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
echo -e "    ${ARROW} Scribe UI:     ${BOLD}http://localhost:${APP_PORT}/scribe${RESET}"
echo -e "    ${ARROW} NeMo agent:    ${BOLD}http://localhost:${AGENT_PORT}${RESET}  ${DIM}engine: ${NEMO_SESSION_ENGINE}${RESET}"
echo -e "    ${ARROW} Mercure:       ${BOLD}http://localhost:${MERCURE_PORT}${RESET}"
echo ""
echo -e "  ${DIM}Useful commands:${RESET}"
echo -e "    ${ARROW} Health check:  ${DIM}./scripts/health-checks.sh${RESET}"
echo -e "    ${ARROW} Rebuild:       ${DIM}dc up -d --build${RESET}"
echo -e "    ${ARROW} Service logs:  ${DIM}dc logs -f <service>${RESET}"
echo -e "    ${ARROW} Stop:          ${DIM}dc stop${RESET}"
echo -e "    ${ARROW} Remove:        ${DIM}dc down${RESET}"
echo ""

# =============================================================================
# STEP 6: Stream Logs + Wait
# =============================================================================
START_DEV_LOG_TAIL="${START_DEV_LOG_TAIL:-0}"
if ! [[ "$START_DEV_LOG_TAIL" =~ ^([0-9]+|all)$ ]]; then
    echo -e "  ${YELLOW}${BOLD}Warning:${RESET} ${DIM}Invalid START_DEV_LOG_TAIL='${START_DEV_LOG_TAIL}', using 0${RESET}"
    START_DEV_LOG_TAIL=0
fi

echo -e "  ${DIM}Streaming new nemo-agent logs (Ctrl+C to stop; START_DEV_LOG_TAIL=N for history)...${RESET}"
echo ""

dc logs -f --tail="$START_DEV_LOG_TAIL" nemo-agent 2>&1 | while IFS= read -r line; do
    case "$line" in
        *"/health"*) ;; # skip Docker healthcheck spam
        *WARNING*|*ERROR*|*Traceback*|*"POST "*|*"GET "*|*WebSocket*|*"model"*|*"loaded"*)
            echo -e "    ${CYAN}[nemo]${RESET} $line" ;;
    esac
done &
TAIL_PID=$!

# Wait - Ctrl+C triggers cleanup
wait $TAIL_PID 2>/dev/null
