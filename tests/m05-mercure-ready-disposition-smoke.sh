#!/bin/bash
# Prove the M05 Mercure gate fails before campaign state, sentinel, or replay.
#
# Usage:
#   bash tests/m05-mercure-ready-disposition-smoke.sh
#
# Docker, network, GPU, and service commands are replaced with failing shims.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
RUNNER="$REPO_ROOT/var/quality/0.5.2-asr-accuracy/m05-baseline/mercure-ready-disposition-campaign-run-once.sh"
SMOKE_ROOT="$(mktemp -d)"
FAKE_BIN="$SMOKE_ROOT/fake-bin"
FAKE_COMMAND_LOG="$SMOKE_ROOT/fake-command.log"
NEGATIVE_CASES=(
    stopped_mercure
    identity_drift
    dns_failure
    publish_rejection
    malformed_receipt
    receipt_tamper
    hash_drift
)

cleanup() {
    rm -rf -- "$SMOKE_ROOT"
}
trap cleanup EXIT INT TERM

mkdir "$FAKE_BIN"
for command_name in docker curl nvidia-smi tmux; do
    # shellcheck disable=SC2016 # The generated shim expands these at execution.
    printf '%s\n' \
        '#!/bin/bash' \
        'printf "%s\n" "$(basename "$0")" >> "$FAKE_COMMAND_LOG"' \
        'exit 99' \
        > "$FAKE_BIN/$command_name"
    chmod +x "$FAKE_BIN/$command_name"
done
export FAKE_COMMAND_LOG
export PATH="$FAKE_BIN:$PATH"

for proof_case in "${NEGATIVE_CASES[@]}"; do
    proof_root="$SMOKE_ROOT/$proof_case"
    set +e
    M05_MERCURE_PROOF_STATE_ROOT="$proof_root" \
        bash "$RUNNER" --cpu-proof-case "$proof_case" \
        > "$SMOKE_ROOT/$proof_case.stdout" \
        2> "$SMOKE_ROOT/$proof_case.stderr"
    proof_status=$?
    set -e

    if [[ "$proof_status" -eq 0 ]]; then
        echo "error: $proof_case unexpectedly passed" >&2
        exit 1
    fi
    if [[ -e "$proof_root/campaign" ]] \
        || [[ -e "$proof_root/campaign/attempt-spent.txt" ]] \
        || [[ -e "$proof_root/campaign/eval-called.txt" ]]; then
        echo "error: $proof_case crossed the pre-sentinel boundary" >&2
        exit 1
    fi
    if ! rg -q '^stage=before_readiness$' "$proof_root/events.log"; then
        echo "error: $proof_case lacks a readiness boundary receipt" >&2
        exit 1
    fi
done

success_root="$SMOKE_ROOT/success"
M05_MERCURE_PROOF_STATE_ROOT="$success_root" \
    bash "$RUNNER" --cpu-proof-case success \
    > "$SMOKE_ROOT/success.stdout" \
    2> "$SMOKE_ROOT/success.stderr"
test -s "$success_root/campaign/attempt-spent.txt"
test -s "$success_root/campaign/eval-called.txt"

readiness_line="$(rg -n '^stage=validate_readiness$' \
    "$success_root/events.log" | cut -d: -f1)"
sentinel_line="$(rg -n '^stage=create_sentinel$' \
    "$success_root/events.log" | cut -d: -f1)"
eval_line="$(rg -n '^stage=eval$' "$success_root/events.log" | cut -d: -f1)"
if [[ "$readiness_line" -ge "$sentinel_line" ]] \
    || [[ "$sentinel_line" -ge "$eval_line" ]]; then
    echo "error: success proof did not preserve readiness-before-sentinel order" >&2
    exit 1
fi

wrapper_readiness_line="$(rg -n \
    '^    validate_mercure_readiness_receipt$' "$RUNNER" | head -1 | cut -d: -f1)"
wrapper_sentinel_line="$(rg -n \
    '^    mkdir "\$CAMPAIGN_ROOT"$' "$RUNNER" | head -1 | cut -d: -f1)"
wrapper_eval_line="$(rg -n \
    '^        \./scripts/eval-corrected-fixtures\.sh --development-corpus' \
    "$RUNNER" | head -1 | cut -d: -f1)"
if [[ "$wrapper_readiness_line" -ge "$wrapper_sentinel_line" ]] \
    || [[ "$wrapper_sentinel_line" -ge "$wrapper_eval_line" ]]; then
    echo "error: runtime wrapper source order crossed the sentinel boundary" >&2
    exit 1
fi

if [[ -s "$FAKE_COMMAND_LOG" ]]; then
    echo "error: CPU smoke invoked a forbidden runtime command" >&2
    exit 1
fi
if rg -q -i \
    'authorization|bearer|jwt|secret|aws_access|aws_session|container_ip' \
    "$SMOKE_ROOT"; then
    echo "error: CPU smoke persisted a sensitive-shaped field" >&2
    exit 1
fi

printf '%s\n' \
    'cases=8 negative_cases=7 success_cases=1' \
    'failures_stopped_before_campaign=true' \
    'readiness_precedes_sentinel_and_eval=true' \
    'forbidden_runtime_commands=0' \
    'sensitive_fields=0'
