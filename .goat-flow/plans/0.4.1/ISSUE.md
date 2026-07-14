# ISSUE - 0.4.1: Corpus-Driven Quality + First Readiness Steps

**Status:** in progress (2026-07-12; M01 is complete at `4bd4992`, M02 is complete at
`58a14ac`, and M03 is complete at `f203f1b`. M04 received milestone-specific approval and
confirmed the marginal-slot fold mechanism with PHI-safe evidence. Its first guarded candidate
improved targeted attribution but regressed corpus phantom identities 10 -> 18. The stable-alias
redesign then passed the approved targeted attribution-delta and flag-OFF hash gates, but the
20-fixture corpus regressed corrected strict 88.985% -> 87.695% and incorrect-confident 10.595%
-> 12.240%. The stable-alias product/test delta has been removed, the diagnostic guard remains
`0` for user visits, and the reusable grounded scorer plus failure evidence are retained. M04 is
complete at `71b2e0e`. M05 Ask First intake is ready: its original field session is unavailable,
the replacement browser artifact has one overlapping cross-ID duplicate candidate, and canonical
day5-c09 has zero (not the plan's prior “3”) under the declared rule. Diagnostic-only Phase 0
was explicitly approved and is complete: a fresh 1x replay also scored zero pairs, so the mechanism
was not reproduced and all speculative fixes were rejected. The user approved closing M05 as
diagnostic-only / no-fix, and every required suite passed with no product edit, byte-identity run,
or corpus re-baseline applicable. Commit `4721c7c` landed that detector, but the user's subsequent
132.8-second browser check reproduced a cross-ID same-person overlap that exact wording equality
missed. M05 is reopened; one diagnostic-only 132.8-second real-browser replay with existing
JSON/slot instrumentation confirmed the mechanism: the engine emits one Patient under two
substantial cache slots and the adapter preserves both. M05 is stopped at a fresh goat-debug D3
product-scope gate. The user approved planning only: a narrow, default-OFF adapter guard would
withhold only cross-slot decoder variants sharing four consecutive words within 0.5 seconds and
at least 0.80 word-LCS similarity, with equal normalized vocabulary so no distinct clinical word
is discarded; a grounded scorer must first reproduce target=1 and canonical 20=2. The exact
seven-file product/scorer scope received explicit implementation approval on 2026-07-12 and is
locally implemented with 35 focused tests green. The first retained-artifact score triggered the
metric kill: manual/instrumented/canonical grounded counts are 0/0/0 because the real target has
one vocabulary difference despite 0.875 word-LCS and a shared four-word phrase. The user approved
the bounded refinement: equal word multisets or exactly one `and`/`but` substitution, with every
other predicate unchanged. Red proof was exactly four intended failures and 35 passing existing/
anti-target contracts; the shared Counter-based scorer/runtime predicate now passes 39/39 focused
contracts. Ruff, Compose, and scorer/runtime Gruff are clean; retained-artifact gates remain, so
manual/instrumented now score 1/1. The exact accepted canonical 20 scores 0, not the planned 2:
both planning candidates contain distinct non-connector words, exposing the earlier jq probe as
invalid for the corpus baseline too. The user approved correcting safe canonical repeats to 0,
treating those two near-matches as anti-targets that remain visible, and using the retained
connector pair as the required 1 -> 0 behavior metric. M05 is in its byte/target/corpus testing
gate, but its first flag-OFF c02 correction timed out after 120 seconds before a corrected artifact
or hash existed. Streaming finalized with zero quality errors and health/CUDA remain live; c03/c08
did not start. M05 is stopped at this cross-boundary gate failure; no candidate is accepted or
enabled. The user approved one clean isolated c02 retry; a repeated timeout stops before c03/c08.
The clean retry passed in 50.821 seconds and reproduced the c02 canonical hash exactly; c03/c08
then completed in 11.636/11.823 seconds. The flag-OFF gate passes `HASH_MATCHES=3/3`, strict
100.0/96.8/93.8, zero source-chip findings, and clean health/CUDA/runtime logs. The exact guard-ON
target then failed causal acceptance: it scored zero pairs without any withheld-row event, emitted
63 vs 67 baseline rows, and changed phantom merges 3 -> 10 because the original pair was not
reproduced. M05 is stopped before browser/corpus work; the candidate remains unaccepted.
The running agent has been restored to guard `0` with health/CUDA live.
The user approved the declared target-kill closure. Runtime/config/guard-test rollback is exact;
the improved PHI-safe scorer, contracts, and rejection evidence remain. M05 is complete as a
diagnostic/no-fix milestone: focused scorer tests pass 7/7, fresh retained counts are 1/1/0,
Python passes 608, PHPUnit 37/159, Playwright 48, PHPStan and PHP-CS are clean, and all 12 enabled
preflight checks pass. Ruff is clean/formatted, scorer Gruff is A/100, and learning index/stats,
context validation, PHI/symbol scans, and `git diff --check` are clean. The guard default was not
promoted and the guard-ON corpus run was correctly closed as inapplicable after the target kill.
M05 is complete at `4abcf6a`. On 2026-07-13 the user explicitly inserted and approved M00 ahead of
M06: align effective model defaults across Terraform, Compose, setup/eval scripts, and stack docs
without changing any selected model, deploying infrastructure, touching `.env`, or sharing NeMo's
GPU. M00 implementation and verification are complete: Python 608, PHPUnit 37/159, Playwright 48,
PHPStan/CS/Ruff, preflight, context validation, learning stats, end health/CUDA, Compose scenarios,
and focused static gates pass. Terraform validation is explicitly UNVERIFIED because modules are
not initialized, and the optional Bedrock token probe is HUMAN-PENDING with no token spent. Exact
M00 scope was committed by the user at `4b92591` and the worktree is clean. Every later milestone
retains its individual execution/token approval gate. M06 Ask First intake is ready: retained M02
evidence confirms the 50-second day3-c01 hold, and the live tree corrects the active mechanism to
`nemo_streaming_engine.py` rather than the windowed `_transcribe_unemitted` path. The user
explicitly approved M06 within that declared scope on 2026-07-13; Phase 0 diagnostic/default-OFF
instrumentation and two 1x replays are complete. Count/time-only evidence proves a repeatedly
refreshed old mutable tail pins the global stability frontier behind stable clock-ready rows: c01
reproduced 50.0s zero emission / 55.0s between batches / a 25-row burst, while c03 measured
25.0s / 30.0s / 15 rows. A default-OFF 10-second stability bound is selected for Phase 1 because
the captured evidence projects <=15-second delivery on both fixtures. That narrow candidate and
ADR-009 are now implemented with 34 focused tests green. The first flag-off c02 stream finalized
cleanly, but its correction request timed out after 120.001 seconds before any corrected artifact
or hash existed; c03/c08 did not start. M06 is stopped at the declared cross-boundary gate with
health/CUDA live. The user approved one clean isolated c02 retry on 2026-07-13; a repeated timeout
would stop again. The retry passed in 50.013 seconds with strict 100.0%, zero chip findings, and the
exact retained c02 hash. c03/c08 then completed and the canonical gate passed `HASH_MATCHES=3/3`;
c08 attribution movement is isolated to the documented role-worker wobble while its canonical
bytes remain exact. Flag-on c01 then passed: maximum zero emission 50 -> 10 seconds, maximum batch
interval 55 -> 15 seconds, largest burst 25 -> 9 rows, with WER/strict/seams within noise. Corpus
and the required c01 browser acceptance remain. A manual c01-unrelated replay exposed separate
frontend grouping/confidence-chip work; no frontend edit was made inside M06. The subsequent
correct c01 browser replay passed runtime delivery at 10/15 seconds and a 9-row largest burst.
Six screenshots from a second correct replay show visible rows advancing 14 -> 89 through the
known long Patient turn, so browser acceptance passes. The full flag-on corpus started detached
at `var/quality/full-corpus-20260712T233933Z/` with clean health/CUDA and live evidence monitors.
An interrupted assistant turn ended the first wrapper during fixture 3 after two complete
fixtures, without an app/GPU fatal signature. The 18 explicit remaining stems resumed in
independent OS sessions at `2026-07-13T00:35:39Z` and exited 18/18, yielding 20 complete corpus
artifacts and zero correction failures. M06 is stopped at a corpus-gate regression: four startup
gaps exceed the proposed <=15-second batch interval (worst 25 seconds on day1-c08). Corrected
WER/strict/incorrect-confident means move 21.825/88.985/10.595 -> 22.165/88.945/11.000, and
source-chip findings remain 62. The candidate remains default-OFF; no refinement or full-suite
promotion has started. The running agent is restored to max hold `0` and healthy CUDA/NeMo. The
user then approved one narrow cadence-aware refinement: release only stable clock-ready rows when
the next fixed audio decision would overshoot the configured stability bound. Three causal startup
violations plus the no-ready-row c06 control gate it before any second corpus promotion. Direct
contracts are green and day1-c03 now emits its first stable batch at 15 rather than 20 seconds,
but its post-stop correction timed out after 120.001 seconds. The remaining three isolated
fixtures did not start. The user approved a live-only alternate gate on 2026-07-13: score the
retained c03 history and run c06/c08/day3-c05 at 1x through the existing live runner, with no
correction call or product edit. That alternate gate passed all four sessions: causal ready-row
waits are 0/5/5/5 seconds, mean live WER moves only +0.025 points, strict and
incorrect-confident means are flat, quality errors are zero, and health/CUDA/fatal scans are
clean. The second 20-fixture corpus remains the next promotion gate. A final c08 browser replay
visually passed the manual cadence check: first rows appeared at 20 seconds versus 25-26 seconds
default-off, later ready-row wait was at most one 5-second tick, and session
`ac9bec81-313c-4c13-93e8-07e9a7164178` finalized 106 rows / zero errors with correction complete.
The confidence chip stayed absent. Adjacent same-role rows still render as separate cards; that
separately requested frontend grouping work remains outside M06. The user chose to finish M06
first. Its second 20-fixture corpus passes with 20/20 corrections, zero causal violations/errors,
maximum `5s` stable-ready wait, and corrected WER/strict/incorrect-confident deltas
`+0.345/-0.250/+0.195pp`; source-chip findings improve `62 -> 61`. Final health/CUDA/fatal gates
pass. M06 is stopped before suites because the post-run Compose recreate returned effective max
hold `10`, not tracked fallback `0`; evidence/log defaults restored and `.env` remains protected.
The user aligned the protected local environment with tracked defaults, and a normal recreate now
passes effective streaming/max-hold/evidence/log values `streaming/0/0/console`, health, CUDA, and
fatal scan without the assistant reading or editing `.env`. M06 is complete: Python 624, PHPUnit
37/159, Playwright 48, JavaScript 15, PHPStan, PHP-CS, Ruff, all 12 enabled preflight checks,
context validation, learning index/stats, and `git diff --check` pass on the final code state. M07
Phase 0 reproduced the 84.4/59.4/84.4 c08 role bifurcation with byte-identical rows and found one
mixed repair/regression proposal in the accepted corpus. The first cited per-speaker repair
candidate missed at 78.1% because it omitted late `speaker_3`. Its approved completeness revision
passed 43 focused tests, but the first revised scorer snapshot again read 78.1% after an 8-second
settle; the complete correct map arrived 11.578 seconds after finalize, 3.427 seconds after
history collection. Its approved 20-second-settle rerun then passed five consecutive c08 runs at
84.4% strict with complete correct maps. The full-length spot gate improved c02/c03/c08 by
+0.1/+0.5/+0.3pp, then stopped on c07 at 86.8% vs 87.2% baseline (-0.4pp); day5-c08 was aborted.
C07 canonical rows and its best-dyadic ceiling also changed (-0.5pp), so role-lane harm is not
proven. One explicitly approved unchanged c07 retry exactly reproduced the failed canonical hash
and 86.8% score. Per its pre-approved decision rule, revision 2 is rejected; day5-c08 and broad
suites were not run. The user approved rejected/no-ship closure, and the complete role,
timeline, and test candidate is restored to HEAD. Restored-tree verification passes focused 38,
Python 624, PHPUnit 37/159, Playwright 49, PHPStan, PHP-CS, Ruff, and all 12 enabled preflight
checks. M07 is complete as rejected/no-ship: exactly five retained decision/footgun/lesson files
are staged, with no product candidate or CHANGELOG entry. M08's six-note evaluation and routing
are complete with a FAIL verdict: only 1/6 notes is content-clean, 21/209 claims are unsupported
and unflagged, warning precision is low, and one corrected-source note is truncated. Product
behavior is unchanged. Closure passes Python 624, PHPUnit 37/159, Playwright 49, JavaScript 15,
PHPStan, PHP-CS, canonical Ruff lint, preflight, context validation, learning statistics, and
final health/CUDA. M08 is complete.)
**Owner:** Matthew Hansen
**Created:** 2026-07-11

## Why

The 2026-07-11 full-corpus baseline (`../0.4.0/CORPUS-BASELINE-2026-07-11.md`; artifacts
`var/quality/full-corpus-20260710T2328Z/`) measured all 20 fixtures at full length for the
first time and put hard numbers on the system:

- Corrected WER improves 20/20 fixtures (mean 27.5% -> 21.8%) - the correction lane works.
- Strict speaker attribution is the ceiling: mean 89.2% live, flat through correction, with
  ~10% incorrect-confident rows and 62 source-chip errors (~1.0% of corrected rows,
  bleed-family dominated). The recorded streaming-engine families (cross-talk bleed,
  dual-identity duplicates, emission starvation, c08 role bifurcation) are where the next
  quality points live.
- Overlapped speech is essentially lost (per-fixture overlap WER ~86%+) - unquantified
  corpus-wide until now.
- The sweep itself found a correction defect (tail-sliver veto - fixed in the M09 commit
  set) and two harness gaps (eval corpus-mode fragility; docker-log-rotation evidence loss).
- Trust surfaces exist (fidelity flags, source notices, confidence values persisted) but the
  reopened M10 denial-precision debt and the unbuilt UX-M7 styling limit them, and
  correction still depends on an open browser tab (ROADMAP Phase 3).

## Milestones

- **M01 - Harness and evidence hardening.** Eval corpus mode records failures and continues;
  correction failure metadata gains a chunk index; e2e browser lane gets PHP server workers;
  ROADMAP.md refreshed against reality. Protects every later milestone's evidence.
- **M02 - Fidelity denial precision (reopened M10 debt).** Split clinician questions stop
  producing false denial flags; corpus acceptance runs stop showing the two known false
  positives.
- **M03 - Low-confidence styling + garble hedging (UX-M7 successor).** Persisted per-row
  acoustic confidence becomes visible styling and summary-side hedging so ASR garble stops
  reading as certainty ("calf" -> "back area" class). Phase 0 proved it does not predict the
  separate ~10% wrong-role rows; M07/M11 retain that responsibility.
- **M04 (Ask First - streaming engine) - Cross-talk bleed mechanism confirmation.**
  Confirm-first protocol from footguns/runtime.md ("fold short real interjections"):
  instrument per-window slot shares on bleed fixtures before any threshold change; then fix
  or explicitly reject. The approved Phase 2 re-scope uses TextGrid-grounded harmful/benign
  fold spans plus corpus strict/incorrect-confident and no-new-identity gates; the low-precision
  source-chip count remains diagnostic.
- **M05 (Ask First - streaming engine) - Dual-identity duplicate speakers.** day5
  speaker_0/speaker_3 duplicate emission; uses M04 instrumentation.
- **M06 (Ask First - streaming engine) - Long-turn emission starvation.** day3 windows
  19-28 zero-emission hold; separate emission-gate investigation.
- **M07 (Ask First - role agent) - c08 role-map bifurcation.** Decouple correct
  single-speaker repairs from wrong coupled flips so whole-map damping stops suppressing
  good fixes; addresses the bimodal 59.4/84.4 strict attribution.
- **M08 - Note-fidelity corpus audit.** Six stratified corrected-source notes were generated and
  audited at the approved 6-request/12-generation cap. The audit fails: 21/209 claims are
  unsupported and unflagged, fidelity warning precision is 12.5%, wording-warning precision is
  20.0%, and one long note input is truncated. No product behavior changed.
- **M09 - Overlap-speech loss assessment (analysis-only).** Quantify corpus-wide overlap
  WER/attribution from existing artifacts; produce the decision input for 0.5.0 model work
  vs UI mitigation. No runtime changes.
- **M10 (Ask First - server/lifecycle) - Server-side correction/summary orchestration.**
  ROADMAP Phase 3: Stop/finalized enqueues a durable server-side job so correction no longer
  depends on an open tab; observable job states; idempotent retries.
- **M11 - Clinician review queue (ROADMAP Phase 2 slice).** One reviewable lane for
  mixed-role cards, chip-scorer findings, low-confidence rows, and fidelity-flagged
  sentences - turning tonight's ~10% incorrect-confident measurement into workflow instead
  of hidden risk.

## M08 routed quality debt

Evidence root: `var/quality/note-fidelity-audit-20260713T234904Z/`; exact claim text and source
rows are pinned in each fixture's `audit.md`, with arithmetic in `aggregate.json`.

- **Reopen fidelity coverage:** 14 unflagged occurrences belong to existing known families;
  day5-c09 includes the exact self-corrected-weight false negative retained by M02.
- **Reopen M02 precision:** four direct split-question/composite denials are falsely flagged.
  The separate c07 denial warning follows an upstream corrected-role fold and is not evidence
  against the connector predicate.
- **Reopen M03 precision and linkage:** 12/15 wording warnings are false positives. Two of the
  three true warnings identify wrong prose but cite unrelated low-confidence rows, so future
  tests must assert both the sentence and the row that triggered it.
- **Summary-input cap:** day1-c07 retained only 32,698/33,922 corrected-source characters. The
  32,768-character selection contract is therefore not corpus-safe.
- **Novel regression specimens:** record unsupported demographics, symptom inference,
  medication-status generalisation, stale medication availability, invented treatment rationale,
  unsupported future-plan elaboration, and completed test/order assertions. These seven shapes
  remain visible records for the owning fix; M08 does not tune the shipped checker mid-sample.

## Order and gating

M01 first (cheap; every later milestone's evidence depends on it). M02 and M03 next,
independent and small-to-medium. M04 is the big rock and MUST follow its confirm-first
protocol - instrumentation evidence before any fold-threshold change; M05/M06 consume M04's
instrumentation and follow it. M07 is independent of M04-M06 (role-agent lane, not audio).
M08 runs after M02+M03 land (it audits their effect). M09 is analysis-only and can
interleave anywhere. M10 and M11 are the readiness pair; M10 before M11 (the queue reads
job states). Every Ask First milestone requires explicit user approval at execution start;
none is pre-approved by this plan.

## Shared acceptance context

- Corpus reference: `var/quality/full-corpus-20260710T2328Z/` table in
  `../0.4.0/CORPUS-BASELINE-2026-07-11.md`. Streaming-engine changes (M04-M06) must pass
  the @60s byte-identity gates where applicable AND improve their targeted corpus metric
  without regressing strict attribution or incorrect-confident rates corpus-wide.
- **Corpus re-run command** (all milestones cite this instead of restating it):
  `CORRECTED_FIXTURE_RUN_DIR=var/quality/full-corpus-<UTC-stamp> EVAL_PACE=1x
  scripts/eval-corrected-fixtures.sh --all` - after 0.4.1-M01 lands, corpus mode records
  failures and continues; before M01, know that one unavailable correction aborts the run
  (`scripts/eval-corrected-fixtures.sh`, search: "correction unavailable").
- **Byte-identity gate protocol**: the @60s c02/c03/c08 trio with canonical
  `text,start,end,speaker_id` hashes - protocol and reference hashes in
  `../0.4.0/M01-streaming-rebaseline.md`; most recent pass evidence
  `var/quality/corrected-fixtures/20260710T194743Z-m09-gpu-trio/`.
- **Re-baseline rule**: corpus-delta targets (for example M04's chip-error halving) compare
  against the MOST RECENT full-corpus table, not blindly against 20260710T2328Z - if
  another streaming milestone landed in between, re-run the corpus first and re-derive the
  target from that fresh table, or the delta is unattributable.
- GPU discipline unchanged: NeMo owns the GPU; verify device liveness in-container before
  baseline-gated evals (`docker compose exec -T nemo-agent python -c "import torch;
  print(torch.cuda.is_available())"`); restart after any NeMo internal error; grep agent
  logs between fixtures (`Traceback|websocket\.error|CUDA error|illegal memory`).

## Execution protocol for every 0.4.1 milestone

1. Read `CLAUDE.md`, `AGENTS.md`, this ISSUE, the milestone file, and every footgun the
   milestone names BEFORE any edit. Milestone files cite evidence paths - read the evidence,
   do not trust the summary sentence.
2. Declare SCOPE (mode/complexity/files/non-goals) at start; Ask First milestones require
   the user's explicit approval message before the first product edit - this plan set does
   NOT pre-approve anything.
3. Evidence artifacts go under `var/quality/<milestone-slug>-<UTC-stamp>/`; plan checkboxes
   and CHANGELOG update as tasks complete; never commit or push - stage and hand the user
   the command.
4. Docker container logs rotate: capture `correction.completed`/instrumentation lines into
   the evidence dir DURING long runs (2026-07-11 lesson; 13/15 lines lost to rotation).

## Out of scope

- Raw WER/model work (0.5.0: lexicon plateau -> conditional Parakeet fine-tune; M09 here
  only produces the overlap evidence).
- Auth/PHI/retention (ROADMAP Phase 4+), production ops (Phase 5), pilot (Phase 6).
- Reintroducing any path in ROADMAP "Rejected Or Deferred Paths".
