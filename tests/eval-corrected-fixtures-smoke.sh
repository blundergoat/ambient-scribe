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

export CORRECTED_FIXTURE_RUN_DIR="$SMOKE_DIR/run"
# Sourcing exposes control helpers without starting the developer-facing eval.
source "$REPO_ROOT/scripts/eval-corrected-fixtures.sh"
mkdir -p "$RUN_DIR"

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

printf 'eval-corrected-fixtures smoke passed: fixtures=3 ok=2 failed=1; single fixture failed hard\n'
