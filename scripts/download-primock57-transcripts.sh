#!/usr/bin/env bash
#
# Download PriMock57 ground-truth transcripts (Praat TextGrid) for the local
# demo consultation WAV fixtures, so transcription quality can be measured
# against a reference. Companion to `scripts/generate-demo-consultation-audio.py`
# (which produces the mixed-mono WAVs); this fetches the matching per-channel
# TextGrids and names them after the WAV they belong to.
#
#   <wav-stem>.wav  ->  <wav-stem>.doctor.TextGrid + <wav-stem>.patient.TextGrid
#
# Source: https://github.com/babylonhealth/primock57 (transcripts/, CC BY 4.0).
# TextGrids are only fetched for consultations whose WAV already exists locally.
#
# Usage: scripts/download-primock57-transcripts.sh
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEST="${AMBIENT_SCRIBE_PRIMOCK57_FIXTURE_DIR:-${REPO_ROOT}/tests/fixtures/audio}"
MANIFEST_PATH="${REPO_ROOT}/tests/fixtures/audio/development-corpus-0.5.0.json"
BASE_URL="https://raw.githubusercontent.com/babylonhealth/primock57/main/transcripts"

downloaded=0
skipped=0
failed=0

shopt -s nullglob
wav_files=("${DEST}"/primock57-day*-consultation*.wav)
approved_stem_output="$(python3 - "${MANIFEST_PATH}" <<'PY'
import json
import sys
from pathlib import Path

manifest = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
fixtures = manifest.get("fixtures") if isinstance(manifest, dict) else None
if (
    manifest.get("schema_version") != "ambient-scribe-development-corpus/v1"
    or not isinstance(fixtures, list)
    or len(fixtures) != 10
):
    raise SystemExit("development manifest must contain ten v1 fixtures")
stems = [fixture.get("stem") if isinstance(fixture, dict) else None for fixture in fixtures]
if any(not isinstance(stem, str) or not stem.startswith("primock57-") for stem in stems):
    raise SystemExit("development manifest contains an invalid PriMock57 stem")
if len(stems) != len(set(stems)):
    raise SystemExit("development manifest contains duplicate stems")
print("\n".join(stems))
PY
)" || {
    echo "development manifest mismatch: unable to load approved WAV stems" >&2
    exit 2
}
mapfile -t approved_stems <<< "${approved_stem_output}"
discovered_stems=()
# Filename discovery is the only local WAV operation allowed before boundary approval.
for wav_path in "${wav_files[@]}"; do
    discovered_stems+=("$(basename "${wav_path}" .wav)")
done

declare -A approved_by_stem=()
declare -A discovered_by_stem=()
missing_stems=()
extra_stems=()
# Each manifest stem becomes one exact local allowlist member.
for approved_stem in "${approved_stems[@]}"; do
    approved_by_stem["${approved_stem}"]=1
done
# Each discovered name is classified before any WAV body or remote URL is opened.
for discovered_stem in "${discovered_stems[@]}"; do
    discovered_by_stem["${discovered_stem}"]=1
    # An extra local WAV could be sealed or otherwise outside development approval.
    if [[ -z "${approved_by_stem[${discovered_stem}]+x}" ]]; then
        extra_stems+=("${discovered_stem}")
    fi
done
# Missing approved audio makes an all-corpus transcript download incomplete.
for approved_stem in "${approved_stems[@]}"; do
    # The downloader must not turn a partial local corpus into plausible complete evidence.
    if [[ -z "${discovered_by_stem[${approved_stem}]+x}" ]]; then
        missing_stems+=("${approved_stem}")
    fi
done

# Any missing or extra name stops before curl and before WAV content access.
if [[ "${#missing_stems[@]}" -ne 0 || "${#extra_stems[@]}" -ne 0 ]]; then
    echo "development manifest mismatch: missing=${missing_stems[*]:-none} extra=${extra_stems[*]:-none}" >&2
    exit 2
fi

staged_files=()
cleanup_staged_files() {
    local staged_file
    # Interrupted downloads leave no temporary transcript beside approved fixtures.
    for staged_file in "${staged_files[@]}"; do
        rm -f -- "${staged_file}"
    done
}
trap cleanup_staged_files EXIT

# Every approved WAV receives Doctor and Patient truth in manifest order.
for wav_path in "${wav_files[@]}"; do
    stem="$(basename "${wav_path}" .wav)"

    # Approved stems still need a parseable PriMock day/consultation source identity.
    if [[ "${stem}" =~ ^primock57-day([0-9]+)-consultation([0-9]+)(-|$) ]]; then
        day="${BASH_REMATCH[1]}"
        consultation="${BASH_REMATCH[2]}"
    else
        echo "skip  ${stem}.wav does not match a PriMock57 day/consultation id"
        skipped=$((skipped + 1))
        continue
    fi

    # Each channel is staged beside its final path so publication stays atomic.
    for channel in doctor patient; do
        source_url="${BASE_URL}/day${day}_consultation${consultation}_${channel}.TextGrid"
        out_path="${DEST}/${stem}.${channel}.TextGrid"
        tmp_file="$(mktemp "${DEST}/.${stem}.${channel}.XXXXXX.tmp")"
        staged_files+=("${tmp_file}")

        # Validate the body is a real TextGrid, not a 404 page or an LFS pointer.
        if curl -fsS -m 60 "${source_url}" -o "${tmp_file}" \
            && head -n 1 "${tmp_file}" | grep -q 'ooTextFile'; then
            mv -- "${tmp_file}" "${out_path}"
            echo "ok    ${stem}.${channel}.TextGrid"
            downloaded=$((downloaded + 1))
        else
            rm -f "${tmp_file}"
            echo "FAIL  ${source_url}"
            failed=$((failed + 1))
        fi
    done
done

echo "---- transcripts: downloaded=${downloaded} skipped=${skipped} failed=${failed} ----"
[[ "${failed}" -eq 0 ]]
