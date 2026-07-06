#!/usr/bin/env bash
# Score post-stop corrected transcripts against PriMock57 fixtures.
#
# This developer-only runner streams fixture WAVs through the live WebSocket,
# waits for the stopped-session quality row, calls the existing correction
# endpoint while retained audio is still available, then scores both live and
# corrected artifacts. Use it before changing alignment or model choices so the
# clinician-facing summary path is measured across more than one replay.

set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

AGENT_HTTP_URL="${AGENT_HTTP_URL:-http://localhost:48101}"
AGENT_WS_URL="${AGENT_WS_URL:-ws://localhost:48101}"
FIXTURE_DIR="${FIXTURE_DIR:-tests/fixtures/audio}"
PYTHON_BIN="${PYTHON_BIN:-strands_agents/.venv/bin/python}"
QUALITY_RECORD_PATH="${SESSION_QUALITY_HOST_PATH:-strands_agents/var/quality/sessions.jsonl}"
RUN_ID="${CORRECTED_FIXTURE_RUN_ID:-$(date -u +%Y%m%dT%H%M%SZ)}"
RUN_DIR="${CORRECTED_FIXTURE_RUN_DIR:-var/quality/corrected-fixtures/$RUN_ID}"
RUN_STARTED_AT="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
SOURCE_CHIP_SCORE_TEXT_PATH="$RUN_DIR/source-chip-score.txt"
SOURCE_CHIP_SCORE_JSON_PATH="$RUN_DIR/source-chip-score.json"
SOURCE_CHIP_FAIL_ON_FINDINGS="${CORRECTED_SOURCE_CHIP_FAIL_ON_FINDINGS:-0}"
CHUNK_MS="${EVAL_CHUNK_MS:-5000}"
ROLE_SETTLE_SECONDS="${EVAL_ROLE_TIMELINE_SETTLE_SECONDS:-8}"
SECONDS_LIMIT="${EVAL_FIXTURE_SECONDS:-}"
declare -a FIXTURE_QUERIES=()
declare -a FIXTURE_PATHS=()
declare -a REPORT_ROWS=()

usage() {
  cat <<'USAGE'
Usage:
  scripts/eval-corrected-fixtures.sh [--seconds N] <fixture-stem-or-wav> [...]
  scripts/eval-corrected-fixtures.sh [--seconds N] --all

Examples:
  scripts/eval-corrected-fixtures.sh --seconds 60 consultation03
  scripts/eval-corrected-fixtures.sh --seconds 60 consultation02 consultation03 consultation08

Environment:
  AGENT_HTTP_URL                 default http://localhost:48101
  AGENT_WS_URL                   default ws://localhost:48101
  EVAL_FIXTURE_SECONDS           optional cutoff to stream from each WAV
  CORRECTED_FIXTURE_RUN_DIR      optional artifact directory
  EVAL_ROLE_TIMELINE_SETTLE_SECONDS
                                  seconds to wait for visible live Doctor/Patient labels; default 8
  CORRECTED_SOURCE_CHIP_FAIL_ON_FINDINGS
                                  set 1 to fail the eval when source-chip findings exist; default 0
USAGE
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --seconds)
      # The user must choose a duration so cutoff scoring matches the heard audio.
      if [[ $# -lt 2 ]]; then
        echo "error: --seconds requires a numeric value" >&2
        exit 2
      fi
      SECONDS_LIMIT="$2"
      shift 2
      ;;
    --all)
      FIXTURE_QUERIES+=("__all__")
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      FIXTURE_QUERIES+=("$1")
      shift
      ;;
  esac
done

resolve_fixtures() {
  # Resolve fixture names to WAV paths shown in the Demo Audio picker.
  # No explicit fixture means a developer wants the whole local fixture set.
  if [[ ${#FIXTURE_QUERIES[@]} -eq 0 ]]; then
    FIXTURE_QUERIES+=("__all__")
  fi

  # Each query is either the all sentinel, a direct path, or a unique stem match.
  for query in "${FIXTURE_QUERIES[@]}"; do
    # The --all sentinel expands to every local WAV fixture.
    if [[ "$query" == "__all__" ]]; then
      while IFS= read -r fixture_path; do
        FIXTURE_PATHS+=("$fixture_path")
      done < <(find "$FIXTURE_DIR" -maxdepth 1 -type f -name '*.wav' | sort)
      continue
    fi

    # Direct paths let a developer score a newly generated local fixture.
    if [[ -f "$query" ]]; then
      FIXTURE_PATHS+=("$query")
      continue
    fi

    local -a matches=()
    while IFS= read -r fixture_path; do
      matches+=("$fixture_path")
    done < <(find "$FIXTURE_DIR" -maxdepth 1 -type f -name "*${query}*.wav" | sort)

    # Empty matches mean the developer likely mistyped the consultation number.
    if [[ ${#matches[@]} -eq 0 ]]; then
      echo "error: no WAV fixture matches '$query' under $FIXTURE_DIR" >&2
      exit 2
    fi

    # Ambiguous matches would make the saved score artifact hard to trust.
    if [[ ${#matches[@]} -gt 1 ]]; then
      printf "error: fixture query '%s' matched multiple files:\n" "$query" >&2
      printf '  %s\n' "${matches[@]}" >&2
      exit 2
    fi

    FIXTURE_PATHS+=("${matches[0]}")
  done
}

require_ready_agent() {
  # Check that the local NeMo agent can accept replay and correction calls.
  # The virtualenv owns websocket and scorer dependencies for local evals.
  if [[ ! -x "$PYTHON_BIN" ]]; then
    echo "error: Python venv not found at $PYTHON_BIN" >&2
    exit 2
  fi

  # A failed health check means replay would not produce scoreable artifacts.
  if ! curl -fsS "$AGENT_HTTP_URL/health" >/dev/null; then
    echo "error: agent health check failed at $AGENT_HTTP_URL/health" >&2
    exit 2
  fi
}

fixture_textgrid_paths() {
  # Return doctor/patient TextGrid paths for the fixture the user selected.
  local wav_path="$1"
  local stem="${wav_path%.wav}"
  local doctor_grid="${stem}.doctor.TextGrid"
  local patient_grid="${stem}.patient.TextGrid"

  # Both reference channels are required for fair role and WER scoring.
  if [[ ! -f "$doctor_grid" || ! -f "$patient_grid" ]]; then
    echo "error: missing TextGrid pair for $wav_path" >&2
    exit 2
  fi

  printf '%s\t%s\n' "$doctor_grid" "$patient_grid"
}

stream_fixture_to_websocket() {
  # Replay one WAV through the live browser PCM WebSocket contract.
  local wav_path="$1"
  local session_id="$2"

  "$PYTHON_BIN" - "$wav_path" "$SECONDS_LIMIT" "$AGENT_WS_URL" "$session_id" "$CHUNK_MS" <<'PY'
import asyncio
import sys
import wave

import websockets


async def main() -> None:
    """Send the selected fixture as the same PCM chunks the browser sends."""
    wav_path, seconds_limit, agent_ws_url, session_id, chunk_ms = sys.argv[1:6]
    with wave.open(wav_path, "rb") as wav_file:
        sample_rate = wav_file.getframerate()
        channels = wav_file.getnchannels()
        sample_width = wav_file.getsampwidth()
        total_frames = wav_file.getnframes()

        # Non-browser-shaped fixtures cannot test the live consultation path.
        if sample_rate != 16000 or channels != 1 or sample_width != 2:
            raise SystemExit(
                "fixture must be 16 kHz mono signed 16-bit PCM for the browser PCM path"
            )

        frame_limit = total_frames
        # A duration cap lets developers run quick smoke tests from the same fixture.
        if seconds_limit:
            frame_limit = min(total_frames, int(float(seconds_limit) * sample_rate))

        chunk_frames = max(1, int(sample_rate * (int(chunk_ms) / 1000)))
        sent_frames = 0
        uri = f"{agent_ws_url.rstrip('/')}/ws/transcribe/{session_id}"

        async with websockets.connect(
            uri,
            additional_headers={"x-correlation-id": f"eval-corrected-{session_id}"},
        ) as websocket:
            # Each chunk advances the server's live transcript exactly once.
            while sent_frames < frame_limit:
                frames_to_read = min(chunk_frames, frame_limit - sent_frames)
                audio_chunk = wav_file.readframes(frames_to_read)

                # Empty audio means the requested clip has fully streamed.
                if audio_chunk == b"":
                    break

                await websocket.send(audio_chunk)
                sent_frames += frames_to_read


asyncio.run(main())
PY
}

wait_for_quality_record() {
  # Wait until the stopped replay writes its session quality artifact.
  local session_id="$1"
  local quality_path="$2"

  # The quality JSONL row proves finalize ran for the user's replay.
  for _ in $(seq 1 90); do
    # The JSONL file appears only after the first finalized session.
    if [[ -f "$QUALITY_RECORD_PATH" ]]; then
      "$PYTHON_BIN" - "$QUALITY_RECORD_PATH" "$session_id" "$quality_path" <<'PY' && return 0
import json
import sys
from pathlib import Path

record_path = Path(sys.argv[1])
session_id = sys.argv[2]
out_path = Path(sys.argv[3])

with record_path.open(encoding="utf-8") as records:
    # Each JSONL record is one finalized consultation.
    for line in records:
        # Blank lines are ignored so manual inspection does not break scoring.
        if not line.strip():
            continue
        record = json.loads(line)
        # Matching session ID gives this fixture its quality record.
        if record.get("session_id") == session_id:
            out_path.write_text(json.dumps(record, sort_keys=True), encoding="utf-8")
            raise SystemExit(0)

raise SystemExit(1)
PY
    fi
    sleep 1
  done

  echo "error: no session.quality JSONL row found for $session_id" >&2
  exit 1
}

fetch_json() {
  # Fetch one agent JSON artifact with retry while the session is in grace.
  local url="$1"
  local output_path="$2"

  # Agent routes can lag briefly behind finalize, so retry before failing the fixture.
  for _ in $(seq 1 30); do
    if curl -fsS "$url" -o "$output_path"; then
      return 0
    fi
    sleep 1
  done

  echo "error: JSON artifact was not available: $url" >&2
  exit 1
}

live_role_label_count() {
  # Count live rows that have settled Doctor/Patient labels before scoring.
  # Use after replay stops so the live score reflects the transcript the user sees.
  local history_path="$1"

  "$PYTHON_BIN" - "$history_path" <<'PY'
import json
import sys
from pathlib import Path

history = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
segments = history.get("segments", [])
visible_role_count = 0

# Each row is one visible transcript line the clinician would review after replay stops.
for segment in segments:
    # Only visible consultation roles count; unknown rows stay unresolved in the user's transcript.
    if segment.get("role") in {"DOCTOR", "PATIENT"}:
        visible_role_count += 1

print(visible_role_count)
PY
}

wait_for_live_role_labels() {
  # Fetch live history until role inference has updated the transcript the user would review.
  # This avoids scoring the pre-label race that appears just after a demo replay stops.
  local session_id="$1"
  local history_path="$2"
  local labeled_row_count

  # Each second gives the role worker one chance to publish settled visible labels.
  for elapsed_second in $(seq 0 "$ROLE_SETTLE_SECONDS"); do
    fetch_json "$AGENT_HTTP_URL/session/$session_id/history" "$history_path"
    labeled_row_count="$(live_role_label_count "$history_path")"

    # Any Doctor/Patient label proves the saved live artifact has role timing applied.
    if [[ "$labeled_row_count" != "0" ]]; then
      return 0
    fi

    # The wait budget is spent, so keep the unresolved artifact for honest diagnosis.
    if [[ "$elapsed_second" -lt "$ROLE_SETTLE_SECONDS" ]]; then
      sleep 1
    fi
  done

  echo "warn: live role labels did not settle for $session_id before scoring" >&2
}

cutoff_seconds_for_history() {
  # Choose the scoring cutoff from visible rows or the requested fixture cap.
  local history_path="$1"
  local wav_path="$2"

  "$PYTHON_BIN" - "$history_path" "$wav_path" "$SECONDS_LIMIT" <<'PY'
import json
import sys
import wave

history_path, wav_path, seconds_limit = sys.argv[1:4]
history = json.load(open(history_path, encoding="utf-8"))
segments = history.get("segments", [])

# Visible rows define the exact audio span the user can review.
if segments:
    print(max(float(segment.get("end", 0.0) or 0.0) for segment in segments))
    raise SystemExit(0)

# A requested cap still gives empty transcripts a fair cutoff.
if seconds_limit:
    print(float(seconds_limit))
    raise SystemExit(0)

with wave.open(wav_path, "rb") as wav_file:
    print(wav_file.getnframes() / wav_file.getframerate())
PY
}

write_correction_request() {
  # Build the JSON body the browser sends before summary generation.
  local history_path="$1"
  local request_path="$2"

  "$PYTHON_BIN" - "$history_path" "$request_path" <<'PY'
import json
import sys
from pathlib import Path

history_path = Path(sys.argv[1])
request_path = Path(sys.argv[2])
history = json.loads(history_path.read_text(encoding="utf-8"))
request_path.write_text(
    json.dumps({"force": True, "segments": history.get("segments", [])}),
    encoding="utf-8",
)
PY
}

request_correction() {
  # Run post-stop correction while retained audio is still in memory.
  local session_id="$1"
  local request_path="$2"
  local response_path="$3"

  curl -fsS \
    -H 'Content-Type: application/json' \
    --data-binary "@$request_path" \
    "$AGENT_HTTP_URL/session/$session_id/correction" \
    -o "$response_path"

  "$PYTHON_BIN" - "$response_path" <<'PY'
import json
import sys
from pathlib import Path

payload = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))

# Unavailable correction means the fixture cannot prove the corrected summary lane.
if payload.get("status") != "ready":
    raise SystemExit(f"correction unavailable: {payload}")
PY
}

assert_no_websocket_errors() {
  # Fail the fixture if NeMo logged a WebSocket error for this session.
  local session_id="$1"

  local error_count
  error_count="$(
    docker compose logs nemo-agent --since "$RUN_STARTED_AT" 2>/dev/null \
      | grep "websocket.error" \
      | grep -c "$session_id" || true
  )"

  # Server-side WebSocket errors make the user's visible transcript untrustworthy.
  if [[ "$error_count" != "0" ]]; then
    echo "error: websocket.error found for $session_id" >&2
    docker compose logs nemo-agent --since "$RUN_STARTED_AT" 2>/dev/null \
      | grep "$session_id" \
      | grep "websocket.error" >&2 || true
    exit 1
  fi
}

score_artifact() {
  # Score a live or corrected transcript artifact against TextGrid truth.
  local artifact_path="$1"
  local cutoff_seconds="$2"
  local doctor_grid="$3"
  local patient_grid="$4"
  local score_path="$5"

  "$PYTHON_BIN" scripts/transcript-quality.py \
    "$artifact_path" \
    "$cutoff_seconds" \
    "$doctor_grid" \
    "$patient_grid" \
    > "$score_path"
}

append_report_row() {
  # Print one compact live-vs-corrected comparison row.
  local fixture_name="$1"
  local live_score_path="$2"
  local corrected_score_path="$3"
  local corrected_response_path="$4"
  local fixture_run_dir="$5"

  "$PYTHON_BIN" - "$fixture_name" "$live_score_path" "$corrected_score_path" "$corrected_response_path" "$fixture_run_dir" <<'PY'
import json
import re
import sys
from pathlib import Path

fixture_name = sys.argv[1]
live_score = Path(sys.argv[2]).read_text(encoding="utf-8")
corrected_score = Path(sys.argv[3]).read_text(encoding="utf-8")
correction = json.loads(Path(sys.argv[4]).read_text(encoding="utf-8"))
run_dir = sys.argv[5]


def metric(text: str, pattern: str) -> str:
    """Return one metric value from transcript-quality output."""
    match = re.search(pattern, text)

    # Missing metrics mean the scorer output changed and this report is unsafe.
    if match is None:
        raise SystemExit(f"missing metric: {pattern}")

    return match.group(1)


def pct(text: str, label: str) -> str:
    """Return the percentage part of one scorer line."""
    return metric(text, rf"{re.escape(label)}:\s+(n/a|[\d.]+%)")


def seam(text: str) -> str:
    """Return the seam re-read count shown by the scorer."""
    return metric(text, r"seam re-read count:\s+(\d+)")


row = [
    fixture_name[:44],
    pct(live_score, "strict attribution (non-overlap)"),
    pct(corrected_score, "strict attribution (non-overlap)"),
    pct(live_score, "word error rate (non-overlap)"),
    pct(corrected_score, "word error rate (non-overlap)"),
    pct(live_score, "incorrect-confident rate (non-overlap)"),
    pct(corrected_score, "incorrect-confident rate (non-overlap)"),
    seam(live_score),
    seam(corrected_score),
    str(correction.get("word_count", "")),
    run_dir,
]
print("\t".join(row))
PY
}

run_fixture() {
  # Stream, correct, score, and report one fixture.
  local wav_path="$1"
  local fixture_name
  fixture_name="$(basename "${wav_path%.wav}")"
  local session_id
  session_id="$("$PYTHON_BIN" - <<'PY'
import uuid
print(uuid.uuid4())
PY
)"
  local fixture_run_dir="$RUN_DIR/$fixture_name"
  mkdir -p "$fixture_run_dir"

  local grids
  grids="$(fixture_textgrid_paths "$wav_path")"
  local doctor_grid="${grids%%$'\t'*}"
  local patient_grid="${grids##*$'\t'}"
  local history_path="$fixture_run_dir/live-history.json"
  local quality_path="$fixture_run_dir/quality.json"
  local corrected_path="$fixture_run_dir/corrected-transcript.json"
  local correction_request_path="$fixture_run_dir/correction-request.json"
  local correction_response_path="$fixture_run_dir/correction-response.json"
  local live_score_path="$fixture_run_dir/live-transcript-quality.txt"
  local corrected_score_path="$fixture_run_dir/corrected-transcript-quality.txt"

  printf 'corrected eval fixture=%s session_id=%s\n' "$fixture_name" "$session_id" >&2
  stream_fixture_to_websocket "$wav_path" "$session_id"
  wait_for_quality_record "$session_id" "$quality_path"
  wait_for_live_role_labels "$session_id" "$history_path"
  assert_no_websocket_errors "$session_id"
  write_correction_request "$history_path" "$correction_request_path"
  request_correction "$session_id" "$correction_request_path" "$correction_response_path"
  fetch_json "$AGENT_HTTP_URL/session/$session_id/corrected-transcript" "$corrected_path"

  local cutoff_seconds
  cutoff_seconds="$(cutoff_seconds_for_history "$history_path" "$wav_path")"
  score_artifact "$history_path" "$cutoff_seconds" "$doctor_grid" "$patient_grid" "$live_score_path"
  score_artifact "$corrected_path" "$cutoff_seconds" "$doctor_grid" "$patient_grid" "$corrected_score_path"

  REPORT_ROWS+=(
    "$(
      append_report_row \
        "$fixture_name" \
        "$live_score_path" \
        "$corrected_score_path" \
        "$correction_response_path" \
        "$fixture_run_dir"
    )"
  )
}

score_source_chips_for_run() {
  # Save the corrected source-chip QA reports beside this run's fixture artifacts.
  # Use after every fixture finished so the summary's Doctor/Patient chips carry
  # saved review evidence; a run with no corrected artifacts fails the eval here.
  "$PYTHON_BIN" scripts/corrected-source-chip-score.py "$RUN_DIR" \
    > "$SOURCE_CHIP_SCORE_TEXT_PATH"
  "$PYTHON_BIN" scripts/corrected-source-chip-score.py --json "$RUN_DIR" \
    > "$SOURCE_CHIP_SCORE_JSON_PATH"
}

resolve_fixtures
require_ready_agent
mkdir -p "$RUN_DIR"

# Each selected fixture becomes one saved live/corrected comparison.
for fixture_path in "${FIXTURE_PATHS[@]}"; do
  run_fixture "$fixture_path"
done

printf '\nCorrected transcript fixture eval\n'
printf '%-44s %8s %8s %8s %8s %8s %8s %4s %4s %5s %s\n' \
  'fixture' 'liveStr' 'corrStr' 'liveWER' 'corrWER' 'liveBad' 'corrBad' 'lSea' 'cSea' 'words' 'artifacts'
# The report keeps exact artifact paths so developers can inspect the rows.
for row in "${REPORT_ROWS[@]}"; do
  IFS=$'\t' read -r \
    fixture live_strict corrected_strict live_wer corrected_wer \
    live_bad corrected_bad live_seam corrected_seam word_count artifact_dir \
    <<<"$row"
  printf '%-44s %8s %8s %8s %8s %8s %8s %4s %4s %5s %s\n' \
    "$fixture" "$live_strict" "$corrected_strict" "$live_wer" "$corrected_wer" \
    "$live_bad" "$corrected_bad" "$live_seam" "$corrected_seam" "$word_count" "$artifact_dir"
done

score_source_chips_for_run

# A missing summary line means the scorer report shape changed and this eval is unsafe.
if ! source_chip_summary="$(grep -m1 '^artifacts=' "$SOURCE_CHIP_SCORE_TEXT_PATH")"; then
  echo "error: source-chip summary line missing from $SOURCE_CHIP_SCORE_TEXT_PATH" >&2
  exit 1
fi
printf '\nsource-chip score: %s\n' "$source_chip_summary"
printf 'source-chip report: %s\n' "$SOURCE_CHIP_SCORE_TEXT_PATH"
printf 'source-chip json:   %s\n' "$SOURCE_CHIP_SCORE_JSON_PATH"

# Findings are QA evidence by default; strict callers opt into failing the eval,
# after the saved reports and summary above so the evidence is never lost.
if [[ "$SOURCE_CHIP_FAIL_ON_FINDINGS" == "1" ]] \
  && [[ "$source_chip_summary" =~ findings=([0-9]+) ]] \
  && [[ "${BASH_REMATCH[1]}" != "0" ]]; then
  echo "error: corrected source-chip findings present; see $SOURCE_CHIP_SCORE_TEXT_PATH" >&2
  exit 1
fi

printf '\nrun artifacts: %s\n' "$RUN_DIR"
