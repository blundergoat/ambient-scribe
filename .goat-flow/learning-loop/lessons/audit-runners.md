---
category: verification
last_reviewed: 2026-07-31
---

# Audit Runner Lessons

## Lesson: Prove process and artifact contracts before paid work

**Created:** 2026-07-14
**Decision changed:** Freeze a runner identity only after static checks and a direct synthetic
exercise of its real entry function both pass.
**Trigger phase:** VERIFY
**Incident count:** 2
**Latest occurrence:** 2026-07-31
**What happened:** M08's one-shot shell reaped a bare `nohup` child before replay, while its first
provenance reader expected `id` instead of the retained rows' `segment_id`. No audio or provider
request was lost: a no-token artifact probe caught the schema error, and `setsid --fork` plus
process, first-log-line, and unchanged-ledger checks proved the replacement launch.
M05B later declared two proof-only patch variables that its successor runner never consumed;
ShellCheck rejected both as `SC2034` before the contract identity was frozen, and the variables
were removed before the successor-only default-entry smoke ran.
**Evidence:** `var/quality/note-fidelity-audit-20260713T234904Z/` and
`var/quality/0.5.2-asr-accuracy/m05-baseline/approval-entry-remediation-cpu-proof/20260730T204841Z/verification-correction.txt`
(search: `54/54 citations` and `SC2034`).
**Prevention:** Before paid or long evidence runs, assert one known non-zero result through every
new artifact reader. After launch, require live process state, first progress, and an owned exit
sentinel; a successful shell return proves neither schema compatibility nor detachment. For
approval runners, run Bash syntax and ShellCheck before freezing hashes, declare proof-only state
only when it is consumed, and exercise the exact default entry function with failing command shims.

## Lesson: Keep repository lint separate from changed-file formatting

**Created:** 2026-07-14
**What happened:** M08's canonical Ruff lint passed, and its runner passed a focused format check.
An extra repository-wide format check found 34 untouched files outside the milestone's boundary.
**Evidence:** `var/quality/note-fidelity-audit-20260713T234904Z/verify-ruff-format-full.log`.
**Prevention:** Run the repository's canonical Ruff `check` gate, then format-check only edited
Python files unless a separately scoped milestone adopts a whole-repository formatter baseline.

## Lesson: Exclude an evidence manifest from its own file scan

**Created:** 2026-07-18
**What happened:** M00B manual-acceptance sealing opened `manifest.tsv` before scanning the evidence root.
The scan therefore hashed the manifest at 510 bytes while the same command was still appending rows; the
completed file was 2,308 bytes and failed its own verifier. The failure occurred before permissions changed,
and the failed manifest was preserved before the corrected seal.
**Evidence:** `var/quality/0.5.0-m00b-manual-acceptance-20260718T111928Z/verification/manifest-failure.md`
(search: "The first manifest command").
**Prevention:** Exclude the exact final manifest path from the scan even when a non-existent-path precheck
passes, because shell redirection creates it before `find` executes. Verify every listed file and the expected
row count before making any artifact read-only; preserve a failed ledger under a distinct failure name.

## Lesson: Keep conditional proposal arms in separate verification runtimes

**Created:** 2026-07-18
**What happened:** M01 prepared a default-off context seam and a later conditional prompt rule in one
temporary tree. Three proposal checks either omitted an existing helper from the mirror or accidentally
loaded the conditional prompt while verifying that the seam kept the baseline prompt byte-identical. The
frozen SHA test rejected the contamination before any repository source or provider action.
**Evidence:** `var/quality/0.5.0-m01-soap-prompt-20260718T112802Z/verification/`
(search: `proposal precheck failure`).
**Prevention:** Give each conditional arm its own import root, copy every transitive helper needed by the
focused test, and print the selected module origin plus frozen prompt hash before accepting the result.
Treat a mirror/import failure as verifier failure, preserve it, and rerun from a clean arm rather than
changing the candidate to satisfy an incomplete sandbox.

## Lesson: Separate application VRAM from unrelated desktop allocations

**Created:** 2026-07-19
**What happened:** M01.4 compared a `7728 MiB` total GPU sample with an old `6270 MiB` application
startup budget. The service had been stopped at `2055 MiB` while the user was playing a game, so the
application added about `5673 MiB`; the unchanged baseline later restarted at the same total. The
approved packet was followed correctly, but its total-memory metric attributed external VRAM to the app.
**Evidence:** `var/quality/0.5.0-m01-soap-prompt-20260718T112802Z/` (search:
`candidate_peak_gpu_used_mib=7728` and `gpu_used_before_start_mib=2055`).
**Prevention:** Freeze two resource gates separately: application delta from an immediate stable external
baseline, and an absolute total safety ceiling. Record both values, preserve external workloads as
context, and never use a historical total-idle sample as the application's own allocation.

## Lesson: Lifecycle telemetry must own the action it measures

**Created:** 2026-07-19
**What happened:** M01.8 started the approved NeMo container in one tool call and launched its sampler in
the next. The service reached healthy within every resource cap, but agent orchestration delayed the first
sample until 17 seconds after the start request, so the required from-start evidence was incomplete.
**Evidence:** `var/quality/0.5.0-m01-soap-prompt-r1-20260718T203012Z/verification/`
(search: `incomplete_missing_early_samples`).
**Prevention:** Freeze a small print-only runner before approval and make that one process own stop/start,
the first sample, cadence, and health transition. Never split a lifecycle action from its monitor across
agent tool calls; passing later samples cannot reconstruct a missing startup interval.
