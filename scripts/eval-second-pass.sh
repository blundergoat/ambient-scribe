#!/usr/bin/env bash
# Evaluate an offline ASR candidate against selected development consultations.
# Operators use it before changing the model that corrects a transcript after
# Stop. Production shape preserves the same row assembly the clinician receives,
# while dry runs validate sources and evidence paths without loading NeMo.
# TDT v3 stays the default until Unified passes the complete frozen comparison.
set -euo pipefail

# Default paths keep fixture evidence local and never alter the consultation app.
FIXTURE_DIR="${FIXTURE_DIR:-tests/fixtures/audio}"
PYTHON_BIN="${PYTHON_BIN:-strands_agents/.venv/bin/python}"
RUN_ID="${SECOND_PASS_RUN_ID:-$(date -u +%Y%m%dT%H%M%SZ)}"
RUN_DIR="${SECOND_PASS_RUN_DIR:-var/quality/second-pass/$RUN_ID}"
MODEL="nvidia/parakeet-tdt-0.6b-v3"
DRY_RUN=0
SECONDS_LIMIT=""
PRODUCTION_SHAPE=0
DEVELOPMENT_MANIFEST=""
LIVE_HISTORY_PATHS=()
EXPECTED_DEVELOPMENT_STEMS=(
    "primock57-day1-consultation02-i-have-sore-red-skin"
    "primock57-day1-consultation03-i-have-terrible-headache"
    "primock57-day1-consultation06-hard-to-breathe"
    "primock57-day1-consultation07-i-have-a-cough-and-cold"
    "primock57-day1-consultation08-i-have-dry-itchy-skin"
    "primock57-day2-consultation03-i-cant-hear-very-well-and-my-face-is-a-bit-numb"
    "primock57-day2-consultation09-i-cant-move-my-left-arm"
    "primock57-day3-consultation01-lips-swelling-after-eating-a-sandwich"
    "primock57-day5-consultation03-im-feeling-very-anxious"
    "primock57-day5-consultation09-tired-all-the-time"
)
EXPECTED_LIVE_HISTORY_PATHS=(
    "var/quality/full-corpus-20260713T084709Z/primock57-day1-consultation02-i-have-sore-red-skin/live-history.json"
    "var/quality/full-corpus-20260713T084709Z/primock57-day1-consultation03-i-have-terrible-headache/live-history.json"
    "var/quality/full-corpus-20260713T084709Z/primock57-day1-consultation06-hard-to-breathe/live-history.json"
    "var/quality/full-corpus-20260713T084709Z/primock57-day1-consultation07-i-have-a-cough-and-cold/live-history.json"
    "var/quality/full-corpus-20260713T084709Z/primock57-day1-consultation08-i-have-dry-itchy-skin/live-history.json"
    "var/quality/full-corpus-20260713T084709Z/primock57-day2-consultation03-i-cant-hear-very-well-and-my-face-is-a-bit-numb/live-history.json"
    "var/quality/full-corpus-20260710T2328Z/primock57-day2-consultation09-i-cant-move-my-left-arm/live-history.json"
    "var/quality/full-corpus-20260713T084709Z/primock57-day3-consultation01-lips-swelling-after-eating-a-sandwich/live-history.json"
    "var/quality/full-corpus-20260713T084709Z/primock57-day5-consultation03-im-feeling-very-anxious/live-history.json"
    "var/quality/full-corpus-20260713T084709Z/primock57-day5-consultation09-tired-all-the-time/live-history.json"
)
EXPECTED_LIVE_HISTORY_BYTES=(59454 62330 69190 95577 49683 47746 42349 51377 85260 60383)
EXPECTED_LIVE_HISTORY_SHA256=(
    "b8249548eda8145c06cc05305876d89564f9d1e99f22b3ce14a1c1b2984216aa"
    "65ae684d6d6e1cd75bf6d877307b70571f34a2a3ec003e241f637845b0e6ef3d"
    "bd1da3a11cece20e962a0f56ac5f89f612eb379074c33aea4d7e5d23d1884953"
    "4f960e75b7c0d986748306359488283863322c654e042b2d2d7dda8053c6c398"
    "68d72edd813722100c5d4579941c6d93b2a6e983e9cb29e75faa3c4c28d12ce1"
    "d7661ec42ecc27f9f16f2baed3aeaaad05354fd9f105c76abfe20968d2a2c4fc"
    "d691e00b9ffbca619f94b3789e508e95a7e30ba713bf3cfe572b8b6ddb2b20e5"
    "7a43a2d89d39424c80d4fb863eb9e030d9b42c88829e7ddf6c1dfc6e8fadc9a1"
    "72213ae6df70da2dfcea641ed4c030dda5fcff682639f08acc7e341f0855e7ea"
    "c7e4054e8791b225d2a9953145a405e95f62c0a0116bff317ebb8cca1298cad7"
)
PRODUCTION_AUDIO_BYTES=()
PRODUCTION_AUDIO_SHA256=()
MANIFEST_BYTES=""
MANIFEST_SHA256=""

# Explain the validated default and the explicit experiment available to the operator.
usage() {
    cat <<'USAGE'
Usage:
  scripts/eval-second-pass.sh [--model MODEL] [--seconds N] [--dry-run] <fixture-stem-or-wav> [...]
  scripts/eval-second-pass.sh --production-shape --development-manifest PATH \
    --live-history PATH [...10 paths] <10 explicit development stems>

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
Production shape requires the exact ten frozen stems and visible-row sources in
order. No-argument and --all corpus execution are rejected before source access.
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
        # Production shape uses the same corrected-row assembly the clinician receives after Stop.
        --production-shape)
            PRODUCTION_SHAPE=1
            shift
            ;;
        # The versioned manifest authorizes only the frozen development consultations.
        --development-manifest)
            # Missing manifest text cannot identify the corpus contract for this run.
            if [[ $# -lt 2 ]]; then
                echo "error: --development-manifest requires a value" >&2
                exit 2
            fi
            DEVELOPMENT_MANIFEST="$2"
            shift 2
            ;;
        # Each explicit history preserves the rows and roles shown for one consultation.
        --live-history)
            # Missing history text cannot pair a visible transcript with its audio.
            if [[ $# -lt 2 ]]; then
                echo "error: --live-history requires a value" >&2
                exit 2
            fi
            LIVE_HISTORY_PATHS+=("$2")
            shift 2
            ;;
        # Implicit corpus expansion could include a sealed consultation without naming it.
        --all)
            echo "error: --all is forbidden; pass the ten development stems explicitly" >&2
            exit 2
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

# Every remaining positional value is checked so `--all` cannot hide after `--`.
for fixture_selection in "$@"; do
    # Implicit discovery remains forbidden even when it appears after a valid stem.
    if [[ "$fixture_selection" == "--all" ]]; then
        echo "error: --all is forbidden; pass the ten development stems explicitly" >&2
        exit 2
    fi
done

# Production mode freezes the complete corpus and source pairing before output creation.
if [[ "$PRODUCTION_SHAPE" -eq 1 ]]; then
    # A cutoff would compare different audio from the full-consultation contract.
    if [[ -n "$SECONDS_LIMIT" ]]; then
        echo "error: --production-shape does not permit --seconds" >&2
        exit 2
    fi
    # Without the versioned manifest there is no authorized development corpus.
    if [[ -z "$DEVELOPMENT_MANIFEST" ]]; then
        echo "error: --production-shape requires --development-manifest" >&2
        exit 2
    fi
    # Every arm contains exactly the ten frozen consultations, never a selected subset.
    if [[ $# -ne "${#EXPECTED_DEVELOPMENT_STEMS[@]}" ]]; then
        echo "error: --production-shape requires exactly ten fixture stems" >&2
        exit 2
    fi
    # One retained visible transcript must pair with each consultation in the arm.
    if [[ "${#LIVE_HISTORY_PATHS[@]}" -ne "${#EXPECTED_DEVELOPMENT_STEMS[@]}" ]]; then
        echo "error: --production-shape requires exactly ten --live-history paths" >&2
        exit 2
    fi
    # Compare each CLI position before opening the manifest, audio, or visible transcript.
    for fixture_index in "${!EXPECTED_DEVELOPMENT_STEMS[@]}"; do
        fixture_position=$((fixture_index + 1))
        # A different name or order would make the two model arms incomparable.
        if [[ "${!fixture_position}" != "${EXPECTED_DEVELOPMENT_STEMS[$fixture_index]}" ]]; then
            echo "error: production fixture order differs at position $fixture_position" >&2
            exit 2
        fi
    done

    resolved_development_manifest="$($PYTHON_BIN -c \
        'import pathlib, sys; print(pathlib.Path(sys.argv[1]).resolve())' \
        "$DEVELOPMENT_MANIFEST")"
    expected_development_manifest="$(pwd)/tests/fixtures/audio/development-corpus-0.5.0.json"
    # A redirected manifest could authorize different consultations or scoring bytes.
    if [[ "$resolved_development_manifest" != "$expected_development_manifest" ]]; then
        echo "error: production shape requires $expected_development_manifest" >&2
        exit 2
    fi
    # A reused output directory could overwrite or mix evidence from another model arm.
    if [[ -e "$RUN_DIR" ]]; then
        echo "error: production evidence directory already exists: $RUN_DIR" >&2
        exit 2
    fi

    # Each supplied history path must name the pre-frozen scaffold for its exact position.
    for fixture_index in "${!EXPECTED_LIVE_HISTORY_PATHS[@]}"; do
        resolved_live_history="$($PYTHON_BIN -c \
            'import pathlib, sys; print(pathlib.Path(sys.argv[1]).resolve())' \
            "${LIVE_HISTORY_PATHS[$fixture_index]}")"
        expected_live_history="$(pwd)/${EXPECTED_LIVE_HISTORY_PATHS[$fixture_index]}"
        # A namesake or swapped history could change roles and correction assembly.
        if [[ "$resolved_live_history" != "$expected_live_history" ]]; then
            echo "error: live-history path differs at position $((fixture_index + 1))" >&2
            exit 2
        fi
    done

    # The Python contract independently proves the exact ten ordered labels without fixture access.
    "$PYTHON_BIN" - "$@" <<'PY'
import runpy
import sys

production_evaluator = runpy.run_path("scripts/second_pass_production.py")
production_evaluator["validate_production_corpus_selection"](sys.argv[1:])
PY

    development_corpus_arguments=()
    # Pass all ten literal stems to the manifest/scorer authority in the same order.
    for fixture_selection in "$@"; do
        development_corpus_arguments+=(--stem "$fixture_selection")
    done
    "$PYTHON_BIN" scripts/development-corpus.py \
        --manifest "$DEVELOPMENT_MANIFEST" \
        --repo-root "$(pwd)" \
        "${development_corpus_arguments[@]}" \
        --json

    MANIFEST_BYTES="$(stat -c '%s' "$DEVELOPMENT_MANIFEST")"
    MANIFEST_SHA256="$(sha256sum "$DEVELOPMENT_MANIFEST" | cut -d' ' -f1)"
    # Source bytes are rechecked now and again inside the container for each case.
    for fixture_index in "${!EXPECTED_DEVELOPMENT_STEMS[@]}"; do
        production_wav="$FIXTURE_DIR/${EXPECTED_DEVELOPMENT_STEMS[$fixture_index]}.wav"
        production_live_history="${LIVE_HISTORY_PATHS[$fixture_index]}"
        # A missing frozen scaffold cannot preserve the transcript the clinician already saw.
        if [[ ! -f "$production_live_history" ]]; then
            echo "error: missing live-history source: $production_live_history" >&2
            exit 2
        fi
        actual_live_history_bytes="$(stat -c '%s' "$production_live_history")"
        # A changed size means this is no longer the scaffold frozen before either result.
        if [[ "$actual_live_history_bytes" != "${EXPECTED_LIVE_HISTORY_BYTES[$fixture_index]}" ]]; then
            echo "error: live-history size drift at position $((fixture_index + 1))" >&2
            exit 2
        fi
        actual_live_history_sha256="$(sha256sum "$production_live_history" | cut -d' ' -f1)"
        # Same-sized changed rows still invalidate the user-visible model comparison.
        if [[ "$actual_live_history_sha256" != "${EXPECTED_LIVE_HISTORY_SHA256[$fixture_index]}" ]]; then
            echo "error: live-history hash drift at position $((fixture_index + 1))" >&2
            exit 2
        fi
        PRODUCTION_AUDIO_BYTES+=("$(stat -c '%s' "$production_wav")")
        PRODUCTION_AUDIO_SHA256+=("$(sha256sum "$production_wav" | cut -d' ' -f1)")
    done
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
    local live_history_path="$4"
    local fixture_index="$5"
    local seconds_arguments=()
    local production_arguments=()
    # A selected cutoff keeps the dry artifact aligned with the operator's intended interval.
    if [[ -n "$SECONDS_LIMIT" ]]; then
        seconds_arguments=(--seconds "$SECONDS_LIMIT")
    fi
    # Production dry runs prove exact source identity without importing application NeMo code.
    if [[ "$PRODUCTION_SHAPE" -eq 1 ]]; then
        production_arguments=(
            --production-shape
            --fixture-stem "${EXPECTED_DEVELOPMENT_STEMS[$fixture_index]}"
            --live-history "$live_history_path"
            --development-manifest "$DEVELOPMENT_MANIFEST"
            --expected-audio-bytes "${PRODUCTION_AUDIO_BYTES[$fixture_index]}"
            --expected-audio-sha256 "${PRODUCTION_AUDIO_SHA256[$fixture_index]}"
            --expected-live-history-bytes "${EXPECTED_LIVE_HISTORY_BYTES[$fixture_index]}"
            --expected-live-history-sha256 "${EXPECTED_LIVE_HISTORY_SHA256[$fixture_index]}"
            --expected-manifest-bytes "$MANIFEST_BYTES"
            --expected-manifest-sha256 "$MANIFEST_SHA256"
        )
    fi

    "$PYTHON_BIN" scripts/second_pass_asr.py \
        --dry-run \
        --model "$MODEL" \
        --audio "$wav_path" \
        --history-output "$history_path" \
        --metadata-output "$metadata_path" \
        "${production_arguments[@]}" \
        "${seconds_arguments[@]}"
}

# Run a real candidate inside the pinned NeMo container and copy safe evidence back.
run_container_asr() {
    local wav_path="$1"
    local history_path="$2"
    local metadata_path="$3"
    local fixture_name="$4"
    local live_history_path="$5"
    local fixture_index="$6"

    # A stopped NeMo container cannot produce a pinned-runtime comparison for the operator.
    if ! docker compose ps --format json nemo-agent >/dev/null 2>&1; then
        echo "error: nemo-agent container is not available; start the local stack first" >&2
        return 1
    fi

    local safe_fixture_name
    safe_fixture_name="${fixture_name//[^A-Za-z0-9_.-]/_}"
    local container_runner_dir="/tmp/ambient-second-pass-${RUN_ID}-${safe_fixture_name}-runner"
    local container_script="${container_runner_dir}/second_pass_asr.py"
    local container_production_helper="${container_runner_dir}/second_pass_production.py"
    local container_audio="/tmp/ambient-second-pass-${RUN_ID}-${safe_fixture_name}.wav"
    local container_history="/tmp/ambient-second-pass-${RUN_ID}-${safe_fixture_name}-history.json"
    local container_metadata="/tmp/ambient-second-pass-${RUN_ID}-${safe_fixture_name}-metadata.json"
    local container_source_dir="/tmp/ambient-second-pass-${RUN_ID}/${safe_fixture_name}"
    local container_live_history="${container_source_dir}/live-history.json"
    local container_manifest="${container_source_dir}/development-corpus-0.5.0.json"
    local seconds_arguments=()
    local production_arguments=()
    # A selected cutoff keeps container inference on the operator's intended interval.
    if [[ -n "$SECONDS_LIMIT" ]]; then
        seconds_arguments=(--seconds "$SECONDS_LIMIT")
    fi

    docker compose exec -T nemo-agent mkdir -p "$container_runner_dir"
    docker compose cp scripts/second_pass_asr.py "nemo-agent:${container_script}" >/dev/null
    docker compose cp scripts/second_pass_production.py \
        "nemo-agent:${container_production_helper}" >/dev/null
    # Production sources use exact names so cross-consultation pairing fails inside the container.
    if [[ "$PRODUCTION_SHAPE" -eq 1 ]]; then
        container_audio="${container_source_dir}/${fixture_name}.wav"
        docker compose exec -T nemo-agent mkdir -p "$container_source_dir"
        docker compose cp "$wav_path" "nemo-agent:${container_audio}" >/dev/null
        docker compose cp "$live_history_path" "nemo-agent:${container_live_history}" >/dev/null
        docker compose cp "$DEVELOPMENT_MANIFEST" "nemo-agent:${container_manifest}" >/dev/null
        production_arguments=(
            --production-shape
            --fixture-stem "$fixture_name"
            --live-history "$container_live_history"
            --development-manifest "$container_manifest"
            --expected-audio-bytes "${PRODUCTION_AUDIO_BYTES[$fixture_index]}"
            --expected-audio-sha256 "${PRODUCTION_AUDIO_SHA256[$fixture_index]}"
            --expected-live-history-bytes "${EXPECTED_LIVE_HISTORY_BYTES[$fixture_index]}"
            --expected-live-history-sha256 "${EXPECTED_LIVE_HISTORY_SHA256[$fixture_index]}"
            --expected-manifest-bytes "$MANIFEST_BYTES"
            --expected-manifest-sha256 "$MANIFEST_SHA256"
        )
    # Legacy explicit comparisons retain their isolated copied WAV behavior.
    else
        docker compose cp "$wav_path" "nemo-agent:${container_audio}" >/dev/null
    fi

    local candidate_exit_status=0
    docker compose exec -T -e PYTHONPATH=/app nemo-agent python "$container_script" \
        --model "$MODEL" \
        --audio "$container_audio" \
        --history-output "$container_history" \
        --metadata-output "$container_metadata" \
        "${production_arguments[@]}" \
        "${seconds_arguments[@]}" || candidate_exit_status=$?

    local candidate_artifacts_exist=0
    # Every production exit, including import/model failure, must retain its evidence pair.
    if docker compose exec -T nemo-agent test -f "$container_history" \
        && docker compose exec -T nemo-agent test -f "$container_metadata"; then
        candidate_artifacts_exist=1
        # Production retains failures; legacy retains only its existing successful result behavior.
        if [[ "$PRODUCTION_SHAPE" -eq 1 || "$candidate_exit_status" -eq 0 ]]; then
            docker compose cp "nemo-agent:${container_history}" "$history_path" >/dev/null
            docker compose cp "nemo-agent:${container_metadata}" "$metadata_path" >/dev/null
        fi
    fi

    docker compose exec -T nemo-agent sh -c \
        "rm -f '$container_script' '$container_production_helper' '$container_audio' '$container_history' '$container_metadata'" \
        >/dev/null || true
    docker compose exec -T nemo-agent rmdir "$container_runner_dir" >/dev/null || true

    # Production cleanup removes only the copied scaffold and manifest for this one case.
    if [[ "$PRODUCTION_SHAPE" -eq 1 ]]; then
        docker compose exec -T nemo-agent rm -f \
            "$container_live_history" "$container_manifest" >/dev/null || true
        docker compose exec -T nemo-agent rmdir "$container_source_dir" >/dev/null || true
        docker compose exec -T nemo-agent rmdir \
            "/tmp/ambient-second-pass-${RUN_ID}" >/dev/null || true
    fi

    # Missing production evidence is an evaluator failure, never a reason to rerun the case.
    if [[ "$PRODUCTION_SHAPE" -eq 1 && "$candidate_artifacts_exist" -ne 1 ]]; then
        echo "error: candidate did not return history and metadata evidence" >&2
        return 1
    fi

    return "$candidate_exit_status"
}

# Score one resolved consultation or create its dry-run evidence for operator review.
score_fixture() {
    local wav_path="$1"
    local live_history_path="$2"
    local fixture_index="$3"
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
        run_host_dry_run \
            "$wav_path" \
            "$history_path" \
            "$metadata_path" \
            "$live_history_path" \
            "$fixture_index"
        return 0
    fi

    local candidate_exit_status=0
    run_container_asr \
        "$wav_path" \
        "$history_path" \
        "$metadata_path" \
        "$fixture_name" \
        "$live_history_path" \
        "$fixture_index" || candidate_exit_status=$?

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
    return "$candidate_exit_status"
}

mkdir -p "$RUN_DIR"

# Each requested consultation gets its own deterministic evidence folder.
fixture_index=0
for fixture_query in "$@"; do
    live_history_path=""
    # Production uses the exact manifest path and frozen source at this campaign position.
    if [[ "$PRODUCTION_SHAPE" -eq 1 ]]; then
        wav_path="$FIXTURE_DIR/${EXPECTED_DEVELOPMENT_STEMS[$fixture_index]}.wav"
        live_history_path="${LIVE_HISTORY_PATHS[$fixture_index]}"
    # Legacy mode retains its existing explicit path or unambiguous query behavior.
    else
        wav_path="$(resolve_fixture_wav "$fixture_query")"
    fi
    score_fixture "$wav_path" "$live_history_path" "$fixture_index"
    fixture_index=$((fixture_index + 1))
done
