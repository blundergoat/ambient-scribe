#!/bin/bash
# =============================================================================
# env-detect.sh - Shared environment context for ambient-scribe scripts
# =============================================================================
# Source this file from any script:
#   source "$(dirname "${BASH_SOURCE[0]}")/env-detect.sh"
#
# Provides:
#   REPO_ROOT              - Project root directory
#   SCRIPTS_DIR            - Scripts directory
#   COMPOSE_FILE           - Path to docker-compose.yml
#   COMPOSE_PROJECT        - Docker Compose project name (ambient-scribe)
#
# Functions:
#   dc [args...]           - Wraps docker compose with project file/name
#   find_container <name>  - Returns 0 if container is running
#
# GPU detection:
#   HAS_NVIDIA_SMI         - "true" if nvidia-smi is available
#   GPU_NAME               - GPU model name (empty if no GPU)
#   GPU_VRAM_MB            - Total VRAM in MB (0 if no GPU)
#
# Colors:
#   RED, GREEN, YELLOW, BLUE, CYAN, DIM, BOLD, RESET
#
# Symbols:
#   PASS, FAIL, WARN, ARROW
# =============================================================================

# Prevent double-sourcing
if [[ -n "${_ENV_DETECT_LOADED:-}" ]]; then
    return 0 2>/dev/null || true
fi
_ENV_DETECT_LOADED=1

# --- Path detection ---
SCRIPTS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPTS_DIR/.." && pwd)"
COMPOSE_FILE="$REPO_ROOT/docker-compose.yml"
COMPOSE_PROJECT="ambient-scribe"

# --- Source .env if present ---
if [[ -f "$REPO_ROOT/.env" ]]; then
    set -o allexport
    # shellcheck disable=SC1091
    source "$REPO_ROOT/.env"
    set +o allexport
fi

# --- Docker Compose wrapper ---
# Every script uses this instead of typing `docker compose` directly.
dc() {
    docker compose -f "$COMPOSE_FILE" -p "$COMPOSE_PROJECT" "$@"
}

# --- Container name resolution ---
# Fetch running container names once for efficient lookup.
_RUNNING_CONTAINERS="$(docker ps --format '{{.Names}}' 2>/dev/null || true)"

_resolve_container() {
    local candidates=("$@")
    for name in "${candidates[@]}"; do
        if echo "$_RUNNING_CONTAINERS" | grep -q "^${name}$"; then
            echo "$name"
            return 0
        fi
    done
    echo "${candidates[0]}"  # default to first candidate
}

# Check if a container is running. Returns 0 if running, 1 if not.
find_container() {
    echo "$_RUNNING_CONTAINERS" | grep -q "^${1}$"
}

# Resolve ambient-scribe container names (handles compose naming convention)
NEMO_CONTAINER=$(_resolve_container "${COMPOSE_PROJECT}-nemo-agent-1" "nemo-agent")
APP_CONTAINER=$(_resolve_container "${COMPOSE_PROJECT}-app-1" "app")
MERCURE_CONTAINER=$(_resolve_container "${COMPOSE_PROJECT}-mercure-1" "mercure")

# --- GPU detection (host-side nvidia-smi only, no container launch) ---
# HAS_NVIDIA_SMI means "an adapter is actually visible", not just "the binary
# exists": on WSL2 the GPU adapter can silently drop while nvidia-smi still
# exits 0 with EMPTY output, and the failure would otherwise only surface later
# as a cryptic "no adapters were found" container start error
# (footguns/runtime.md, search: "silently move NeMo to CPU").
HAS_NVIDIA_SMI="false"
NVIDIA_SMI_PRESENT="false"
GPU_NAME=""
GPU_VRAM_MB=0

# The driver tooling is installed; now prove an adapter actually answers.
if command -v nvidia-smi &>/dev/null; then
    NVIDIA_SMI_PRESENT="true"
    GPU_NAME=$(nvidia-smi --query-gpu=name --format=csv,noheader 2>/dev/null | head -1)
    _vram_raw=$(nvidia-smi --query-gpu=memory.total --format=csv,noheader,nounits 2>/dev/null | head -1)
    GPU_VRAM_MB="${_vram_raw:-0}"
    # Strip whitespace
    GPU_VRAM_MB="${GPU_VRAM_MB// /}"
    unset _vram_raw

    # Only a named adapter counts as a usable GPU for live transcription.
    if [[ -n "${GPU_NAME// /}" ]]; then
        HAS_NVIDIA_SMI="true"
    fi
fi

# --- Colors ---
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
DIM='\033[2m'
BOLD='\033[1m'
RESET='\033[0m'

# --- Symbols ---
PASS="${GREEN}✔${RESET}"
FAIL="${RED}✘${RESET}"
WARN="${YELLOW}○${RESET}"
ARROW="${BLUE}▸${RESET}"

# --- Export ---
export REPO_ROOT SCRIPTS_DIR COMPOSE_FILE COMPOSE_PROJECT
export NEMO_CONTAINER APP_CONTAINER MERCURE_CONTAINER
export HAS_NVIDIA_SMI GPU_NAME GPU_VRAM_MB
