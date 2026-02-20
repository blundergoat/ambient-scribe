#!/bin/bash
# =============================================================================
# Install Ollama - Installs Ollama with GPU support for local development
# =============================================================================
# Usage: ./scripts/install-ollama.sh [--port PORT] [--model MODEL] [--force]
#
# Installs Ollama inside the current environment (Linux / WSL2) and verifies
# GPU detection. On WSL2, this installs a separate Ollama instance that uses
# CUDA passthrough - the Windows Ollama often lacks support for newer GPUs
# (e.g. RTX 50-series Blackwell architecture).
#
# What it does:
#   1. Installs system dependencies (zstd)
#   2. Installs Ollama via the official install script
#   3. Starts Ollama and verifies GPU detection
#   4. Pulls the configured model
#   5. Updates .env with OLLAMA_HOST
#
# Options:
#   --port PORT   Port for Ollama to listen on (default: 11435 on WSL, 11434 otherwise)
#   --model MODEL Model to pull (default: from .env OLLAMA_MODEL, or qwen2.5:7b)
#   --force       Reinstall even if Ollama is already installed
#
# After install, start Ollama with:
#   OLLAMA_HOST=0.0.0.0:PORT ollama serve
#
# Or use start-dev.sh which auto-detects and starts it.
# =============================================================================

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"

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

ERRORS=0

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

info() {
    echo -e "     ${DIM}$1${RESET}"
}

# ── Detect environment ──────────────────────────────────────────────
IS_WSL=false
if grep -qi microsoft /proc/version 2>/dev/null; then
    IS_WSL=true
fi

# ── Parse flags ─────────────────────────────────────────────────────
OLLAMA_PORT=""
OLLAMA_MODEL=""
FORCE_INSTALL=false
shift_next=""

for arg in "$@"; do
    case "$arg" in
        --port)   shift_next=port ;;
        --model)  shift_next=model ;;
        --force)  FORCE_INSTALL=true ;;
        *)
            if [[ "${shift_next}" == "port" ]]; then
                OLLAMA_PORT="$arg"
                shift_next=""
            elif [[ "${shift_next}" == "model" ]]; then
                OLLAMA_MODEL="$arg"
                shift_next=""
            else
                echo "Unknown option: $arg"
                echo "Usage: $0 [--port PORT] [--model MODEL] [--force]"
                exit 1
            fi
            ;;
    esac
done

# Defaults: WSL uses 11435 to avoid conflicts with Windows Ollama on 11434
if [[ -z "$OLLAMA_PORT" ]]; then
    if [[ "$IS_WSL" == true ]]; then
        OLLAMA_PORT=11435
    else
        OLLAMA_PORT=11434
    fi
fi

# Read model from .env if not specified
if [[ -z "$OLLAMA_MODEL" && -f "$REPO_ROOT/.env" ]]; then
    OLLAMA_MODEL="$(grep -E '^OLLAMA_MODEL=' "$REPO_ROOT/.env" 2>/dev/null | head -1 | cut -d= -f2-)"
fi
OLLAMA_MODEL="${OLLAMA_MODEL:-qwen2.5:7b}"

OLLAMA_URL="http://localhost:${OLLAMA_PORT}"

# ── Header ──────────────────────────────────────────────────────────
echo ""
echo -e "${BOLD}  Ambient Scribe - Install Ollama${RESET}"
echo -e "  ${DIM}$(printf '─%.0s' {1..44})${RESET}"
echo ""

if [[ "$IS_WSL" == true ]]; then
    echo -e "  ${CYAN}WSL2 detected${RESET} ${DIM}- installing inside WSL for GPU passthrough${RESET}"
    echo ""
fi

# ── 1. Check prerequisites ──────────────────────────────────────────
echo -e "  ${BOLD}Checking prerequisites${RESET}"
echo ""

# curl
step "curl"
if command -v curl &>/dev/null; then
    pass
else
    fail "not found - required for Ollama install"
fi

# GPU
step "NVIDIA GPU"
if command -v nvidia-smi &>/dev/null; then
    GPU_NAME=$(nvidia-smi --query-gpu=name --format=csv,noheader 2>/dev/null | head -1 | xargs)
    GPU_VRAM=$(nvidia-smi --query-gpu=memory.total --format=csv,noheader 2>/dev/null | head -1 | xargs)
    COMPUTE_CAP=$(nvidia-smi --query-gpu=compute_cap --format=csv,noheader 2>/dev/null | head -1 | xargs)
    if [[ -n "$GPU_NAME" ]]; then
        pass "${GPU_NAME} (${GPU_VRAM}, cc ${COMPUTE_CAP})"
    else
        fail "nvidia-smi found but no GPU detected"
    fi
else
    echo -e "${YELLOW}!${RESET}  ${DIM}nvidia-smi not found - Ollama will run on CPU only${RESET}"
fi

# CUDA libs (WSL-specific)
if [[ "$IS_WSL" == true ]]; then
    step "WSL CUDA libraries"
    if [[ -f /usr/lib/wsl/lib/libcuda.so.1 ]]; then
        pass
    else
        echo -e "${YELLOW}!${RESET}  ${DIM}CUDA libs missing - GPU may not work${RESET}"
        info "Ensure NVIDIA GPU driver is installed on Windows"
    fi
fi

if [[ $ERRORS -gt 0 ]]; then
    echo ""
    echo -e "  ${RED}${BOLD}Cannot continue - fix the above errors${RESET}"
    echo ""
    exit 1
fi

# ── 2. Install system dependencies ──────────────────────────────────
echo ""
echo -e "  ${BOLD}System dependencies${RESET}"
echo ""

step "zstd"
if command -v zstd &>/dev/null; then
    pass "already installed"
else
    if sudo apt-get install -y zstd >/dev/null 2>&1; then
        pass "installed"
    else
        fail "could not install - run: sudo apt-get install zstd"
    fi
fi

# ── 3. Install Ollama ───────────────────────────────────────────────
echo ""
echo -e "  ${BOLD}Installing Ollama${RESET}"
echo ""

OLLAMA_BIN="/usr/local/bin/ollama"
NEEDS_INSTALL=true

if [[ -f "$OLLAMA_BIN" && "$FORCE_INSTALL" != true ]]; then
    CURRENT_VERSION=$("$OLLAMA_BIN" --version 2>/dev/null | grep -oE '[0-9]+\.[0-9]+\.[0-9]+' || echo "unknown")
    step "Ollama binary"
    pass "v${CURRENT_VERSION} already installed"
    NEEDS_INSTALL=false
fi

if [[ "$NEEDS_INSTALL" == true ]]; then
    step "Downloading & installing Ollama"
    install_output=$(curl -fsSL https://ollama.com/install.sh | sh 2>&1)
    install_exit=$?
    if [[ $install_exit -eq 0 ]]; then
        INSTALLED_VERSION=$("$OLLAMA_BIN" --version 2>/dev/null | grep -oE '[0-9]+\.[0-9]+\.[0-9]+' || echo "unknown")
        pass "v${INSTALLED_VERSION}"

        # Check if installer detected the GPU
        if echo "$install_output" | grep -qi "nvidia gpu detected"; then
            step "Installer GPU check"
            pass "NVIDIA GPU detected by installer"
        fi
    else
        fail "install failed — check https://ollama.com/download"
        echo "$install_output" | tail -5 | while IFS= read -r line; do
            info "$line"
        done
        echo ""
        exit 1
    fi
fi

# ── 4. Start Ollama and verify GPU ──────────────────────────────────
echo ""
echo -e "  ${BOLD}Starting Ollama${RESET}"
echo ""

OLLAMA_LOG="/tmp/ollama-install-test.log"

# Check if Ollama is already running on our target port
step "Ollama on port ${OLLAMA_PORT}"
if curl -sf "${OLLAMA_URL}/" >/dev/null 2>&1; then
    pass "already running"
else
    # Start Ollama in the background
    OLLAMA_HOST="0.0.0.0:${OLLAMA_PORT}" "$OLLAMA_BIN" serve >"$OLLAMA_LOG" 2>&1 &
    OLLAMA_PID=$!

    # Wait for it to start (up to 15 seconds)
    STARTED=false
    for i in $(seq 1 15); do
        if curl -sf "${OLLAMA_URL}/" >/dev/null 2>&1; then
            STARTED=true
            break
        fi
        sleep 1
    done

    if [[ "$STARTED" == true ]]; then
        pass "started (pid ${OLLAMA_PID})"
    else
        fail "failed to start — check ${OLLAMA_LOG}"
        if [[ -f "$OLLAMA_LOG" ]]; then
            tail -5 "$OLLAMA_LOG" | while IFS= read -r line; do
                info "$line"
            done
        fi
        exit 1
    fi
fi

# Check GPU detection from Ollama logs
step "GPU detection"
sleep 2  # Give Ollama a moment to finish GPU probing
if [[ -f "$OLLAMA_LOG" ]]; then
    GPU_LINE=$(grep -i "inference compute" "$OLLAMA_LOG" 2>/dev/null | tail -1 || true)
    if [[ -n "$GPU_LINE" ]]; then
        GPU_LIB=$(echo "$GPU_LINE" | grep -oP 'library=\K\w+' || echo "unknown")
        GPU_AVAIL=$(echo "$GPU_LINE" | grep -oP 'available="\K[^"]+' || echo "unknown")
        GPU_COMPUTE=$(echo "$GPU_LINE" | grep -oP 'compute=\K[0-9.]+' || echo "unknown")
        pass "${GPU_LIB} (compute ${GPU_COMPUTE}, ${GPU_AVAIL} available)"
    else
        NO_GPU=$(grep -iE "no compatible GPU|no gpu|cpu only" "$OLLAMA_LOG" 2>/dev/null || true)
        if [[ -n "$NO_GPU" ]]; then
            echo -e "${YELLOW}!${RESET}  ${DIM}No GPU detected — Ollama will use CPU${RESET}"
            info "This is much slower. Check GPU drivers and CUDA installation."
        else
            echo -e "${YELLOW}!${RESET}  ${DIM}Could not confirm GPU status from logs${RESET}"
        fi
    fi
else
    echo -e "${DIM}skipped${RESET}  ${DIM}(Ollama was already running — check with: curl ${OLLAMA_URL}/api/ps)${RESET}"
fi

# ── 5. Pull model ───────────────────────────────────────────────────
echo ""
echo -e "  ${BOLD}Pulling model${RESET}"
echo ""

step "Model ${OLLAMA_MODEL}"
if curl -sf "${OLLAMA_URL}/api/tags" 2>/dev/null | grep -q "\"${OLLAMA_MODEL}\""; then
    pass "already available"
else
    echo -e "${YELLOW}downloading...${RESET}"
    info "This may take a few minutes on first install"
    echo ""

    if OLLAMA_HOST="${OLLAMA_URL}" "$OLLAMA_BIN" pull "$OLLAMA_MODEL" 2>&1; then
        step "Model ${OLLAMA_MODEL}"
        pass "ready"
    else
        step "Model ${OLLAMA_MODEL}"
        fail "pull failed"
    fi
fi

# Verify model loads on GPU
step "GPU offloading"
curl -s "${OLLAMA_URL}/api/generate" -d "{
  \"model\": \"${OLLAMA_MODEL}\",
  \"prompt\": \"hi\",
  \"options\": {\"num_predict\": 1}
}" >/dev/null 2>&1

# Give model a moment to settle in VRAM
sleep 1

PS_OUTPUT=$(curl -s "${OLLAMA_URL}/api/ps" 2>/dev/null)
VRAM_BYTES=$(echo "$PS_OUTPUT" | python3 -c "
import sys, json
try:
    d = json.load(sys.stdin)
    for m in d.get('models', []):
        print(m.get('size_vram', 0))
        break
except: print(0)
" 2>/dev/null || echo "0")

VRAM_GB="0"
if [[ "$VRAM_BYTES" -gt 0 ]] 2>/dev/null; then
    VRAM_GB=$(python3 -c "print(f'{${VRAM_BYTES}/(1024**3):.1f}')" 2>/dev/null || echo "?")
    pass "${VRAM_GB} GB in VRAM"
else
    echo -e "${YELLOW}!${RESET}  ${DIM}Model not loaded to GPU — will run on CPU (slower)${RESET}"
    info "Check Ollama logs: ${OLLAMA_LOG}"
fi

# ── 6. Update .env ──────────────────────────────────────────────────
echo ""
echo -e "  ${BOLD}Updating .env${RESET}"
echo ""

if [[ -f "$REPO_ROOT/.env" ]]; then
    EXPECTED_HOST="http://localhost:${OLLAMA_PORT}"

    # Add or update OLLAMA_HOST
    if grep -q '^OLLAMA_HOST=' "$REPO_ROOT/.env" 2>/dev/null; then
        CURRENT_HOST=$(grep -E '^OLLAMA_HOST=' "$REPO_ROOT/.env" | head -1 | cut -d= -f2-)
        if [[ "$CURRENT_HOST" != "$EXPECTED_HOST" ]]; then
            sed -i "s|^OLLAMA_HOST=.*|OLLAMA_HOST=${EXPECTED_HOST}|" "$REPO_ROOT/.env"
            step "OLLAMA_HOST"
            pass "updated to ${EXPECTED_HOST}"
        else
            step "OLLAMA_HOST"
            pass "already set to ${EXPECTED_HOST}"
        fi
    else
        # Insert OLLAMA_HOST after the Ollama settings comment block
        if grep -q '^OLLAMA_MODEL=' "$REPO_ROOT/.env"; then
            sed -i "/^OLLAMA_MODEL=/a OLLAMA_HOST=${EXPECTED_HOST}" "$REPO_ROOT/.env"
        else
            echo "OLLAMA_HOST=${EXPECTED_HOST}" >> "$REPO_ROOT/.env"
        fi
        step "OLLAMA_HOST"
        pass "added ${EXPECTED_HOST}"
    fi

    # Ensure OLLAMA_MODEL matches
    if grep -q '^OLLAMA_MODEL=' "$REPO_ROOT/.env" 2>/dev/null; then
        CURRENT_MODEL=$(grep -E '^OLLAMA_MODEL=' "$REPO_ROOT/.env" | head -1 | cut -d= -f2-)
        if [[ "$CURRENT_MODEL" != "$OLLAMA_MODEL" ]]; then
            sed -i "s|^OLLAMA_MODEL=.*|OLLAMA_MODEL=${OLLAMA_MODEL}|" "$REPO_ROOT/.env"
            step "OLLAMA_MODEL"
            pass "updated to ${OLLAMA_MODEL}"
        fi
    fi
else
    step ".env"
    echo -e "${YELLOW}!${RESET}  ${DIM}not found — run ./scripts/setup-initial.sh first${RESET}"
fi

# ── Summary ─────────────────────────────────────────────────────────
echo ""
echo -e "  ${DIM}$(printf '─%.0s' {1..44})${RESET}"
echo ""

if [[ $ERRORS -eq 0 ]]; then
    echo -e "  ${GREEN}${BOLD}Ollama installed and ready!${RESET}"
    echo ""
    echo -e "  ${DIM}Configuration:${RESET}"
    echo -e "    ${ARROW} Host:    ${BOLD}${OLLAMA_URL}${RESET}"
    echo -e "    ${ARROW} Model:   ${BOLD}${OLLAMA_MODEL}${RESET}"
    if [[ "$VRAM_BYTES" -gt 0 ]] 2>/dev/null; then
        echo -e "    ${ARROW} GPU:     ${GREEN}${BOLD}enabled${RESET} ${DIM}(${VRAM_GB} GB VRAM)${RESET}"
    else
        echo -e "    ${ARROW} GPU:     ${YELLOW}CPU only${RESET}"
    fi
    echo ""
    echo -e "  ${DIM}Next steps:${RESET}"
    echo -e "    ${ARROW} Start dev server:  ${BOLD}./scripts/start-dev.sh${RESET}"
    echo -e "    ${ARROW} Restart Ollama:    ${BOLD}OLLAMA_HOST=0.0.0.0:${OLLAMA_PORT} ollama serve${RESET}"
    echo ""
    if [[ "$IS_WSL" == true ]]; then
        echo -e "  ${DIM}Note: Ollama is running as a background process (not a system service).${RESET}"
        echo -e "  ${DIM}It will stop when you close this terminal. start-dev.sh will auto-start it.${RESET}"
        echo ""
    fi
else
    echo -e "  ${RED}${BOLD}Installation finished with ${ERRORS} error(s)${RESET}"
    echo ""
    exit 1
fi
