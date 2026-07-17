#!/bin/bash
# Install a CPU-only Ollama provider for consultation role labels and notes.
# Use this setup when a developer chooses local/offline AI instead of Bedrock.
# The selected model is pulled, smoke-tested, and proven to use zero VRAM.
# Existing Ollama processes are never killed or reconfigured automatically.
# NeMo keeps exclusive access to the workstation's GPU.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DEFAULT_OLLAMA_MODEL="qwen3.5:9b"
OLLAMA_BINARY="/usr/local/bin/ollama"
OLLAMA_LOG="/tmp/ollama-install-test.log"
OLLAMA_INSTALL_SCRIPT=""
MODEL_TAGS_RESPONSE=""
MODEL_PROCESS_RESPONSE=""

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
BLUE='\033[0;34m'
DIM='\033[2m'
BOLD='\033[1m'
RESET='\033[0m'

PASS="${GREEN}✔${RESET}"
FAIL="${RED}✘${RESET}"
ARROW="${BLUE}▸${RESET}"
ERRORS=0

# Show how an operator selects the local model without changing project configuration.
usage() {
    cat <<'USAGE'
Usage: ./scripts/install-ollama.sh [--port PORT] [--model MODEL] [--force]

Install and verify a CPU-only Ollama server for Ambient Scribe.

Options:
  --port PORT   Ollama port (default: 11435 on WSL, 11434 otherwise)
  --model MODEL Exact model tag to pull; overrides .env and the qwen3.5:9b default
  --force       Reinstall the Ollama binary even when it already exists

Model precedence:
  --model -> .env ROLE_AGENT_OLLAMA_MODEL -> legacy .env OLLAMA_MODEL -> qwen3.5:9b

The installer never edits .env. After it finishes, add this setting manually if needed:
  ROLE_AGENT_OLLAMA_MODEL=<selected model>
USAGE
}

# Print one aligned setup check before its pass/fail result.
step() {
    printf "  ${ARROW} %-44s" "$1"
}

# Mark one setup requirement ready for the operator.
pass() {
    local detail="${1:-}"

    # An available detail explains what the user can rely on next.
    if [[ -n "$detail" ]]; then
        echo -e "${PASS}  ${DIM}${detail}${RESET}"
    # A detail-free pass keeps compact checks readable.
    else
        echo -e "${PASS}"
    fi
}

# Record a setup failure that must be fixed before local notes can work.
fail() {
    local message="$1"
    ERRORS=$((ERRORS + 1))
    echo -e "${FAIL}  ${RED}${message}${RESET}"
}

# Add a supporting instruction beneath the current operator check.
info() {
    echo -e "     ${DIM}$1${RESET}"
}

# Remove only installer-owned scratch responses when this command ends.
cleanup_installer_files() {
    local scratch_file

    # Each non-empty path was created by this run and contains no consultation content.
    for scratch_file in "$OLLAMA_INSTALL_SCRIPT" "$MODEL_TAGS_RESPONSE" "$MODEL_PROCESS_RESPONSE"; do
        # An unset path means that setup stage never needed its scratch file.
        if [[ -n "$scratch_file" && -f "$scratch_file" ]]; then
            rm -f -- "$scratch_file"
        fi
    done
}

trap cleanup_installer_files EXIT

# Read one local model setting without printing or rewriting the user's .env file.
env_file_value() {
    local setting_name="$1"
    local configured_value

    # A developer without local overrides receives the checked-in Qwen default.
    if [[ ! -f "$REPO_ROOT/.env" ]]; then
        return 0
    fi

    configured_value="$(
        grep -E "^[[:space:]]*(export[[:space:]]+)?${setting_name}=" "$REPO_ROOT/.env" \
            2>/dev/null \
            | head -1 \
            | cut -d= -f2-
    )"
    configured_value="${configured_value#"${configured_value%%[![:space:]]*}"}"
    configured_value="${configured_value%"${configured_value##*[![:space:]]}"}"
    configured_value="${configured_value%\"}"
    configured_value="${configured_value#\"}"
    configured_value="${configured_value%\'}"
    configured_value="${configured_value#\'}"
    printf '%s' "$configured_value"
}

# Normalize a bare Ollama name to the exact tag returned by the model API.
exact_ollama_tag() {
    local configured_model="$1"

    # A bare name means the operator selected Ollama's `latest` tag.
    if [[ "$configured_model" != *:* ]]; then
        printf '%s:latest' "$configured_model"
        return 0
    fi

    printf '%s' "$configured_model"
}

# Confirm `/api/tags` contains the exact requested model, never a same-prefix variant.
model_tag_is_available() {
    local response_path="$1"
    local expected_model_tag="$2"

    python3 -c '
import json
import sys

response_path, expected_model_tag = sys.argv[1:3]
with open(response_path, encoding="utf-8") as response_file:
    response = json.load(response_file)
# An empty inventory means the selected consultation model is not installed.
available_models = response.get("models", [])
available_tags = set()
# Each API record contributes one exact tag for the operator-selected model check.
for available_model in available_models:
    # A malformed record cannot prove that the requested model is installed.
    if not isinstance(available_model, dict):
        continue
    # A missing model name becomes an empty tag that cannot match a valid selection.
    available_tags.add(
        str(available_model.get("name") or available_model.get("model") or "")
    )
raise SystemExit(0 if expected_model_tag in available_tags else 1)
' "$response_path" "$expected_model_tag"
}

# Return the exact loaded model's VRAM bytes, or `missing` when no process matches.
loaded_model_vram_bytes() {
    local response_path="$1"
    local expected_model_tag="$2"

    python3 -c '
import json
import sys

response_path, expected_model_tag = sys.argv[1:3]
with open(response_path, encoding="utf-8") as response_file:
    response = json.load(response_file)
# No loaded models means the one-token smoke did not leave placement evidence.
loaded_models = response.get("models", [])
# Each loaded model is checked until the exact operator-selected tag is found.
for loaded_model in loaded_models:
    # A malformed process record cannot prove CPU-only placement.
    if not isinstance(loaded_model, dict):
        continue
    # A missing model name stays unmatched instead of borrowing another process.
    model_tag = str(loaded_model.get("name") or loaded_model.get("model") or "")
    # Only the exact selected model can provide CPU-placement evidence for this setup.
    if model_tag == expected_model_tag:
        model_vram_bytes = loaded_model.get("size_vram")
        # A missing VRAM measurement is invalid evidence, never proof of zero use.
        if model_vram_bytes is None:
            print("invalid")
            raise SystemExit(0)
        # A non-integer measurement cannot prove the exact zero-VRAM requirement.
        if isinstance(model_vram_bytes, bool) or not isinstance(model_vram_bytes, int):
            print("invalid")
            raise SystemExit(0)
        print(int(model_vram_bytes))
        raise SystemExit(0)
print("missing")
' "$response_path" "$expected_model_tag"
}

IS_WSL=false
# WSL uses a separate port so a Windows Ollama can remain available without a collision.
if grep -qi microsoft /proc/version 2>/dev/null; then
    IS_WSL=true
fi

OLLAMA_PORT=""
OLLAMA_MODEL=""
FORCE_INSTALL=false

# Operator flags take precedence over every local/default setting.
while [[ $# -gt 0 ]]; do
    # Each option changes only this installer run or shows its operator guidance.
    case "$1" in
        # Help gives the operator the non-mutating setup contract and stops.
        --help|-h)
            usage
            exit 0
            ;;
        # A custom port avoids a local service collision.
        --port)
            # A missing value cannot identify where the local model should listen.
            if [[ $# -lt 2 ]]; then
                echo "error: --port requires a value" >&2
                exit 2
            fi
            OLLAMA_PORT="$2"
            shift 2
            ;;
        # An explicit model lets an operator override the local default for this install.
        --model)
            # A missing tag cannot be pulled or matched exactly.
            if [[ $# -lt 2 ]]; then
                echo "error: --model requires a value" >&2
                exit 2
            fi
            OLLAMA_MODEL="$2"
            shift 2
            ;;
        # Reinstalling is opt-in because a working local binary need not change.
        --force)
            FORCE_INSTALL=true
            shift
            ;;
        # Unknown flags stop before any system package or model is changed.
        *)
            echo "error: unknown option: $1" >&2
            usage >&2
            exit 2
            ;;
    esac
done

# WSL avoids the usual Windows Ollama port; other Linux hosts keep the standard port.
if [[ -z "$OLLAMA_PORT" ]]; then
    # A WSL developer may already have Windows Ollama on 11434.
    if [[ "$IS_WSL" == true ]]; then
        OLLAMA_PORT=11435
    # Native Linux can use Ollama's standard port.
    else
        OLLAMA_PORT=11434
    fi
fi

# The current role-model setting is the first local default after `--model`.
if [[ -z "$OLLAMA_MODEL" ]]; then
    OLLAMA_MODEL="$(env_file_value "ROLE_AGENT_OLLAMA_MODEL")"
fi
# Older setups keep their existing local model until the user adopts the canonical key.
if [[ -z "$OLLAMA_MODEL" ]]; then
    OLLAMA_MODEL="$(env_file_value "OLLAMA_MODEL")"
fi
# A fresh setup receives the approved exact Qwen tag.
if [[ -z "$OLLAMA_MODEL" ]]; then
    OLLAMA_MODEL="$DEFAULT_OLLAMA_MODEL"
fi

# Unsafe characters could corrupt the JSON smoke request or produce an ambiguous model name.
if [[ ! "$OLLAMA_MODEL" =~ ^[A-Za-z0-9._/-]+(:[A-Za-z0-9._-]+)?$ ]]; then
    echo "error: model must be an Ollama name with an optional exact tag" >&2
    exit 2
fi

EXPECTED_MODEL_TAG="$(exact_ollama_tag "$OLLAMA_MODEL")"
OLLAMA_URL="http://localhost:${OLLAMA_PORT}"

echo ""
echo -e "${BOLD}  Ambient Scribe - Install CPU-only Ollama${RESET}"
echo -e "  ${DIM}$(printf '─%.0s' {1..44})${RESET}"
echo ""

echo -e "  ${BOLD}Checking prerequisites${RESET}"
echo ""

step "curl"
# Downloading the official installer and calling the local API both require curl.
if command -v curl >/dev/null 2>&1; then
    pass
# Missing curl stops before any partial installation can occur.
else
    fail "not found - required for Ollama setup"
fi

step "python3"
# Structural API checks use Python already required by Ambient Scribe.
if command -v python3 >/dev/null 2>&1; then
    pass
# Without a JSON parser, exact tags and zero-VRAM status cannot be proven safely.
else
    fail "not found - required for exact model and CPU checks"
fi

# Any missing prerequisite stops before system packages or models change.
if [[ $ERRORS -gt 0 ]]; then
    echo ""
    echo -e "  ${RED}${BOLD}Cannot continue - fix the checks above${RESET}"
    exit 1
fi

echo ""
echo -e "  ${BOLD}System dependencies${RESET}"
echo ""

step "zstd"
# Existing zstd support means the model package can unpack immediately.
if command -v zstd >/dev/null 2>&1; then
    pass "already installed"
# A missing decompressor is installed only when the operator runs this setup command.
elif sudo apt-get install -y zstd >/dev/null 2>&1; then
    pass "installed"
# Package-manager failure leaves the system unchanged enough for an operator to repair manually.
else
    fail "could not install - run: sudo apt-get install zstd"
fi

# A missing decompressor stops before downloading the Ollama binary.
if [[ $ERRORS -gt 0 ]]; then
    exit 1
fi

echo ""
echo -e "  ${BOLD}Installing Ollama${RESET}"
echo ""

NEEDS_INSTALL=true
# A working binary is retained unless the operator explicitly requested replacement.
if [[ -f "$OLLAMA_BINARY" && "$FORCE_INSTALL" != true ]]; then
    CURRENT_VERSION="$(
        "$OLLAMA_BINARY" --version 2>/dev/null \
            | grep -oE '[0-9]+\.[0-9]+\.[0-9]+' \
            || echo "unknown"
    )"
    step "Ollama binary"
    pass "v${CURRENT_VERSION} already installed"
    NEEDS_INSTALL=false
fi

# A fresh or explicitly forced setup downloads before executing the vendor installer.
if [[ "$NEEDS_INSTALL" == true ]]; then
    step "Downloading Ollama installer"
    OLLAMA_INSTALL_SCRIPT="$(mktemp)"
    # Writing the installer first avoids executing an unreviewable network pipe.
    if curl -fsSL https://ollama.com/install.sh -o "$OLLAMA_INSTALL_SCRIPT"; then
        pass "downloaded"
    # A failed download stops without invoking a partial script.
    else
        fail "download failed - check https://ollama.com/download"
        exit 1
    fi

    step "Installing Ollama"
    # The operator explicitly started this installer, so the downloaded vendor script may run.
    if INSTALL_OUTPUT="$(sh "$OLLAMA_INSTALL_SCRIPT" 2>&1)"; then
        INSTALLED_VERSION="$(
            "$OLLAMA_BINARY" --version 2>/dev/null \
                | grep -oE '[0-9]+\.[0-9]+\.[0-9]+' \
                || echo "unknown"
        )"
        pass "v${INSTALLED_VERSION}"
    # Vendor installer output is limited so the operator gets a useful failure without log noise.
    else
        fail "install failed - check https://ollama.com/download"
        echo "$INSTALL_OUTPUT" | tail -5 | while IFS= read -r installer_line; do
            info "$installer_line"
        done
        exit 1
    fi
fi

echo ""
echo -e "  ${BOLD}Starting CPU-only Ollama${RESET}"
echo ""

step "Ollama on port ${OLLAMA_PORT}"
# An existing server is preserved and verified after the model smoke.
if curl -sf "${OLLAMA_URL}/" >/dev/null 2>&1; then
    pass "already running; CPU status will be verified"
# A new server hides NVIDIA, ROCm, and Vulkan devices before it loads any model.
else
    CUDA_VISIBLE_DEVICES=-1 \
    ROCR_VISIBLE_DEVICES=-1 \
    GGML_VK_VISIBLE_DEVICES=-1 \
    OLLAMA_HOST="0.0.0.0:${OLLAMA_PORT}" \
        "$OLLAMA_BINARY" serve >"$OLLAMA_LOG" 2>&1 &
    OLLAMA_PID=$!
    STARTED=false

    # The operator sees a bounded wait while the local model service becomes reachable.
    for _attempt in $(seq 1 15); do
        # A successful health response means model setup can continue.
        if curl -sf "${OLLAMA_URL}/" >/dev/null 2>&1; then
            STARTED=true
            break
        fi
        sleep 1
    done

    # A ready CPU-only server can now receive the selected model.
    if [[ "$STARTED" == true ]]; then
        pass "started CPU-only (pid ${OLLAMA_PID})"
    # Startup failure keeps the NeMo GPU untouched and points the operator at the local log.
    else
        fail "failed to start - check ${OLLAMA_LOG}"
        # A short tail gives the operator a practical cause without flooding setup output.
        if [[ -f "$OLLAMA_LOG" ]]; then
            tail -5 "$OLLAMA_LOG" | while IFS= read -r ollama_log_line; do
                info "$ollama_log_line"
            done
        fi
        exit 1
    fi
fi

echo ""
echo -e "  ${BOLD}Preparing local model${RESET}"
echo ""

MODEL_TAGS_RESPONSE="$(mktemp)"
# The model inventory must be readable before an exact tag can be accepted.
if ! curl -fsS "${OLLAMA_URL}/api/tags" -o "$MODEL_TAGS_RESPONSE"; then
    fail "could not read Ollama model inventory"
    exit 1
fi

step "Model ${OLLAMA_MODEL}"
# An exact API tag means this consultation model is already available locally.
if model_tag_is_available "$MODEL_TAGS_RESPONSE" "$EXPECTED_MODEL_TAG"; then
    pass "already available"
# A missing exact tag is pulled once; same-prefix alternatives never satisfy the check.
else
    echo -e "${YELLOW}downloading...${RESET}"
    info "The first pull may take several minutes."
    echo ""
    # Pull failure leaves the page unavailable and needs an explicit operator action.
    if OLLAMA_HOST="$OLLAMA_URL" "$OLLAMA_BINARY" pull "$OLLAMA_MODEL"; then
        step "Model ${OLLAMA_MODEL}"
        pass "downloaded"
    # Network, disk, or model-name errors cannot be treated as readiness.
    else
        step "Model ${OLLAMA_MODEL}"
        fail "pull failed"
        exit 1
    fi

    # The refreshed inventory proves the exact requested tag—not a prefix—was installed.
    if ! curl -fsS "${OLLAMA_URL}/api/tags" -o "$MODEL_TAGS_RESPONSE" \
        || ! model_tag_is_available "$MODEL_TAGS_RESPONSE" "$EXPECTED_MODEL_TAG"; then
        fail "download completed but exact tag ${EXPECTED_MODEL_TAG} is unavailable"
        exit 1
    fi
fi

step "One-token model smoke"
# A one-token response proves the selected local model can serve the user's next request.
if curl -fsS "${OLLAMA_URL}/api/generate" \
    -H 'Content-Type: application/json' \
    -d "{\"model\":\"${OLLAMA_MODEL}\",\"prompt\":\"ping\",\"stream\":false,\"options\":{\"num_predict\":1}}" \
    >/dev/null; then
    pass
# Loading or generation failure means role labels and notes remain unavailable.
else
    fail "model did not complete the one-token smoke"
    exit 1
fi

MODEL_PROCESS_RESPONSE="$(mktemp)"
# The loaded-process response is the only acceptance proof that Ollama used zero VRAM.
if ! curl -fsS "${OLLAMA_URL}/api/ps" -o "$MODEL_PROCESS_RESPONSE"; then
    fail "could not verify the loaded model's CPU usage"
    exit 1
fi

MODEL_VRAM_BYTES="$(loaded_model_vram_bytes "$MODEL_PROCESS_RESPONSE" "$EXPECTED_MODEL_TAG")"
step "NeMo GPU isolation"
# A missing process means the smoke did not leave verifiable model placement evidence.
if [[ "$MODEL_VRAM_BYTES" == "missing" ]]; then
    fail "loaded model was absent from /api/ps; CPU-only use is unverified"
    exit 1
# Any VRAM use would compete with NeMo and requires a manual CPU-only restart.
elif [[ "$MODEL_VRAM_BYTES" =~ ^[0-9]+$ && "$MODEL_VRAM_BYTES" -gt 0 ]]; then
    fail "Ollama is using ${MODEL_VRAM_BYTES} VRAM bytes"
    info "Restart the existing Ollama server CPU-only, then rerun this check:"
    info "CUDA_VISIBLE_DEVICES=-1 ROCR_VISIBLE_DEVICES=-1 GGML_VK_VISIBLE_DEVICES=-1"
    info "OLLAMA_HOST=0.0.0.0:${OLLAMA_PORT} ollama serve"
    exit 1
# Zero VRAM proves the user's local note and role model leaves the GPU to NeMo.
elif [[ "$MODEL_VRAM_BYTES" == "0" ]]; then
    pass "0 VRAM bytes; Ollama is CPU-only"
# Malformed API data cannot be interpreted as a safe GPU-isolation result.
else
    fail "Ollama returned an invalid VRAM value"
    exit 1
fi

echo ""
echo -e "  ${DIM}$(printf '─%.0s' {1..44})${RESET}"
echo ""
echo -e "  ${GREEN}${BOLD}CPU-only Ollama is ready.${RESET}"
echo ""
echo -e "  ${DIM}Configuration:${RESET}"
echo -e "    ${ARROW} Host:  ${BOLD}${OLLAMA_URL}${RESET}"
echo -e "    ${ARROW} Model: ${BOLD}${OLLAMA_MODEL}${RESET}"
echo -e "    ${ARROW} GPU:   ${GREEN}${BOLD}disabled${RESET} ${DIM}(reserved for NeMo)${RESET}"
echo ""
echo -e "  ${DIM}Add this setting to .env manually if needed:${RESET}"
echo -e "    ${BOLD}ROLE_AGENT_OLLAMA_MODEL=${OLLAMA_MODEL}${RESET}"
echo ""
