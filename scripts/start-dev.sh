#!/bin/bash
# =============================================================================
# Start Dev - Launches PHP + Python services for local development
# =============================================================================
# Usage: ./scripts/start-dev.sh
#
# Starts services based on MODEL_PROVIDER:
#
#   ollama (default):
#     1. Ollama (auto-starts via binary or Docker if needed)
#     2. Python FastAPI agent on port 8081
#     3. Mercure hub on port 3701 (if MERCURE_URL is set in .env)
#     4. PHP Symfony dev server on port 8082
#
#   bedrock:
#     1. Python FastAPI agent on port 8081
#     2. Mercure hub on port 3701 (if MERCURE_URL is set in .env)
#     3. PHP Symfony dev server on port 8082
#     (Requires AWS credentials - no local LLM needed)
#
# Prerequisites:
#   - Run ./scripts/setup-initial.sh first (auto-runs if missing)
#   - Ollama (MODEL_PROVIDER=ollama): binary, Docker, or running instance
#   - Bedrock (MODEL_PROVIDER=bedrock): AWS credentials configured
#
# Environment:
#   MODEL_PROVIDER - LLM backend: 'ollama' (default) or 'bedrock'
#   AGENT_PORT     - Python agent port (default: 8081)
#   APP_PORT       - PHP app port (default: 8082)
#   MERCURE_PORT   - Mercure hub port (default: 3701)
#   OLLAMA_HOST    - Ollama URL (default: http://localhost:11434)
#   OLLAMA_MODEL   - Model name (default: from .env or qwen2.5:14b)
#
# Press Ctrl+C to stop all services.
# =============================================================================

set -uo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PYTHON_AGENT_DIR="$REPO_ROOT/strands_agents"
VENV_DIR="$PYTHON_AGENT_DIR/.venv"

# ── Configurable ports ──────────────────────────────────────────────
AGENT_PORT="${AGENT_PORT:-8081}"
APP_PORT="${APP_PORT:-8082}"
MERCURE_PORT="${MERCURE_PORT:-3701}"

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

# ── Log directory ──────────────────────────────────────────────────
LOG_DIR="$REPO_ROOT/var/log/dev"
mkdir -p "$LOG_DIR"
find "$LOG_DIR" -name "*.log" -mtime +7 -delete 2>/dev/null || true
TIMESTAMP="$(date +%Y%m%d-%H%M%S)"
PHP_LOG="$LOG_DIR/php-${TIMESTAMP}.log"
AGENT_LOG="$LOG_DIR/agent-${TIMESTAMP}.log"

# ── Track child PIDs and state for cleanup ─────────────────────────
PIDS=()
OLLAMA_DOCKER=false
MERCURE_DOCKER=false

cleanup() {
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
    if [[ "$MERCURE_DOCKER" == "true" ]]; then
        docker stop goat-mercure-dev >/dev/null 2>&1 || true
        docker rm goat-mercure-dev >/dev/null 2>&1 || true
    fi
    if [[ "$OLLAMA_DOCKER" == "true" ]]; then
        docker stop ollama >/dev/null 2>&1 || true
    fi
    echo -e "  ${PASS} All services stopped"
    # Show log paths if logs were written
    if [[ -s "$PHP_LOG" || -s "$AGENT_LOG" ]]; then
        echo ""
        echo -e "  ${DIM}Logs:${RESET}"
        [[ -s "$PHP_LOG" ]]   && echo -e "    ${ARROW} PHP:   ${DIM}${PHP_LOG}${RESET}"
        [[ -s "$AGENT_LOG" ]] && echo -e "    ${ARROW} Agent: ${DIM}${AGENT_LOG}${RESET}"
    fi
    exit 0
}

trap cleanup SIGINT SIGTERM

header() {
    echo ""
    echo -e "${BOLD}  Ambient Scribe - Dev Server${RESET}"
    echo -e "  ${DIM}$(printf '─%.0s' {1..44})${RESET}"
    echo ""
}

# ── Preflight ───────────────────────────────────────────────────────
header
STARTUP_START=$SECONDS

# ── Docker cleanup (free ports if docker compose is running) ──────
if command -v docker &>/dev/null; then
    running=$(docker ps --filter "name=ambient-scribe" --format "{{.Names}}" 2>/dev/null || true)
    if [[ -n "$running" ]]; then
        echo -e "  ${YELLOW}${BOLD}Docker containers detected${RESET}"
        echo ""
        while IFS= read -r c; do
            echo -e "    ${ARROW} ${c}"
        done <<< "$running"
        echo ""
        echo -e "  ${ARROW} Running ${BOLD}docker compose down${RESET}..."
        if docker compose -f "$REPO_ROOT/docker-compose.yml" down >/dev/null 2>&1; then
            echo -e "  ${ARROW} Docker containers      ${PASS}  ${DIM}stopped${RESET}"
        else
            echo -e "  ${ARROW} Docker containers      ${FAIL}  ${RED}failed to stop${RESET}"
            echo -e "     ${DIM}Free ports ${AGENT_PORT}, ${APP_PORT} manually before retrying${RESET}"
            exit 1
        fi
        echo ""
    fi
fi

# ── Kill stale processes on our ports ──────────────────────────────
if command -v lsof &>/dev/null; then
    for port in "$AGENT_PORT" "$APP_PORT" "$MERCURE_PORT"; do
        stale_pids=$(lsof -ti :"$port" 2>/dev/null || true)
        if [[ -n "$stale_pids" ]]; then
            echo -e "  ${YELLOW}${BOLD}Port ${port} in use${RESET} ${DIM}- killing stale process(es)${RESET}"
            echo "$stale_pids" | xargs kill 2>/dev/null || true
            sleep 1
            # Force kill if still alive
            remaining=$(lsof -ti :"$port" 2>/dev/null || true)
            if [[ -n "$remaining" ]]; then
                echo "$remaining" | xargs kill -9 2>/dev/null || true
                sleep 1
            fi
        fi
    done
fi

# Check venv + vendor exist - install dependencies if missing
if [[ ! -f "$VENV_DIR/bin/uvicorn" || ! -f "$REPO_ROOT/vendor/autoload.php" ]]; then
    echo -e "  ${YELLOW}${BOLD}Dependencies missing${RESET}"
    [[ ! -f "$VENV_DIR/bin/uvicorn" ]] && echo -e "    ${ARROW} Python venv not found"
    [[ ! -f "$REPO_ROOT/vendor/autoload.php" ]] && echo -e "    ${ARROW} PHP vendor/ not found"
    echo ""
    echo -e "  ${ARROW} Running ${BOLD}./scripts/dependencies-install.sh${RESET}..."
    echo ""
    if "$REPO_ROOT/scripts/dependencies-install.sh"; then
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

env_default MODEL_PROVIDER  "ollama"
env_default OLLAMA_MODEL    "qwen2.5:14b"
env_default OLLAMA_HOST     "http://localhost:11434"

# Mercure vars - needed for streaming mode
env_default MERCURE_JWT_SECRET  ""
env_default MERCURE_URL         ""
env_default MERCURE_PUBLIC_URL  ""

# Bedrock vars - only needed when MODEL_PROVIDER=bedrock, but load them
# so the validation section can check them without requiring export-before-run
env_default AWS_ACCESS_KEY_ID     ""
env_default AWS_SECRET_ACCESS_KEY ""
env_default AWS_SESSION_TOKEN     ""
env_default AWS_DEFAULT_REGION    ""
env_default AWS_PROFILE           ""
env_default MODEL_ID              ""

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
    if OLLAMA_TAGS_CACHE="$(curl -sf "${OLLAMA_HOST}/api/tags" 2>/dev/null)"; then
        echo -e "  ${ARROW} Ollama                 ${PASS}  ${DIM}running at ${OLLAMA_HOST}${RESET}"
    else
        # Try to start ollama serve if the binary exists
        if command -v ollama &>/dev/null; then
            # Extract port from OLLAMA_HOST for the serve command
            OLLAMA_SERVE_PORT=$(echo "$OLLAMA_HOST" | grep -oE '[0-9]+$' || echo "11434")
            echo -e "  ${ARROW} Starting Ollama...     ${DIM}${OLLAMA_HOST}${RESET}"
            OLLAMA_HOST="0.0.0.0:${OLLAMA_SERVE_PORT}" ollama serve >/dev/null 2>&1 &
            PIDS+=($!)

            # Wait for Ollama to be ready
            for i in $(seq 1 15); do
                if OLLAMA_TAGS_CACHE="$(curl -sf "${OLLAMA_HOST}/api/tags" 2>/dev/null)"; then
                    echo -e "  ${ARROW} Ollama                 ${PASS}  ${DIM}started${RESET}"
                    break
                fi
                if [[ $i -eq 15 ]]; then
                    echo -e "  ${ARROW} Ollama                 ${FAIL}  ${RED}failed to start${RESET}"
                    echo -e "     ${DIM}Check that port 11434 is free${RESET}"
                    cleanup
                fi
                sleep 1
            done
        elif command -v docker &>/dev/null; then
            # No ollama binary - try Docker instead
            echo -e "  ${ARROW} Starting Ollama via Docker..."

            # Check if an ollama container already exists (stopped)
            if docker ps -a --filter "name=^ollama$" --format "{{.Names}}" 2>/dev/null | grep -q "^ollama$"; then
                docker start ollama >/dev/null 2>&1
            else
                docker run -d --name ollama -p 11434:11434 ollama/ollama >/dev/null 2>&1
            fi
            OLLAMA_DOCKER=true

            # Wait for Ollama to be ready
            for i in $(seq 1 20); do
                if OLLAMA_TAGS_CACHE="$(curl -sf "${OLLAMA_HOST}/api/tags" 2>/dev/null)"; then
                    echo -e "  ${ARROW} Ollama                 ${PASS}  ${DIM}running via Docker${RESET}"
                    break
                fi
                if [[ $i -eq 20 ]]; then
                    echo -e "  ${ARROW} Ollama                 ${FAIL}  ${RED}Docker container failed to start${RESET}"
                    echo -e "     ${DIM}Check: docker logs ollama${RESET}"
                    cleanup
                fi
                sleep 1
            done
        else
            echo -e "  ${ARROW} Ollama                 ${FAIL}  ${RED}not running${RESET}"
            echo -e "     ${DIM}Install from https://ollama.com or install Docker${RESET}"
            cleanup
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
                cleanup
            fi
        elif ollama pull "$OLLAMA_MODEL" 2>&1; then
            echo -e "  ${ARROW} Model ${OLLAMA_MODEL}     ${PASS}  ${DIM}ready${RESET}"
        else
            echo -e "  ${ARROW} Model ${OLLAMA_MODEL}     ${FAIL}  ${RED}pull failed${RESET}"
            cleanup
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
        cleanup
    fi

    if [[ -n "${AWS_PROFILE:-}" ]]; then
        echo -e "  ${ARROW} AWS credentials        ${PASS}  ${DIM}profile: ${AWS_PROFILE}${RESET}"
    else
        echo -e "  ${ARROW} AWS credentials        ${PASS}  ${DIM}access key configured${RESET}"
    fi
    echo -e "  ${ARROW} Region                 ${DIM}${AWS_DEFAULT_REGION:-us-east-1}${RESET}"

else

    echo -e "  ${FAIL} ${RED}Unknown MODEL_PROVIDER: ${MODEL_PROVIDER}${RESET}"
    echo -e "     ${DIM}Set MODEL_PROVIDER to 'ollama' or 'bedrock' in .env${RESET}"
    exit 1

fi

# ── 2. Python Agent ─────────────────────────────────────────────────
echo ""
echo -e "  ${BOLD}Starting services${RESET}"
echo ""

export MODEL_PROVIDER
if [[ "$MODEL_PROVIDER" == "ollama" ]]; then
    export OLLAMA_HOST
    export OLLAMA_MODEL
else
    # Pass through AWS credentials for Bedrock
    [[ -n "${AWS_ACCESS_KEY_ID:-}" ]]     && export AWS_ACCESS_KEY_ID
    [[ -n "${AWS_SECRET_ACCESS_KEY:-}" ]] && export AWS_SECRET_ACCESS_KEY
    [[ -n "${AWS_SESSION_TOKEN:-}" ]]     && export AWS_SESSION_TOKEN
    [[ -n "${AWS_DEFAULT_REGION:-}" ]]    && export AWS_DEFAULT_REGION
    [[ -n "${AWS_PROFILE:-}" ]]           && export AWS_PROFILE
    [[ -n "${MODEL_ID:-}" ]]              && export MODEL_ID
fi

# Launch agent and Mercure concurrently, then health-check both
echo -e "  ${ARROW} Python agent           ${DIM}http://localhost:${AGENT_PORT}${RESET}"
cd "$PYTHON_AGENT_DIR" || exit 1
"$VENV_DIR/bin/uvicorn" api.server:app \
    --host 0.0.0.0 \
    --port "$AGENT_PORT" \
    --log-level warning \
    > >(while IFS= read -r line; do
        echo "$line" >> "$AGENT_LOG"
        echo -e "    ${CYAN}[agent]${RESET} $line"
    done) \
    2>&1 &
PIDS+=($!)
cd "$REPO_ROOT" || exit 1

# ── 3. Mercure (optional - for streaming mode) ─────────────────────
# If MERCURE_URL is set in .env, start a Mercure Docker container
# so the frontend can receive streamed tokens in real-time.
# Launched in parallel with the agent to save startup time.
MERCURE_LAUNCHED=false
if [[ -n "$MERCURE_URL" && -n "$MERCURE_JWT_SECRET" ]]; then
    if ! command -v docker &>/dev/null; then
        echo -e "  ${YELLOW}${BOLD}Mercure skipped${RESET} ${DIM}- Docker not available (streaming disabled)${RESET}"
    else
        # Stop any existing Mercure dev container
        if docker ps -a --filter "name=^goat-mercure-dev$" --format "{{.Names}}" 2>/dev/null | grep -q "^goat-mercure-dev$"; then
            docker rm -f goat-mercure-dev >/dev/null 2>&1 || true
        fi

        echo -e "  ${ARROW} Mercure                ${DIM}http://localhost:${MERCURE_PORT}${RESET}"
        if docker run -d --name goat-mercure-dev \
            -p "${MERCURE_PORT}:${MERCURE_PORT}" \
            -e MERCURE_PUBLISHER_JWT_KEY="$MERCURE_JWT_SECRET" \
            -e MERCURE_SUBSCRIBER_JWT_KEY="$MERCURE_JWT_SECRET" \
            -e SERVER_NAME=":${MERCURE_PORT}" \
            -e "MERCURE_EXTRA_DIRECTIVES=anonymous
cors_origins http://localhost:${APP_PORT}" \
            dunglas/mercure >/dev/null 2>&1; then
            MERCURE_LAUNCHED=true
            MERCURE_DOCKER=true
        else
            echo -e "  ${ARROW} Mercure                ${FAIL}  ${RED}Docker run failed${RESET}"
            echo -e "     ${DIM}Falling back to sync mode${RESET}"
        fi
    fi
else
    echo -e "  ${ARROW} Mercure                ${DIM}disabled (MERCURE_URL not set)${RESET}"
fi

# Interleaved health-check loop: poll agent and Mercure concurrently
AGENT_READY=false
MERCURE_READY=false
[[ "$MERCURE_LAUNCHED" != "true" ]] && MERCURE_READY=true  # skip if not launched
for i in $(seq 1 15); do
    if [[ "$AGENT_READY" != "true" ]]; then
        if curl -sf "http://localhost:${AGENT_PORT}/health" >/dev/null 2>&1; then
            echo -e "  ${ARROW} Python agent           ${PASS}  ${DIM}ready${RESET}"
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
    if [[ $i -eq 15 ]]; then
        if [[ "$AGENT_READY" != "true" ]]; then
            echo -e "  ${ARROW} Python agent           ${FAIL}  ${RED}failed to start${RESET}"
            echo -e "     ${DIM}See log: ${AGENT_LOG}${RESET}"
            cleanup
        fi
        if [[ "$MERCURE_READY" != "true" ]]; then
            echo -e "  ${ARROW} Mercure                ${FAIL}  ${RED}failed to start${RESET}"
            echo -e "     ${DIM}Check: docker logs goat-mercure-dev${RESET}"
            echo -e "     ${DIM}Falling back to sync mode${RESET}"
            MERCURE_DOCKER=false
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
# Verify AGENT_ENDPOINT in .env matches the port we're actually using:
if [[ -f "$REPO_ROOT/.env" ]]; then
    EXPECTED_ENDPOINT="http://localhost:${AGENT_PORT}"
    CURRENT_ENDPOINT=$(grep -E '^AGENT_ENDPOINT=' "$REPO_ROOT/.env" 2>/dev/null | head -1 | cut -d= -f2-)
    if [[ "$CURRENT_ENDPOINT" != "$EXPECTED_ENDPOINT" ]]; then
        echo -e "  ${YELLOW}${BOLD}Fixing${RESET} AGENT_ENDPOINT in .env → ${DIM}${EXPECTED_ENDPOINT}${RESET}"
        sed -i "s|^AGENT_ENDPOINT=.*|AGENT_ENDPOINT=${EXPECTED_ENDPOINT}|" "$REPO_ROOT/.env"
    fi
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
PIDS+=($!)

# Wait for PHP to be ready - check if accepting connections (any HTTP response)
for i in $(seq 1 10); do
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
        cleanup
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

if [[ "$MODEL_PROVIDER" == "ollama" ]]; then
    OLLAMA_LABEL="${OLLAMA_HOST}"
    [[ "$OLLAMA_DOCKER" == "true" ]] && OLLAMA_LABEL="${OLLAMA_HOST} (Docker)"
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
echo ""
echo -e "  ${DIM}Logs:${RESET}"
echo -e "    ${ARROW} PHP:           ${DIM}${PHP_LOG}${RESET}"
echo -e "    ${ARROW} Agent:         ${DIM}${AGENT_LOG}${RESET}"
echo ""
echo -e "  ${DIM}Press Ctrl+C to stop all services${RESET}"
echo ""

# ── Wait for all children ───────────────────────────────────────────
wait
