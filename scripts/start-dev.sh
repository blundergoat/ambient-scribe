#!/bin/bash
# =============================================================================
# Start Dev - Launches local PHP + Ollama with Dockerized NeMo/Mercure
# =============================================================================
# Usage: ./scripts/start-dev.sh
#
# Starts services based on ROLE_AGENT_MODEL_PROVIDER
# (legacy MODEL_PROVIDER is still accepted):
#
#   ollama (default):
#     1. Ollama (auto-starts via binary or Docker if needed)
#     2. NeMo FastAPI agent via Docker Compose on the first free port in 8001-8010
#     3. Mercure hub via Docker Compose on port 3701
#     4. PHP Symfony dev server on port 8082
#
#   bedrock:
#     1. NeMo FastAPI agent via Docker Compose on the first free port in 8001-8010
#     2. Mercure hub via Docker Compose on port 3701
#     3. PHP Symfony dev server on port 8082
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
#   AGENT_PORT     - NeMo agent starting port (default: 8001)
#   AGENT_PORT_MAX - Highest NeMo agent port to try (default: 8010)
#   APP_PORT       - PHP app starting port (default: 8082)
#   APP_PORT_MAX   - Highest PHP app port to try (default: 8090)
#   MERCURE_PORT   - Mercure hub port (default: 3701)
#   OLLAMA_HOST    - Ollama URL (default: http://localhost:11434)
#   OLLAMA_MODEL   - Model name (default: from .env or qwen2.5:14b)
#
# Press Ctrl+C to stop all services and containers started by this script.
# =============================================================================

set -uo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
COMPOSE_FILE="$REPO_ROOT/docker-compose.yml"

# ── Configurable ports ──────────────────────────────────────────────
AGENT_PORT="${AGENT_PORT:-8001}"
AGENT_PORT_MAX="${AGENT_PORT_MAX:-8010}"
APP_PORT="${APP_PORT:-8082}"
APP_PORT_MAX="${APP_PORT_MAX:-8090}"
MERCURE_PORT="${MERCURE_PORT:-3701}"

if (( AGENT_PORT_MAX < AGENT_PORT )); then
    AGENT_PORT_MAX="$AGENT_PORT"
fi

if (( APP_PORT_MAX < APP_PORT )); then
    APP_PORT_MAX="$APP_PORT"
fi

# ── Colors ──────────────────────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
DIM='\033[2m'
BOLD='\033[1m'
RESET='\033[0m'

PASS="${GREEN}✔${RESET}"
FAIL="${RED}✘${RESET}"
ARROW="${BLUE}▸${RESET}"

TRANSCRIPTION_MODE="docker"
TRANSCRIPTION_NOTE="NeMo agent runs via docker compose"
DOCKER_STACK_STARTED=false
OLLAMA_HOST_LOCAL=""
OLLAMA_HOST_DOCKER=""

# ── Log directory ──────────────────────────────────────────────────
LOG_DIR="$REPO_ROOT/var/log/dev"
mkdir -p "$LOG_DIR"
find "$LOG_DIR" -name "*.log" -mtime +7 -delete 2>/dev/null || true
TIMESTAMP="$(date +%Y%m%d-%H%M%S)"
PHP_LOG="$LOG_DIR/php-${TIMESTAMP}.log"
AGENT_LOG="$LOG_DIR/agent-${TIMESTAMP}.log"
COMPOSE_LOG="$LOG_DIR/compose-${TIMESTAMP}.log"

# ── Track child PIDs and state for cleanup ─────────────────────────
PIDS=()
OLLAMA_DOCKER=false
MERCURE_DOCKER=false

cleanup() {
    local exit_code="${1:-0}"

    echo ""
    echo -e "${DIM}  Shutting down...${RESET}"
    for pid in "${PIDS[@]}"; do
        kill "$pid" 2>/dev/null || true
    done
    # Poll until all children exit, force-kill after 5 seconds
    local deadline=$(( SECONDS + 5 ))
    while (( SECONDS < deadline )); do
        local still_running=false
        for pid in "${PIDS[@]}"; do
            if kill -0 "$pid" 2>/dev/null; then
                still_running=true
                break
            fi
        done
        $still_running || break
        sleep 0.2
    done
    # Force-kill any stragglers
    for pid in "${PIDS[@]}"; do
        if kill -0 "$pid" 2>/dev/null; then
            kill -9 "$pid" 2>/dev/null || true
            wait "$pid" 2>/dev/null || true
        fi
    done
    # Stop Docker containers we started
    if [[ "$DOCKER_STACK_STARTED" == "true" ]]; then
        docker compose -f "$COMPOSE_FILE" down >/dev/null 2>&1 || true
    fi
    if [[ "$OLLAMA_DOCKER" == "true" ]]; then
        docker stop ollama >/dev/null 2>&1 || true
    fi
    echo -e "  ${PASS} All services stopped"
    # Show log paths if logs were written
    if [[ -s "$PHP_LOG" || -s "$AGENT_LOG" || -s "$COMPOSE_LOG" ]]; then
        echo ""
        echo -e "  ${DIM}Logs:${RESET}"
        [[ -s "$PHP_LOG" ]]   && echo -e "    ${ARROW} PHP:   ${DIM}${PHP_LOG}${RESET}"
        [[ -s "$AGENT_LOG" ]] && echo -e "    ${ARROW} Agent: ${DIM}${AGENT_LOG}${RESET}"
        [[ -s "$COMPOSE_LOG" ]] && echo -e "    ${ARROW} Docker:${DIM} ${COMPOSE_LOG}${RESET}"
    fi
    exit "$exit_code"
}

trap 'cleanup 130' SIGINT SIGTERM

header() {
    echo ""
    echo -e "${BOLD}  Ambient Scribe - Dev Server${RESET}"
    echo -e "  ${DIM}$(printf '─%.0s' {1..44})${RESET}"
    echo ""
}

# ── Preflight ───────────────────────────────────────────────────────
header
STARTUP_START=$SECONDS

docker_compose() {
    docker compose -f "$COMPOSE_FILE" "$@"
}

stop_named_container_if_present() {
    local container_name="$1"
    local label="$2"

    if ! docker ps -a --filter "name=^${container_name}$" --format "{{.Names}}" 2>/dev/null | grep -q "^${container_name}$"; then
        return 0
    fi

    echo -e "  ${YELLOW}${BOLD}Legacy ${label} container detected${RESET}"
    if docker rm -f "$container_name" >/dev/null 2>&1; then
        echo -e "  ${ARROW} ${label}               ${PASS}  ${DIM}removed (${container_name})${RESET}"
        return 0
    fi

    echo -e "  ${ARROW} ${label}               ${FAIL}  ${RED}could not remove ${container_name}${RESET}"
    return 1
}

port_pids() {
    local port="$1"

    if command -v lsof &>/dev/null; then
        lsof -tiTCP:"$port" -sTCP:LISTEN 2>/dev/null || true
        return
    fi

    if command -v fuser &>/dev/null; then
        fuser "${port}/tcp" 2>/dev/null | tr ' ' '\n' | sed '/^$/d' || true
    fi
}

port_is_in_use() {
    local port="$1"

    if command -v ss &>/dev/null; then
        if ss -Hltn 2>/dev/null | awk '{print $4}' | grep -Eq "(^|:)$port$"; then
            return 0
        fi
    fi

    [[ -n "$(port_pids "$port")" ]]
}

find_available_port() {
    local start_port="$1"
    local end_port="$2"
    local candidate

    for candidate in $(seq "$start_port" "$end_port"); do
        if ! port_is_in_use "$candidate"; then
            echo "$candidate"
            return 0
        fi
    done

    return 1
}

port_usage_hint() {
    local port="$1"

    if command -v lsof &>/dev/null; then
        lsof -nP -iTCP:"$port" -sTCP:LISTEN 2>/dev/null | awk 'NR > 1 { printf "%s(pid=%s) ", $1, $2 }'
        return
    fi

    if command -v ss &>/dev/null; then
        ss -Hltpn "sport = :$port" 2>/dev/null | awk '{print $NF}' | paste -sd', ' -
    fi
}

select_available_port() {
    local var_name="$1"
    local label="$2"
    local range_end="$3"
    local port="${!var_name}"
    local next_port=""

    if ! port_is_in_use "$port"; then
        return 0
    fi

    if (( range_end <= port )); then
        return 1
    fi

    next_port="$(find_available_port "$((port + 1))" "$range_end")" || return 1
    printf -v "$var_name" '%s' "$next_port"

    echo -e "  ${YELLOW}${BOLD}${label} port ${port} still busy${RESET}"
    echo -e "     ${DIM}Using port ${next_port} instead (range ${port}-${range_end}).${RESET}"
}

require_port_free() {
    local port="$1"
    local label="$2"
    local usage_hint=""

    if ! port_is_in_use "$port"; then
        return 0
    fi

    echo -e "  ${FAIL} ${RED}${label} port ${port} is already in use${RESET}"
    usage_hint="$(port_usage_hint "$port")"
    if [[ -n "$usage_hint" ]]; then
        echo -e "     ${DIM}Current listener: ${usage_hint}${RESET}"
    fi
    return 1
}

dockerize_ollama_host() {
    local host="$1"
    local port
    local host_ip

    case "$host" in
        http://localhost:*)
            port="${host##*:}"
            host_ip="$(detect_host_primary_ip)"
            if [[ -n "$host_ip" ]] && curl -sf "http://${host_ip}:${port}/api/tags" >/dev/null 2>&1; then
                echo "http://${host_ip}:${port}"
            else
                echo "http://host.docker.internal:${port}"
            fi
            ;;
        http://127.0.0.1:*)
            port="${host##*:}"
            host_ip="$(detect_host_primary_ip)"
            if [[ -n "$host_ip" ]] && curl -sf "http://${host_ip}:${port}/api/tags" >/dev/null 2>&1; then
                echo "http://${host_ip}:${port}"
            else
                echo "http://host.docker.internal:${port}"
            fi
            ;;
        http://0.0.0.0:*)
            port="${host##*:}"
            host_ip="$(detect_host_primary_ip)"
            if [[ -n "$host_ip" ]] && curl -sf "http://${host_ip}:${port}/api/tags" >/dev/null 2>&1; then
                echo "http://${host_ip}:${port}"
            else
                echo "http://host.docker.internal:${port}"
            fi
            ;;
        *)
            echo "$host"
            ;;
    esac
}

detect_host_primary_ip() {
    if command -v ip &>/dev/null; then
        ip route get 1 2>/dev/null | awk '
            {
                for (i = 1; i <= NF; i++) {
                    if ($i == "src") {
                        print $(i + 1)
                        exit
                    }
                }
            }
        '
        return
    fi

    hostname -I 2>/dev/null | awk '{print $1}'
}

detect_running_ollama_host() {
    local pid
    local configured_host

    while IFS= read -r pid; do
        configured_host="$(tr '\0' '\n' <"/proc/${pid}/environ" 2>/dev/null | grep -E '^OLLAMA_HOST=' | head -1 | cut -d= -f2-)"

        if [[ -z "$configured_host" ]]; then
            continue
        fi

        case "$configured_host" in
            http://*)
                echo "$configured_host"
                return 0
                ;;
            0.0.0.0:*)
                echo "http://localhost:${configured_host##*:}"
                return 0
                ;;
            127.0.0.1:*|localhost:*)
                echo "http://localhost:${configured_host##*:}"
                return 0
                ;;
        esac
    done < <(pgrep -f 'ollama serve' 2>/dev/null || true)

    return 1
}

export_aws_profile_credentials() {
    if [[ -z "${AWS_PROFILE:-}" || -n "${AWS_ACCESS_KEY_ID:-}" ]]; then
        return 0
    fi

    if ! command -v aws &>/dev/null; then
        echo -e "  ${FAIL} ${RED}AWS CLI not found${RESET}"
        echo -e "     ${DIM}Install AWS CLI or export AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY.${RESET}"
        return 1
    fi

    local exported
    if ! exported="$(AWS_PAGER='' aws configure export-credentials --profile "$AWS_PROFILE" --format env 2>/dev/null)"; then
        echo -e "  ${FAIL} ${RED}Could not export AWS credentials for profile ${AWS_PROFILE}${RESET}"
        echo -e "     ${DIM}Run aws sso login --profile ${AWS_PROFILE}, or export static AWS credentials.${RESET}"
        return 1
    fi

    eval "$exported"
    export AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY AWS_SESSION_TOKEN
}

follow_agent_logs() {
    docker_compose logs -f --tail=0 nemo-agent \
        > >(while IFS= read -r line; do
            echo "$line" >> "$AGENT_LOG"
            echo -e "    ${CYAN}[agent]${RESET} $line"
        done) \
        2>&1 &
    PIDS+=($!)
}

upsert_env_value() {
    local key="$1"
    local value="$2"
    local file="$REPO_ROOT/.env"

    if grep -qE "^${key}=" "$file" 2>/dev/null; then
        sed -i "s|^${key}=.*|${key}=${value}|" "$file"
    else
        printf '\n%s=%s\n' "$key" "$value" >> "$file"
    fi
}

show_compose_failure_details() {
    echo -e "  ${ARROW} Docker compose         ${FAIL}  ${RED}nemo-agent/mercure failed to start${RESET}"
    echo -e "     ${DIM}See log: ${COMPOSE_LOG}${RESET}"

    if grep -Eqi 'could not select device driver|nvidia-container-toolkit|capabilities: \[\[gpu\]\]|no NVIDIA driver|nvidia-smi' "$COMPOSE_LOG"; then
        echo -e "     ${DIM}GPU runtime is unavailable. Install NVIDIA Container Toolkit and verify Docker GPU access:${RESET}"
        echo -e "     ${DIM}docker run --rm --gpus all nvidia/cuda:12.4.1-base-ubuntu22.04 nvidia-smi${RESET}"
    fi

    if grep -Eqi 'port is already allocated|address already in use|bind: address already in use' "$COMPOSE_LOG"; then
        echo -e "     ${DIM}A required port is still busy. Check AGENT_PORT=${AGENT_PORT}, APP_PORT=${APP_PORT}, MERCURE_PORT=${MERCURE_PORT}.${RESET}"
    fi

    if [[ -s "$COMPOSE_LOG" ]]; then
        echo -e "     ${DIM}Last compose log lines:${RESET}"
        tail -n 15 "$COMPOSE_LOG" | while IFS= read -r line; do
            echo -e "       ${DIM}${line}${RESET}"
        done
    fi
}

start_compose_stack() {
    : > "$COMPOSE_LOG"

    BUILDKIT_PROGRESS=plain docker_compose up -d --build nemo-agent mercure >"$COMPOSE_LOG" 2>&1 &
    local compose_pid=$!
    PIDS+=("$compose_pid")

    local -a spinner=('|' '/' '-' '\\')
    local elapsed=0
    local index=0

    while kill -0 "$compose_pid" 2>/dev/null; do
        printf "\r  ${ARROW} Docker build/start     ${DIM}%s %ss elapsed${RESET}" "${spinner[$((index % ${#spinner[@]}))]}" "$elapsed"
        sleep 2
        elapsed=$((elapsed + 2))
        index=$((index + 1))
    done

    wait "$compose_pid"
    local status=$?
    local remaining_pids=()
    local pid
    for pid in "${PIDS[@]}"; do
        [[ "$pid" != "$compose_pid" ]] && remaining_pids+=("$pid")
    done
    PIDS=("${remaining_pids[@]}")
    printf "\r\033[K"
    return "$status"
}

# ── Docker checks / cleanup ────────────────────────────────────────
if ! command -v docker &>/dev/null; then
    echo -e "  ${FAIL} ${RED}Docker is required for the NeMo agent${RESET}"
    echo -e "     ${DIM}Install Docker and docker compose, then retry.${RESET}"
    exit 1
fi

if ! docker compose version >/dev/null 2>&1; then
    echo -e "  ${FAIL} ${RED}docker compose is required${RESET}"
    echo -e "     ${DIM}Install the Docker Compose plugin, then retry.${RESET}"
    exit 1
fi

running=$(docker ps --filter "name=ambient-scribe" --format "{{.Names}}" 2>/dev/null || true)
if [[ -n "$running" ]]; then
    echo -e "  ${YELLOW}${BOLD}Docker containers detected${RESET}"
    echo ""
    while IFS= read -r c; do
        echo -e "    ${ARROW} ${c}"
    done <<< "$running"
    echo ""
    echo -e "  ${ARROW} Running ${BOLD}docker compose down${RESET}..."
    if docker_compose down >/dev/null 2>&1; then
        echo -e "  ${ARROW} Docker containers      ${PASS}  ${DIM}stopped${RESET}"
    else
        echo -e "  ${ARROW} Docker containers      ${FAIL}  ${RED}failed to stop${RESET}"
        echo -e "     ${DIM}Free ports ${AGENT_PORT}, ${APP_PORT}, ${MERCURE_PORT} manually before retrying${RESET}"
        exit 1
    fi
    echo ""
fi

if ! stop_named_container_if_present "goat-mercure-dev" "Mercure"; then
    exit 1
fi

# ── Free agent/app ports early to avoid bind failures ──────────────
if ! select_available_port AGENT_PORT "NeMo agent" "$AGENT_PORT_MAX"; then
    echo -e "  ${FAIL} ${RED}Could not reserve a port for the NeMo agent${RESET}"
    exit 1
fi

if ! select_available_port APP_PORT "PHP app" "$APP_PORT_MAX"; then
    echo -e "  ${FAIL} ${RED}Could not reserve a port for the PHP app${RESET}"
    exit 1
fi

if ! require_port_free "$MERCURE_PORT" "Mercure"; then
    echo -e "     ${DIM}Stop the conflicting listener or change MERCURE_PORT before retrying.${RESET}"
    exit 1
fi

# Bootstrap .env if needed so Symfony and docker compose share the same defaults
if [[ ! -f "$REPO_ROOT/.env" ]]; then
    if [[ -f "$REPO_ROOT/.env.example" ]]; then
        cp "$REPO_ROOT/.env.example" "$REPO_ROOT/.env"
        echo -e "  ${ARROW} .env                   ${PASS}  ${DIM}created from .env.example${RESET}"
    else
        echo -e "  ${FAIL} ${RED}.env is missing and .env.example was not found${RESET}"
        exit 1
    fi
fi

# Check local PHP deps exist - install if missing
if [[ ! -f "$REPO_ROOT/vendor/autoload.php" ]]; then
    echo -e "  ${YELLOW}${BOLD}Dependencies missing${RESET}"
    [[ ! -f "$REPO_ROOT/vendor/autoload.php" ]] && echo -e "    ${ARROW} PHP vendor/ not found"
    echo ""
    echo -e "  ${ARROW} Running ${BOLD}./scripts/dependencies-install.sh --php${RESET}..."
    echo ""
    if "$REPO_ROOT/scripts/dependencies-install.sh" --php; then
        echo ""
        echo -e "  ${PASS} Dependencies installed - continuing startup"
        echo ""
    else
        echo ""
        echo -e "  ${FAIL} ${RED}Dependency install failed - fix errors above and retry${RESET}"
        exit 1
    fi
fi

# ── Load .env defaults ──────────────────────────────────────────────
# Helper: read a var from .env if not already set in the environment
env_default() {
    local var="$1" fallback="$2"
    if [[ -z "${!var:-}" && -f "$REPO_ROOT/.env" ]]; then
        local val
        val="$(grep -E "^[[:space:]]*${var}=" "$REPO_ROOT/.env" 2>/dev/null | head -1 | cut -d= -f2-)"
        printf -v "$var" '%s' "${val:-$fallback}"
    else
        printf -v "$var" '%s' "${!var:-$fallback}"
    fi
}

env_default MODEL_PROVIDER  ""
env_default ROLE_AGENT_MODEL_PROVIDER ""
env_default OLLAMA_MODEL    "qwen2.5:14b"
env_default OLLAMA_HOST     "http://localhost:11434"
env_default ROLE_AGENT_MODEL_ID ""
env_default ROLE_AGENT_OLLAMA_MODEL ""

# Mercure vars - needed for streaming mode
env_default MERCURE_JWT_SECRET  ""
env_default MERCURE_URL         ""
env_default MERCURE_PUBLIC_URL  ""
env_default NEMO_MODEL_PROVIDER "local"
env_default NEMO_STREAM_INPUT_FORMAT "pcm"

# Bedrock vars - only needed when MODEL_PROVIDER=bedrock, but load them
# so the validation section can check them without requiring export-before-run
env_default AWS_ACCESS_KEY_ID     ""
env_default AWS_SECRET_ACCESS_KEY ""
env_default AWS_SESSION_TOKEN     ""
env_default AWS_DEFAULT_REGION    ""
env_default AWS_PROFILE           ""
env_default MODEL_ID              ""

if [[ -n "${ROLE_AGENT_MODEL_PROVIDER:-}" ]]; then
    if [[ -n "${MODEL_PROVIDER:-}" && "$MODEL_PROVIDER" != "$ROLE_AGENT_MODEL_PROVIDER" ]]; then
        echo -e "  ${YELLOW}${BOLD}Warning:${RESET} ${DIM}Ignoring legacy MODEL_PROVIDER=${MODEL_PROVIDER} in favor of ROLE_AGENT_MODEL_PROVIDER=${ROLE_AGENT_MODEL_PROVIDER}.${RESET}"
    fi
    MODEL_PROVIDER="$ROLE_AGENT_MODEL_PROVIDER"
elif [[ -n "${MODEL_PROVIDER:-}" ]]; then
    ROLE_AGENT_MODEL_PROVIDER="$MODEL_PROVIDER"
else
    MODEL_PROVIDER="ollama"
    ROLE_AGENT_MODEL_PROVIDER="ollama"
fi

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
    if echo "$OLLAMA_TAGS_CACHE" | grep -q "\"${OLLAMA_MODEL}\""; then
        echo -e "${PASS}  ${DIM}available${RESET}"
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
if [[ -f "$REPO_ROOT/.env" ]]; then
    EXPECTED_ENDPOINT="http://localhost:${AGENT_PORT}"
    EXPECTED_WS_URL="ws://localhost:${AGENT_PORT}"
    EXPECTED_MERCURE_URL="http://localhost:${MERCURE_PORT}/.well-known/mercure"
    EXPECTED_STREAM_INPUT_FORMAT="pcm"
    CURRENT_ENDPOINT=$(grep -E '^AGENT_ENDPOINT=' "$REPO_ROOT/.env" 2>/dev/null | head -1 | cut -d= -f2-)
    CURRENT_WS_URL=$(grep -E '^NEMO_WEBSOCKET_URL=' "$REPO_ROOT/.env" 2>/dev/null | head -1 | cut -d= -f2-)
    CURRENT_MERCURE_INTERNAL_URL=$(grep -E '^MERCURE_URL=' "$REPO_ROOT/.env" 2>/dev/null | head -1 | cut -d= -f2-)
    CURRENT_MERCURE_URL=$(grep -E '^MERCURE_PUBLIC_URL=' "$REPO_ROOT/.env" 2>/dev/null | head -1 | cut -d= -f2-)
    CURRENT_STREAM_INPUT_FORMAT=$(grep -E '^NEMO_STREAM_INPUT_FORMAT=' "$REPO_ROOT/.env" 2>/dev/null | head -1 | cut -d= -f2-)
    if [[ "$CURRENT_ENDPOINT" != "$EXPECTED_ENDPOINT" ]]; then
        echo -e "  ${YELLOW}${BOLD}Fixing${RESET} AGENT_ENDPOINT in .env → ${DIM}${EXPECTED_ENDPOINT}${RESET}"
        upsert_env_value "AGENT_ENDPOINT" "$EXPECTED_ENDPOINT"
    fi
    if [[ "$CURRENT_WS_URL" != "$EXPECTED_WS_URL" ]]; then
        echo -e "  ${YELLOW}${BOLD}Fixing${RESET} NEMO_WEBSOCKET_URL in .env → ${DIM}${EXPECTED_WS_URL}${RESET}"
        upsert_env_value "NEMO_WEBSOCKET_URL" "$EXPECTED_WS_URL"
    fi
    if [[ "$CURRENT_MERCURE_INTERNAL_URL" != "$EXPECTED_MERCURE_URL" ]]; then
        echo -e "  ${YELLOW}${BOLD}Fixing${RESET} MERCURE_URL in .env → ${DIM}${EXPECTED_MERCURE_URL}${RESET}"
        upsert_env_value "MERCURE_URL" "$EXPECTED_MERCURE_URL"
    fi
    if [[ "$CURRENT_MERCURE_URL" != "$EXPECTED_MERCURE_URL" ]]; then
        echo -e "  ${YELLOW}${BOLD}Fixing${RESET} MERCURE_PUBLIC_URL in .env → ${DIM}${EXPECTED_MERCURE_URL}${RESET}"
        upsert_env_value "MERCURE_PUBLIC_URL" "$EXPECTED_MERCURE_URL"
    fi
    if [[ "$CURRENT_STREAM_INPUT_FORMAT" != "$EXPECTED_STREAM_INPUT_FORMAT" ]]; then
        echo -e "  ${YELLOW}${BOLD}Fixing${RESET} NEMO_STREAM_INPUT_FORMAT in .env → ${DIM}${EXPECTED_STREAM_INPUT_FORMAT}${RESET}"
        upsert_env_value "NEMO_STREAM_INPUT_FORMAT" "$EXPECTED_STREAM_INPUT_FORMAT"
    fi
fi

# The PHP port must stay stable once Mercure is up; if it becomes busy mid-startup,
# fail clearly instead of killing an unrelated listener.
if ! require_port_free "$APP_PORT" "PHP app"; then
    echo -e "  ${FAIL} ${RED}PHP app port ${APP_PORT} became busy while containers were starting${RESET}"
    echo -e "     ${DIM}Free the port and retry so Mercure CORS stays aligned with the app URL.${RESET}"
    cleanup 1
fi

# Clear Symfony cache to avoid stale container issues.
# Use rm -rf instead of bin/console cache:clear - the console command
# itself can fail if the cached container was compiled with broken config.
rm -rf "$REPO_ROOT/var/cache/dev" 2>/dev/null || true
echo -e "  ${ARROW} Symfony cache          ${PASS}  ${DIM}cleared${RESET}"

echo -e "  ${ARROW} PHP app                ${DIM}http://localhost:${APP_PORT}${RESET}"

php -S "0.0.0.0:${APP_PORT}" -t "$REPO_ROOT/public" \
    > >(while IFS= read -r line; do
        # Always write to log file (full detail)
        echo "$line" >> "$PHP_LOG"
        # Console: skip [debug] noise, only show [critical], [error], [warning],
        # [info], HTTP status lines, and server startup/shutdown messages
        case "$line" in
            *"[debug]"*) ;;  # skip debug lines on console
            *) echo -e "    ${GREEN}[php]${RESET}   $line" ;;
        esac
    done) \
    2>&1 &
PHP_SERVER_PID=$!
PIDS+=("$PHP_SERVER_PID")

# Wait for PHP to be ready - check if accepting connections (any HTTP response)
for i in $(seq 1 10); do
    if ! kill -0 "$PHP_SERVER_PID" 2>/dev/null; then
        echo -e "  ${ARROW} PHP app                ${FAIL}  ${RED}process exited before becoming ready${RESET}"
        echo -e "     ${DIM}See log: ${PHP_LOG}${RESET}"
        cleanup 1
    fi
    HTTP_CODE=$(curl -so /dev/null -w "%{http_code}" "http://localhost:${APP_PORT}/" 2>/dev/null) || HTTP_CODE="000"
    if [[ "$HTTP_CODE" != "000" ]]; then
        if [[ "$HTTP_CODE" == "200" ]]; then
            echo -e "  ${ARROW} PHP app                ${PASS}  ${DIM}ready${RESET}"
        else
            echo -e "  ${ARROW} PHP app                ${PASS}  ${DIM}running ${YELLOW}(HTTP ${HTTP_CODE})${RESET}"
            echo -e "     ${DIM}App returned ${HTTP_CODE} - check log: ${PHP_LOG}${RESET}"
        fi
        break
    fi
    if [[ $i -eq 10 ]]; then
        echo -e "  ${ARROW} PHP app                ${FAIL}  ${RED}failed to start${RESET}"
        echo -e "     ${DIM}See log: ${PHP_LOG}${RESET}"
        cleanup 1
    fi
    sleep 1
done

# ── Ready ───────────────────────────────────────────────────────────
echo ""
echo -e "  ${DIM}$(printf '─%.0s' {1..44})${RESET}"
echo ""
STARTUP_ELAPSED=$(( SECONDS - STARTUP_START ))
echo -e "  ${GREEN}${BOLD}Ready!${RESET} ${DIM}(${STARTUP_ELAPSED}s)${RESET}  Open ${BOLD}http://localhost:${APP_PORT}${RESET} in your browser"
echo ""
echo -e "  ${DIM}Services:${RESET}"
echo -e "    ${ARROW} Chat UI:       ${BOLD}http://localhost:${APP_PORT}${RESET}"
echo -e "    ${ARROW} Agent API:     ${BOLD}http://localhost:${AGENT_PORT}${RESET}"
echo -e "    ${ARROW} WebSocket:     ${BOLD}ws://localhost:${AGENT_PORT}${RESET}"

if [[ "$MODEL_PROVIDER" == "ollama" ]]; then
    OLLAMA_LABEL="${OLLAMA_HOST_LOCAL}"
    [[ "$OLLAMA_DOCKER" == "true" ]] && OLLAMA_LABEL="${OLLAMA_HOST_LOCAL} (Docker)"
    echo -e "    ${ARROW} Ollama:        ${BOLD}${OLLAMA_LABEL}${RESET}"
    echo -e "    ${ARROW} Model:         ${BOLD}${OLLAMA_MODEL}${RESET}"
else
    echo -e "    ${ARROW} Provider:      ${BOLD}AWS Bedrock${RESET}"
    echo -e "    ${ARROW} Region:        ${BOLD}${AWS_DEFAULT_REGION:-us-east-1}${RESET}"
fi

if [[ "$MERCURE_DOCKER" == "true" ]]; then
    echo -e "    ${ARROW} Mercure:       ${BOLD}http://localhost:${MERCURE_PORT}${RESET}"
    echo -e "    ${ARROW} Mode:          ${BOLD}streaming${RESET} ${DIM}(real-time tokens via Mercure)${RESET}"
else
    echo -e "    ${ARROW} Mode:          ${BOLD}sync${RESET}${DIM} (set MERCURE_URL in .env to enable streaming)${RESET}"
fi
echo -e "    ${ARROW} Transcription: ${BOLD}${TRANSCRIPTION_MODE}${RESET}"
echo -e "    ${ARROW} Note:          ${DIM}${TRANSCRIPTION_NOTE}${RESET}"
echo ""
echo -e "  ${DIM}Logs:${RESET}"
echo -e "    ${ARROW} PHP:           ${DIM}${PHP_LOG}${RESET}"
echo -e "    ${ARROW} Agent:         ${DIM}${AGENT_LOG}${RESET}"
echo -e "    ${ARROW} Docker:        ${DIM}${COMPOSE_LOG}${RESET}"
echo ""
echo -e "  ${DIM}Press Ctrl+C to stop all services${RESET}"
echo ""

# ── Wait for all children ───────────────────────────────────────────
wait
