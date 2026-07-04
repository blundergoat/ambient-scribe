#!/bin/bash
# =============================================================================
# Setup Initial - First-time Docker-first setup for Ambient Scribe
# =============================================================================
# Usage: ./scripts/setup-initial.sh [OPTIONS]
#
# Options:
#   --skip-deps    Skip host-side dependency install (composer, pip)
#   --help, -h     Show this help
#
# What it does:
#   1. Preflight checks (Docker, .env, GPU, VRAM)
#   2. Generate Mercure JWT if missing
#   3. Build Docker images (NeMo is ~15-30 min first time)
#   4. Start containers
#   5. Health-check all services
#   6. Install host-side dev dependencies (for composer preflight)
#   7. Verify HTTP endpoints
#
# When to run:
#   - New machine / first clone
#   - After major Dockerfile changes
#   - When start-dev.sh tells you to
#
# For daily startup:          scripts/start-dev.sh
# To diagnose issues:         scripts/health-checks.sh
# =============================================================================

set -uo pipefail

source "$(dirname "${BASH_SOURCE[0]}")/env-detect.sh"

ERRORS=0

# ── Parse flags ─────────────────────────────────────────────────────
SKIP_DEPS=false

for arg in "$@"; do
    case "$arg" in
        --skip-deps) SKIP_DEPS=true ;;
        --help|-h)
            echo "Usage: $0 [--skip-deps]"
            echo ""
            echo "  --skip-deps    Skip host-side deps (composer install, pip install)"
            exit 0
            ;;
    esac
done

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
    echo -e "${WARN}  ${DIM}${msg}${RESET}"
}

header() {
    echo ""
    echo -e "${BOLD}  Ambient Scribe - Initial Setup${RESET}"
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
        status=$(docker inspect "$container" --format '{{.State.Health.Status}}' 2>/dev/null || echo "missing")

        if [[ "$status" == "healthy" ]]; then
            echo -ne "\r\033[K"
            echo -e "  ${ARROW} ${padded} ${PASS}  ${DIM}healthy${RESET}"
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

header
STARTUP_START=$SECONDS

# =============================================================================
# STEP 1: Preflight Checks
# =============================================================================
echo -e "  ${BOLD}Preflight Checks${RESET}"
echo ""

# Docker daemon
step "Docker daemon"
if ! command -v docker &>/dev/null; then
    fail "not found - install Docker Engine or Docker Desktop"
    echo ""
    echo -e "  ${RED}${BOLD}Cannot continue without Docker${RESET}"
    exit 1
elif ! docker ps &>/dev/null 2>&1; then
    fail "not running - start Docker Desktop or dockerd"
    echo ""
    echo -e "  ${RED}${BOLD}Cannot continue without Docker${RESET}"
    exit 1
else
    pass
fi

# .env file
step ".env file"
if [[ -f "$REPO_ROOT/.env" ]]; then
    pass "exists"
else
    if [[ -f "$REPO_ROOT/.env.example" ]]; then
        cp "$REPO_ROOT/.env.example" "$REPO_ROOT/.env"
        # Re-source to pick up new values
        set -o allexport
        # shellcheck disable=SC1091
        source "$REPO_ROOT/.env"
        set +o allexport
        pass "created from .env.example"
    else
        fail ".env.example not found"
    fi
fi

# MERCURE_JWT_SECRET validation
step "MERCURE_JWT_SECRET"
if [[ -z "${MERCURE_JWT_SECRET:-}" ]]; then
    fail "not set in .env"
elif [[ ${#MERCURE_JWT_SECRET} -lt 32 ]]; then
    fail "too short (${#MERCURE_JWT_SECRET} chars, need >= 32 for HS256)"
else
    pass "${#MERCURE_JWT_SECRET} chars"
fi

# nvidia-smi on host
step "nvidia-smi"
if [[ "$HAS_NVIDIA_SMI" == "true" ]]; then
    pass "${GPU_NAME}"
else
    fail "not found - install NVIDIA drivers"
    echo -e "     ${DIM}WSL2: install the Windows NVIDIA driver (not the Linux one)${RESET}"
fi

# Docker GPU passthrough
step "Docker GPU passthrough"
if docker run --rm --gpus all nvidia/cuda:12.4.0-base-ubuntu22.04 nvidia-smi &>/dev/null; then
    pass
else
    fail "docker cannot access GPU"
    echo -e "     ${DIM}Install NVIDIA Container Toolkit:${RESET}"
    echo -e "     ${DIM}https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/install-guide.html${RESET}"
fi

# VRAM check
step "GPU VRAM (>= 12 GB)"
if [[ "$HAS_NVIDIA_SMI" == "true" ]]; then
    vram_gb=$(( GPU_VRAM_MB / 1024 ))
    if [[ $GPU_VRAM_MB -ge 12288 ]]; then
        pass "${vram_gb} GB (${GPU_VRAM_MB} MB)"
    else
        fail "${vram_gb} GB - need >= 12 GB for NeMo (Sortformer + Parakeet)"
    fi
else
    fail "cannot check - nvidia-smi not available"
fi

if [[ $ERRORS -gt 0 ]]; then
    echo ""
    echo -e "  ${RED}${BOLD}Cannot continue - ${ERRORS} preflight check(s) failed${RESET}"
    echo ""
    exit 1
fi

echo ""

# =============================================================================
# STEP 2: Generate Mercure JWT
# =============================================================================
echo -e "  ${BOLD}Mercure JWT${RESET}"
echo ""

step "MERCURE_PUBLISHER_JWT"
if [[ -n "${MERCURE_PUBLISHER_JWT:-}" ]]; then
    pass "already set"
else
    # Generate JWT: {"mercure":{"publish":["*"]}}
    JWT_PAYLOAD=""
    if command -v php &>/dev/null; then
        JWT_PAYLOAD=$(php -r '
            $secret = getenv("MERCURE_JWT_SECRET") ?: "ambient-scribe-mercure-dev-secret-key";
            $header = rtrim(strtr(base64_encode(json_encode(["typ"=>"JWT","alg"=>"HS256"])), "+/", "-_"), "=");
            $payload = rtrim(strtr(base64_encode(json_encode(["mercure"=>["publish"=>["*"]]])), "+/", "-_"), "=");
            $sig = rtrim(strtr(base64_encode(hash_hmac("sha256", "$header.$payload", $secret, true)), "+/", "-_"), "=");
            echo "$header.$payload.$sig";
        ' 2>/dev/null)
    elif command -v python3 &>/dev/null; then
        JWT_PAYLOAD=$(python3 -c "
import base64, hashlib, hmac, json, os
secret = os.environ.get('MERCURE_JWT_SECRET', 'ambient-scribe-mercure-dev-secret-key')
def b64(data): return base64.urlsafe_b64encode(json.dumps(data).encode()).rstrip(b'=').decode()
header = b64({'typ': 'JWT', 'alg': 'HS256'})
payload = b64({'mercure': {'publish': ['*']}})
sig = base64.urlsafe_b64encode(hmac.new(secret.encode(), f'{header}.{payload}'.encode(), hashlib.sha256).digest()).rstrip(b'=').decode()
print(f'{header}.{payload}.{sig}')
" 2>/dev/null)
    fi

    if [[ -n "$JWT_PAYLOAD" ]]; then
        # Write to .env
        if grep -q '^MERCURE_PUBLISHER_JWT=' "$REPO_ROOT/.env" 2>/dev/null; then
            sed -i "s|^MERCURE_PUBLISHER_JWT=.*|MERCURE_PUBLISHER_JWT=${JWT_PAYLOAD}|" "$REPO_ROOT/.env"
        else
            echo "MERCURE_PUBLISHER_JWT=${JWT_PAYLOAD}" >> "$REPO_ROOT/.env"
        fi
        export MERCURE_PUBLISHER_JWT="$JWT_PAYLOAD"
        pass "generated and saved to .env"
    else
        fail "could not generate - install PHP or Python"
    fi
fi

echo ""

# =============================================================================
# STEP 3: Build Docker Images
# =============================================================================
echo -e "  ${BOLD}Building Docker images${RESET}"
echo -e "  ${DIM}NeMo image is large - first build takes 15-30 minutes${RESET}"
echo ""

if dc build --progress=tty 2>&1; then
    echo ""
    echo -e "  ${ARROW} Docker images              ${PASS}  ${DIM}all built${RESET}"
else
    echo ""
    echo -e "  ${ARROW} Docker images              ${FAIL}  ${RED}build failed${RESET}"
    echo -e "     ${DIM}Check build output above for errors${RESET}"
    exit 1
fi

echo ""

# =============================================================================
# STEP 4: Start Containers
# =============================================================================
echo -e "  ${BOLD}Starting containers${RESET}"
echo ""

if dc up -d 2>&1 | tail -3; then
    echo -e "  ${ARROW} Docker Compose             ${PASS}  ${DIM}containers started${RESET}"
else
    echo -e "  ${ARROW} Docker Compose             ${FAIL}  ${RED}failed to start${RESET}"
    echo -e "     ${DIM}Check: dc logs${RESET}"
    exit 1
fi

echo ""

# =============================================================================
# STEP 5: Health Checks
# =============================================================================
echo -e "  ${BOLD}Health checks${RESET}"
echo -e "  ${DIM}NeMo model loading takes 60-120s on first start${RESET}"
echo ""

# Refresh container names after starting
_RUNNING_CONTAINERS="$(docker ps --format '{{.Names}}' 2>/dev/null || true)"
NEMO_CONTAINER=$(_resolve_container "${COMPOSE_PROJECT}-nemo-agent-1" "nemo-agent")
APP_CONTAINER=$(_resolve_container "${COMPOSE_PROJECT}-app-1" "app")
MERCURE_CONTAINER=$(_resolve_container "${COMPOSE_PROJECT}-mercure-1" "mercure")

wait_healthy "$MERCURE_CONTAINER"  "Mercure"    30
wait_healthy "$NEMO_CONTAINER"     "nemo-agent" 180
wait_healthy "$APP_CONTAINER"      "app"        60

if [[ $ERRORS -gt 0 ]]; then
    echo ""
    echo -e "  ${YELLOW}${BOLD}Some services failed to become healthy${RESET}"
    echo -e "  ${DIM}Check logs: dc logs <service>${RESET}"
    echo ""
fi

echo ""

# =============================================================================
# STEP 6: Host-side Dev Dependencies (for composer preflight)
# =============================================================================
if [[ "$SKIP_DEPS" == "true" ]]; then
    echo -e "  ${DIM}Skipping host-side deps (--skip-deps)${RESET}"
else
    echo -e "  ${BOLD}Host-side dev dependencies${RESET}"
    echo -e "  ${DIM}For running composer preflight on the host${RESET}"
    echo ""

    # Composer install
    step "composer install"
    if command -v composer &>/dev/null; then
        if composer install --working-dir="$REPO_ROOT" 2>&1 | tail -1; then
            pass
        else
            fail "composer install failed"
        fi
    else
        warn "composer not found - skip host-side PHP deps"
    fi

    # Python venv + test deps
    PYTHON_AGENT_DIR="$REPO_ROOT/strands_agents"
    step "Python venv"
    if command -v python3 &>/dev/null; then
        if [[ -d "$PYTHON_AGENT_DIR/.venv" ]]; then
            pass "already exists"
        else
            python3 -m venv "$PYTHON_AGENT_DIR/.venv"
            pass "created"
        fi

        step "pip install test deps"
        if [[ -f "$REPO_ROOT/tests/python/requirements-dev.txt" ]]; then
            if "$PYTHON_AGENT_DIR/.venv/bin/pip" install -q \
                -r "$REPO_ROOT/tests/python/requirements-dev.txt" 2>&1 | tail -1; then
                pass
            else
                fail "pip install failed"
            fi
        else
            warn "tests/python/requirements-dev.txt not found"
        fi
    else
        warn "python3 not found - skip host-side Python deps"
    fi
fi

echo ""

# =============================================================================
# STEP 7: Verify HTTP Endpoints
# =============================================================================
echo -e "  ${BOLD}Verifying endpoints${RESET}"
echo ""

step "/health (nemo-agent)"
HTTP_CODE=$(curl -sf -o /dev/null -w "%{http_code}" --connect-timeout 5 "http://localhost:8001/health" 2>/dev/null) || HTTP_CODE="000"
if [[ "$HTTP_CODE" == "200" ]]; then
    pass
else
    warn "HTTP ${HTTP_CODE} - may still be loading models"
fi

step "/scribe (app)"
HTTP_CODE=$(curl -sf -o /dev/null -w "%{http_code}" --connect-timeout 5 "http://localhost:8082/scribe" 2>/dev/null) || HTTP_CODE="000"
if [[ "$HTTP_CODE" == "200" ]]; then
    pass
else
    warn "HTTP ${HTTP_CODE}"
fi

step "Mercure hub"
HTTP_CODE=$(curl -sf -o /dev/null -w "%{http_code}" --connect-timeout 5 "http://localhost:3701/.well-known/mercure" 2>/dev/null) || HTTP_CODE="000"
if [[ "$HTTP_CODE" =~ ^(200|401|400)$ ]]; then
    pass
else
    warn "HTTP ${HTTP_CODE}"
fi

echo ""

# =============================================================================
# COMPLETE
# =============================================================================
echo -e "  ${DIM}$(printf '─%.0s' {1..44})${RESET}"
echo ""
STARTUP_ELAPSED=$(( SECONDS - STARTUP_START ))
MINUTES=$(( STARTUP_ELAPSED / 60 ))
SECS=$(( STARTUP_ELAPSED % 60 ))
echo -e "  ${GREEN}${BOLD}Setup complete!${RESET} ${DIM}(${MINUTES}m ${SECS}s)${RESET}"
echo ""
echo -e "  ${DIM}Services:${RESET}"
echo -e "    ${ARROW} Scribe UI:     ${BOLD}http://localhost:8082/scribe${RESET}"
echo -e "    ${ARROW} NeMo agent:    ${BOLD}http://localhost:8001${RESET}"
echo -e "    ${ARROW} Mercure:       ${BOLD}http://localhost:3701${RESET}"
echo ""
echo -e "  ${DIM}Next steps:${RESET}"
echo -e "    ${ARROW} Daily startup:    ${BOLD}./scripts/start-dev.sh${RESET}"
echo -e "    ${ARROW} Health check:     ${BOLD}./scripts/health-checks.sh${RESET}"
echo -e "    ${ARROW} Quality checks:   ${BOLD}composer preflight${RESET}"
echo ""
