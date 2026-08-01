#!/usr/bin/env bash
# Exercise corrected-fixture corpus control without streaming audio or loading NeMo.
#
# The smoke sources the eval functions, feeds a safe unavailable response into
# the report helper, and proves a three-fixture corpus visits the fixture after
# the failure. It also pins the single-fixture path to a non-zero result.

set -Eeuo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SMOKE_DIR="$(mktemp -d)"
trap 'rm -rf "$SMOKE_DIR"' EXIT

# Freeze the expected manifest answer so this smoke never depends on the active
# ignored WAV channel state restored by a prior real-fixture run.
CORPUS_HELPER_STUB="$SMOKE_DIR/development-corpus.py"
cat > "$CORPUS_HELPER_STUB" <<'PY'
import json

print(
    json.dumps(
        {
            "status": "valid",
            "fixture_count": 10,
            "stems": [
                "primock57-day1-consultation02-i-have-sore-red-skin",
                "primock57-day1-consultation03-i-have-terrible-headache",
                "primock57-day1-consultation06-hard-to-breathe",
                "primock57-day1-consultation07-i-have-a-cough-and-cold",
                "primock57-day1-consultation08-i-have-dry-itchy-skin",
                "primock57-day2-consultation03-i-cant-hear-very-well-and-my-face-is-a-bit-numb",
                "primock57-day2-consultation09-i-cant-move-my-left-arm",
                "primock57-day3-consultation01-lips-swelling-after-eating-a-sandwich",
                "primock57-day5-consultation03-im-feeling-very-anxious",
                "primock57-day5-consultation09-tired-all-the-time",
            ],
        }
    )
)
PY

export CORRECTED_FIXTURE_RUN_DIR="$SMOKE_DIR/run"
export EVAL_DEVELOPMENT_CORPUS_HELPER="$CORPUS_HELPER_STUB"
# Sourcing exposes control helpers without starting the developer-facing eval.
source "$REPO_ROOT/scripts/eval-corrected-fixtures.sh"
mkdir -p "$RUN_DIR"

# One prefixed JSON log line becomes a text-free, session-scoped artifact.
ALLOCATION_SESSION_ID="00000000-0000-4000-8000-000000000401"
ALLOCATION_PATH="$SMOKE_DIR/allocation-diagnostics.json"
cat <<JSON | extract_allocation_diagnostics_from_logs \
    "$ALLOCATION_SESSION_ID" "$ALLOCATION_PATH"
nemo-agent-1 | {"event":"correction.allocation_diagnostics","session_id":"$ALLOCATION_SESSION_ID","allocation":{"schema_version":1,"allocation_mode":"global_proportional","corrected_asr_words":2,"allocated_asr_words":2,"retained_live_words":0,"final_display_words":2,"rows":[{"row_index":0,"segment_id":"corrected-0001","output_start_index":0,"output_end_index":2,"output_word_count":2,"anchor_outcome":"not_observed","anchor_score_class":"not_observed","anchor_score":null,"anchor_clamped":false,"source_runs":[{"source":"corrected_asr","source_start_index":0,"source_end_index":2,"word_count":2,"source_run_index":0,"output_start_index":0,"output_end_index":2}]}],"accounting":{"source_run_words":2,"allocation_output_words":2,"output_word_coverage_complete":true,"final_output_matches_allocation":true,"duplicate_corrected_asr_source_indices":[],"unallocated_corrected_asr_source_indices":[]},"chunk_provenance":{"status":"observed","ranges":[{"chunk_index":1,"start_index":0,"end_index":1},{"chunk_index":2,"start_index":1,"end_index":2}],"seam_indices":[1]}}}
JSON

"$PYTHON_BIN" - "$ALLOCATION_PATH" "$ALLOCATION_SESSION_ID" <<'PY'
import json
import sys
from pathlib import Path

artifact = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
if artifact["session_id"] != sys.argv[2]:
    raise SystemExit("allocation artifact lost its session identity")
if artifact["allocation"]["chunk_provenance"]["seam_indices"] != [1]:
    raise SystemExit("allocation artifact lost observed chunk provenance")


def assert_text_free(value):
    if isinstance(value, dict):
        if "text" in value:
            raise SystemExit("allocation artifact retained transcript text")
        for nested in value.values():
            assert_text_free(nested)
    elif isinstance(value, list):
        for nested in value:
            assert_text_free(nested)


assert_text_free(artifact)
PY

if printf '%s\n' '{"event":"another.event"}' \
    | extract_allocation_diagnostics_from_logs \
        "$ALLOCATION_SESSION_ID" "$SMOKE_DIR/missing-allocation.json" \
        >/dev/null 2>&1; then
    echo "smoke failure: missing allocation event was accepted" >&2
    exit 1
fi

LIVE_SCORE_PATH="$SMOKE_DIR/live-score.txt"
CORRECTION_RESPONSE_PATH="$SMOKE_DIR/correction-response.json"
FIXTURE_RUN_DIR="$RUN_DIR/fixture-two"
mkdir -p "$FIXTURE_RUN_DIR"
printf '%s\n' \
    'strict attribution (non-overlap): 88.0%' \
    'word error rate (non-overlap): 22.0%' \
    'incorrect-confident rate (non-overlap): 12.0%' \
    'seam re-read count: 2' \
    > "$LIVE_SCORE_PATH"
printf '%s\n' \
    '{"status":"unavailable","reason_category":"empty_result","attempts":1,"retried":false,"failed_chunk_index":2,"chunk_count_planned":3}' \
    > "$CORRECTION_RESPONSE_PATH"

failed_row="$(
    append_failed_report_row \
        'fixture-two' \
        '00000000-0000-4000-8000-000000000402' \
        "$LIVE_SCORE_PATH" \
        "$CORRECTION_RESPONSE_PATH" \
        "$FIXTURE_RUN_DIR"
)"

# The failed row must retain live metrics while every corrected metric is explicit.
if [[ "$failed_row" != *$'fixture-two\t88.0%\tFAILED\t22.0%\tFAILED\t12.0%\tFAILED\t2\t-'* ]]; then
    echo "smoke failure: failed report row did not mark corrected columns FAILED" >&2
    exit 1
fi
if ! grep -q '"failed_chunk_index": 2' "$FIXTURE_RUN_DIR/correction-failure.json"; then
    echo "smoke failure: fixture failure metadata was not persisted" >&2
    exit 1
fi
if ! grep -q '"reason_category": "empty_result"' "$RUN_DIR/correction-failures.jsonl"; then
    echo "smoke failure: run-level failure ledger was not persisted" >&2
    exit 1
fi

declare -a visited_fixtures=()
run_fixture() {
    # Simulate one unavailable correction between two healthy corpus fixtures.
    local fixture_name
    fixture_name="$(basename "${1%.wav}")"
    visited_fixtures+=("$fixture_name")
    if [[ "$fixture_name" == "fixture-two" ]]; then
        REPORT_ROWS+=("$failed_row")
        LAST_FIXTURE_OUTCOME="correction_unavailable"
        return 0
    fi
    LAST_FIXTURE_OUTCOME="ready"
    REPORT_ROWS+=("$fixture_name"$'\t90.0%\t91.0%\t20.0%\t19.0%\t10.0%\t9.0%\t0\t0\t10\tready\t'"$RUN_DIR/$fixture_name")
}

FIXTURE_PATHS=("fixture-one.wav" "fixture-two.wav" "fixture-three.wav")
REPORT_ROWS=()
CORPUS_MODE=true
reset_fixture_counts
run_selected_fixtures

if [[ "${visited_fixtures[*]}" != "fixture-one fixture-two fixture-three" ]]; then
    echo "smoke failure: corpus stopped before the fixture after the failure" >&2
    exit 1
fi
if [[ "$(fixture_summary_line)" != "fixtures=3 ok=2 failed=1" ]]; then
    echo "smoke failure: summary sentinel counts are wrong" >&2
    exit 1
fi
rendered_report="$(print_fixture_report)"
# The terminal table must retain all fixtures and expose the failed corrected lane.
if [[ "$rendered_report" != *"fixture-one"* \
    || "$rendered_report" != *"fixture-two"* \
    || "$rendered_report" != *"fixture-three"* \
    || "$rendered_report" != *"FAILED:empty_result"* ]]; then
    echo "smoke failure: corpus report omitted or hid a fixture outcome" >&2
    exit 1
fi

# A named single-fixture gate keeps the historical non-zero failure behavior.
FIXTURE_PATHS=("fixture-two.wav")
REPORT_ROWS=()
visited_fixtures=()
CORPUS_MODE=false
reset_fixture_counts
if run_selected_fixtures; then
    echo "smoke failure: single-fixture correction failure exited zero" >&2
    exit 1
fi

# --- Development-corpus selection stays fail-closed and manifest-ordered ---
FIXTURE_QUERIES=()
FIXTURE_PATHS=()
resolve_development_corpus_fixtures
expected_corpus_paths=(
    "tests/fixtures/audio/primock57-day1-consultation02-i-have-sore-red-skin.wav"
    "tests/fixtures/audio/primock57-day1-consultation03-i-have-terrible-headache.wav"
    "tests/fixtures/audio/primock57-day1-consultation06-hard-to-breathe.wav"
    "tests/fixtures/audio/primock57-day1-consultation07-i-have-a-cough-and-cold.wav"
    "tests/fixtures/audio/primock57-day1-consultation08-i-have-dry-itchy-skin.wav"
    "tests/fixtures/audio/primock57-day2-consultation03-i-cant-hear-very-well-and-my-face-is-a-bit-numb.wav"
    "tests/fixtures/audio/primock57-day2-consultation09-i-cant-move-my-left-arm.wav"
    "tests/fixtures/audio/primock57-day3-consultation01-lips-swelling-after-eating-a-sandwich.wav"
    "tests/fixtures/audio/primock57-day5-consultation03-im-feeling-very-anxious.wav"
    "tests/fixtures/audio/primock57-day5-consultation09-tired-all-the-time.wav"
)
if [[ "${FIXTURE_PATHS[*]}" != "${expected_corpus_paths[*]}" ]]; then
    echo "smoke failure: development corpus selection differs from manifest order" >&2
    printf 'got:  %s\n' "${FIXTURE_PATHS[@]}" >&2
    exit 1
fi

# Mixing the frozen corpus with any other selection fails closed.
FIXTURE_QUERIES=("__all__")
FIXTURE_PATHS=()
corpus_conflict_exit=0
(resolve_development_corpus_fixtures) >/dev/null 2>&1 || corpus_conflict_exit=$?
if [[ "$corpus_conflict_exit" != "2" ]]; then
    echo "smoke failure: mixed corpus selection did not fail closed (exit $corpus_conflict_exit)" >&2
    exit 1
fi

# A rejected corpus helper stops selection before any fixture path resolves.
cat > "$SMOKE_DIR/rejecting-corpus.py" <<'PY'
raise SystemExit("development corpus rejected: smoke stub")
PY
FIXTURE_QUERIES=()
FIXTURE_PATHS=()
real_development_corpus_helper="$DEVELOPMENT_CORPUS_HELPER"
DEVELOPMENT_CORPUS_HELPER="$SMOKE_DIR/rejecting-corpus.py"
corpus_reject_exit=0
(resolve_development_corpus_fixtures) >/dev/null 2>&1 || corpus_reject_exit=$?
DEVELOPMENT_CORPUS_HELPER="$real_development_corpus_helper"
if [[ "$corpus_reject_exit" != "2" ]]; then
    echo "smoke failure: rejected corpus helper did not fail closed (exit $corpus_reject_exit)" >&2
    exit 1
fi

printf 'eval-corrected-fixtures smoke passed: fixtures=3 ok=2 failed=1; single fixture failed hard; corpus selection fail-closed\n'
