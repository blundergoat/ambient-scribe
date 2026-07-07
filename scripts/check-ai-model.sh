#!/usr/bin/env bash
#
# Diagnose and repair the off-GPU AI model (role inference + summaries).
# Run this when the UI shows "AI model unavailable".
#
# It checks the configured role/summary model provider. For Bedrock it validates
# local AWS configuration, confirms the configured model exists in the region,
# and invokes it with a one-token probe. For Ollama it checks that the agent
# container can reach the bundled Ollama service and that the configured model
# is pulled, pulling it if missing. Role inference and summaries use Ollama or
# Bedrock - never the NeMo GPU.
#
# Usage: ./scripts/check-ai-model.sh
set -uo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"

# All compose calls target this repo's project, no matter the caller's cwd.
dc() {
  docker compose --project-directory "$REPO_ROOT" "$@"
}

env_file_value() {
  local key="$1"
  local value

  if [[ ! -f "$REPO_ROOT/.env" ]]; then
    return 0
  fi

  value="$(grep -E "^[[:space:]]*(export[[:space:]]+)?${key}=" "$REPO_ROOT/.env" 2>/dev/null | head -1 | cut -d= -f2-)"
  value="${value#"${value%%[![:space:]]*}"}"
  value="${value%"${value##*[![:space:]]}"}"
  value="${value%\"}"
  value="${value#\"}"
  value="${value%\'}"
  value="${value#\'}"
  printf '%s' "$value"
}

ENV_ROLE_AGENT_PROVIDER="$(env_file_value "ROLE_AGENT_MODEL_PROVIDER")"
ENV_LEGACY_PROVIDER="$(env_file_value "MODEL_PROVIDER")"
PROVIDER="${ROLE_AGENT_MODEL_PROVIDER:-${ENV_ROLE_AGENT_PROVIDER:-${MODEL_PROVIDER:-${ENV_LEGACY_PROVIDER:-ollama}}}}"
PROVIDER="${PROVIDER,,}"

ENV_ROLE_AGENT_OLLAMA_MODEL="$(env_file_value "ROLE_AGENT_OLLAMA_MODEL")"
MODEL="${ROLE_AGENT_OLLAMA_MODEL:-${ENV_ROLE_AGENT_OLLAMA_MODEL:-qwen3.5:9b}}"

ENV_AWS_PROFILE="$(env_file_value "AWS_PROFILE")"
ENV_AWS_ACCESS_KEY_ID="$(env_file_value "AWS_ACCESS_KEY_ID")"
ENV_AWS_SECRET_ACCESS_KEY="$(env_file_value "AWS_SECRET_ACCESS_KEY")"
ENV_AWS_SESSION_TOKEN="$(env_file_value "AWS_SESSION_TOKEN")"
ENV_AWS_DEFAULT_REGION="$(env_file_value "AWS_DEFAULT_REGION")"
ENV_ROLE_AGENT_MODEL_ID="$(env_file_value "ROLE_AGENT_MODEL_ID")"

AWS_PROFILE_VALUE="${AWS_PROFILE:-${ENV_AWS_PROFILE:-}}"
AWS_ACCESS_KEY_ID_VALUE="${AWS_ACCESS_KEY_ID:-${ENV_AWS_ACCESS_KEY_ID:-}}"
AWS_SECRET_ACCESS_KEY_VALUE="${AWS_SECRET_ACCESS_KEY:-${ENV_AWS_SECRET_ACCESS_KEY:-}}"
AWS_SESSION_TOKEN_VALUE="${AWS_SESSION_TOKEN:-${ENV_AWS_SESSION_TOKEN:-}}"
AWS_DEFAULT_REGION_VALUE="${AWS_DEFAULT_REGION:-${ENV_AWS_DEFAULT_REGION:-ap-southeast-2}}"
ROLE_AGENT_MODEL_ID_VALUE="${ROLE_AGENT_MODEL_ID:-${ENV_ROLE_AGENT_MODEL_ID:-au.anthropic.claude-haiku-4-5-20251001-v1:0}}"

check_bedrock() {
  echo "Checking AI model service (bedrock)…"
  echo "✔ provider is AWS Bedrock"
  echo "· configured model ${ROLE_AGENT_MODEL_ID_VALUE}"
  echo "· configured region ${AWS_DEFAULT_REGION_VALUE}"

  if [[ -n "$AWS_PROFILE_VALUE" ]]; then
    echo "✔ AWS credentials configured via profile"
  elif [[ -n "$AWS_ACCESS_KEY_ID_VALUE" && -n "$AWS_SECRET_ACCESS_KEY_VALUE" ]]; then
    echo "✔ AWS credentials configured via access key"
  else
    echo "✘ AWS credentials are not configured."
    echo "  Set AWS_ACCESS_KEY_ID + AWS_SECRET_ACCESS_KEY, or AWS_PROFILE, then retry."
    exit 1
  fi

  if command -v aws >/dev/null 2>&1; then
    aws_env=(AWS_DEFAULT_REGION="$AWS_DEFAULT_REGION_VALUE")
    if [[ -n "$AWS_PROFILE_VALUE" ]]; then
      aws_env+=(AWS_PROFILE="$AWS_PROFILE_VALUE")
    else
      aws_env+=(
        AWS_ACCESS_KEY_ID="$AWS_ACCESS_KEY_ID_VALUE"
        AWS_SECRET_ACCESS_KEY="$AWS_SECRET_ACCESS_KEY_VALUE"
      )
      if [[ -n "$AWS_SESSION_TOKEN_VALUE" ]]; then
        aws_env+=(AWS_SESSION_TOKEN="$AWS_SESSION_TOKEN_VALUE")
      fi
    fi

    if env "${aws_env[@]}" aws sts get-caller-identity >/dev/null 2>&1; then
      echo "✔ AWS credentials resolve with STS"
    else
      echo "✘ AWS credentials did not resolve with STS."
      echo "  Check your AWS profile, access keys, session token, or SSO login."
      exit 1
    fi

    # The model line above only echoes config; prove the identifier exists here.
    if env "${aws_env[@]}" aws bedrock get-inference-profile \
        --inference-profile-identifier "$ROLE_AGENT_MODEL_ID_VALUE" >/dev/null 2>&1 \
       || env "${aws_env[@]}" aws bedrock get-foundation-model \
        --model-identifier "$ROLE_AGENT_MODEL_ID_VALUE" >/dev/null 2>&1; then
      echo "✔ model exists in ${AWS_DEFAULT_REGION_VALUE}"
    else
      echo "✘ model ${ROLE_AGENT_MODEL_ID_VALUE} is not available in ${AWS_DEFAULT_REGION_VALUE}."
      echo "  Geo prefixes differ per region (us./au./global.). List what this region offers:"
      echo "    aws bedrock list-inference-profiles --query 'inferenceProfileSummaries[].inferenceProfileId'"
      exit 1
    fi

    # A one-token invoke proves credentials, region, and model-access grants end to end.
    converse_error="$(env "${aws_env[@]}" aws bedrock-runtime converse \
      --model-id "$ROLE_AGENT_MODEL_ID_VALUE" \
      --messages '[{"role":"user","content":[{"text":"ping"}]}]' \
      --inference-config '{"maxTokens":1}' 2>&1 >/dev/null)"
    if [[ -z "$converse_error" ]]; then
      echo "✔ model responds to a live invoke"
    elif [[ "$converse_error" == *"Invalid choice"* ]]; then
      echo "○ aws CLI too old for 'bedrock-runtime converse'; skipped live model probe"
    elif [[ "$converse_error" == *"AccessDenied"* ]]; then
      echo "✘ model invoke was denied. Enable access to this model in the Bedrock"
      echo "  console (Model access) for ${AWS_DEFAULT_REGION_VALUE}, then retry."
      exit 1
    else
      echo "✘ model invoke failed:"
      echo "  ${converse_error}" | head -3
      exit 1
    fi
  else
    echo "○ aws CLI not found; skipped live credential and model probes"
  fi

  echo "✔ AI model provider ready. Ollama checks skipped for Bedrock."
}

check_ollama() {
  echo "Checking AI model service (ollama: ${MODEL})…"

  # 1. Is the ollama service running?
  if ! dc ps --status running ollama >/dev/null 2>&1 \
     || [ -z "$(dc ps --status running -q ollama 2>/dev/null)" ]; then
    echo "→ ollama service is not running. Starting it…"
    dc up -d ollama
  fi

  # 2. Can the agent reach it in-network?
  if dc exec -T nemo-agent curl -fsS -m 6 http://ollama:11434/api/tags >/dev/null 2>&1; then
    echo "✔ agent can reach ollama at http://ollama:11434"
  else
    echo "✘ agent cannot reach http://ollama:11434."
    echo "  Ensure docker-compose.yml sets OLLAMA_HOST=http://ollama:11434 for nemo-agent, then:"
    echo "    docker compose up -d nemo-agent"
    exit 1
  fi

  # 3. Is the model pulled? Pull it if not (persists in the ollama_data volume).
  # The agent requests the exact configured tag, so a same-prefix model
  # (qwen3.5:7b vs qwen3.5:9b) must not count as present.
  local expected_tag="${MODEL}"
  if [[ "$MODEL" != *:* ]]; then
    expected_tag="${MODEL}:latest"
  fi
  if dc exec -T ollama ollama list 2>/dev/null | awk 'NR>1 {print $1}' \
      | grep -Fxq -e "${MODEL}" -e "${expected_tag}"; then
    echo "✔ model ${MODEL} is present"
  else
    echo "→ model ${MODEL} not found - pulling (~9 GB, one time)…"
    if ! dc exec -T ollama ollama pull "${MODEL}"; then
      echo "✘ pull failed for ${MODEL}. Check the model name, network access, and disk space."
      exit 1
    fi
  fi

  echo "✔ AI model ready. Reload the Scribe page and retry."
}

case "$PROVIDER" in
  bedrock)
    check_bedrock
    ;;
  ollama)
    check_ollama
    ;;
  *)
    echo "✘ Unknown ROLE_AGENT_MODEL_PROVIDER=${PROVIDER}"
    echo "  Set ROLE_AGENT_MODEL_PROVIDER to 'bedrock' or 'ollama'."
    exit 1
    ;;
esac
