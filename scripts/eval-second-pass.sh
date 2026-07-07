#!/usr/bin/env bash
# Evaluate an offline second-pass ASR candidate against existing audio fixtures.
set -euo pipefail

FIXTURE_DIR="${FIXTURE_DIR:-tests/fixtures/audio}"
PYTHON_BIN="${PYTHON_BIN:-strands_agents/.venv/bin/python}"
RUN_ID="${SECOND_PASS_RUN_ID:-$(date -u +%Y%m%dT%H%M%SZ)}"
RUN_DIR="${SECOND_PASS_RUN_DIR:-var/quality/second-pass/$RUN_ID}"
MODEL="nvidia/parakeet-unified-en-0.6b"
DRY_RUN=0
SECONDS_LIMIT=""

usage() {
  cat <<'USAGE'
Usage:
  scripts/eval-second-pass.sh [--model MODEL] [--seconds N] [--dry-run] <fixture-stem-or-wav> [...]

Examples:
  scripts/eval-second-pass.sh --dry-run consultation02
  scripts/eval-second-pass.sh --seconds 60 consultation02
  scripts/eval-second-pass.sh --model nvidia/parakeet-unified-en-0.6b consultation02

This fixture-only spike runs a newer offline ASR candidate and writes artifacts
under var/quality/second-pass/<run-id>/. Real inference executes inside the
running nemo-agent container so the live app Dockerfile and startup path stay
unchanged.
USAGE
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --help|-h)
      usage
      exit 0
      ;;
    --dry-run)
      DRY_RUN=1
      shift
      ;;
    --model)
      if [[ $# -lt 2 ]]; then
        echo "error: --model requires a value" >&2
        exit 2
      fi
      MODEL="$2"
      shift 2
      ;;
    --seconds)
      if [[ $# -lt 2 ]]; then
        echo "error: --seconds requires a value" >&2
        exit 2
      fi
      SECONDS_LIMIT="$2"
      shift 2
      ;;
    --)
      shift
      break
      ;;
    -*)
      echo "error: unknown option: $1" >&2
      usage >&2
      exit 2
      ;;
    *)
      break
      ;;
  esac
done

if [[ $# -eq 0 ]]; then
  echo "error: provide at least one fixture stem or WAV path" >&2
  usage >&2
  exit 2
fi

resolve_fixture_wav() {
  local query="$1"

  if [[ -f "$query" ]]; then
    printf '%s\n' "$query"
    return 0
  fi

  local matches=()
  while IFS= read -r candidate; do
    matches+=("$candidate")
  done < <(find "$FIXTURE_DIR" -maxdepth 1 -type f -name "*${query}*.wav" | sort)

  if [[ "${#matches[@]}" -eq 1 ]]; then
    printf '%s\n' "${matches[0]}"
    return 0
  fi

  if [[ "${#matches[@]}" -eq 0 ]]; then
    echo "error: no fixture WAV matched '$query' in $FIXTURE_DIR" >&2
  else
    echo "error: fixture query '$query' matched multiple WAVs:" >&2
    printf '  %s\n' "${matches[@]}" >&2
  fi
  return 1
}

cutoff_seconds_for_history() {
  local history_path="$1"
  local wav_path="$2"

  "$PYTHON_BIN" - "$history_path" "$wav_path" "${SECONDS_LIMIT:-}" <<'PY'
import json
import sys
import wave

history_path, wav_path, seconds_limit = sys.argv[1:4]
history = json.load(open(history_path, encoding="utf-8"))
segments = history.get("segments", [])
if segments:
    print(max(float(segment.get("end", 0.0) or 0.0) for segment in segments))
    raise SystemExit(0)

if seconds_limit:
    print(float(seconds_limit))
    raise SystemExit(0)

with wave.open(wav_path, "rb") as wav_file:
    print(wav_file.getnframes() / wav_file.getframerate())
PY
}

run_host_dry_run() {
  local wav_path="$1"
  local history_path="$2"
  local metadata_path="$3"
  local seconds_args=()
  if [[ -n "$SECONDS_LIMIT" ]]; then
    seconds_args=(--seconds "$SECONDS_LIMIT")
  fi

  "$PYTHON_BIN" scripts/second_pass_asr.py \
    --dry-run \
    --model "$MODEL" \
    --audio "$wav_path" \
    --history-output "$history_path" \
    --metadata-output "$metadata_path" \
    "${seconds_args[@]}"
}

run_container_asr() {
  local wav_path="$1"
  local history_path="$2"
  local metadata_path="$3"
  local fixture_name="$4"

  if ! docker compose ps --format json nemo-agent >/dev/null 2>&1; then
    echo "error: nemo-agent container is not available; start the local stack first" >&2
    return 1
  fi

  local safe_name
  safe_name="${fixture_name//[^A-Za-z0-9_.-]/_}"
  local container_script="/tmp/ambient-second-pass-${RUN_ID}-${safe_name}.py"
  local container_audio="/tmp/ambient-second-pass-${RUN_ID}-${safe_name}.wav"
  local container_history="/tmp/ambient-second-pass-${RUN_ID}-${safe_name}-history.json"
  local container_metadata="/tmp/ambient-second-pass-${RUN_ID}-${safe_name}-metadata.json"
  local seconds_args=()
  if [[ -n "$SECONDS_LIMIT" ]]; then
    seconds_args=(--seconds "$SECONDS_LIMIT")
  fi

  docker compose cp scripts/second_pass_asr.py "nemo-agent:${container_script}" >/dev/null
  docker compose cp "$wav_path" "nemo-agent:${container_audio}" >/dev/null

  local exit_status=0
  docker compose exec -T nemo-agent python "$container_script" \
    --model "$MODEL" \
    --audio "$container_audio" \
    --history-output "$container_history" \
    --metadata-output "$container_metadata" \
    "${seconds_args[@]}" || exit_status=$?

  if [[ "$exit_status" -eq 0 ]]; then
    docker compose cp "nemo-agent:${container_history}" "$history_path" >/dev/null
    docker compose cp "nemo-agent:${container_metadata}" "$metadata_path" >/dev/null
  fi

  docker compose exec -T nemo-agent sh -c \
    "rm -f '$container_script' '$container_audio' '$container_history' '$container_metadata'" >/dev/null || true

  return "$exit_status"
}

score_fixture() {
  local wav_path="$1"
  local fixture_name
  fixture_name="$(basename "${wav_path%.wav}")"
  local stem="${wav_path%.wav}"
  local doctor_grid="${stem}.doctor.TextGrid"
  local patient_grid="${stem}.patient.TextGrid"

  if [[ ! -f "$doctor_grid" || ! -f "$patient_grid" ]]; then
    echo "error: missing TextGrid pair for $wav_path" >&2
    return 1
  fi

  local fixture_run_dir="$RUN_DIR/$fixture_name"
  mkdir -p "$fixture_run_dir"
  local history_path="$fixture_run_dir/history.json"
  local metadata_path="$fixture_run_dir/metadata.json"
  local score_path="$fixture_run_dir/transcript-quality.txt"

  printf 'second-pass fixture=%s model=%s dry_run=%s\n' "$fixture_name" "$MODEL" "$DRY_RUN" >&2
  if [[ "$DRY_RUN" -eq 1 ]]; then
    run_host_dry_run "$wav_path" "$history_path" "$metadata_path"
    return 0
  fi

  run_container_asr "$wav_path" "$history_path" "$metadata_path" "$fixture_name"

  local cutoff_seconds
  cutoff_seconds="$(cutoff_seconds_for_history "$history_path" "$wav_path")"
  "$PYTHON_BIN" scripts/transcript-quality.py \
    "$history_path" \
    "$cutoff_seconds" \
    "$doctor_grid" \
    "$patient_grid" \
    > "$score_path"

  printf 'score artifact: %s\n' "$score_path"
  sed -n '1,18p' "$score_path"
}

mkdir -p "$RUN_DIR"

for fixture_query in "$@"; do
  wav_path="$(resolve_fixture_wav "$fixture_query")"
  score_fixture "$wav_path"
done
