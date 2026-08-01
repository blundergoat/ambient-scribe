#!/bin/bash
# Prove the M05 successor approval entry and Mercure readiness boundaries.
#
# Usage:
#   bash tests/approval-entry-boundaries-smoke.sh
#
# Every runtime-facing command resolves to a failing shim. The valid synthetic
# default entry must stop at the first fake Docker boundary with status 97.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
M05_ROOT="$REPO_ROOT/var/quality/0.5.2-asr-accuracy/m05-baseline"
RUNNER="$M05_ROOT/mercure-approval-ready-disposition-campaign-run-once.sh"
CONTRACT="$M05_ROOT/mercure-approval-ready-disposition-campaign-contract.json"
READINESS_HELPER="$REPO_ROOT/scripts/probe-mercure-readiness.py"
SMOKE_ROOT="$(mktemp -d)"
FAKE_BIN="$SMOKE_ROOT/fake-bin"
FAKE_COMMAND_LOG="$SMOKE_ROOT/fake-command.log"
FAKE_DOCKER_STATUS=97
READINESS_NEGATIVE_CASES=(
    stopped_mercure
    identity_drift
    dns_failure
    publish_rejection
    malformed_receipt
    receipt_tamper
    hash_drift
)
ENTRY_NEGATIVE_CASES=(
    missing_record
    malformed_schema
    packet_hash_drift
    contract_hash_drift
    helper_hash_drift
    runner_hash_drift
    test_hash_drift
    stale_runtime_identity
    extra_campaign
    extra_readiness_publish
    extra_replay
    extra_correction
    extra_retry
    extra_service_action
    extra_nemo_recreate
    baseline_authorized
    m06_authorized
    commit_authorized
    push_authorized
    outer_root_mismatch
    proof_root_outside_tmp
)

cleanup() {
    rm -rf -- "$SMOKE_ROOT"
}
trap cleanup EXIT INT TERM

line_count() {
    local path="$1"
    if [[ -f "$path" ]]; then
        wc -l < "$path" | tr -d ' '
    else
        printf '0\n'
    fi
}

file_sha256() {
    sha256sum "$1" | awk '{print $1}'
}

rewrite_record() {
    local filter="$1"
    local record_path="$2"
    local temporary_path="$record_path.tmp"

    jq -S "$filter" "$record_path" > "$temporary_path"
    mv "$temporary_path" "$record_path"
}

create_valid_entry_fixture() {
    local proof_root="$1"
    local packet_path="$proof_root/runtime-packet.md"
    local record_path="$proof_root/runtime-approval.json"

    mkdir -p "$proof_root"
    printf '%s\n' 'synthetic M05 approval-ready runtime packet' > "$packet_path"
    jq -S -n \
        --arg approval_packet_sha256 "$(file_sha256 "$packet_path")" \
        --arg contract_sha256 "$(file_sha256 "$CONTRACT")" \
        --arg readiness_helper_sha256 "$(file_sha256 "$READINESS_HELPER")" \
        --arg runner_sha256 "$(file_sha256 "$RUNNER")" \
        --arg entry_smoke_sha256 "$(file_sha256 "$0")" \
        --arg mercure_container_id "$(printf 'a%.0s' {1..64})" \
        --arg mercure_image_id "sha256:$(printf 'b%.0s' {1..64})" \
        --arg nemo_image_id "sha256:$(printf 'c%.0s' {1..64})" \
        '{
            schema_version:
                "ambient-scribe-m05-mercure-approval-ready-disposition-approval/v1",
            decision: "approved",
            campaigns: 1,
            readiness_publishes: 1,
            replays: 10,
            http_correction_calls: 10,
            http_retries: 0,
            nemo_service_recreates: 2,
            mercure_service_actions: 0,
            runtime_mode_before: "console",
            runtime_mode_during: "json",
            runtime_mode_after: "console",
            reruns_allowed: false,
            additional_nemo_service_recreates_allowed: false,
            credentials_rotated_or_revoked: true,
            source_chip_gate: "complete_disposition",
            baseline_acceptance_authorized: false,
            m06_m11_authorized: false,
            commits_or_pushes_authorized: false,
            approval_packet_sha256: $approval_packet_sha256,
            contract_sha256: $contract_sha256,
            readiness_helper_sha256: $readiness_helper_sha256,
            runner_sha256: $runner_sha256,
            entry_smoke_sha256: $entry_smoke_sha256,
            runtime: {
                mercure_container_id: $mercure_container_id,
                mercure_image_id: $mercure_image_id,
                nemo_image_id: $nemo_image_id
            }
        }' > "$record_path"
}

mutate_entry_fixture() {
    local proof_case="$1"
    local proof_root="$2"
    local packet_path="$proof_root/runtime-packet.md"
    local record_path="$proof_root/runtime-approval.json"

    case "$proof_case" in
        valid)
            ;;
        missing_record)
            rm -f -- "$record_path"
            ;;
        malformed_schema)
            rewrite_record '.schema_version = "invalid"' "$record_path"
            ;;
        packet_hash_drift)
            printf '%s\n' 'drift' >> "$packet_path"
            ;;
        contract_hash_drift)
            rewrite_record '.contract_sha256 = ("0" * 64)' "$record_path"
            ;;
        helper_hash_drift)
            rewrite_record '.readiness_helper_sha256 = ("0" * 64)' "$record_path"
            ;;
        runner_hash_drift)
            rewrite_record '.runner_sha256 = ("0" * 64)' "$record_path"
            ;;
        test_hash_drift)
            rewrite_record '.entry_smoke_sha256 = ("0" * 64)' "$record_path"
            ;;
        stale_runtime_identity)
            rewrite_record '.runtime.mercure_container_id = "stale"' "$record_path"
            ;;
        extra_campaign)
            rewrite_record '.campaigns = 2' "$record_path"
            ;;
        extra_readiness_publish)
            rewrite_record '.readiness_publishes = 2' "$record_path"
            ;;
        extra_replay)
            rewrite_record '.replays = 11' "$record_path"
            ;;
        extra_correction)
            rewrite_record '.http_correction_calls = 11' "$record_path"
            ;;
        extra_retry)
            rewrite_record '.http_retries = 1' "$record_path"
            ;;
        extra_service_action)
            rewrite_record '.mercure_service_actions = 1' "$record_path"
            ;;
        extra_nemo_recreate)
            rewrite_record '.nemo_service_recreates = 3' "$record_path"
            ;;
        baseline_authorized)
            rewrite_record '.baseline_acceptance_authorized = true' "$record_path"
            ;;
        m06_authorized)
            rewrite_record '.m06_m11_authorized = true' "$record_path"
            ;;
        commit_authorized)
            rewrite_record '.commits_or_pushes_authorized = true' "$record_path"
            ;;
        push_authorized)
            rewrite_record '.commits_or_pushes_authorized = true' "$record_path"
            ;;
        outer_root_mismatch)
            ;;
        *)
            echo "error: unknown approval-entry proof case: $proof_case" >&2
            return 2
            ;;
    esac
}

assert_no_campaign_state() {
    local proof_root="$1"
    if [[ -e "$proof_root/campaign" ]] \
        || [[ -e "$proof_root/campaign/attempt-spent.txt" ]] \
        || [[ -e "$proof_root/campaign/eval-called.txt" ]]; then
        echo "error: approval-entry proof crossed the campaign boundary" >&2
        return 1
    fi
}

mkdir "$FAKE_BIN"
: > "$FAKE_COMMAND_LOG"
for command_name in docker curl nvidia-smi tmux; do
    # shellcheck disable=SC2016 # Generated shims expand these at execution.
    printf '%s\n' \
        '#!/bin/bash' \
        'printf "%s\n" "$(basename "$0")" >> "$FAKE_COMMAND_LOG"' \
        "exit $FAKE_DOCKER_STATUS" \
        > "$FAKE_BIN/$command_name"
    chmod +x "$FAKE_BIN/$command_name"
done
export FAKE_COMMAND_LOG
export PATH="$FAKE_BIN:$PATH"

for proof_case in "${READINESS_NEGATIVE_CASES[@]}"; do
    proof_root="$SMOKE_ROOT/readiness/$proof_case"
    set +e
    M05_APPROVAL_READY_PROOF_STATE_ROOT="$proof_root" \
        bash "$RUNNER" --cpu-readiness-proof-case "$proof_case" \
        > "$SMOKE_ROOT/readiness-$proof_case.stdout" \
        2> "$SMOKE_ROOT/readiness-$proof_case.stderr"
    proof_status=$?
    set -e

    if [[ "$proof_status" -eq 0 ]]; then
        echo "error: readiness case $proof_case unexpectedly passed" >&2
        exit 1
    fi
    assert_no_campaign_state "$proof_root"
    if ! rg -q '^stage=before_readiness$' "$proof_root/events.log"; then
        echo "error: readiness case $proof_case lacks its boundary receipt" >&2
        exit 1
    fi
done

readiness_success_root="$SMOKE_ROOT/readiness/success"
M05_APPROVAL_READY_PROOF_STATE_ROOT="$readiness_success_root" \
    bash "$RUNNER" --cpu-readiness-proof-case success \
    > "$SMOKE_ROOT/readiness-success.stdout" \
    2> "$SMOKE_ROOT/readiness-success.stderr"
test -s "$readiness_success_root/campaign/attempt-spent.txt"
test -s "$readiness_success_root/campaign/eval-called.txt"

readiness_line="$(
    rg -n '^stage=validate_readiness$' "$readiness_success_root/events.log" \
        | cut -d: -f1
)"
sentinel_line="$(
    rg -n '^stage=create_sentinel$' "$readiness_success_root/events.log" \
        | cut -d: -f1
)"
eval_line="$(
    rg -n '^stage=eval$' "$readiness_success_root/events.log" | cut -d: -f1
)"
if [[ "$readiness_line" -ge "$sentinel_line" ]] \
    || [[ "$sentinel_line" -ge "$eval_line" ]]; then
    echo "error: readiness success crossed the sentinel ordering" >&2
    exit 1
fi

for proof_case in "${ENTRY_NEGATIVE_CASES[@]}"; do
    fake_calls_before="$(line_count "$FAKE_COMMAND_LOG")"
    if [[ "$proof_case" == "proof_root_outside_tmp" ]]; then
        proof_root="/var/tmp/m05-approval-entry-outside-proof"
        expected_outer_root=""
    else
        proof_root="$SMOKE_ROOT/entry/$proof_case"
        create_valid_entry_fixture "$proof_root"
        mutate_entry_fixture "$proof_case" "$proof_root"
        expected_outer_root="$proof_root/preflight-root/outer"
        if [[ "$proof_case" == "outer_root_mismatch" ]]; then
            expected_outer_root="$proof_root/preflight-root/not-the-outer-root"
        fi
    fi

    set +e
    M05_APPROVAL_ENTRY_PROOF_ROOT="$proof_root" \
        M05_APPROVAL_ENTRY_EXPECTED_OUTER_ROOT="$expected_outer_root" \
        M05_APPROVAL_ENTRY_FAKE_BIN="$FAKE_BIN" \
        bash "$RUNNER" --cpu-entry-proof "$proof_case" \
        > "$SMOKE_ROOT/entry-$proof_case.stdout" \
        2> "$SMOKE_ROOT/entry-$proof_case.stderr"
    proof_status=$?
    set -e

    if [[ "$proof_status" -eq 0 ]]; then
        echo "error: approval-entry case $proof_case unexpectedly passed" >&2
        exit 1
    fi
    fake_calls_after="$(line_count "$FAKE_COMMAND_LOG")"
    if [[ "$fake_calls_after" -ne "$fake_calls_before" ]]; then
        echo "error: approval-entry case $proof_case reached a fake runtime call" >&2
        exit 1
    fi
    if [[ "$proof_case" != "proof_root_outside_tmp" ]]; then
        assert_no_campaign_state "$proof_root"
    fi
done

valid_root="$SMOKE_ROOT/entry/valid"
create_valid_entry_fixture "$valid_root"
fake_calls_before="$(line_count "$FAKE_COMMAND_LOG")"
set +e
M05_APPROVAL_ENTRY_PROOF_ROOT="$valid_root" \
    M05_APPROVAL_ENTRY_EXPECTED_OUTER_ROOT="$valid_root/preflight-root/outer" \
    M05_APPROVAL_ENTRY_FAKE_BIN="$FAKE_BIN" \
    bash "$RUNNER" --cpu-entry-proof valid \
    > "$SMOKE_ROOT/entry-valid.stdout" \
    2> "$SMOKE_ROOT/entry-valid.stderr"
valid_status=$?
set -e
fake_calls_after="$(line_count "$FAKE_COMMAND_LOG")"

if [[ "$valid_status" -ne "$FAKE_DOCKER_STATUS" ]]; then
    echo "error: valid approval entry did not stop at fake Docker status 97" >&2
    exit 1
fi
if [[ "$((fake_calls_after - fake_calls_before))" -ne 1 ]]; then
    echo "error: valid approval entry did not make exactly one fake runtime call" >&2
    exit 1
fi
if [[ "$(tail -1 "$FAKE_COMMAND_LOG")" != "docker" ]]; then
    echo "error: valid approval entry did not stop at Docker first" >&2
    exit 1
fi
assert_no_campaign_state "$valid_root"

approval_line="$(
    rg -n '^stage=approval_validated$' "$valid_root/events.log" | cut -d: -f1
)"
preflight_line="$(
    rg -n '^stage=shared_cpu_preflight_complete$' "$valid_root/events.log" \
        | cut -d: -f1
)"
root_line="$(
    rg -n '^stage=outer_preflight_root_preserved$' "$valid_root/events.log" \
        | cut -d: -f1
)"
docker_line="$(
    rg -n '^stage=first_docker_boundary$' "$valid_root/events.log" | cut -d: -f1
)"
if [[ "$approval_line" -ge "$preflight_line" ]] \
    || [[ "$preflight_line" -ge "$root_line" ]] \
    || [[ "$root_line" -ge "$docker_line" ]]; then
    echo "error: valid approval entry stages are out of order" >&2
    exit 1
fi

jq -e '
    .status == "complete"
    and .approval_policy == "shared_none"
    and .outer_preflight_root_mutated == false
    and .runtime_counters.docker_daemon_calls == 0
    and .runtime_counters.network_calls == 0
    and .runtime_counters.gpu_calls == 0
    and .runtime_counters.service_actions == 0
' "$valid_root/preflight-root/outer/cpu-preflight.json" >/dev/null

if rg -q -i \
    'AWS_ACCESS_KEY_ID=|AWS_SECRET_ACCESS_KEY=|AWS_SESSION_TOKEN=|MERCURE_JWT_SECRET=|Authorization:[[:space:]]*Bearer' \
    "$SMOKE_ROOT"; then
    echo "error: CPU smoke persisted a credential assignment" >&2
    exit 1
fi

printf '%s\n' \
    'readiness_cases=8 readiness_negative=7 readiness_success=1' \
    'entry_cases=22 entry_negative=21 entry_success=1' \
    'valid_entry_fake_docker_calls=1' \
    'invalid_entry_fake_runtime_calls=0' \
    'outer_preflight_root_preserved=true' \
    'campaign_state_created=false' \
    'sensitive_assignments=0'
