#!/usr/bin/env bash
# Evaluate an offline ASR candidate against a selected consultation fixture.
# Use this helper when an operator compares post-visit wording before changing
# the model used by the Scribe app. Dry runs validate selection and evidence
# paths without loading NeMo or consuming the consultation GPU.
# TDT v3 is validated; Unified remains an explicit pinned-runtime experiment.
set -euo pipefail

# Default paths keep fixture evidence local and never alter the consultation app.
FIXTURE_DIR="${FIXTURE_DIR:-tests/fixtures/audio}"
PYTHON_BIN="${PYTHON_BIN:-strands_agents/.venv/bin/python}"
RUN_ID="${SECOND_PASS_RUN_ID:-$(date -u +%Y%m%dT%H%M%SZ)}"
RUN_DIR="${SECOND_PASS_RUN_DIR:-var/quality/second-pass/$RUN_ID}"
MODEL="nvidia/parakeet-tdt-0.6b-v3"
DRY_RUN=0
SECONDS_LIMIT=""

# Explain the validated default and the explicit experiment available to the operator.
usage() {
    cat <<'USAGE'
Usage:
  scripts/eval-second-pass.sh [--model MODEL] [--seconds N] [--dry-run] <fixture-stem-or-wav> [...]

Examples:
  scripts/eval-second-pass.sh --dry-run day1-consultation02
  scripts/eval-second-pass.sh --seconds 60 day1-consultation02
  scripts/eval-second-pass.sh --model nvidia/parakeet-unified-en-0.6b day1-consultation02

The validated default is nvidia/parakeet-tdt-0.6b-v3 in the pinned NeMo 26.02 /
Toolkit 2.7.3 runtime. Unified remains an explicit --model experiment because
the recorded pinned-runtime attempt failed during model construction; revisit it
only after the checkpoint revision or pinned runtime changes.

Artifacts are written under var/quality/second-pass/<run-id>/. Real inference
executes inside the running nemo-agent container without changing the live app.
USAGE
}

# Operator options are read before any fixture or container is touched.
while [[ $# -gt 0 ]]; do
    # Each option selects evidence behavior without changing the live Scribe model.
    case "$1" in
        # Help gives the operator the model/runtime decision without creating evidence.
        --help|-h)
            usage
            exit 0
            ;;
        # Dry runs prove fixture and output wiring without loading a model.
        --dry-run)
            DRY_RUN=1
            shift
            ;;
        # An explicit model keeps Unified selectable for a future compatibility probe.
        --model)
            # Missing model text cannot identify the candidate the operator wants to compare.
            if [[ $# -lt 2 ]]; then
                echo "error: --model requires a value" >&2
                exit 2
            fi
            MODEL="$2"
            shift 2
            ;;
        # A cutoff lets the operator compare only the leading consultation interval.
        --seconds)
            # Missing seconds cannot define the audible interval to evaluate.
            if [[ $# -lt 2 ]]; then
                echo "error: --seconds requires a value" >&2
                exit 2
            fi
            SECONDS_LIMIT="$2"
            shift 2
            ;;
        # `--` ends options so a fixture name beginning with a dash remains selectable.
        --)
            shift
            break
            ;;
        # Unknown options stop before any GPU or evidence work begins.
        -*)
            echo "error: unknown option: $1" >&2
            usage >&2
            exit 2
            ;;
        # The first fixture selection starts the operator's requested work list.
        *)
            break
            ;;
    esac
done

# No fixture means there is no consultation for the operator to compare.
if [[ $# -eq 0 ]]; then
    echo "error: provide at least one fixture stem or WAV path" >&2
    usage >&2
    exit 2
fi

# Resolve one operator selection to exactly one consultation WAV.
resolve_fixture_wav() {
    local fixture_selection="$1"

    # A direct WAV path is already the exact consultation the operator selected.
    if [[ -f "$fixture_selection" ]]; then
        printf '%s\n' "$fixture_selection"
        return 0
    fi

    local matching_wavs=()
    # Every matching filename is retained so ambiguous consultation labels fail visibly.
    while IFS= read -r matching_wav; do
        matching_wavs+=("$matching_wav")
    done < <(find "$FIXTURE_DIR" -maxdepth 1 -type f -name "*${fixture_selection}*.wav" | sort)

    # One match gives the operator a deterministic consultation artifact.
    if [[ "${#matching_wavs[@]}" -eq 1 ]]; then
        printf '%s\n' "${matching_wavs[0]}"
        return 0
    fi

    # No match tells the operator to correct the consultation name or fixture path.
    if [[ "${#matching_wavs[@]}" -eq 0 ]]; then
        echo "error: no fixture WAV matched '$fixture_selection' in $FIXTURE_DIR" >&2
    # Multiple matches prevent the report from silently scoring the wrong consultation.
    else
        echo "error: fixture query '$fixture_selection' matched multiple WAVs:" >&2
        printf '  %s\n' "${matching_wavs[@]}" >&2
    fi
    return 1
}

# Choose the spoken cutoff used to compare the candidate with its reference transcript.
cutoff_seconds_for_history() {
    local history_path="$1"
    local wav_path="$2"

    # An absent cutoff falls back to the generated rows, then the complete consultation duration.
    "$PYTHON_BIN" - "$history_path" "$wav_path" "${SECONDS_LIMIT:-}" <<'PY'
import json
import sys
import wave

history_path, wav_path, seconds_limit = sys.argv[1:4]
history = json.load(open(history_path, encoding="utf-8"))
segments = history.get("segments", [])
# Generated rows define the exact audible interval represented in the candidate artifact.
if segments:
    print(max(float(segment.get("end", 0.0) or 0.0) for segment in segments))
    raise SystemExit(0)

# An operator cutoff remains authoritative when dry evidence has no transcript rows.
if seconds_limit:
    print(float(seconds_limit))
    raise SystemExit(0)

# A full-fixture comparison uses the source WAV duration when no shorter interval exists.
with wave.open(wav_path, "rb") as wav_file:
    print(wav_file.getnframes() / wav_file.getframerate())
PY
}

# Validate one fixture selection and output path on the host without loading NeMo.
run_host_dry_run() {
    local wav_path="$1"
    local history_path="$2"
    local metadata_path="$3"
    local seconds_arguments=()
    # A selected cutoff keeps the dry artifact aligned with the operator's intended interval.
    if [[ -n "$SECONDS_LIMIT" ]]; then
        seconds_arguments=(--seconds "$SECONDS_LIMIT")
    fi

    "$PYTHON_BIN" scripts/second_pass_asr.py \
        --dry-run \
        --model "$MODEL" \
        --audio "$wav_path" \
        --history-output "$history_path" \
        --metadata-output "$metadata_path" \
        "${seconds_arguments[@]}"
}

# Run a real candidate inside the pinned NeMo container and copy safe evidence back.
run_container_asr() {
    local wav_path="$1"
    local history_path="$2"
    local metadata_path="$3"
    local fixture_name="$4"

    # A stopped NeMo container cannot produce a pinned-runtime comparison for the operator.
    if ! docker compose ps --format json nemo-agent >/dev/null 2>&1; then
        echo "error: nemo-agent container is not available; start the local stack first" >&2
        return 1
    fi

    local safe_fixture_name
    safe_fixture_name="${fixture_name//[^A-Za-z0-9_.-]/_}"
    local container_script="/tmp/ambient-second-pass-${RUN_ID}-${safe_fixture_name}.py"
    local container_audio="/tmp/ambient-second-pass-${RUN_ID}-${safe_fixture_name}.wav"
    local container_history="/tmp/ambient-second-pass-${RUN_ID}-${safe_fixture_name}-history.json"
    local container_metadata="/tmp/ambient-second-pass-${RUN_ID}-${safe_fixture_name}-metadata.json"
    local seconds_arguments=()
    # A selected cutoff keeps container inference on the operator's intended interval.
    if [[ -n "$SECONDS_LIMIT" ]]; then
        seconds_arguments=(--seconds "$SECONDS_LIMIT")
    fi

    docker compose cp scripts/second_pass_asr.py "nemo-agent:${container_script}" >/dev/null
    docker compose cp "$wav_path" "nemo-agent:${container_audio}" >/dev/null

    local candidate_exit_status=0
    docker compose exec -T nemo-agent python "$container_script" \
        --model "$MODEL" \
        --audio "$container_audio" \
        --history-output "$container_history" \
        --metadata-output "$container_metadata" \
        "${seconds_arguments[@]}" || candidate_exit_status=$?

    # Successful inference returns both artifacts needed for the operator's comparison.
    if [[ "$candidate_exit_status" -eq 0 ]]; then
        docker compose cp "nemo-agent:${container_history}" "$history_path" >/dev/null
        docker compose cp "nemo-agent:${container_metadata}" "$metadata_path" >/dev/null
    fi

    docker compose exec -T nemo-agent sh -c \
        "rm -f '$container_script' '$container_audio' '$container_history' '$container_metadata'" \
        >/dev/null || true

    return "$candidate_exit_status"
}

# Score one resolved consultation or create its dry-run evidence for operator review.
score_fixture() {
    local wav_path="$1"
    local fixture_name
    fixture_name="$(basename "${wav_path%.wav}")"
    local fixture_stem="${wav_path%.wav}"
    local doctor_grid="${fixture_stem}.doctor.TextGrid"
    local patient_grid="${fixture_stem}.patient.TextGrid"

    # Both reference speakers are required before a real candidate can be scored honestly.
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
    # Dry mode stops after proving the selected TDT default and evidence destinations.
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

# Each requested consultation gets its own deterministic evidence folder.
for fixture_query in "$@"; do
    wav_path="$(resolve_fixture_wav "$fixture_query")"
    score_fixture "$wav_path"
done
