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

# consultation number -> local WAV stem (must match the generated .wav files)
declare -A STEM=(
  [02]="primock57-day1-consultation02-i-have-sore-red-skin"
  [03]="primock57-day1-consultation03-i-have-terrible-headache"
  [04]="primock57-day1-consultation04-i-dont-feel-well-i-have-a-cough-and-runny-nose"
  [05]="primock57-day1-consultation05-lower-abdominal-pain"
  [06]="primock57-day1-consultation06-hard-to-breathe"
  [07]="primock57-day1-consultation07-i-have-a-cough-and-cold"
  [08]="primock57-day1-consultation08-i-have-dry-itchy-skin"
)

downloaded=0
skipped=0
failed=0

for num in $(printf '%s\n' "${!STEM[@]}" | sort); do
  stem="${STEM[$num]}"

  # Only fetch transcripts that pair with an existing WAV fixture.
  if [[ ! -f "${DEST}/${stem}.wav" ]]; then
    echo "skip  ${stem}.wav not present - run generate-demo-consultation-audio.py first"
    skipped=$((skipped + 1))
    continue
  fi

  for channel in doctor patient; do
    source_url="${BASE_URL}/day1_consultation${num}_${channel}.TextGrid"
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
