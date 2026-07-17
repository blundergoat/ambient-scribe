#!/usr/bin/env bash
# Diagnose the off-GPU models used for speaker labels and final notes.
# Run when the Scribe page reports that its AI model is unavailable.
# The checker resolves role and summary settings exactly like their factories,
# validates every unique provider/model pair, and avoids duplicate paid probes.
# It never sends consultation text or uses the GPU reserved for NeMo.

set -uo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DEFAULT_BEDROCK_MODEL="au.anthropic.claude-haiku-4-5-20251001-v1:0"
DEFAULT_OLLAMA_MODEL="qwen3.5:9b"

# Keep every Compose action anchored to this checkout for the operator's active stack.
dc() {
    docker compose --project-directory "$REPO_ROOT" "$@"
}

# Read one optional setting without printing unrelated or secret environment values.
env_file_value() {
    local setting_name="$1"
    local configured_value

    # A developer without local overrides receives the checked-in safe defaults.
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

# The role provider follows process, local override, legacy process/override, then Ollama.
ENV_ROLE_AGENT_PROVIDER="$(env_file_value "ROLE_AGENT_MODEL_PROVIDER")"
ENV_LEGACY_PROVIDER="$(env_file_value "MODEL_PROVIDER")"
ROLE_AGENT_PROVIDER="${ROLE_AGENT_MODEL_PROVIDER:-${ENV_ROLE_AGENT_PROVIDER:-${MODEL_PROVIDER:-${ENV_LEGACY_PROVIDER:-ollama}}}}"
ROLE_AGENT_PROVIDER="${ROLE_AGENT_PROVIDER,,}"

# A missing role Bedrock ID keeps the clinician on the approved AU Haiku profile.
ENV_ROLE_AGENT_MODEL_ID="$(env_file_value "ROLE_AGENT_MODEL_ID")"
ROLE_AGENT_BEDROCK_MODEL="${ROLE_AGENT_MODEL_ID:-${ENV_ROLE_AGENT_MODEL_ID:-$DEFAULT_BEDROCK_MODEL}}"

# A missing local role model keeps the exact tool-capable Qwen tag used by the app.
ENV_ROLE_AGENT_OLLAMA_MODEL="$(env_file_value "ROLE_AGENT_OLLAMA_MODEL")"
ROLE_AGENT_OLLAMA_MODEL_VALUE="${ROLE_AGENT_OLLAMA_MODEL:-${ENV_ROLE_AGENT_OLLAMA_MODEL:-$DEFAULT_OLLAMA_MODEL}}"

# The summary follows the effective role provider unless the operator separates it deliberately.
ENV_SUMMARY_AGENT_PROVIDER="$(env_file_value "SUMMARY_AGENT_MODEL_PROVIDER")"
SUMMARY_AGENT_PROVIDER_VALUE="${SUMMARY_AGENT_MODEL_PROVIDER:-${ENV_SUMMARY_AGENT_PROVIDER:-$ROLE_AGENT_PROVIDER}}"
SUMMARY_AGENT_PROVIDER_VALUE="${SUMMARY_AGENT_PROVIDER_VALUE,,}"

# A missing summary Bedrock ID inherits the selected role ID before using the approved fallback.
ENV_SUMMARY_AGENT_MODEL_ID="$(env_file_value "SUMMARY_AGENT_MODEL_ID")"
SUMMARY_AGENT_BEDROCK_MODEL="${SUMMARY_AGENT_MODEL_ID:-${ENV_SUMMARY_AGENT_MODEL_ID:-${ROLE_AGENT_BEDROCK_MODEL:-$DEFAULT_BEDROCK_MODEL}}}"

# A missing summary Ollama tag inherits the selected role model before using Qwen.
ENV_SUMMARY_AGENT_OLLAMA_MODEL="$(env_file_value "SUMMARY_AGENT_OLLAMA_MODEL")"
SUMMARY_AGENT_OLLAMA_MODEL_VALUE="${SUMMARY_AGENT_OLLAMA_MODEL:-${ENV_SUMMARY_AGENT_OLLAMA_MODEL:-${ROLE_AGENT_OLLAMA_MODEL_VALUE:-$DEFAULT_OLLAMA_MODEL}}}"

# Existing AWS settings are read by name only and never printed as credential values.
ENV_AWS_PROFILE="$(env_file_value "AWS_PROFILE")"
ENV_AWS_ACCESS_KEY_ID="$(env_file_value "AWS_ACCESS_KEY_ID")"
ENV_AWS_SECRET_ACCESS_KEY="$(env_file_value "AWS_SECRET_ACCESS_KEY")"
ENV_AWS_SESSION_TOKEN="$(env_file_value "AWS_SESSION_TOKEN")"
ENV_AWS_DEFAULT_REGION="$(env_file_value "AWS_DEFAULT_REGION")"
AWS_PROFILE_VALUE="${AWS_PROFILE:-${ENV_AWS_PROFILE:-}}"
AWS_ACCESS_KEY_ID_VALUE="${AWS_ACCESS_KEY_ID:-${ENV_AWS_ACCESS_KEY_ID:-}}"
AWS_SECRET_ACCESS_KEY_VALUE="${AWS_SECRET_ACCESS_KEY:-${ENV_AWS_SECRET_ACCESS_KEY:-}}"
AWS_SESSION_TOKEN_VALUE="${AWS_SESSION_TOKEN:-${ENV_AWS_SESSION_TOKEN:-}}"
AWS_DEFAULT_REGION_VALUE="${AWS_DEFAULT_REGION:-${ENV_AWS_DEFAULT_REGION:-ap-southeast-2}}"

# Choose the model a user receives for one resolved provider setting.
model_for_provider() {
    local provider_name="$1"
    local bedrock_model="$2"
    local ollama_model="$3"

    # The effective provider determines which model serves the user's requested flow.
    case "$provider_name" in
        # Bedrock users receive the configured inference profile.
        bedrock)
            printf '%s' "$bedrock_model"
            ;;
        # Offline users receive the configured exact Ollama tag.
        ollama)
            printf '%s' "$ollama_model"
            ;;
        # Unknown providers remain visible so validation can fail with actionable guidance.
        *)
            printf '%s' "unresolved-model"
            ;;
    esac
}

ROLE_AGENT_MODEL_VALUE="$(
    model_for_provider \
        "$ROLE_AGENT_PROVIDER" \
        "$ROLE_AGENT_BEDROCK_MODEL" \
        "$ROLE_AGENT_OLLAMA_MODEL_VALUE"
)"
SUMMARY_AGENT_MODEL_VALUE="$(
    model_for_provider \
        "$SUMMARY_AGENT_PROVIDER_VALUE" \
        "$SUMMARY_AGENT_BEDROCK_MODEL" \
        "$SUMMARY_AGENT_OLLAMA_MODEL_VALUE"
)"

# Build the AWS CLI environment without exposing credentials in output or process arguments.
bedrock_cli_environment() {
    BEDROCK_AWS_ENV=(AWS_DEFAULT_REGION="$AWS_DEFAULT_REGION_VALUE")

    # A named profile keeps its normal credential-chain behavior.
    if [[ -n "$AWS_PROFILE_VALUE" ]]; then
        BEDROCK_AWS_ENV+=(AWS_PROFILE="$AWS_PROFILE_VALUE")
        return 0
    fi

    BEDROCK_AWS_ENV+=(
        AWS_ACCESS_KEY_ID="$AWS_ACCESS_KEY_ID_VALUE"
        AWS_SECRET_ACCESS_KEY="$AWS_SECRET_ACCESS_KEY_VALUE"
    )
    # Temporary credentials need their session token to reach Bedrock.
    if [[ -n "$AWS_SESSION_TOKEN_VALUE" ]]; then
        BEDROCK_AWS_ENV+=(AWS_SESSION_TOKEN="$AWS_SESSION_TOKEN_VALUE")
    fi
}

# Validate one unique Bedrock model used by role labels, notes, or both.
check_bedrock_model() {
    local model_id="$1"
    local user_flows="$2"
    local live_probe_error=""
    local -a BEDROCK_AWS_ENV=()

    echo "Checking AI model service (bedrock: ${user_flows})…"
    echo "✔ provider is AWS Bedrock"
    echo "· configured model ${model_id}"
    echo "· configured region ${AWS_DEFAULT_REGION_VALUE}"

    # A profile gives the operator a credential source without revealing its contents.
    if [[ -n "$AWS_PROFILE_VALUE" ]]; then
        echo "✔ AWS credentials configured via profile"
    # Access keys are accepted only as a complete pair.
    elif [[ -n "$AWS_ACCESS_KEY_ID_VALUE" && -n "$AWS_SECRET_ACCESS_KEY_VALUE" ]]; then
        echo "✔ AWS credentials configured via access key"
    # Missing credentials would make labels or notes fail after the consultation starts.
    else
        echo "✘ AWS credentials are not configured."
        echo "  Set AWS_ACCESS_KEY_ID + AWS_SECRET_ACCESS_KEY, or AWS_PROFILE, then retry."
        return 1
    fi

    # A missing CLI leaves live access unverified instead of presenting a false pass.
    if ! command -v aws >/dev/null 2>&1; then
        echo "○ aws CLI not found; skipped live credential and model probes"
        return 0
    fi

    bedrock_cli_environment
    # STS confirms the credential chain before the model-specific checks run.
    if env "${BEDROCK_AWS_ENV[@]}" aws sts get-caller-identity >/dev/null 2>&1; then
        echo "✔ AWS credentials resolve with STS"
    # Invalid or expired credentials must be fixed before the user starts recording.
    else
        echo "✘ AWS credentials did not resolve with STS."
        echo "  Check your AWS profile, access keys, session token, or SSO login."
        return 1
    fi

    # The configured ID must exist in Sydney before a note or role request can use it.
    if env "${BEDROCK_AWS_ENV[@]}" aws bedrock get-inference-profile \
        --inference-profile-identifier "$model_id" >/dev/null 2>&1 \
        || env "${BEDROCK_AWS_ENV[@]}" aws bedrock get-foundation-model \
            --model-identifier "$model_id" >/dev/null 2>&1; then
        echo "✔ model exists in ${AWS_DEFAULT_REGION_VALUE}"
    # Region/model mismatches are actionable and must not degrade into summary-time errors.
    else
        echo "✘ model ${model_id} is not available in ${AWS_DEFAULT_REGION_VALUE}."
        echo "  List the inference profiles available in this region:"
        echo "    aws bedrock list-inference-profiles --query 'inferenceProfileSummaries[].inferenceProfileId'"
        return 1
    fi

    # A one-token call proves the exact runtime dependency without spending on consultation text.
    if live_probe_error="$(
        env "${BEDROCK_AWS_ENV[@]}" aws bedrock-runtime converse \
            --model-id "$model_id" \
            --messages '[{"role":"user","content":[{"text":"ping"}]}]' \
            --inference-config '{"maxTokens":1}' \
            2>&1 >/dev/null
    )"; then
        echo "✔ model responds to a live one-token invoke"
    # Older AWS CLIs cannot perform the live dependency proof, so the skip stays visible.
    elif [[ "$live_probe_error" == *"Invalid choice"* ]]; then
        echo "○ aws CLI too old for 'bedrock-runtime converse'; skipped live model probe"
    # Missing model access needs an operator action before notes can be generated.
    elif [[ "$live_probe_error" == *"AccessDenied"* ]]; then
        echo "✘ model invoke was denied. Enable access to this model in Bedrock for"
        echo "  ${AWS_DEFAULT_REGION_VALUE}, then retry."
        return 1
    # Any other provider failure is shown without leaking credentials or consultation text.
    else
        echo "✘ model invoke failed:"
        echo "  ${live_probe_error}" | head -3
        return 1
    fi
}

# Normalize a model without an explicit tag to the exact Ollama `latest` name.
exact_ollama_tag() {
    local configured_model="$1"

    # Bare names mean the user selected Ollama's `latest` tag.
    if [[ "$configured_model" != *:* ]]; then
        printf '%s:latest' "$configured_model"
        return 0
    fi

    printf '%s' "$configured_model"
}

# Validate one unique CPU Ollama model used by role labels, notes, or both.
check_ollama_model() {
    local configured_model="$1"
    local user_flows="$2"
    local expected_tag
    expected_tag="$(exact_ollama_tag "$configured_model")"

    echo "Checking AI model service (ollama: ${user_flows}, ${configured_model})…"

    # A stopped local provider is started so the page can complete its model pre-flight.
    if ! dc ps --status running ollama >/dev/null 2>&1 \
        || [[ -z "$(dc ps --status running -q ollama 2>/dev/null)" ]]; then
        echo "→ ollama service is not running. Starting it…"
        dc up -d ollama
    fi

    # The agent must reach the bundled service from its own network before a visit starts.
    if dc exec -T nemo-agent curl -fsS -m 6 http://ollama:11434/api/tags >/dev/null 2>&1; then
        echo "✔ agent can reach ollama at http://ollama:11434"
    # A host-only Ollama cannot provide labels or notes to the containerized app.
    else
        echo "✘ agent cannot reach http://ollama:11434."
        echo "  Confirm docker-compose.yml pins OLLAMA_HOST, then recreate nemo-agent."
        return 1
    fi

    # Exact tags prevent a smaller similarly named model from passing readiness checks.
    if dc exec -T ollama ollama list 2>/dev/null \
        | awk 'NR>1 {print $1}' \
        | grep -Fxq -e "$configured_model" -e "$expected_tag"; then
        echo "✔ model ${configured_model} is present"
    # Missing model data is repaired in the persistent Ollama volume for the next consultation.
    else
        echo "→ model ${configured_model} not found - pulling once into the Ollama volume…"
        # A failed pull leaves the page unavailable and needs an operator-facing reason.
        if ! dc exec -T ollama ollama pull "$configured_model"; then
            echo "✘ pull failed for ${configured_model}. Check the name, network, and disk space."
            return 1
        fi
    fi
}

declare -a MODEL_PAIR_ORDER=()
declare -A MODEL_PAIR_PROVIDER=()
declare -A MODEL_PAIR_MODEL=()
declare -A MODEL_PAIR_FLOWS=()

# Register one user flow while merging an identical provider/model pair for one probe.
register_model_pair() {
    local user_flow="$1"
    local provider_name="$2"
    local model_name="$3"
    local pair_key="${provider_name}|${model_name}"

    # Shared role/summary settings need one provider check and one paid call, not two.
    if [[ -n "${MODEL_PAIR_FLOWS[$pair_key]:-}" ]]; then
        MODEL_PAIR_FLOWS["$pair_key"]="${MODEL_PAIR_FLOWS[$pair_key]} and ${user_flow}"
        return 0
    fi

    MODEL_PAIR_ORDER+=("$pair_key")
    MODEL_PAIR_PROVIDER["$pair_key"]="$provider_name"
    MODEL_PAIR_MODEL["$pair_key"]="$model_name"
    MODEL_PAIR_FLOWS["$pair_key"]="$user_flow"
}

# Check every distinct model the user depends on before declaring the page ready.
validate_registered_models() {
    local pair_key
    local provider_name
    local model_name
    local user_flows

    # Each distinct pair is checked once even when both role labels and notes share it.
    for pair_key in "${MODEL_PAIR_ORDER[@]}"; do
        provider_name="${MODEL_PAIR_PROVIDER[$pair_key]}"
        model_name="${MODEL_PAIR_MODEL[$pair_key]}"
        user_flows="${MODEL_PAIR_FLOWS[$pair_key]}"

        # Each provider uses its own live dependency proof before the page is ready.
        case "$provider_name" in
            # Bedrock checks include one live token for this unique model.
            bedrock)
                check_bedrock_model "$model_name" "$user_flows" || return 1
                ;;
            # Ollama checks stay inside the bundled CPU-only service.
            ollama)
                check_ollama_model "$model_name" "$user_flows" || return 1
                ;;
            # A typo cannot be treated as configured readiness.
            *)
                echo "✘ Unknown model provider '${provider_name}' for ${user_flows}"
                echo "  Use 'bedrock' or 'ollama'."
                return 1
                ;;
        esac
    done
}

# e.g. an operator runs this after the page blocks Start with “AI model unavailable”.
register_model_pair "role labels" "$ROLE_AGENT_PROVIDER" "$ROLE_AGENT_MODEL_VALUE"
register_model_pair "summary notes" "$SUMMARY_AGENT_PROVIDER_VALUE" "$SUMMARY_AGENT_MODEL_VALUE"

# Any failed dependency keeps the consultation pre-flight closed.
if ! validate_registered_models; then
    exit 1
fi

echo "✔ All configured role and summary models are ready. Reload the Scribe page and retry."
