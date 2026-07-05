#!/usr/bin/env bash
# Runs PriMock57 fixture audio through the live WebSocket transcription path.
# Use this when a transcription change needs numbers instead of manual browser
# replay: the script sends PCM chunks, pulls history, reads the quality record,
# runs transcript-quality.py, and appends a compact trend row.

set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

AGENT_HTTP_URL="${AGENT_HTTP_URL:-http://localhost:48101}"
AGENT_WS_URL="${AGENT_WS_URL:-ws://localhost:48101}"
FIXTURE_DIR="${FIXTURE_DIR:-tests/fixtures/audio}"
PYTHON_BIN="${PYTHON_BIN:-strands_agents/.venv/bin/python}"
QUALITY_RECORD_PATH="${SESSION_QUALITY_HOST_PATH:-strands_agents/var/quality/sessions.jsonl}"
TREND_FILE="${QUALITY_TREND_FILE:-var/quality/trend.jsonl}"
RUN_STARTED_AT="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
RUN_ID="$(date -u +%Y%m%dT%H%M%SZ)"
RUN_DIR="${QUALITY_RUN_DIR:-var/quality/runs/$RUN_ID}"
CHUNK_MS="${EVAL_CHUNK_MS:-5000}"
ROLE_TIMELINE_SETTLE_SECONDS="${EVAL_ROLE_TIMELINE_SETTLE_SECONDS:-1}"
SECONDS_LIMIT="${EVAL_FIXTURE_SECONDS:-}"
REPORT_ONLY=0
declare -a FIXTURE_QUERIES=()
declare -a FIXTURE_PATHS=()
declare -a REPORT_ROWS=()

usage() {
  cat <<'USAGE'
Usage:
  scripts/eval-fixtures.sh [--seconds N] <fixture-stem-or-wav> [...]
  scripts/eval-fixtures.sh [--seconds N] --all
  scripts/eval-fixtures.sh --report

Examples:
  scripts/eval-fixtures.sh --seconds 31 consultation02
  scripts/eval-fixtures.sh --all
  scripts/eval-fixtures.sh --report

Environment:
  AGENT_HTTP_URL              default http://localhost:48101
  AGENT_WS_URL                default ws://localhost:48101
  EVAL_FIXTURE_SECONDS        optional cutoff to stream from each WAV
  EVAL_CHUNK_MS               PCM chunk size in milliseconds, default 5000
  EVAL_ROLE_TIMELINE_SETTLE_SECONDS
                              seconds to wait for final role logs, default 1
  SESSION_QUALITY_HOST_PATH   default strands_agents/var/quality/sessions.jsonl
  QUALITY_TREND_FILE          default var/quality/trend.jsonl
USAGE
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --seconds)
      # A missing value means the evaluator cannot know how much audio to stream.
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
    --report)
      REPORT_ONLY=1
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

print_trend_report() {
  "$PYTHON_BIN" - "$TREND_FILE" <<'PY'
import json
import sys
from pathlib import Path

trend_path = Path(sys.argv[1])

# No trend file means the user has not run a fixture eval on this checkout yet.
if not trend_path.exists():
    print(f"No trend file found at {trend_path}")
    raise SystemExit(0)

rows = []
with trend_path.open(encoding="utf-8") as trend_file:
    # Each non-empty JSONL row is one past fixture visit in the eval history.
    for line in trend_file:
        # Empty lines are ignored so manual edits do not break the report view.
        if line.strip():
            rows.append(json.loads(line))

# No rows means the trend file exists but there are no scoreable visits yet.
if not rows:
    print(f"No trend rows found at {trend_path}")
    raise SystemExit(0)


def _fmt_delta(value):
    """Format current-vs-previous movement for the compact operator report."""
    # Missing prior values are shown as n/a so old rows stay readable.
    if value is None:
        return "n/a"
    return f"{value:+.1f}" if abs(value) >= 1 else f"{value:+.2f}"


def _fmt_optional(value):
    """Format an optional percentage from trend rows across schema versions."""
    # Old trend rows did not include attribution, so the report keeps them visible.
    if value is None:
        return "n/a"
    return f"{value:.1f}"


def _fmt_int_optional(value):
    """Format an optional integer from trend rows across schema versions."""
    # Old trend rows did not include phantom-speaker counts.
    if value is None:
        return "n/a"
    return str(value)


latest_by_fixture = {}
# The newest row per fixture is what a developer wants when checking current health.
for row in rows:
    latest_by_fixture[row["fixture"]] = row

print(
    "fixture                                      cutoff  recall   dup   wer   frag  seam "
    "strict dStrict cover incWr  ceil  oracl attr  dAttr phant flips  conf  err"
)
# Each report row is one fixture's latest saved visit. Strict attribution is
# the M20 headline (uncertain rows count as incorrect); the free oracle stays
# diagnostic only.
for fixture, row in sorted(latest_by_fixture.items()):
    metrics = row["metrics"]
    quality = row["quality"]
    deltas = row.get("deltas", {})
    print(
        f"{fixture[:44]:44} "
        f"{row['cutoff_seconds']:6.1f} "
        f"{metrics['recall_percent']:6.1f} "
        f"{metrics['duplication_percent']:5.1f} "
        f"{_fmt_optional(metrics.get('word_error_rate_percent')):>5} "
        f"{_fmt_optional(metrics.get('fragment_rate_non_overlap_percent')):>5} "
        f"{_fmt_int_optional(metrics.get('seam_reread_count')):>5} "
        f"{_fmt_optional(metrics.get('strict_attribution_non_overlap_percent')):>6} "
        f"{_fmt_delta(deltas.get('strict_attribution_non_overlap_percent')):>7} "
        f"{_fmt_optional(metrics.get('uncertainty_coverage_non_overlap_percent')):>5} "
        f"{_fmt_optional(metrics.get('incorrect_confident_rate_non_overlap_percent')):>5} "
        f"{_fmt_optional(metrics.get('best_dyadic_mapping_accuracy_non_overlap_percent')):>5} "
        f"{_fmt_optional(metrics.get('speaker_oracle_accuracy_non_overlap_percent')):>6} "
        f"{_fmt_optional(metrics.get('attribution_accuracy_non_overlap_percent')):>5} "
        f"{_fmt_delta(deltas.get('attribution_accuracy_non_overlap_percent')):>6} "
        f"{_fmt_int_optional(metrics.get('phantom_speaker_count')):>5} "
        f"{quality.get('role_flips_accepted', 0):5} "
        f"{quality.get('final_confidence', 0.0):5.2f} "
        f"{quality.get('error_count', 0):3}"
    )
PY
}

resolve_fixtures() {
  # No explicit fixture means "all" so batch evals are one command.
  if [[ ${#FIXTURE_QUERIES[@]} -eq 0 ]]; then
    FIXTURE_QUERIES+=("__all__")
  fi

  for query in "${FIXTURE_QUERIES[@]}"; do
    # The --all sentinel expands to every local WAV fixture.
    if [[ "$query" == "__all__" ]]; then
      while IFS= read -r fixture_path; do
        FIXTURE_PATHS+=("$fixture_path")
      done < <(find "$FIXTURE_DIR" -maxdepth 1 -type f -name '*.wav' | sort)
      continue
    fi

    # Direct paths let a developer evaluate a newly generated fixture by filename.
    if [[ -f "$query" ]]; then
      FIXTURE_PATHS+=("$query")
      continue
    fi

    local -a matches=()
    while IFS= read -r fixture_path; do
      matches+=("$fixture_path")
    done < <(find "$FIXTURE_DIR" -maxdepth 1 -type f -name "*${query}*.wav" | sort)

    # Empty matches mean the operator likely mistyped the consultation number.
    if [[ ${#matches[@]} -eq 0 ]]; then
      echo "error: no WAV fixture matches '$query' under $FIXTURE_DIR" >&2
      exit 2
    fi

    # Ambiguous matches would make trend rows hard to compare later.
    if [[ ${#matches[@]} -gt 1 ]]; then
      printf "error: fixture query '%s' matched multiple files:\n" "$query" >&2
      printf '  %s\n' "${matches[@]}" >&2
      exit 2
    fi

    FIXTURE_PATHS+=("${matches[0]}")
  done
}

require_ready_agent() {
  # A missing venv means the runner cannot stream WebSocket audio.
  if [[ ! -x "$PYTHON_BIN" ]]; then
    echo "error: Python venv not found at $PYTHON_BIN" >&2
    exit 2
  fi

  # The agent must be healthy before the script starts a scoreable session.
  if ! curl -fsS "$AGENT_HTTP_URL/health" >/dev/null; then
    echo "error: agent health check failed at $AGENT_HTTP_URL/health" >&2
    exit 2
  fi
}

fixture_textgrid_paths() {
  local wav_path="$1"
  local stem="${wav_path%.wav}"
  local doctor_grid="${stem}.doctor.TextGrid"
  local patient_grid="${stem}.patient.TextGrid"

  # Both speaker channels are required for a fair PriMock reference.
  if [[ ! -f "$doctor_grid" || ! -f "$patient_grid" ]]; then
    echo "error: missing TextGrid pair for $wav_path" >&2
    exit 2
  fi

  printf '%s\t%s\n' "$doctor_grid" "$patient_grid"
}

stream_fixture_to_websocket() {
  local wav_path="$1"
  local session_id="$2"

  "$PYTHON_BIN" - "$wav_path" "$SECONDS_LIMIT" "$AGENT_WS_URL" "$session_id" "$CHUNK_MS" <<'PY'
import asyncio
import sys
import wave

import websockets

wav_path, seconds_limit, agent_ws_url, session_id, chunk_ms = sys.argv[1:6]


async def main() -> None:
    """Stream one WAV as browser PCM chunks to the live WebSocket."""
    with wave.open(wav_path, "rb") as wav_file:
        sample_rate = wav_file.getframerate()
        channels = wav_file.getnchannels()
        sample_width = wav_file.getsampwidth()
        total_frames = wav_file.getnframes()

        if sample_rate != 16000 or channels != 1 or sample_width != 2:
            raise SystemExit(
                "fixture must be 16 kHz mono signed 16-bit PCM for the browser PCM path"
            )

        frame_limit = total_frames
        if seconds_limit:
            frame_limit = min(total_frames, int(float(seconds_limit) * sample_rate))

        chunk_frames = max(1, int(sample_rate * (int(chunk_ms) / 1000)))
        sent_frames = 0
        uri = f"{agent_ws_url.rstrip('/')}/ws/transcribe/{session_id}"

        async with websockets.connect(
            uri,
            additional_headers={"x-correlation-id": f"eval-fixture-{session_id}"},
        ) as websocket:
            while sent_frames < frame_limit:
                frames_to_read = min(chunk_frames, frame_limit - sent_frames)
                audio_chunk = wav_file.readframes(frames_to_read)

                if audio_chunk == b"":
                    break

                await websocket.send(audio_chunk)
                sent_frames += frames_to_read


asyncio.run(main())
PY
}

wait_for_quality_record() {
  local session_id="$1"
  local out_path="$2"

  for _ in $(seq 1 90); do
    # The JSONL file appears only after the first finalized session.
    if [[ -f "$QUALITY_RECORD_PATH" ]]; then
      "$PYTHON_BIN" - "$QUALITY_RECORD_PATH" "$session_id" "$out_path" <<'PY' && return 0
import json
import sys
from pathlib import Path

record_path = Path(sys.argv[1])
session_id = sys.argv[2]
out_path = Path(sys.argv[3])

with record_path.open(encoding="utf-8") as records:
    for line in records:
        if not line.strip():
            continue
        record = json.loads(line)
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

fetch_history() {
  local session_id="$1"
  local history_path="$2"

  for _ in $(seq 1 30); do
    if curl -fsS "$AGENT_HTTP_URL/session/$session_id/history" -o "$history_path"; then
      return 0
    fi
    sleep 1
  done

  echo "error: history was not available for $session_id" >&2
  exit 1
}

cutoff_seconds_for_history() {
  local history_path="$1"
  local wav_path="$2"

  "$PYTHON_BIN" - "$history_path" "$wav_path" "$SECONDS_LIMIT" <<'PY'
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

assert_no_websocket_errors() {
  local session_id="$1"

  local error_count
  error_count="$(
    docker compose logs nemo-agent --since "$RUN_STARTED_AT" 2>/dev/null \
      | grep "websocket.error" \
      | grep -c "$session_id" || true
  )"

  # Any server-side WebSocket error means the quality numbers are not trustworthy.
  if [[ "$error_count" != "0" ]]; then
    echo "error: websocket.error found for $session_id" >&2
    docker compose logs nemo-agent --since "$RUN_STARTED_AT" 2>/dev/null \
      | grep "$session_id" \
      | grep "websocket.error" >&2 || true
    exit 1
  fi
}

write_role_timeline() {
  local session_id="$1"
  local timeline_path="$2"
  local quality_path="$3"

  # Final role logs can trail the quality row by a moment after the user stops replay.
  sleep "$ROLE_TIMELINE_SETTLE_SECONDS"

  # Docker logs are the source of truth for role decisions made during this browser visit;
  # the quality record is a disconnect-time snapshot, so the timeline cross-checks its counters.
  docker compose logs nemo-agent --since "$RUN_STARTED_AT" 2>/dev/null \
    | "$PYTHON_BIN" scripts/role-timeline.py --quality-json "$quality_path" "$session_id" \
    > "$timeline_path"

  # A mismatch usually means role decisions landed after the quality snapshot.
  if grep -q '"flip_counts_match": false' "$timeline_path"; then
    echo "warn: session.quality flip counters disagree with the role timeline for $session_id (see $timeline_path)" >&2
  fi
}

write_window_continuity() {
  local session_id="$1"
  local window_path="$2"

  # Per-window speaker maps let row diagnostics flag seam-crossing rows.
  docker compose logs nemo-agent --since "$RUN_STARTED_AT" 2>/dev/null \
    | "$PYTHON_BIN" scripts/window-continuity.py "$session_id" \
    > "$window_path"

  # Zero windows means the agent is not logging JSON continuity rows (LOG_FORMAT).
  if grep -q '"window_count": 0' "$window_path"; then
    echo "warn: no window-continuity rows captured for $session_id - is nemo-agent running with LOG_FORMAT=json?" >&2
  fi
}

append_trend_and_print_row() {
  local fixture_name="$1"
  local session_id="$2"
  local cutoff_seconds="$3"
  local history_path="$4"
  local quality_path="$5"
  local score_path="$6"
  local timeline_path="$7"
  local window_path="$8"
  local row_diagnostics_path="$9"

  "$PYTHON_BIN" - \
    "$TREND_FILE" \
    "$fixture_name" \
    "$session_id" \
    "$cutoff_seconds" \
    "$history_path" \
    "$quality_path" \
    "$score_path" \
    "$timeline_path" \
    "$window_path" \
    "$row_diagnostics_path" <<'PY'
import json
import re
import sys
from datetime import UTC, datetime
from pathlib import Path

trend_path = Path(sys.argv[1])
fixture = sys.argv[2]
session_id = sys.argv[3]
cutoff_seconds = float(sys.argv[4])
history_path = sys.argv[5]
quality_path = Path(sys.argv[6])
score_path = Path(sys.argv[7])
timeline_path = Path(sys.argv[8])
window_path = Path(sys.argv[9])
row_diagnostics_path = Path(sys.argv[10])
score_text = score_path.read_text(encoding="utf-8")
quality = json.loads(quality_path.read_text(encoding="utf-8"))


def required(pattern: str) -> re.Match[str]:
    """Return the first score match or fail the eval row clearly."""
    match = re.search(pattern, score_text)
    # A missing metric means the scorer output changed and the trend row is unsafe.
    if not match:
        raise SystemExit(f"could not parse transcript-quality output: {pattern}")
    return match


def optional_percent_with_counts(label: str) -> tuple[float | None, int, int]:
    """Parse an optional percent line plus its scored segment counts."""
    match = required(rf"{re.escape(label)}:\s+(n/a|[\d.]+%)\s+\((\d+)/(\d+)\)")
    raw_percent = match.group(1)

    # n/a means the session had no fair denominator for this user-visible metric.
    if raw_percent == "n/a":
        return None, int(match.group(2)), int(match.group(3))

    return float(raw_percent.removesuffix("%")), int(match.group(2)), int(match.group(3))


def optional_wer(label: str) -> dict[str, int | float | None]:
    """Parse one WER line with edit counts for trend storage."""
    match = required(
        rf"{re.escape(label)}:\s+(n/a|[\d.]+%)\s+"
        r"\(S/I/D=(\d+)/(\d+)/(\d+), ref=(\d+), hyp=(\d+)\)"
    )
    raw_percent = match.group(1)

    # n/a means the region had no reference words to score.
    percent = None if raw_percent == "n/a" else float(raw_percent.removesuffix("%"))
    return {
        "percent": percent,
        "substitutions": int(match.group(2)),
        "insertions": int(match.group(3)),
        "deletions": int(match.group(4)),
        "reference_words": int(match.group(5)),
        "hypothesis_words": int(match.group(6)),
    }


def optional_rate_with_counts(label: str) -> tuple[float | None, int, int]:
    """Parse a percent rate line plus numerator and denominator counts."""
    match = required(rf"{re.escape(label)}:\s+(n/a|[\d.]+%)\s+\((\d+)/(\d+)\)")
    raw_percent = match.group(1)

    # Empty denominators are displayed as n/a in the scorer output.
    if raw_percent == "n/a":
        return None, int(match.group(2)), int(match.group(3))

    return float(raw_percent.removesuffix("%")), int(match.group(2)), int(match.group(3))


attribution_percent, attribution_correct, attribution_total = optional_percent_with_counts(
    "speaker attribution accuracy"
)
clean_attribution_percent, clean_attribution_correct, clean_attribution_total = (
    optional_percent_with_counts("speaker attribution accuracy (non-overlap)")
)
speaker_oracle_percent, speaker_oracle_correct, speaker_oracle_total = (
    optional_percent_with_counts("speaker oracle accuracy")
)
clean_speaker_oracle_percent, clean_speaker_oracle_correct, clean_speaker_oracle_total = (
    optional_percent_with_counts("speaker oracle accuracy (non-overlap)")
)

gap_match = required(r"role mapping gap \(non-overlap\):\s+(n/a|[-+]?[\d.]+pp)")
gap_raw = gap_match.group(1)
role_mapping_gap = None if gap_raw == "n/a" else float(gap_raw.removesuffix("pp"))

strict_attribution_percent, strict_attribution_correct, strict_attribution_total = (
    optional_percent_with_counts("strict attribution (non-overlap)")
)
labeled_row_percent, labeled_row_correct, labeled_row_total = optional_percent_with_counts(
    "labeled-row accuracy (non-overlap)"
)
uncertainty_percent, uncertainty_rows, uncertainty_total = optional_percent_with_counts(
    "uncertainty coverage (non-overlap)"
)
incorrect_confident_percent, incorrect_confident_rows, incorrect_confident_total = (
    optional_percent_with_counts("incorrect-confident rate (non-overlap)")
)
best_dyadic_percent, best_dyadic_correct, best_dyadic_total = optional_percent_with_counts(
    "best dyadic mapping accuracy (non-overlap)"
)
headroom_match = required(r"role mapping headroom \(non-overlap\):\s+(n/a|[-+]?[\d.]+pp)")
headroom_raw = headroom_match.group(1)
role_mapping_headroom = (
    None if headroom_raw == "n/a" else float(headroom_raw.removesuffix("pp"))
)
overall_wer = optional_wer("word error rate")
clean_wer = optional_wer("word error rate (non-overlap)")
overlap_wer = optional_wer("word error rate (overlap)")
fragment_percent, fragment_count, segment_count = optional_rate_with_counts("fragment rate")
clean_fragment_percent, clean_fragment_count, clean_segment_count = optional_rate_with_counts(
    "fragment rate (non-overlap)"
)
segments_per_minute_match = required(
    r"segments per minute:\s+([\d.]+)\s+\(segments=(\d+)\)"
)

metrics = {
    "hypothesis_words": int(required(r"hypothesis words:\s+(\d+)").group(1)),
    "reference_words": int(required(r"reference words .*?:\s+(\d+)").group(1)),
    "duplication_percent": float(
        required(r"4-gram duplication in hypothesis:\s+([\d.]+)%").group(1)
    ),
    "recall_percent": float(
        required(r"reference vocabulary recall:\s+([\d.]+)%").group(1)
    ),
    "length_ratio": float(required(r"length ratio hyp/ref:\s+([\d.]+)").group(1)),
    "word_error_rate_percent": overall_wer["percent"],
    "word_error_substitutions": overall_wer["substitutions"],
    "word_error_insertions": overall_wer["insertions"],
    "word_error_deletions": overall_wer["deletions"],
    "word_error_reference_words": overall_wer["reference_words"],
    "word_error_hypothesis_words": overall_wer["hypothesis_words"],
    "word_error_rate_non_overlap_percent": clean_wer["percent"],
    "word_error_non_overlap_substitutions": clean_wer["substitutions"],
    "word_error_non_overlap_insertions": clean_wer["insertions"],
    "word_error_non_overlap_deletions": clean_wer["deletions"],
    "word_error_non_overlap_reference_words": clean_wer["reference_words"],
    "word_error_non_overlap_hypothesis_words": clean_wer["hypothesis_words"],
    "word_error_rate_overlap_percent": overlap_wer["percent"],
    "word_error_overlap_substitutions": overlap_wer["substitutions"],
    "word_error_overlap_insertions": overlap_wer["insertions"],
    "word_error_overlap_deletions": overlap_wer["deletions"],
    "word_error_overlap_reference_words": overlap_wer["reference_words"],
    "word_error_overlap_hypothesis_words": overlap_wer["hypothesis_words"],
    "segments_per_minute": float(segments_per_minute_match.group(1)),
    "segment_count": int(segments_per_minute_match.group(2)),
    "fragment_rate_percent": fragment_percent,
    "fragment_count": fragment_count,
    "fragment_segment_count": segment_count,
    "fragment_rate_non_overlap_percent": clean_fragment_percent,
    "fragment_non_overlap_count": clean_fragment_count,
    "fragment_non_overlap_segment_count": clean_segment_count,
    "seam_reread_count": int(required(r"seam re-read count:\s+(\d+)").group(1)),
    "attribution_accuracy_percent": attribution_percent,
    "attribution_correct_segments": attribution_correct,
    "attribution_scored_segments": attribution_total,
    "attribution_accuracy_non_overlap_percent": clean_attribution_percent,
    "attribution_non_overlap_correct_segments": clean_attribution_correct,
    "attribution_non_overlap_scored_segments": clean_attribution_total,
    "strict_attribution_non_overlap_percent": strict_attribution_percent,
    "strict_attribution_correct_rows": strict_attribution_correct,
    "strict_attribution_clean_rows": strict_attribution_total,
    "labeled_row_accuracy_non_overlap_percent": labeled_row_percent,
    "labeled_row_correct_rows": labeled_row_correct,
    "labeled_rows": labeled_row_total,
    "uncertainty_coverage_non_overlap_percent": uncertainty_percent,
    "uncertain_rows": uncertainty_rows,
    "incorrect_confident_rate_non_overlap_percent": incorrect_confident_percent,
    "incorrect_confident_rows": incorrect_confident_rows,
    "speaker_oracle_accuracy_percent": speaker_oracle_percent,
    "speaker_oracle_correct_segments": speaker_oracle_correct,
    "speaker_oracle_scored_segments": speaker_oracle_total,
    "speaker_oracle_accuracy_non_overlap_percent": clean_speaker_oracle_percent,
    "speaker_oracle_non_overlap_correct_segments": clean_speaker_oracle_correct,
    "speaker_oracle_non_overlap_scored_segments": clean_speaker_oracle_total,
    "role_mapping_gap_non_overlap_points": role_mapping_gap,
    "best_dyadic_mapping_accuracy_non_overlap_percent": best_dyadic_percent,
    "best_dyadic_mapping_correct_segments": best_dyadic_correct,
    "best_dyadic_mapping_scored_segments": best_dyadic_total,
    "role_mapping_headroom_non_overlap_points": role_mapping_headroom,
    "overlap_span_seconds": float(required(r"overlap-span seconds:\s+([\d.]+)").group(1)),
    "phantom_speaker_count": int(required(r"phantom speaker count:\s+(\d+)").group(1)),
}

previous = None
trend_path.parent.mkdir(parents=True, exist_ok=True)
# Existing trend rows let the report show movement from the last run of this fixture.
if trend_path.exists():
    with trend_path.open(encoding="utf-8") as trend_file:
        # Each non-empty line is one prior scoreable visit.
        for line in trend_file:
            # Blank lines are skipped so operators can inspect/edit the file safely.
            if not line.strip():
                continue
            row = json.loads(line)
            # Only the same fixture is a valid before/after comparison.
            if row.get("fixture") == fixture:
                previous = row

deltas = {}
# First runs have no previous row, so deltas stay empty and print as n/a.
if previous is not None:
    # Deltas are only calculated for numeric metrics present in both schema versions.
    for key in (
        "duplication_percent",
        "recall_percent",
        "length_ratio",
        "word_error_rate_percent",
        "fragment_rate_non_overlap_percent",
        "seam_reread_count",
        "attribution_accuracy_non_overlap_percent",
        "strict_attribution_non_overlap_percent",
        "incorrect_confident_rate_non_overlap_percent",
        "uncertainty_coverage_non_overlap_percent",
        "speaker_oracle_accuracy_non_overlap_percent",
        "role_mapping_gap_non_overlap_points",
        "phantom_speaker_count",
    ):
        previous_value = previous.get("metrics", {}).get(key)
        current_value = metrics.get(key)

        # Missing old attribution values are expected until the first M16 run lands.
        if isinstance(previous_value, (int, float)) and isinstance(
            current_value, (int, float)
        ):
            deltas[key] = current_value - previous_value

row = {
    "ts": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
    "fixture": fixture,
    "session_id": session_id,
    "cutoff_seconds": cutoff_seconds,
    "metrics": metrics,
    "quality": {
        "chunks": quality.get("chunks", 0),
        "emitted_segments": quality.get("emitted_segments", 0),
        "held_segments": quality.get("held_segments", 0),
        "phantom_speaker_merges": quality.get("phantom_speaker_merges", 0),
        "role_flips_accepted": quality.get("role_flips_accepted", 0),
        "role_flips_suppressed": quality.get("role_flips_suppressed", 0),
        "role_truncation_events": quality.get("role_truncation_events", 0),
        "final_confidence": quality.get("final_confidence", 0.0),
        "error_count": quality.get("error_count", 0),
    },
    "deltas": deltas,
    "paths": {
        "history": history_path,
        "quality": str(quality_path),
        "score": str(score_path),
        "role_timeline": str(timeline_path),
        "window_continuity": str(window_path),
        "row_diagnostics": str(row_diagnostics_path),
    },
}

with trend_path.open("a", encoding="utf-8") as trend_file:
    trend_file.write(json.dumps(row, sort_keys=True) + "\n")


def fmt_delta(value):
    """Format current-vs-previous changes for the compact table."""
    if value is None:
        return "n/a"
    return f"{value:+.1f}" if abs(value) >= 1 else f"{value:+.2f}"


def fmt_optional(value):
    """Format optional percentages so older schema rows stay readable."""
    if value is None:
        return "n/a"
    return f"{value:.1f}"


print(
    "\t".join(
        [
            fixture[:44],
            f"{cutoff_seconds:.1f}",
            f"{metrics['recall_percent']:.1f}",
            f"{metrics['duplication_percent']:.1f}",
            fmt_optional(metrics["word_error_rate_percent"]),
            fmt_optional(metrics["fragment_rate_non_overlap_percent"]),
            str(metrics["seam_reread_count"]),
            fmt_optional(metrics["strict_attribution_non_overlap_percent"]),
            fmt_delta(deltas.get("strict_attribution_non_overlap_percent")),
            fmt_optional(metrics["uncertainty_coverage_non_overlap_percent"]),
            fmt_optional(metrics["incorrect_confident_rate_non_overlap_percent"]),
            fmt_optional(metrics["best_dyadic_mapping_accuracy_non_overlap_percent"]),
            fmt_optional(metrics["speaker_oracle_accuracy_non_overlap_percent"]),
            fmt_optional(metrics["attribution_accuracy_non_overlap_percent"]),
            fmt_delta(deltas.get("attribution_accuracy_non_overlap_percent")),
            str(metrics["phantom_speaker_count"]),
            str(row["quality"]["role_flips_accepted"]),
            f"{row['quality']['final_confidence']:.2f}",
            str(row["quality"]["error_count"]),
        ]
    )
)
PY
}

run_fixture() {
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
  local history_path="$fixture_run_dir/history.json"
  local quality_path="$fixture_run_dir/quality.json"
  local score_path="$fixture_run_dir/transcript-quality.txt"
  local timeline_path="$fixture_run_dir/role-timeline.jsonl"
  local window_path="$fixture_run_dir/window-continuity.jsonl"
  local row_diagnostics_path="$fixture_run_dir/row-diagnostics.json"

  printf 'eval fixture=%s session_id=%s\n' "$fixture_name" "$session_id" >&2
  stream_fixture_to_websocket "$wav_path" "$session_id"
  wait_for_quality_record "$session_id" "$quality_path"
  # The role queue keeps applying tail-batch flips for several seconds after
  # disconnect; the timeline step sleeps for that settle window, so history is
  # fetched AFTER it to score the final labels the clinician actually sees.
  # Fetching earlier made attribution depend on a fetch-vs-flip race (M20).
  write_role_timeline "$session_id" "$timeline_path" "$quality_path"
  fetch_history "$session_id" "$history_path"
  assert_no_websocket_errors "$session_id"
  write_window_continuity "$session_id" "$window_path"

  local cutoff_seconds
  cutoff_seconds="$(cutoff_seconds_for_history "$history_path" "$wav_path")"
  "$PYTHON_BIN" scripts/transcript-quality.py \
    --quality-json "$quality_path" \
    --window-artifact "$window_path" \
    --row-diagnostics-json "$row_diagnostics_path" \
    "$history_path" \
    "$cutoff_seconds" \
    "$doctor_grid" \
    "$patient_grid" \
    > "$score_path"

  REPORT_ROWS+=(
    "$(
      append_trend_and_print_row \
        "$fixture_name" \
        "$session_id" \
        "$cutoff_seconds" \
        "$history_path" \
        "$quality_path" \
        "$score_path" \
        "$timeline_path" \
        "$window_path" \
        "$row_diagnostics_path"
    )"
  )
}

if [[ "$REPORT_ONLY" -eq 1 ]]; then
  print_trend_report
  exit 0
fi

resolve_fixtures
require_ready_agent
mkdir -p "$RUN_DIR" "$(dirname "$TREND_FILE")"

for fixture_path in "${FIXTURE_PATHS[@]}"; do
  run_fixture "$fixture_path"
done

printf '\nAmbient Scribe fixture eval\n'
printf '%s%s\n' \
  'fixture                                      cutoff  recall   dup   wer   frag  seam ' \
  'strict dStrict cover incWr  ceil  oracl attr  dAttr phant flips  conf  err'
for row in "${REPORT_ROWS[@]}"; do
  IFS=$'\t' read -r \
    fixture cutoff recall duplication wer fragment seam \
    strict delta_strict coverage incorrect_confident ceiling oracle \
    attribution delta_attribution phantoms flips confidence errors \
    <<<"$row"
  printf '%-44s %6s %6s %5s %5s %5s %5s %6s %7s %5s %5s %5s %6s %5s %6s %5s %5s %5s %3s\n' \
    "$fixture" "$cutoff" "$recall" "$duplication" "$wer" "$fragment" "$seam" \
    "$strict" "$delta_strict" "$coverage" "$incorrect_confident" "$ceiling" \
    "$oracle" "$attribution" "$delta_attribution" "$phantoms" "$flips" \
    "$confidence" "$errors"
done
printf '\ntrend: %s\nrun artifacts: %s\n' "$TREND_FILE" "$RUN_DIR"
