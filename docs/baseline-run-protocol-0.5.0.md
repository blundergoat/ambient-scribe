# 0.5.0 no-intervention baseline run protocol

This protocol measures the transcript a clinician sees and the corrected source used for notes.
It fixes the commands and evidence shape before any GPU replay can influence the evaluation rules.
It keeps all ten development visits in one ordered lane so a failed visit cannot be quietly replaced.
Use it only after T01.8 approves every human-owned value and the GPU/loading Ask First gate clears.

Status: frozen by T03.1 and human-ratified at T01.8; GPU execution still requires Ask First approval
Protocol version: `ambient-scribe-baseline-run/v1`

## Execution boundary

- The operation is measurement-only. Do not edit prompts, detectors, decoder settings, lexicons,
  clinical knowledge, environment files, runtime configuration, application code, fixtures, or
  scorer rules from the T03.3 identity freeze through T03.10 non-intervention proof.
- Do not start, restart, rebuild, reload, or reconfigure NeMo under this protocol. If the approved
  runtime is not already healthy and GPU-backed, stop and obtain a separate Ask First approval.
- Provider requests and generations are not part of T03.4 or T03.5. T03.8 may run only after the
  GPU lanes have stopped, under the separately approved T03.2 branch and cap.
- One fixture runner may execute at a time. Live and corrected runners, provider generation, and any
  other NeMo/GPU work must never overlap.
- Manual browser replay is prohibited during baseline capture. Any later M01-M03 diagnostic replay
  must use one of the ten development stems below and must live outside baseline evidence.

## Exact corpus and order

Every runner invocation receives these ten full stems as ten explicit positional arguments:

1. `primock57-day1-consultation02-i-have-sore-red-skin`
2. `primock57-day1-consultation03-i-have-terrible-headache`
3. `primock57-day1-consultation06-hard-to-breathe`
4. `primock57-day1-consultation07-i-have-a-cough-and-cold`
5. `primock57-day1-consultation08-i-have-dry-itchy-skin`
6. `primock57-day2-consultation03-i-cant-hear-very-well-and-my-face-is-a-bit-numb`
7. `primock57-day2-consultation09-i-cant-move-my-left-arm`
8. `primock57-day3-consultation01-lips-swelling-after-eating-a-sandwich`
9. `primock57-day5-consultation03-im-feeling-very-anxious`
10. `primock57-day5-consultation09-tired-all-the-time`

No empty invocation, `--all`, partial consultation slug, glob, generated list, or implicit fixture
discovery is valid 0.5.0 evidence. A duplicate, missing, extra, reordered, or sealed stem invalidates
the affected baseline directory.

## Lanes, repetitions, and pacing

| Order | Lane | Runner | Full-corpus repetitions | Source scored |
| --- | --- | --- | ---: | --- |
| 1 | `live` | `scripts/eval-fixtures.sh` | 3 | Persisted live history after settled role labels |
| 2 | `corrected` | `scripts/eval-corrected-fixtures.sh` | 3 | Live history and post-stop corrected transcript |

- Exactly three repetitions per lane were ratified by the session user at T01.8 on 2026-07-17.
- Complete live repetitions `01`, `02`, and `03` before corrected repetitions `01`, `02`, and `03`.
- Each repetition is full length, `EVAL_PACE=1x`, `EVAL_CHUNK_MS=5000`, no `--seconds`, and no
  `EVAL_FIXTURE_SECONDS` value.
- Both lanes use `EVAL_ROLE_TIMELINE_SETTLE_SECONDS=8` and
  `EVAL_REQUIRE_STRUCTURED_LOGS=1` so saved labels have inspectable role evidence.
- Corrected source-chip findings remain evidence rather than aborting the untouched run:
  `CORRECTED_SOURCE_CHIP_FAIL_ON_FINDINGS=0`.
- Median-of-three is the primary repetition aggregate. All three raw runs, worst-run values,
  minimum, maximum, range, and median absolute deviation remain mandatory evidence.

## Human-ratified aggregation and resource caps

- Macro-per-fixture is the primary corpus statistic; pooled counts are secondary and cannot
  overturn the primary verdict. Critical events and worst-run gates remain conjunctive.
- Every live or corrected repetition must finish within `150 minutes`. Each three-repetition lane
  is capped at `450 minutes`, and the combined baseline is capped at `900 minutes`.
- Worst observed peak GPU memory must not exceed `14,000 MiB`. A later candidate may use at most
  `512 MiB` more than its accepted baseline while still remaining below the hard cap.
- OOM, illegal memory access, device assertion, CPU fallback, poisoned state, any other runtime
  failure, or a missing required resource sample has a maximum count of zero.
- M01 provider generation remains `not-run` with exactly `0 requests / 0 generations`.
- These values freeze evaluation limits but do not authorize loading, restarting, reconfiguring,
  or decoding with NeMo. The GPU Ask First boundary still applies.

## Immutable artifact layout

T03.3 creates one new root and fails if it already exists:

```text
var/quality/0.5.0-baseline-<UTC YYYYMMDDTHHMMSSZ>/
├── command-ledger.tsv
├── identity/
├── manifest.tsv
├── preflight/
├── live/
│   ├── trend.jsonl
│   ├── repetition-01/
│   ├── repetition-02/
│   └── repetition-03/
├── corrected/
│   ├── repetition-01/
│   ├── repetition-02/
│   └── repetition-03/
├── monitors/
│   ├── live-repetition-01/
│   └── <one directory for every later repetition>
└── verdicts/
```

Each repetition directory owns `runner.log`, `runner-exit.txt`, `wall-time.txt`, and the runner's
native per-fixture artifacts. Each monitor directory owns `agent-follow.log`, `health.jsonl`,
`gpu-samples-10s.csv`, and `fatal-signatures.txt`. Files are written once, recorded with SHA-256 and
byte size in append-only `manifest.tsv`, then made read-only. A stopped or failed root is never reused.

## Exact preflight commands

The following shell initialization is part of the recorded T03.3 command ledger:

```bash
set -Eeuo pipefail

baseline_started_at="$(date -u +%Y%m%dT%H%M%SZ)"
baseline_root="var/quality/0.5.0-baseline-${baseline_started_at}"
development_manifest='tests/fixtures/audio/development-corpus-0.5.0.json'

development_stems=(
    'primock57-day1-consultation02-i-have-sore-red-skin'
    'primock57-day1-consultation03-i-have-terrible-headache'
    'primock57-day1-consultation06-hard-to-breathe'
    'primock57-day1-consultation07-i-have-a-cough-and-cold'
    'primock57-day1-consultation08-i-have-dry-itchy-skin'
    'primock57-day2-consultation03-i-cant-hear-very-well-and-my-face-is-a-bit-numb'
    'primock57-day2-consultation09-i-cant-move-my-left-arm'
    'primock57-day3-consultation01-lips-swelling-after-eating-a-sandwich'
    'primock57-day5-consultation03-im-feeling-very-anxious'
    'primock57-day5-consultation09-tired-all-the-time'
)

[[ "${#development_stems[@]}" -eq 10 ]]
[[ ! -e "$baseline_root" ]]
mkdir -- "$baseline_root"
mkdir -- "$baseline_root/identity" "$baseline_root/preflight" \
    "$baseline_root/live" "$baseline_root/corrected" \
    "$baseline_root/monitors" "$baseline_root/verdicts"

strands_agents/.venv/bin/python scripts/development-corpus.py \
    --manifest "$development_manifest" \
    --stem 'primock57-day1-consultation02-i-have-sore-red-skin' \
    --stem 'primock57-day1-consultation03-i-have-terrible-headache' \
    --stem 'primock57-day1-consultation06-hard-to-breathe' \
    --stem 'primock57-day1-consultation07-i-have-a-cough-and-cold' \
    --stem 'primock57-day1-consultation08-i-have-dry-itchy-skin' \
    --stem 'primock57-day2-consultation03-i-cant-hear-very-well-and-my-face-is-a-bit-numb' \
    --stem 'primock57-day2-consultation09-i-cant-move-my-left-arm' \
    --stem 'primock57-day3-consultation01-lips-swelling-after-eating-a-sandwich' \
    --stem 'primock57-day5-consultation03-im-feeling-very-anxious' \
    --stem 'primock57-day5-consultation09-tired-all-the-time' \
    --json | tee "$baseline_root/preflight/development-corpus.json"

# Human-owned values must be resolved before the first clinician-visible replay is captured.
if rg --no-ignore --hidden 'HUMAN-PENDING' \
    docs/quality-contract-0.5.0.md; then
    printf 'baseline blocked: unresolved HUMAN-PENDING value\n' >&2
    exit 2
fi

curl -fsS 'http://localhost:48101/health' \
    | tee "$baseline_root/preflight/agent-health.json"

docker compose exec -T nemo-agent python -c \
    'import torch; print(torch.cuda.is_available())' \
    | tee "$baseline_root/preflight/cuda-available.txt"

grep -Fx 'True' "$baseline_root/preflight/cuda-available.txt"
```

Starting, restarting, rebuilding, loading, or changing concurrency is not a preflight command. If
health or CUDA proof fails, preserve the preflight directory and stop for a new approval packet.

## Exact runner commands

Before each command, T03.3 starts the three read-only monitors below and records their process IDs.
After each command, it stops only those monitor processes, records the runner's literal exit code,
and performs the failure checks in the next section. The six commands execute sequentially.

The only valid template assignments are, in order:

| Command | `lane_name` | `NN` |
| --- | --- | --- |
| 1 | `live` | `01` |
| 2 | `live` | `02` |
| 3 | `live` | `03` |
| 4 | `corrected` | `01` |
| 5 | `corrected` | `02` |
| 6 | `corrected` | `03` |

Live repetition `NN`, where `NN` is successively `01`, `02`, and `03`:

```bash
lane_name='live'
NN='<value from the table above>'
live_run_directory="$baseline_root/live/repetition-${NN}"
mkdir -- "$live_run_directory"
unset EVAL_FIXTURE_SECONDS

set +e
/usr/bin/time -v -o "$live_run_directory/wall-time.txt" \
    env \
    PYTHON_BIN='strands_agents/.venv/bin/python' \
    EVAL_PACE='1x' \
    EVAL_CHUNK_MS='5000' \
    EVAL_ROLE_TIMELINE_SETTLE_SECONDS='8' \
    EVAL_REQUIRE_STRUCTURED_LOGS='1' \
    QUALITY_RUN_DIR="$live_run_directory/artifacts" \
    QUALITY_TREND_FILE="$baseline_root/live/trend.jsonl" \
    ./scripts/eval-fixtures.sh "${development_stems[@]}" \
    2>&1 | tee "$live_run_directory/runner.log"
runner_exit_code="${PIPESTATUS[0]}"
set -e

printf 'exit_code=%s\n' "$runner_exit_code" \
    | tee "$live_run_directory/runner-exit.txt"
[[ "$runner_exit_code" -eq 0 ]]
```

Corrected repetition `NN`, where `NN` is successively `01`, `02`, and `03`:

```bash
lane_name='corrected'
NN='<value from the table above>'
corrected_run_directory="$baseline_root/corrected/repetition-${NN}"
mkdir -- "$corrected_run_directory"
unset EVAL_FIXTURE_SECONDS

set +e
/usr/bin/time -v -o "$corrected_run_directory/wall-time.txt" \
    env \
    PYTHON_BIN='strands_agents/.venv/bin/python' \
    EVAL_PACE='1x' \
    EVAL_CHUNK_MS='5000' \
    EVAL_ROLE_TIMELINE_SETTLE_SECONDS='8' \
    EVAL_REQUIRE_STRUCTURED_LOGS='1' \
    CORRECTED_SOURCE_CHIP_FAIL_ON_FINDINGS='0' \
    CORRECTED_FIXTURE_RUN_DIR="$corrected_run_directory/artifacts" \
    ./scripts/eval-corrected-fixtures.sh "${development_stems[@]}" \
    2>&1 | tee "$corrected_run_directory/runner.log"
runner_exit_code="${PIPESTATUS[0]}"
set -e

printf 'exit_code=%s\n' "$runner_exit_code" \
    | tee "$corrected_run_directory/runner-exit.txt"
[[ "$runner_exit_code" -eq 0 ]]
```

Array expansion is the only runner argument expansion: it supplies the ten literal values above in
their declared order. It performs no filesystem discovery.

## Resource and runtime sampling

For each repetition, start sampling immediately before its runner command:

```bash
monitor_directory="$baseline_root/monitors/${lane_name}-repetition-${NN}"
mkdir -- "$monitor_directory"

docker compose logs --follow --since "$(date -u +%Y-%m-%dT%H:%M:%SZ)" nemo-agent \
    > "$monitor_directory/agent-follow.log" 2>&1 &
agent_log_process_id="$!"

# Each sample shows whether the clinician-facing transcription runtime stayed healthy and on GPU.
while [[ ! -e "$monitor_directory/stop" ]]; do
    checked_at="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    if health_payload="$(curl -fsS 'http://localhost:48101/health' 2>/dev/null)"; then
        printf '{"checked_at":"%s","status":"ok","payload":%s}\n' \
            "$checked_at" "$health_payload"
    else
        printf '{"checked_at":"%s","status":"failed"}\n' "$checked_at"
    fi
    sleep 10
done > "$monitor_directory/health.jsonl" &
health_process_id="$!"

# GPU samples provide the peak-memory and device evidence used by the frozen scorecard.
while [[ ! -e "$monitor_directory/stop" ]]; do
    docker compose exec -T nemo-agent nvidia-smi \
        --query-gpu=timestamp,name,memory.used,memory.total,utilization.gpu \
        --format=csv,noheader,nounits
    sleep 10
done > "$monitor_directory/gpu-samples-10s.csv" 2>&1 &
gpu_process_id="$!"
```

After the runner exits, create the stop marker, wait for the health/GPU samplers, terminate only the
saved log-follower PID, and record each monitor exit. Monitor failure is a baseline failure, not an
unavailable metric that can be ignored. The human-ratified wall-time and peak-VRAM caps above apply
to every command.

## Failure and kill handling

- A non-zero runner or monitor exit stops the campaign before the next repetition. Preserve the
  complete root; do not rerun the failed fixture or reuse its repetition directory.
- An explicit corrected response with `status=unavailable` remains a failed corrected-lane outcome.
  The corrected runner may finish the other nine fixtures in corpus mode, but the unavailable row
  remains in the denominator and is never selectively replaced.
- After every repetition, scan its continuously captured agent log case-insensitively for
  `out of memory`, `CUDA is not available`, `illegal memory`, `device-side assert`,
  `websocket.error`, `correction.unavailable`, and `correction.failed`. Preserve all matches.
- OOM, illegal-memory, device assertion, CPU fallback, poisoned NeMo state, source-identity failure,
  structured-log absence, or resource-cap breach kills the campaign. Do not restart NeMo under this
  protocol; recovery requires a new Ask First packet.
- Health-sample failure, missing GPU samples, an unrecorded command/exit code, or a fixture count
  other than ten makes the repetition unavailable for promotion and stops later repetitions.
- Never rerun only a poor or failed case. A human-approved infrastructure retry reruns the complete
  ten-fixture lane from repetition `01` in a new timestamped root and retains the failed root.
- Any artifact path containing a primary or contingency holdout stem invalidates the entire 0.5.0
  baseline root and returns to the holdout decision gate.
- Do not change metrics, thresholds, aggregation, corpus membership, repetition count, or scorer
  behavior after any replay output exists.

## Non-intervention and completion proof

T03.3 records source/config hashes and worktree status before replay. T03.10 repeats those exact
commands and requires equality for application, prompt, detector, decoder, frontend, environment,
Compose, runner, scorer, manifest, and expectation inputs. Runtime/evidence outputs are enumerated
separately and do not become source changes.

T03.4 or T03.5 is complete only when all three ordered repetitions have ten retained outcomes,
literal exit records, continuous logs, health/GPU samples, source hashes, and immutable manifest
entries. Failure evidence is complete evidence but never a passing result. No threshold or promotion
claim is made until deterministic scoring and the human M01 acceptance gate are complete.
