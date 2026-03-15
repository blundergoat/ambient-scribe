#!/bin/bash
# =============================================================================
# GPU Prerequisites Check — Ambient Scribe
# =============================================================================
#
# Verifies that the NVIDIA GPU is accessible from both the host and Docker.
# This must pass before attempting to build or run the NeMo container.
#
# Usage:
#   ./scripts/gpu-check.sh
#
# What it checks:
#   1. nvidia-smi is available on the host (NVIDIA driver installed)
#   2. GPU model and VRAM are reported
#   3. Docker can access the GPU via --gpus flag (NVIDIA Container Toolkit)
#   4. CUDA version inside the container
#
# If either check fails, the script prints troubleshooting steps.
# =============================================================================

set -uo pipefail

source "$(dirname "${BASH_SOURCE[0]}")/env-detect.sh"

echo ""
echo -e "${BOLD}Ambient Scribe — GPU Prerequisites Check${RESET}"
echo -e "${DIM}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${RESET}"
echo ""

FAILED=0

# --- Check 1: nvidia-smi on the host ---
echo -e "  ${ARROW} Checking nvidia-smi on host..."
echo ""

if ! command -v nvidia-smi &>/dev/null; then
    echo -e "  ${FAIL} nvidia-smi not found"
    echo ""
    echo "  Troubleshooting:"
    echo "    - Install NVIDIA drivers: https://developer.nvidia.com/drivers"
    echo "    - WSL2: install the Windows NVIDIA driver (not the Linux one)"
    echo "    - After install, restart WSL: wsl --shutdown"
    echo ""
    FAILED=1
else
    echo -e "  ${PASS} nvidia-smi found"
    echo ""
    nvidia-smi
    echo ""

    # Extract GPU name and VRAM
    GPU_NAME=$(nvidia-smi --query-gpu=name --format=csv,noheader 2>/dev/null | head -1)
    GPU_VRAM=$(nvidia-smi --query-gpu=memory.total --format=csv,noheader 2>/dev/null | head -1)
    DRIVER_VERSION=$(nvidia-smi --query-gpu=driver_version --format=csv,noheader 2>/dev/null | head -1)
    CUDA_VERSION=$(nvidia-smi | grep -oP 'CUDA Version: \K[0-9.]+' 2>/dev/null)

    echo -e "  ${PASS} GPU:    ${BOLD}${GPU_NAME}${RESET}"
    echo -e "  ${PASS} VRAM:   ${BOLD}${GPU_VRAM}${RESET}"
    echo -e "  ${PASS} Driver: ${BOLD}${DRIVER_VERSION}${RESET}"
    echo -e "  ${PASS} CUDA:   ${BOLD}${CUDA_VERSION}${RESET}"
    echo ""
fi

# --- Check 2: Docker GPU passthrough ---
echo -e "${DIM}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${RESET}"
echo ""
echo -e "  ${ARROW} Checking Docker GPU passthrough..."
echo ""

if ! command -v docker &>/dev/null; then
    echo -e "  ${FAIL} Docker not found"
    echo ""
    echo "  Troubleshooting:"
    echo "    - Install Docker Desktop with WSL2 backend"
    echo "    - Or install Docker Engine: https://docs.docker.com/engine/install/"
    echo ""
    FAILED=1
else
    DOCKER_GPU_OUTPUT=$(docker run --rm --gpus all nvidia/cuda:12.4.0-base-ubuntu22.04 nvidia-smi 2>&1)
    DOCKER_EXIT=$?

    if [ $DOCKER_EXIT -ne 0 ]; then
        echo -e "  ${FAIL} Docker cannot access the GPU"
        echo ""
        echo "  Output:"
        echo "  $DOCKER_GPU_OUTPUT" | head -5
        echo ""
        echo "  Troubleshooting:"
        echo "    - Install NVIDIA Container Toolkit:"
        echo "        https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/install-guide.html"
        echo "    - After install: sudo systemctl restart docker"
        echo "    - Docker Desktop: Settings → Resources → enable GPU support"
        echo "    - Verify: docker run --rm --gpus all nvidia/cuda:12.4.0-base-ubuntu22.04 nvidia-smi"
        echo ""
        FAILED=1
    else
        echo -e "  ${PASS} Docker GPU passthrough works"
        echo ""
        echo "$DOCKER_GPU_OUTPUT"
        echo ""
    fi
fi

# --- Summary ---
echo -e "${DIM}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${RESET}"
echo ""

if [ $FAILED -eq 0 ]; then
    echo -e "  ${PASS} ${GREEN}${BOLD}All checks passed.${RESET} GPU is ready for NeMo."
    echo ""
    echo "  Next step: build the NeMo container"
    echo "    docker build -t ambient-scribe-nemo ./docker/nemo"
    echo ""
else
    echo -e "  ${FAIL} ${RED}${BOLD}Some checks failed.${RESET} Fix the issues above before proceeding."
    echo ""
fi

exit $FAILED
