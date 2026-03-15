#!/bin/bash
# =============================================================================
# Health Check - Verifies local dev services are running and responsive
# =============================================================================
# Usage: ./scripts/health-check-localdev.sh
#
# Checks the health of all services started by start-dev.sh:
#   - Role inference backend (local Ollama or AWS Bedrock)
#   - Dockerized NeMo agent (default port 48101, or the selected fallback port)
#   - Local PHP Symfony app (default port 48082)
#   - Dockerized Mercure hub (port 48137)
#
# Also checks LLM model availability, endpoint connectivity between services,
# and response times.
#
# Environment (matches start-dev.sh defaults):
#   AGENT_PORT   - NeMo agent port (default: from .env AGENT_ENDPOINT, else 48101)
#   APP_PORT     - PHP app port (default: 48082)
#   OLLAMA_HOST  - Ollama URL (default: from .env, else http://localhost:11434)
#   MERCURE_PORT - Mercure port (default: 48137)
#
# Exit codes:
#   0 - All required services healthy
#   1 - One or more required services unhealthy
# =============================================================================

set -uo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"

env_file_value() {
    local key="$1"
    if [[ -f "$REPO_ROOT/.env" ]]; then
        grep -E "^[[:space:]]*${key}=" "$REPO_ROOT/.env" 2>/dev/null | head -1 | cut -d= -f2-
    fi
}

port_from_url() {
    local url="$1"
    sed -nE 's#^[a-z]+://[^:/]+:([0-9]+).*$#\1#p' <<< "$url"
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

# ── Configurable endpoints ──────────────────────────────────────────
ENV_AGENT_ENDPOINT="$(env_file_value "AGENT_ENDPOINT")"
ENV_OLLAMA_HOST="$(env_file_value "OLLAMA_HOST")"
ENV_OLLAMA_MODEL="$(env_file_value "OLLAMA_MODEL")"
ENV_ROLE_AGENT_OLLAMA_MODEL="$(env_file_value "ROLE_AGENT_OLLAMA_MODEL")"
ENV_ROLE_AGENT_PROVIDER="$(env_file_value "ROLE_AGENT_MODEL_PROVIDER")"
ENV_LEGACY_PROVIDER="$(env_file_value "MODEL_PROVIDER")"
ENV_MERCURE_PUBLIC_URL="$(env_file_value "MERCURE_PUBLIC_URL")"

ROLE_AGENT_MODEL_PROVIDER="${ROLE_AGENT_MODEL_PROVIDER:-${ENV_ROLE_AGENT_PROVIDER:-${MODEL_PROVIDER:-${ENV_LEGACY_PROVIDER:-ollama}}}}"
AGENT_PORT="${AGENT_PORT:-$(port_from_url "${ENV_AGENT_ENDPOINT:-http://localhost:48101}")}"
AGENT_PORT="${AGENT_PORT:-48101}"
APP_PORT="${APP_PORT:-48082}"
MERCURE_PORT="${MERCURE_PORT:-$(port_from_url "${ENV_MERCURE_PUBLIC_URL:-http://localhost:48137/.well-known/mercure}")}"
MERCURE_PORT="${MERCURE_PORT:-48137}"
OLLAMA_HOST="${OLLAMA_HOST:-${ENV_OLLAMA_HOST:-http://localhost:11434}}"
OLLAMA_MODEL="${ROLE_AGENT_OLLAMA_MODEL:-${ENV_ROLE_AGENT_OLLAMA_MODEL:-${OLLAMA_MODEL:-${ENV_OLLAMA_MODEL:-qwen2.5:14b}}}}"

if [[ "$ROLE_AGENT_MODEL_PROVIDER" == "ollama" ]] && ! curl -sf "${OLLAMA_HOST}/api/tags" >/dev/null 2>&1; then
    DETECTED_OLLAMA_HOST="$(detect_running_ollama_host || true)"
    if [[ -n "$DETECTED_OLLAMA_HOST" ]] && curl -sf "${DETECTED_OLLAMA_HOST}/api/tags" >/dev/null 2>&1; then
        OLLAMA_HOST="$DETECTED_OLLAMA_HOST"
    fi
fi

OLLAMA_PORT="$(port_from_url "$OLLAMA_HOST")"
OLLAMA_PORT="${OLLAMA_PORT:-11434}"

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
    printf "  ${ARROW} %-40s" "$1"
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

# Probe a URL and return HTTP status code + response time in ms.
# Sets: PROBE_STATUS, PROBE_TIME_MS, PROBE_BODY
probe() {
    local url="$1"
    local timeout="${2:-5}"
    PROBE_BODY=""
    PROBE_STATUS=""
    PROBE_TIME_MS=""

    local tmp
    tmp=$(mktemp)

    local result
    result=$(curl -sf -o "$tmp" -w "%{http_code} %{time_total}" \
        --connect-timeout "$timeout" --max-time "$timeout" "$url" 2>/dev/null) || true

    PROBE_STATUS=$(echo "$result" | awk '{print $1}')
    local time_secs
    time_secs=$(echo "$result" | awk '{print $2}')
    PROBE_TIME_MS=$(awk "BEGIN {printf \"%.0f\", ${time_secs:-0} * 1000}")
    PROBE_BODY=$(cat "$tmp" 2>/dev/null)
    rm -f "$tmp"
}

# ── Header ──────────────────────────────────────────────────────────
echo ""
echo -e "${BOLD}  Ambient Scribe - Health Check${RESET}"
echo -e "  ${DIM}$(printf '─%.0s' {1..44})${RESET}"

# ═════════════════════════════════════════════════════════════════════
# 0. Environment
# ═════════════════════════════════════════════════════════════════════
section "Environment"

step ".env file"
if [[ -f "$REPO_ROOT/.env" ]]; then
    pass
else
    warn "not found - run: cp .env.example .env"
fi

# Resolve MERCURE_JWT_SECRET from available sources
_mercure_secret="${MERCURE_JWT_SECRET:-}"
if [[ -z "$_mercure_secret" && -f "$REPO_ROOT/.env" ]]; then
    _mercure_secret="$(grep -E '^MERCURE_JWT_SECRET=' "$REPO_ROOT/.env" 2>/dev/null | cut -d= -f2 || true)"
fi
# Fall back to docker-compose.yml hardcoded value (covers Docker-only setups)
if [[ -z "$_mercure_secret" && -f "$REPO_ROOT/docker-compose.yml" ]]; then
    _mercure_secret="$(grep -m1 '^\s*- MERCURE_JWT_SECRET=' "$REPO_ROOT/docker-compose.yml" 2>/dev/null \
        | sed 's/.*MERCURE_JWT_SECRET=//' | sed 's/\s*#.*//' | tr -d ' ' || true)"
    # If it's a ${VAR:-default} reference, extract the default
    if [[ "$_mercure_secret" =~ ^\$\{.*:-(.+)\}$ ]]; then
        _mercure_secret="${BASH_REMATCH[1]}"
    fi
fi

step "MERCURE_JWT_SECRET"
if [[ -n "$_mercure_secret" ]]; then
    if [[ ${#_mercure_secret} -ge 32 ]]; then
        pass "${#_mercure_secret} chars (≥ 32 required)"
    else
        fail "too short (${#_mercure_secret} chars) - HS256 needs ≥ 32 chars (256 bits)"
    fi
else
    # Check if Mercure is actually running - if so, missing secret is a problem
    _mercure_probe=$(curl -sf -o /dev/null -w "%{http_code}" \
        --connect-timeout 2 "http://localhost:${MERCURE_PORT}/.well-known/mercure" 2>/dev/null) || true
    if [[ "$_mercure_probe" =~ ^(200|401|400)$ ]]; then
        fail "not set but Mercure is running - streaming will fail"
    else
        warn "not set (streaming disabled - sync mode only)"
    fi
fi

# Check Mercure triad consistency
_mercure_url="${MERCURE_URL:-}"
_mercure_pub="${MERCURE_PUBLIC_URL:-}"
if [[ -z "$_mercure_url" && -f "$REPO_ROOT/.env" ]]; then
    _mercure_url="$(grep -E '^MERCURE_URL=' "$REPO_ROOT/.env" 2>/dev/null | cut -d= -f2 || true)"
fi
if [[ -z "$_mercure_pub" && -f "$REPO_ROOT/.env" ]]; then
    _mercure_pub="$(grep -E '^MERCURE_PUBLIC_URL=' "$REPO_ROOT/.env" 2>/dev/null | cut -d= -f2 || true)"
fi

_mercure_count=0
[[ -n "$_mercure_secret" ]] && _mercure_count=$((_mercure_count + 1))
[[ -n "$_mercure_url" ]] && _mercure_count=$((_mercure_count + 1))
[[ -n "$_mercure_pub" ]] && _mercure_count=$((_mercure_count + 1))

if [[ $_mercure_count -gt 0 && $_mercure_count -lt 3 ]]; then
    step "Mercure config completeness"
    warn "partial config - need all: MERCURE_JWT_SECRET, MERCURE_URL, MERCURE_PUBLIC_URL"
fi

unset _mercure_secret _mercure_url _mercure_pub _mercure_probe _mercure_count

# ═════════════════════════════════════════════════════════════════════
# 1. Role inference backend
# ═════════════════════════════════════════════════════════════════════
section "Role inference (${ROLE_AGENT_MODEL_PROVIDER})"

if [[ "$ROLE_AGENT_MODEL_PROVIDER" == "ollama" ]]; then
    step "Ollama API responding"
    probe "${OLLAMA_HOST}/api/tags"
    if [[ "$PROBE_STATUS" == "200" ]]; then
        pass "${PROBE_TIME_MS}ms at ${OLLAMA_HOST}"
    else
        fail "Ollama not reachable at ${OLLAMA_HOST}"
    fi

    if [[ "$PROBE_STATUS" == "200" ]]; then
        step "Model: ${OLLAMA_MODEL}"
        model_info=$(echo "$PROBE_BODY" | python3 -c "
import json, sys
try:
    data = json.load(sys.stdin)
    for m in data.get('models', []):
        name = m.get('name', '')
        if name == '${OLLAMA_MODEL}' or name.startswith('${OLLAMA_MODEL}:'):
            gb = m.get('size', 0) / 1e9
            print(f'{name}|{gb:.1f}GB')
            break
except: pass
" 2>/dev/null)
        if [[ -n "$model_info" ]]; then
            model_name="${model_info%%|*}"
            model_size="${model_info#*|}"
            pass "${model_size:-available}"
        else
            fail "not pulled - run: ollama pull ${OLLAMA_MODEL}"
        fi

        step "Model responds"
        gen_result=$(curl -sf --max-time 30 -X POST "${OLLAMA_HOST}/api/generate" \
            -d "{\"model\":\"${model_name:-${OLLAMA_MODEL}}\",\"prompt\":\"hi\",\"stream\":false,\"options\":{\"num_predict\":1}}" 2>/dev/null)
        if echo "$gen_result" | grep -q '"response"'; then
            pass "inference ok"
        else
            warn "inference test failed - model may still be loading"
        fi
    fi
elif [[ "$ROLE_AGENT_MODEL_PROVIDER" == "bedrock" ]]; then
    step "Provider"
    pass "AWS Bedrock"
else
    fail "unknown ROLE_AGENT_MODEL_PROVIDER=${ROLE_AGENT_MODEL_PROVIDER}"
fi

# ═════════════════════════════════════════════════════════════════════
# 2. NeMo agent
# ═════════════════════════════════════════════════════════════════════
section "NeMo agent (localhost:${AGENT_PORT})"

step "Health endpoint"
probe "http://localhost:${AGENT_PORT}/health"
if [[ "$PROBE_STATUS" == "200" ]]; then
    # Verify JSON response shape
    if echo "$PROBE_BODY" | grep -q '"status"'; then
        pass "${PROBE_TIME_MS}ms"
    else
        warn "responded ${PROBE_STATUS} but unexpected body"
    fi
else
    fail "not reachable - is start-dev.sh running?"
fi

step "OpenAPI docs"
probe "http://localhost:${AGENT_PORT}/docs"
if [[ "$PROBE_STATUS" == "200" ]]; then
    pass "${PROBE_TIME_MS}ms"
else
    warn "docs unavailable (non-critical)"
fi

step "POST /transcribe/file validates input"
transcribe_result=$(curl -s -o /dev/null -w "%{http_code}" \
    --connect-timeout 3 --max-time 5 \
    -X POST "http://localhost:${AGENT_PORT}/transcribe/file" 2>/dev/null) || true
if [[ "$transcribe_result" == "422" ]]; then
    pass "endpoint active (422)"
else
    fail "endpoint not responding"
fi

step "GET /session/test/roles"
roles_result=$(curl -sf --connect-timeout 3 --max-time 5 \
    "http://localhost:${AGENT_PORT}/session/test/roles" 2>/dev/null) || true
if echo "$roles_result" | grep -q '"session_id"'; then
    pass "endpoint active"
else
    fail "endpoint not responding"
fi

# ═════════════════════════════════════════════════════════════════════
# 3. PHP App
# ═════════════════════════════════════════════════════════════════════
section "PHP app (localhost:${APP_PORT})"

step "GET / (entrypoint)"
probe "http://localhost:${APP_PORT}/"
if [[ "$PROBE_STATUS" =~ ^(200|302)$ ]]; then
    pass "${PROBE_TIME_MS}ms (HTTP ${PROBE_STATUS})"
else
    fail "not reachable — is start-dev.sh running?"
fi

step "GET /scribe"
probe "http://localhost:${APP_PORT}/scribe"
if [[ "$PROBE_STATUS" == "200" ]]; then
    if echo "$PROBE_BODY" | grep -qi "ambient scribe\|chat\|<html"; then
        pass
    else
        warn "responded but content unexpected"
    fi
else
    warn "scribe UI unavailable (HTTP ${PROBE_STATUS:-000})"
fi

step "GET /scribe/test/roles"
php_roles_result=$(curl -sf --connect-timeout 3 --max-time 5 \
    "http://localhost:${APP_PORT}/scribe/test/roles" 2>/dev/null) || true
if echo "$php_roles_result" | grep -q '"session_id"'; then
    pass "PHP role proxy ok"
else
    fail "endpoint not responding"
fi

# ═════════════════════════════════════════════════════════════════════
# 4. Mercure (optional — via docker compose or start-dev.sh)
# ═════════════════════════════════════════════════════════════════════
section "Mercure (localhost:${MERCURE_PORT})"

step "Hub responding"
probe "http://localhost:${MERCURE_PORT}/.well-known/mercure" 3
# Mercure returns 401 for unauthorized GET, which still means it's alive
if [[ "$PROBE_STATUS" =~ ^(200|401|400)$ ]]; then
    pass "${PROBE_TIME_MS}ms"
else
    warn "not running — set MERCURE_URL in .env and restart start-dev.sh"
fi

# ═════════════════════════════════════════════════════════════════════
# 5. Port conflicts
# ═════════════════════════════════════════════════════════════════════
section "Port usage"

PORT_LABELS=("${AGENT_PORT}:agent" "${APP_PORT}:php" "${MERCURE_PORT}:mercure")
if [[ "$ROLE_AGENT_MODEL_PROVIDER" == "ollama" ]]; then
    PORT_LABELS+=("${OLLAMA_PORT}:ollama")
fi

for port_label in "${PORT_LABELS[@]}"; do
    port="${port_label%%:*}"
    label="${port_label##*:}"
    step "Port ${port} (${label})"
    listener_count=$(ss -tlnp "sport = :${port}" 2>/dev/null | tail -n +2 | wc -l)
    listener_count="${listener_count//[^0-9]/}"
    if [[ "${listener_count:-0}" -eq 1 ]]; then
        pid=$(ss -tlnp "sport = :${port}" 2>/dev/null | grep -oP 'pid=\K[0-9]+' | head -1)
        proc=""
        if [[ -n "$pid" ]]; then
            proc=$(ps -p "$pid" -o comm= 2>/dev/null || true)
        fi
        pass "${proc:+${proc} (pid ${pid})}"
    elif [[ "${listener_count:-0}" -gt 1 ]]; then
        warn "${listener_count} listeners — possible conflict"
    else
        # Port not in use — could be fine if checking before start
        warn "no listener"
    fi
done

# ═════════════════════════════════════════════════════════════════════
# Summary
# ═════════════════════════════════════════════════════════════════════
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
    echo -e "  ${RED}${BOLD}${FAILED}/${TOTAL} checks failed${RESET}${DIM}, ${WARNED} warning(s)${RESET}"
    echo ""
    for f in "${FAILURES[@]}"; do
        echo -e "    ${FAIL}  ${f}"
    done
    echo ""
    echo -e "  ${DIM}Start services with: ./scripts/start-dev.sh${RESET}"
    echo ""
    exit 1
fi
