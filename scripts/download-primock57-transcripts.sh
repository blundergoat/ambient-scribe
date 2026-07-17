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
DEST="${REPO_ROOT}/tests/fixtures/audio"
BASE_URL="https://raw.githubusercontent.com/babylonhealth/primock57/main/transcripts"

downloaded=0
skipped=0
failed=0

shopt -s nullglob
wav_files=("${DEST}"/primock57-day*-consultation*.wav)

if [[ "${#wav_files[@]}" -eq 0 ]]; then
    echo "skip  no PriMock57 WAV fixtures found - run generate-demo-consultation-audio.py first"
    skipped=$((skipped + 1))
fi

for wav_path in "${wav_files[@]}"; do
    stem="$(basename "${wav_path}" .wav)"

    if [[ "${stem}" =~ ^primock57-day([0-9]+)-consultation([0-9]+)(-|$) ]]; then
        day="${BASH_REMATCH[1]}"
        consultation="${BASH_REMATCH[2]}"
    else
        echo "skip  ${stem}.wav does not match a PriMock57 day/consultation id"
        skipped=$((skipped + 1))
        continue
    fi

    for channel in doctor patient; do
        source_url="${BASE_URL}/day${day}_consultation${consultation}_${channel}.TextGrid"
        out_path="${DEST}/${stem}.${channel}.TextGrid"
        tmp_file="$(mktemp)"

        # Validate the body is a real TextGrid, not a 404 page or an LFS pointer.
        if curl -fsS -m 60 "${source_url}" -o "${tmp_file}" \
            && head -n 1 "${tmp_file}" | grep -q 'ooTextFile'; then
            mv "${tmp_file}" "${out_path}"
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
