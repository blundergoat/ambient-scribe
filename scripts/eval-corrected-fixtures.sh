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
CORRECTION_FAILURE_LEDGER_PATH="$RUN_DIR/correction-failures.jsonl"
SOURCE_CHIP_FAIL_ON_FINDINGS="${CORRECTED_SOURCE_CHIP_FAIL_ON_FINDINGS:-0}"
# fast = blast chunks as quickly as the socket accepts (historical baselines);
# 1x   = real-time pacing; the browser and eval both default to 5-second chunks.
PACE_MODE="${EVAL_PACE:-fast}"
CHUNK_MS="${EVAL_CHUNK_MS:-5000}"
ROLE_SETTLE_SECONDS="${EVAL_ROLE_TIMELINE_SETTLE_SECONDS:-8}"
REQUIRE_STRUCTURED_LOGS="${EVAL_REQUIRE_STRUCTURED_LOGS:-0}"
REQUIRE_ALLOCATION_DIAGNOSTICS="${EVAL_REQUIRE_ALLOCATION_DIAGNOSTICS:-0}"
DEVELOPMENT_CORPUS_HELPER="${EVAL_DEVELOPMENT_CORPUS_HELPER:-scripts/development-corpus.py}"
SECONDS_LIMIT="${EVAL_FIXTURE_SECONDS:-}"
CORRECTION_UNAVAILABLE_EXIT_CODE=20
ALL_FIXTURES_REQUESTED=false
DEVELOPMENT_CORPUS_REQUESTED=false
CORPUS_MODE=false
FIXTURE_TOTAL=0
FIXTURE_OK=0
FIXTURE_FAILED=0
LAST_FIXTURE_OUTCOME="ready"
declare -a FIXTURE_QUERIES=()
declare -a FIXTURE_PATHS=()
declare -a REPORT_ROWS=()

usage() {
  cat <<'USAGE'
Usage:
  scripts/eval-corrected-fixtures.sh [--seconds N] <fixture-stem-or-wav> [...]
  scripts/eval-corrected-fixtures.sh [--seconds N] --all
  scripts/eval-corrected-fixtures.sh [--seconds N] --development-corpus

Examples:
  scripts/eval-corrected-fixtures.sh --seconds 60 consultation03-i-have-terrible-headache
  scripts/eval-corrected-fixtures.sh --seconds 60 \
    consultation03-i-have-terrible-headache consultation08-i-have-dry-itchy-skin

Environment:
  AGENT_HTTP_URL                 default http://localhost:48101
  AGENT_WS_URL                   default ws://localhost:48101
  EVAL_FIXTURE_SECONDS           optional cutoff to stream from each WAV
  CORRECTED_FIXTURE_RUN_DIR      optional artifact directory
  EVAL_CHUNK_MS                  PCM chunk size in milliseconds, default 5000
  EVAL_ROLE_TIMELINE_SETTLE_SECONDS
                                  seconds to wait for final role decisions; default 8
  EVAL_REQUIRE_STRUCTURED_LOGS     set 1 to require LOG_FORMAT=json before replay; default 0
  EVAL_REQUIRE_ALLOCATION_DIAGNOSTICS
                                  set 1 to require one PHI-safe allocation event; default 0
  EVAL_DEVELOPMENT_CORPUS_HELPER   test seam for the corpus manifest helper
  CORRECTED_SOURCE_CHIP_FAIL_ON_FINDINGS
                                  set 1 to fail the eval when source-chip findings exist; default 0
  EVAL_PACE                      fast (default, historical-baseline blast) or 1x
                                  (real-time pacing; default chunks match the browser's 5000ms)

Corpus behavior:
  --all or more than one resolved fixture records an unavailable correction,
  marks its corrected report columns FAILED, and continues. A single named
  fixture keeps the fail-fast correction gate.
  --development-corpus runs exactly the ten manifest-authorized fixtures in
  manifest order, validated by scripts/development-corpus.py before any audio
  is opened. It fails closed on manifest, order, or hash drift and cannot be
  combined with named fixtures or --all.
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
      ALL_FIXTURES_REQUESTED=true
      FIXTURE_QUERIES+=("__all__")
      shift
      ;;
    --development-corpus)
      DEVELOPMENT_CORPUS_REQUESTED=true
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

resolve_development_corpus_fixtures() {
  # The frozen ten-fixture manifest is the only authorization for this mode;
  # combining it with named fixtures or --all would reintroduce implicit
  # discovery beside the approved corpus.
  if [[ ${#FIXTURE_QUERIES[@]} -gt 0 ]]; then
    echo "error: --development-corpus cannot be combined with fixture names or --all" >&2
    exit 2
  fi

  # Authorization, order, and hash checks live in scripts/development-corpus.py;
  # any rejection there stops this runner before audio access.
  local corpus_json
  if ! corpus_json="$("$PYTHON_BIN" "$DEVELOPMENT_CORPUS_HELPER" --json)"; then
    echo "error: development corpus validation rejected the run; no fixture was opened" >&2
    exit 2
  fi

  local corpus_stems
  if ! corpus_stems="$("$PYTHON_BIN" - "$corpus_json" <<'PY'
import json
import sys

corpus = json.loads(sys.argv[1])

# Anything but a fully valid ten-fixture answer keeps the runner closed.
if corpus.get("status") != "valid" or corpus.get("fixture_count") != 10:
    raise SystemExit("development corpus response is not a valid ten-fixture set")

for stem in corpus["stems"]:
    print(stem)
PY
  )"; then
    echo "error: development corpus output failed validation; no fixture was opened" >&2
    exit 2
  fi

  # Approved paths are the manifest's exact locations, kept in manifest order;
  # no find-based discovery runs in this mode.
  local corpus_stem
  while IFS= read -r corpus_stem; do
    FIXTURE_PATHS+=("tests/fixtures/audio/${corpus_stem}.wav")
  done <<<"$corpus_stems"
}

select_corpus_mode() {
  # Whole-corpus and multi-fixture runs preserve later evidence after one unavailable correction.
  if [[ "$ALL_FIXTURES_REQUESTED" == "true" || ${#FIXTURE_PATHS[@]} -gt 1 ]]; then
    CORPUS_MODE=true
  fi
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

require_structured_agent_logs() {
  # Span-faithful-grade role diagnostics must fail before streaming if JSON events are unavailable.
  if [[ "$REQUIRE_STRUCTURED_LOGS" != "1" \
    && "$REQUIRE_ALLOCATION_DIAGNOSTICS" != "1" ]]; then
    return 0
  fi

  local log_format
  log_format="$(
    docker compose exec -T nemo-agent sh -c 'printf "%s" "$LOG_FORMAT"' 2>/dev/null || true
  )"
  if [[ "$log_format" != "json" ]]; then
    echo "error: required diagnostics need nemo-agent LOG_FORMAT=json" >&2
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

  "$PYTHON_BIN" - "$wav_path" "$SECONDS_LIMIT" "$AGENT_WS_URL" "$session_id" "$CHUNK_MS" "$PACE_MODE" <<'PY'
import asyncio
import sys
import time
import wave

import websockets


async def main() -> None:
    """Send the selected fixture as the same PCM chunks the browser sends."""
    wav_path, seconds_limit, agent_ws_url, session_id, chunk_ms, pace_mode = sys.argv[1:7]
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

        stream_started = time.monotonic()
        async with websockets.connect(
            uri,
            additional_headers={"x-correlation-id": f"eval-corrected-{session_id}"},
        ) as websocket:
            # Each chunk advances the server's live transcript exactly once.
            while sent_frames < frame_limit:
                # Real-time pacing holds each chunk until its audio-clock moment,
                # so the engine sees the cadence a clinician's replay produces.
                if pace_mode == "1x":
                    audio_clock = sent_frames / sample_rate
                    lag = audio_clock - (time.monotonic() - stream_started)
                    if lag > 0:
                        await asyncio.sleep(lag)

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

  # Transport failures are not safe corpus skips because no structured outcome exists.
  if ! curl -fsS \
      --max-time 120 \
      -H 'Content-Type: application/json' \
      --data-binary "@$request_path" \
      "$AGENT_HTTP_URL/session/$session_id/correction" \
      -o "$response_path"; then
    return 1
  fi

  "$PYTHON_BIN" - "$response_path" "$CORRECTION_UNAVAILABLE_EXIT_CODE" <<'PY'
import json
import re
import sys
from pathlib import Path

payload = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
unavailable_exit_code = int(sys.argv[2])

# Ready is the only status that may continue into corrected transcript scoring.
if payload.get("status") == "ready":
    raise SystemExit(0)

# Unavailable is a safe, explicit correction outcome that corpus mode can record.
if payload.get("status") == "unavailable":
    reason_category = str(payload.get("reason_category") or "unknown")
    # Never echo raw server text into a long-running corpus operator log.
    if re.fullmatch(r"[a-z0-9_]+", reason_category) is None:
        reason_category = "unknown"
    print(f"correction unavailable: reason_category={reason_category}", file=sys.stderr)
    raise SystemExit(unavailable_exit_code)

raise SystemExit("correction response has an unknown status")
PY
}

extract_allocation_diagnostics_from_logs() {
  # Persist one session-matched allocation event without retaining raw service logs.
  local session_id="$1"
  local output_path="$2"

  # File descriptor 3 preserves the pipeline while stdin carries this inline program.
  "$PYTHON_BIN" - "$session_id" "$output_path" 3<&0 <<'PY'
import json
import os
import re
import sys
from pathlib import Path

session_id = sys.argv[1]
output_path = Path(sys.argv[2])
matches = []

with os.fdopen(3, encoding="utf-8", errors="replace") as log_stream:
    for line in log_stream:
        object_start = line.find("{")
        if object_start < 0:
            continue
        try:
            event = json.loads(line[object_start:])
        except json.JSONDecodeError:
            continue
        if (
            isinstance(event, dict)
            and event.get("event") == "correction.allocation_diagnostics"
            and event.get("session_id") == session_id
        ):
            matches.append(event)

if len(matches) != 1:
    raise SystemExit("expected exactly one session allocation diagnostic event")

allocation = matches[0].get("allocation")
if not isinstance(allocation, dict):
    raise SystemExit("allocation diagnostic event has no object payload")


def exact_keys(value, required, context, optional=frozenset()):
    """Reject unknown fields so transcript wording cannot enter the artifact."""
    if not isinstance(value, dict):
        raise SystemExit(f"{context} must be an object")
    keys = set(value)
    if not required.issubset(keys) or not keys.issubset(required | optional):
        raise SystemExit(f"{context} has an unsafe schema")


def integer(value, context, minimum=0):
    """Accept JSON integers but reject booleans and negative indices/counts."""
    if type(value) is not int or value < minimum:
        raise SystemExit(f"{context} must be an integer >= {minimum}")
    return value


exact_keys(
    allocation,
    {
        "schema_version",
        "allocation_mode",
        "corrected_asr_words",
        "allocated_asr_words",
        "retained_live_words",
        "final_display_words",
        "rows",
        "accounting",
        "chunk_provenance",
    },
    "allocation",
)
if allocation["schema_version"] != 1:
    raise SystemExit("unsupported allocation diagnostic schema")
if allocation["allocation_mode"] not in {
    "empty",
    "anchored",
    "global_proportional",
    "unscaffolded",
}:
    raise SystemExit("unknown allocation mode")

for count_name in (
    "corrected_asr_words",
    "allocated_asr_words",
    "retained_live_words",
    "final_display_words",
):
    integer(allocation[count_name], f"allocation.{count_name}")

rows = allocation["rows"]
if not isinstance(rows, list):
    raise SystemExit("allocation.rows must be a list")
output_cursor = 0
source_run_words = 0
for expected_row_index, row in enumerate(rows):
    exact_keys(
        row,
        {
            "row_index",
            "segment_id",
            "output_start_index",
            "output_end_index",
            "output_word_count",
            "anchor_outcome",
            "anchor_score_class",
            "anchor_score",
            "anchor_clamped",
            "source_runs",
        },
        "allocation row",
    )
    if row["row_index"] != expected_row_index:
        raise SystemExit("allocation row indices are not contiguous")
    if re.fullmatch(r"corrected-\d{4}", row["segment_id"]) is None:
        raise SystemExit("allocation segment ID is unsafe")
    row_start = integer(row["output_start_index"], "row output start")
    row_end = integer(row["output_end_index"], "row output end")
    row_count = integer(row["output_word_count"], "row output word count")
    if row_start != output_cursor or row_end != row_start + row_count:
        raise SystemExit("allocation row output accounting is inconsistent")
    if row["anchor_outcome"] not in {"matched", "missing", "not_observed"}:
        raise SystemExit("unknown anchor outcome")
    if row["anchor_score_class"] not in {
        "high",
        "accepted",
        "missing",
        "not_observed",
    }:
        raise SystemExit("unknown anchor score class")
    score = row["anchor_score"]
    if score is not None and (
        isinstance(score, bool)
        or not isinstance(score, (int, float))
        or not 0.0 <= score <= 1.0
    ):
        raise SystemExit("anchor score must be null or a bounded number")
    if type(row["anchor_clamped"]) is not bool:
        raise SystemExit("anchor clamped flag must be boolean")

    source_runs = row["source_runs"]
    if not isinstance(source_runs, list):
        raise SystemExit("allocation source runs must be a list")
    run_cursor = row_start
    for expected_run_index, source_run in enumerate(source_runs):
        exact_keys(
            source_run,
            {
                "source",
                "source_start_index",
                "source_end_index",
                "word_count",
                "source_run_index",
                "output_start_index",
                "output_end_index",
            },
            "allocation source run",
            optional={"source_row_index"},
        )
        if source_run["source"] not in {"corrected_asr", "retained_live"}:
            raise SystemExit("unknown allocation source")
        if source_run["source_run_index"] != expected_run_index:
            raise SystemExit("source run indices are not contiguous")
        source_start = integer(
            source_run["source_start_index"], "source run start"
        )
        source_end = integer(source_run["source_end_index"], "source run end")
        word_count = integer(source_run["word_count"], "source run word count")
        output_start = integer(
            source_run["output_start_index"], "source run output start"
        )
        output_end = integer(
            source_run["output_end_index"], "source run output end"
        )
        if (
            source_end != source_start + word_count
            or output_start != run_cursor
            or output_end != output_start + word_count
        ):
            raise SystemExit("source run accounting is inconsistent")
        if "source_row_index" in source_run:
            integer(source_run["source_row_index"], "source row index")
        run_cursor = output_end
        source_run_words += word_count
    if run_cursor != row_end:
        raise SystemExit("source runs do not cover their output row")
    output_cursor = row_end

accounting = allocation["accounting"]
exact_keys(
    accounting,
    {
        "source_run_words",
        "allocation_output_words",
        "output_word_coverage_complete",
        "final_output_matches_allocation",
        "duplicate_corrected_asr_source_indices",
        "unallocated_corrected_asr_source_indices",
    },
    "allocation accounting",
)
if integer(accounting["source_run_words"], "source run words") != source_run_words:
    raise SystemExit("source run total is inconsistent")
if (
    integer(accounting["allocation_output_words"], "allocation output words")
    != output_cursor
):
    raise SystemExit("allocation output total is inconsistent")
if accounting["output_word_coverage_complete"] is not True:
    raise SystemExit("allocation output coverage is incomplete")
if accounting["final_output_matches_allocation"] is not True:
    raise SystemExit("final output differs from allocation")
if output_cursor != allocation["final_display_words"]:
    raise SystemExit("final display total is inconsistent")
for list_name in (
    "duplicate_corrected_asr_source_indices",
    "unallocated_corrected_asr_source_indices",
):
    values = accounting[list_name]
    if not isinstance(values, list):
        raise SystemExit(f"{list_name} must be a list")
    for value in values:
        integer(value, list_name)

chunk_provenance = allocation["chunk_provenance"]
exact_keys(
    chunk_provenance,
    {"status", "ranges", "seam_indices"},
    "chunk provenance",
)
if chunk_provenance["status"] not in {"observed", "not_observed"}:
    raise SystemExit("unknown chunk provenance status")
if not isinstance(chunk_provenance["ranges"], list) or not isinstance(
    chunk_provenance["seam_indices"], list
):
    raise SystemExit("chunk provenance ranges must be lists")
for chunk_range in chunk_provenance["ranges"]:
    exact_keys(
        chunk_range,
        {"chunk_index", "start_index", "end_index"},
        "chunk range",
    )
    integer(chunk_range["chunk_index"], "chunk index", minimum=1)
    integer(chunk_range["start_index"], "chunk start")
    integer(chunk_range["end_index"], "chunk end", minimum=1)
for seam_index in chunk_provenance["seam_indices"]:
    integer(seam_index, "chunk seam")
if chunk_provenance["status"] == "not_observed" and (
    chunk_provenance["ranges"] or chunk_provenance["seam_indices"]
):
    raise SystemExit("unobserved chunk provenance must be empty")

artifact = {
    "schema_version": 1,
    "session_id": session_id,
    "allocation": allocation,
}
output_path.write_text(
    json.dumps(artifact, indent=2, sort_keys=True) + "\n",
    encoding="utf-8",
)
PY
}

capture_allocation_diagnostics() {
  # Docker log delivery can trail the HTTP response briefly, so retry bounded reads.
  local session_id="$1"
  local output_path="$2"
  local attempt
  local maximum_attempts=1
  if [[ "$REQUIRE_ALLOCATION_DIAGNOSTICS" == "1" ]]; then
    maximum_attempts=5
  fi
  rm -f -- "$output_path"

  for ((attempt = 1; attempt <= maximum_attempts; attempt++)); do
    if docker compose logs --no-color nemo-agent --since "$RUN_STARTED_AT" \
        2>/dev/null \
      | extract_allocation_diagnostics_from_logs "$session_id" "$output_path" \
        2>/dev/null; then
      return 0
    fi
    if ((attempt < maximum_attempts)); then
      sleep 1
    fi
  done

  if [[ "$REQUIRE_ALLOCATION_DIAGNOSTICS" == "1" ]]; then
    echo "error: no valid allocation diagnostic captured for $session_id" >&2
    return 1
  fi
  echo "warn: no allocation diagnostic captured for $session_id" >&2
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

write_role_timeline() {
  # Persist the role decisions that produced the history labels scored below.
  local session_id="$1"
  local timeline_path="$2"
  local quality_path="$3"

  # Tail-batch role updates can land after disconnect and the quality snapshot.
  sleep "$ROLE_SETTLE_SECONDS"
  docker compose logs nemo-agent --since "$RUN_STARTED_AT" 2>/dev/null \
    | "$PYTHON_BIN" scripts/role-timeline.py --quality-json "$quality_path" "$session_id" \
    > "$timeline_path"

  # Console logs discard the structured fields needed to explain visible-role variance.
  if ! grep -Eq '"event": "(role_inference|role_mapping)\.' "$timeline_path"; then
    if [[ "$REQUIRE_STRUCTURED_LOGS" == "1" ]]; then
      echo "error: no structured role events captured for $session_id despite LOG_FORMAT=json" >&2
      exit 1
    fi
    echo "warn: no structured role events captured for $session_id; use EVAL_REQUIRE_STRUCTURED_LOGS=1 for diagnostic gates" >&2
  fi

  # A mismatch is evidence of post-snapshot decisions, not a reason to discard the timeline.
  if grep -q '"flip_counts_match": false' "$timeline_path"; then
    echo "warn: session.quality flip counters disagree with $timeline_path" >&2
  fi
}

score_artifact() {
  # Score a live or corrected transcript artifact against TextGrid truth.
  local artifact_path="$1"
  local cutoff_seconds="$2"
  local doctor_grid="$3"
  local patient_grid="$4"
  local score_path="$5"
  local quality_path="$6"
  local row_diagnostics_path="$7"

  "$PYTHON_BIN" scripts/transcript-quality.py \
    --quality-json "$quality_path" \
    --row-diagnostics-json "$row_diagnostics_path" \
    "$artifact_path" \
    "$cutoff_seconds" \
    "$doctor_grid" \
    "$patient_grid" \
    > "$score_path"
}

prepare_live_score_after_correction() {
  # Finish role evidence and score the live rows the clinician can still review.
  local session_id="$1"
  local timeline_path="$2"
  local quality_path="$3"
  local history_path="$4"
  local wav_path="$5"
  local doctor_grid="$6"
  local patient_grid="$7"
  local live_score_path="$8"
  local live_row_diagnostics_path="$9"

  write_role_timeline "$session_id" "$timeline_path" "$quality_path"
  # Re-fetch after the fixed settle window so scoring uses the timeline's final mapping.
  fetch_json "$AGENT_HTTP_URL/session/$session_id/history" "$history_path"

  local cutoff_seconds
  cutoff_seconds="$(cutoff_seconds_for_history "$history_path" "$wav_path")"
  score_artifact \
    "$history_path" "$cutoff_seconds" "$doctor_grid" "$patient_grid" \
    "$live_score_path" "$quality_path" "$live_row_diagnostics_path"
  printf '%s\n' "$cutoff_seconds"
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
    "ready",
    run_dir,
]
print("\t".join(row))
PY
}

append_failed_report_row() {
  # Persist a PHI-safe unavailable correction and print its explicit FAILED report row.
  local fixture_name="$1"
  local session_id="$2"
  local live_score_path="$3"
  local corrected_response_path="$4"
  local fixture_run_dir="$5"

  "$PYTHON_BIN" - \
    "$fixture_name" \
    "$session_id" \
    "$live_score_path" \
    "$corrected_response_path" \
    "$fixture_run_dir" \
    "$CORRECTION_FAILURE_LEDGER_PATH" <<'PY'
import json
import re
import sys
from pathlib import Path

fixture_name = sys.argv[1]
session_id = sys.argv[2]
live_score = Path(sys.argv[3]).read_text(encoding="utf-8")
correction = json.loads(Path(sys.argv[4]).read_text(encoding="utf-8"))
fixture_run_dir = Path(sys.argv[5])
failure_ledger_path = Path(sys.argv[6])


def metric(text: str, pattern: str) -> str:
    """Return one live metric retained beside a failed corrected lane."""
    match = re.search(pattern, text)

    # Missing live metrics would make the corpus failure row silently incomplete.
    if match is None:
        raise SystemExit(f"missing metric: {pattern}")

    return match.group(1)


def pct(text: str, label: str) -> str:
    """Return the percentage part of one live scorer line."""
    return metric(text, rf"{re.escape(label)}:\s+(n/a|[\d.]+%)")


def positive_int_or_none(value: object) -> int | None:
    """Keep positive ordinals only; missing metadata stays null."""
    return value if isinstance(value, int) and value > 0 else None


# Only the API's explicit unavailable shape is safe to aggregate and continue.
if correction.get("status") != "unavailable":
    raise SystemExit("failure report requires correction status unavailable")

reason_category = str(correction.get("reason_category") or "unknown")
# A category is support metadata, never a place for raw CUDA or clinical text.
if re.fullmatch(r"[a-z0-9_]+", reason_category) is None:
    reason_category = "unknown"

failure = {
    "fixture": fixture_name,
    "session_id": session_id,
    "status": "unavailable",
    "reason_category": reason_category,
    "attempts": max(0, int(correction.get("attempts", 0) or 0)),
    "retried": bool(correction.get("retried", False)),
    "failed_chunk_index": positive_int_or_none(
        correction.get("failed_chunk_index")
    ),
    "chunk_count_planned": positive_int_or_none(
        correction.get("chunk_count_planned")
    ),
}
fixture_run_dir.joinpath("correction-failure.json").write_text(
    json.dumps(failure, indent=2, sort_keys=True) + "\n",
    encoding="utf-8",
)
with failure_ledger_path.open("a", encoding="utf-8") as failure_ledger:
    failure_ledger.write(json.dumps(failure, sort_keys=True) + "\n")

row = [
    fixture_name[:44],
    pct(live_score, "strict attribution (non-overlap)"),
    "FAILED",
    pct(live_score, "word error rate (non-overlap)"),
    "FAILED",
    pct(live_score, "incorrect-confident rate (non-overlap)"),
    "FAILED",
    metric(live_score, r"seam re-read count:\s+(\d+)"),
    "-",
    "-",
    f"FAILED:{reason_category}",
    str(fixture_run_dir),
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
  local timeline_path="$fixture_run_dir/role-timeline.jsonl"
  local live_row_diagnostics_path="$fixture_run_dir/live-row-diagnostics.json"
  local corrected_row_diagnostics_path="$fixture_run_dir/corrected-row-diagnostics.json"
  local allocation_diagnostics_path="$fixture_run_dir/allocation-diagnostics.json"

  LAST_FIXTURE_OUTCOME="ready"

  printf 'corrected eval fixture=%s session_id=%s pace=%s chunk_ms=%s\n' \
    "$fixture_name" "$session_id" "$PACE_MODE" "$CHUNK_MS" >&2
  stream_fixture_to_websocket "$wav_path" "$session_id"
  wait_for_quality_record "$session_id" "$quality_path"
  wait_for_live_role_labels "$session_id" "$history_path"
  assert_no_websocket_errors "$session_id"
  write_correction_request "$history_path" "$correction_request_path"
  local correction_exit_code=0
  request_correction \
    "$session_id" "$correction_request_path" "$correction_response_path" \
    || correction_exit_code=$?

  # Only an explicit unavailable response is a recordable corpus outcome.
  if [[ "$correction_exit_code" == "$CORRECTION_UNAVAILABLE_EXIT_CODE" ]]; then
    local failed_cutoff_seconds
    failed_cutoff_seconds="$(
      prepare_live_score_after_correction \
        "$session_id" \
        "$timeline_path" \
        "$quality_path" \
        "$history_path" \
        "$wav_path" \
        "$doctor_grid" \
        "$patient_grid" \
        "$live_score_path" \
        "$live_row_diagnostics_path"
    )"
    # Reading the value proves the live score finished before the FAILED row is saved.
    if [[ -z "$failed_cutoff_seconds" ]]; then
      echo "error: live cutoff missing for unavailable correction $session_id" >&2
      return 1
    fi
    REPORT_ROWS+=(
      "$(
        append_failed_report_row \
          "$fixture_name" \
          "$session_id" \
          "$live_score_path" \
          "$correction_response_path" \
          "$fixture_run_dir"
      )"
    )
    LAST_FIXTURE_OUTCOME="correction_unavailable"
    return 0
  fi
  # Transport or malformed-response failures remain hard errors in every mode.
  if [[ "$correction_exit_code" != "0" ]]; then
    return "$correction_exit_code"
  fi

  capture_allocation_diagnostics "$session_id" "$allocation_diagnostics_path"
  fetch_json "$AGENT_HTTP_URL/session/$session_id/corrected-transcript" "$corrected_path"

  local cutoff_seconds
  cutoff_seconds="$(
    prepare_live_score_after_correction \
      "$session_id" \
      "$timeline_path" \
      "$quality_path" \
      "$history_path" \
      "$wav_path" \
      "$doctor_grid" \
      "$patient_grid" \
      "$live_score_path" \
      "$live_row_diagnostics_path"
  )"
  score_artifact \
    "$corrected_path" "$cutoff_seconds" "$doctor_grid" "$patient_grid" \
    "$corrected_score_path" "$quality_path" "$corrected_row_diagnostics_path"

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

reset_fixture_counts() {
  # Reset corpus counters before a developer run or the GPU-free control smoke.
  FIXTURE_TOTAL=0
  FIXTURE_OK=0
  FIXTURE_FAILED=0
  LAST_FIXTURE_OUTCOME="ready"
}

run_selected_fixtures() {
  # Visit every selected fixture unless a non-correction error makes evidence unsafe.
  local fixture_path
  for fixture_path in "${FIXTURE_PATHS[@]}"; do
    FIXTURE_TOTAL=$((FIXTURE_TOTAL + 1))
    LAST_FIXTURE_OUTCOME="ready"
    run_fixture "$fixture_path"
    # An unavailable correction is complete evidence in corpus mode, not a lost run.
    if [[ "$LAST_FIXTURE_OUTCOME" == "correction_unavailable" ]]; then
      FIXTURE_FAILED=$((FIXTURE_FAILED + 1))
      # A named fixture remains a hard quality gate exactly as before.
      if [[ "$CORPUS_MODE" != "true" ]]; then
        echo "error: correction unavailable for single-fixture gate" >&2
        return 1
      fi
      continue
    fi

    FIXTURE_OK=$((FIXTURE_OK + 1))
  done
}

fixture_summary_line() {
  # Give detached wrappers one stable terminal count after the last fixture.
  printf 'fixtures=%s ok=%s failed=%s\n' \
    "$FIXTURE_TOTAL" "$FIXTURE_OK" "$FIXTURE_FAILED"
}

print_fixture_report() {
  # Render saved rows, keeping unavailable corrected lanes visibly marked FAILED.
  printf '\nCorrected transcript fixture eval\n'
  printf '%-44s %8s %8s %8s %8s %8s %8s %4s %4s %5s %-24s %s\n' \
    'fixture' 'liveStr' 'corrStr' 'liveWER' 'corrWER' 'liveBad' 'corrBad' \
    'lSea' 'cSea' 'words' 'outcome' 'artifacts'
  # Exact artifact paths let developers inspect every healthy or failed row.
  local row
  for row in "${REPORT_ROWS[@]}"; do
    local fixture live_strict corrected_strict live_wer corrected_wer
    local live_bad corrected_bad live_seam corrected_seam word_count outcome artifact_dir
    IFS=$'\t' read -r \
      fixture live_strict corrected_strict live_wer corrected_wer \
      live_bad corrected_bad live_seam corrected_seam word_count outcome artifact_dir \
      <<<"$row"
    printf '%-44s %8s %8s %8s %8s %8s %8s %4s %4s %5s %-24s %s\n' \
      "$fixture" "$live_strict" "$corrected_strict" "$live_wer" "$corrected_wer" \
      "$live_bad" "$corrected_bad" "$live_seam" "$corrected_seam" "$word_count" \
      "$outcome" "$artifact_dir"
  done
}

main() {
  # Run the developer-facing corrected fixture evaluation from intake to terminal sentinel.
  # The frozen development corpus bypasses find-based discovery entirely.
  if [[ "$DEVELOPMENT_CORPUS_REQUESTED" == "true" ]]; then
    resolve_development_corpus_fixtures
  else
    resolve_fixtures
  fi
  select_corpus_mode
  require_ready_agent
  require_structured_agent_logs
  mkdir -p "$RUN_DIR"
  reset_fixture_counts
  run_selected_fixtures
  print_fixture_report

  score_source_chips_for_run

  local source_chip_summary
  # A missing summary line means the scorer report shape changed and this eval is unsafe.
  if ! source_chip_summary="$(grep -m1 '^artifacts=' "$SOURCE_CHIP_SCORE_TEXT_PATH")"; then
    echo "error: source-chip summary line missing from $SOURCE_CHIP_SCORE_TEXT_PATH" >&2
    return 1
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
    return 1
  fi

  printf '\nrun artifacts: %s\n' "$RUN_DIR"
  fixture_summary_line
}

# Sourced smokes may exercise corpus control without starting a real GPU replay.
if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  main "$@"
fi
