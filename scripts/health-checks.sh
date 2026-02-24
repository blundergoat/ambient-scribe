#!/bin/bash
# =============================================================================
# Health Check — Read-only diagnostics for Ambient Scribe
# =============================================================================
# Usage: ./scripts/health-checks.sh
#
# Checks Docker, GPU, containers, services, configuration, and connectivity.
# Does not modify anything — safe to run at any time.
#
# Exit codes:
#   0 - All checks passed (warnings are OK)
#   1 - One or more checks failed
# =============================================================================

set -uo pipefail

source "$(dirname "${BASH_SOURCE[0]}")/env-detect.sh"

TOTAL=0
PASSED=0
FAILED=0
WARNINGS=0

# ── check() — unified pass/warn/fail reporter ───────────────────────
check() {
    local label="$1" status="$2" detail="${3:-}"
    TOTAL=$((TOTAL + 1))
    local padded
    padded=$(printf "%-32s" "$label")
    if [[ "$status" == "pass" ]]; then
        PASSED=$((PASSED + 1))
        echo -e "  ${ARROW} ${padded} ${PASS}  ${DIM}${detail}${RESET}"
    elif [[ "$status" == "warn" ]]; then
        WARNINGS=$((WARNINGS + 1))
        echo -e "  ${ARROW} ${padded} ${WARN}  ${YELLOW}${detail}${RESET}"
    else
        FAILED=$((FAILED + 1))
        echo -e "  ${ARROW} ${padded} ${FAIL}  ${RED}${detail}${RESET}"
    fi
}

# ── Header ──────────────────────────────────────────────────────────
echo ""
echo -e "${BOLD}  Ambient Scribe — Health Check${RESET}"
echo -e "  ${DIM}$(printf '─%.0s' {1..44})${RESET}"

# ═════════════════════════════════════════════════════════════════════
# Docker
# ═════════════════════════════════════════════════════════════════════
echo ""
echo -e "  ${BOLD}Docker${RESET}"
echo ""

if ! command -v docker &>/dev/null; then
    check "Docker" "fail" "not installed"
elif ! docker ps &>/dev/null 2>&1; then
    check "Docker daemon" "fail" "not running"
else
    check "Docker daemon" "pass" "running"
fi

# ═════════════════════════════════════════════════════════════════════
# GPU
# ═════════════════════════════════════════════════════════════════════
echo ""
echo -e "  ${BOLD}GPU${RESET}"
echo ""

if [[ "$HAS_NVIDIA_SMI" == "true" ]]; then
    check "nvidia-smi" "pass" "available"
    check "GPU model" "pass" "${GPU_NAME}"

    vram_gb=$(( GPU_VRAM_MB / 1024 ))
    if [[ $GPU_VRAM_MB -ge 12288 ]]; then
        check "GPU VRAM" "pass" "${vram_gb} GB (${GPU_VRAM_MB} MB)"
    else
        check "GPU VRAM" "warn" "${vram_gb} GB — NeMo needs >= 12 GB"
    fi

    # Docker GPU passthrough
    if docker run --rm --gpus all nvidia/cuda:12.4.0-base-ubuntu22.04 nvidia-smi &>/dev/null; then
        check "Docker GPU passthrough" "pass" "working"
    else
        check "Docker GPU passthrough" "fail" "docker cannot access GPU"
    fi

    # Current VRAM usage
    vram_used=$(nvidia-smi --query-gpu=memory.used --format=csv,noheader,nounits 2>/dev/null | head -1)
    vram_used="${vram_used// /}"
    if [[ -n "$vram_used" ]]; then
        vram_pct=$(( vram_used * 100 / GPU_VRAM_MB ))
        if [[ $vram_pct -gt 90 ]]; then
            check "VRAM usage" "warn" "${vram_used}/${GPU_VRAM_MB} MB (${vram_pct}%)"
        else
            check "VRAM usage" "pass" "${vram_used}/${GPU_VRAM_MB} MB (${vram_pct}%)"
        fi
    fi
else
    check "nvidia-smi" "fail" "not found — install NVIDIA drivers"
    check "GPU model" "fail" "n/a"
    check "GPU VRAM" "fail" "n/a"
    check "Docker GPU passthrough" "fail" "n/a"
fi

# ═════════════════════════════════════════════════════════════════════
# Containers
# ═════════════════════════════════════════════════════════════════════
echo ""
echo -e "  ${BOLD}Containers${RESET}"
echo ""

# Refresh container state
_RUNNING_CONTAINERS="$(docker ps --format '{{.Names}}' 2>/dev/null || true)"
NEMO_CONTAINER=$(_resolve_container "${COMPOSE_PROJECT}-nemo-agent-1" "nemo-agent")
APP_CONTAINER=$(_resolve_container "${COMPOSE_PROJECT}-app-1" "app")
MERCURE_CONTAINER=$(_resolve_container "${COMPOSE_PROJECT}-mercure-1" "mercure")

for pair in "$NEMO_CONTAINER:nemo-agent" "$APP_CONTAINER:app" "$MERCURE_CONTAINER:mercure"; do
    container="${pair%%:*}"
    label="${pair##*:}"

    if find_container "$container"; then
        health=$(docker inspect "$container" --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}no-healthcheck{{end}}' 2>/dev/null || echo "unknown")
        if [[ "$health" == "healthy" ]]; then
            check "$label" "pass" "running, healthy"
        elif [[ "$health" == "no-healthcheck" ]]; then
            check "$label" "pass" "running"
        else
            check "$label" "warn" "running, ${health}"
        fi
    else
        check "$label" "fail" "not running"
    fi
done

# ═════════════════════════════════════════════════════════════════════
# Services (HTTP probes)
# ═════════════════════════════════════════════════════════════════════
echo ""
echo -e "  ${BOLD}Services${RESET}"
echo ""

# nemo-agent /health
HTTP_CODE=$(curl -sf -o /dev/null -w "%{http_code}" --connect-timeout 5 "http://localhost:8001/health" 2>/dev/null) || HTTP_CODE="000"
if [[ "$HTTP_CODE" == "200" ]]; then
    check "nemo-agent /health" "pass" "HTTP ${HTTP_CODE}"
elif [[ "$HTTP_CODE" != "000" ]]; then
    check "nemo-agent /health" "warn" "HTTP ${HTTP_CODE}"
else
    check "nemo-agent /health" "fail" "not reachable"
fi

# app /scribe
HTTP_CODE=$(curl -sf -o /dev/null -w "%{http_code}" --connect-timeout 5 "http://localhost:8082/scribe" 2>/dev/null) || HTTP_CODE="000"
if [[ "$HTTP_CODE" == "200" ]]; then
    check "app /scribe" "pass" "HTTP ${HTTP_CODE}"
elif [[ "$HTTP_CODE" != "000" ]]; then
    check "app /scribe" "warn" "HTTP ${HTTP_CODE}"
else
    check "app /scribe" "fail" "not reachable"
fi

# Mercure hub
HTTP_CODE=$(curl -sf -o /dev/null -w "%{http_code}" --connect-timeout 5 "http://localhost:3701/.well-known/mercure" 2>/dev/null) || HTTP_CODE="000"
if [[ "$HTTP_CODE" =~ ^(200|401|400)$ ]]; then
    check "Mercure hub" "pass" "HTTP ${HTTP_CODE}"
elif [[ "$HTTP_CODE" != "000" ]]; then
    check "Mercure hub" "warn" "HTTP ${HTTP_CODE}"
else
    check "Mercure hub" "fail" "not reachable"
fi

# ═════════════════════════════════════════════════════════════════════
# Configuration
# ═════════════════════════════════════════════════════════════════════
echo ""
echo -e "  ${BOLD}Configuration${RESET}"
echo ""

# .env exists
if [[ -f "$REPO_ROOT/.env" ]]; then
    check ".env file" "pass" "exists"
else
    check ".env file" "fail" "not found — run setup-initial.sh"
fi

# MERCURE_JWT_SECRET
if [[ -z "${MERCURE_JWT_SECRET:-}" ]]; then
    check "MERCURE_JWT_SECRET" "fail" "not set"
elif [[ ${#MERCURE_JWT_SECRET} -lt 32 ]]; then
    check "MERCURE_JWT_SECRET" "fail" "too short (${#MERCURE_JWT_SECRET} chars, need >= 32)"
else
    check "MERCURE_JWT_SECRET" "pass" "${#MERCURE_JWT_SECRET} chars"
fi

# MERCURE_PUBLISHER_JWT
if [[ -n "${MERCURE_PUBLISHER_JWT:-}" ]]; then
    check "MERCURE_PUBLISHER_JWT" "pass" "set (${#MERCURE_PUBLISHER_JWT} chars)"
else
    check "MERCURE_PUBLISHER_JWT" "fail" "not set — run setup-initial.sh to generate"
fi

# ROLE_AGENT_MODEL_PROVIDER
if [[ -n "${ROLE_AGENT_MODEL_PROVIDER:-}" ]]; then
    check "ROLE_AGENT_MODEL_PROVIDER" "pass" "${ROLE_AGENT_MODEL_PROVIDER}"
else
    check "ROLE_AGENT_MODEL_PROVIDER" "warn" "not set (defaults to bedrock)"
fi

# ═════════════════════════════════════════════════════════════════════
# Connectivity (inter-container)
# ═════════════════════════════════════════════════════════════════════
echo ""
echo -e "  ${BOLD}Connectivity${RESET}"
echo ""

# nemo-agent → Mercure
if find_container "$NEMO_CONTAINER"; then
    INTERNAL_CODE=$(docker exec "$NEMO_CONTAINER" curl -sf -o /dev/null -w "%{http_code}" --connect-timeout 3 "http://mercure:3701/.well-known/mercure" 2>/dev/null) || INTERNAL_CODE="000"
    if [[ "$INTERNAL_CODE" =~ ^(200|401|400)$ ]]; then
        check "nemo-agent → Mercure" "pass" "HTTP ${INTERNAL_CODE}"
    elif [[ "$INTERNAL_CODE" != "000" ]]; then
        check "nemo-agent → Mercure" "warn" "HTTP ${INTERNAL_CODE}"
    else
        check "nemo-agent → Mercure" "fail" "not reachable from nemo-agent"
    fi
else
    check "nemo-agent → Mercure" "fail" "nemo-agent not running"
fi

# app → nemo-agent
if find_container "$APP_CONTAINER"; then
    INTERNAL_CODE=$(docker exec "$APP_CONTAINER" curl -sf -o /dev/null -w "%{http_code}" --connect-timeout 3 "http://nemo-agent:8000/health" 2>/dev/null) || INTERNAL_CODE="000"
    if [[ "$INTERNAL_CODE" == "200" ]]; then
        check "app → nemo-agent" "pass" "HTTP ${INTERNAL_CODE}"
    elif [[ "$INTERNAL_CODE" != "000" ]]; then
        check "app → nemo-agent" "warn" "HTTP ${INTERNAL_CODE}"
    else
        check "app → nemo-agent" "fail" "not reachable from app"
    fi
else
    check "app → nemo-agent" "fail" "app not running"
fi

# ffmpeg in nemo container
if find_container "$NEMO_CONTAINER"; then
    if docker exec "$NEMO_CONTAINER" which ffmpeg &>/dev/null; then
        ffmpeg_ver=$(docker exec "$NEMO_CONTAINER" ffmpeg -version 2>/dev/null | head -1 | grep -oE '[0-9]+\.[0-9]+(\.[0-9]+)?' | head -1)
        check "ffmpeg (nemo container)" "pass" "v${ffmpeg_ver}"
    else
        check "ffmpeg (nemo container)" "fail" "not installed in container"
    fi
else
    check "ffmpeg (nemo container)" "fail" "nemo-agent not running"
fi

# ═════════════════════════════════════════════════════════════════════
# Summary
# ═════════════════════════════════════════════════════════════════════
echo ""
echo -e "  ${DIM}$(printf '─%.0s' {1..44})${RESET}"
echo ""

if [[ $FAILED -eq 0 && $WARNINGS -eq 0 ]]; then
    echo -e "  ${GREEN}${BOLD}All ${TOTAL} checks passed${RESET}"
elif [[ $FAILED -eq 0 ]]; then
    echo -e "  ${GREEN}${BOLD}${PASSED} passed${RESET}, ${YELLOW}${WARNINGS} warning(s)${RESET}  ${DIM}(${TOTAL} total)${RESET}"
else
    echo -e "  ${RED}${BOLD}${FAILED} failed${RESET}, ${GREEN}${PASSED} passed${RESET}, ${YELLOW}${WARNINGS} warning(s)${RESET}  ${DIM}(${TOTAL} total)${RESET}"
fi

echo ""
echo -e "  ${DIM}Services:${RESET}"
echo -e "    ${ARROW} Scribe UI:     ${BOLD}http://localhost:8082/scribe${RESET}"
echo -e "    ${ARROW} NeMo agent:    ${BOLD}http://localhost:8001${RESET}"
echo -e "    ${ARROW} Mercure:       ${BOLD}http://localhost:3701${RESET}"
echo ""

[[ $FAILED -gt 0 ]] && exit 1
exit 0
