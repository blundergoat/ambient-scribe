---
category: eval-metrics
last_reviewed: 2026-07-13
---

# Eval and Metrics Lessons

Lessons about scoring, fixture evals, oracles, and log-derived counters.
Split from `verification.md` on 2026-07-07 (bucket-size threshold).

## Lesson: Strict attribution excludes cross-talk rows - check overlap-touch before predicting gate flips

**Created:** 2026-07-07
**What happened:** The M07 handoff predicted the consult-08 echo-split doctor row would swing
strict attribution between 90.9% (pass) and 87.9% (fail). Reading `scripts/transcript-quality.py`
(search: "touches_any_span") showed rows touching TextGrid doctor∩patient overlap are dropped from
the strict/clean denominator entirely; the probe-timed row (15.92-16.56) touches the
16.12-16.36 cross-talk window, so strict stayed 29/32 and only the with-overlap metric moved
(90.6% -> 90.9%). The earlier rejected naive split landed at ~15.42-15.89 - clean patient-only
time - which is why THAT row was counted and regressed consult-08 to 81.8%.
**Prevention:** Before predicting how a row change moves strict attribution, compute whether the
new span touches a reference overlap span; overlap-touching rows move only with-overlap metrics.
Echo/boundary rows straddle cross-talk BY NATURE, so gates on them must weigh with-overlap
attribution, chip findings, and WER buckets instead of expecting strict-attribution deltas.

## Lesson: Eval history fetch must wait out post-disconnect role churn (2026-07-05)

**Created:** 2026-07-05
**Evidence:** `scripts/eval-fixtures.sh` (search: "Fetching earlier made attribution depend on a fetch-vs-flip race"), `.goat-flow/plans/0.3.0/M20-improve-doctor-patient-detection.md` (search: "fetch-vs-flip race").

During M20 Phase 1, three c03 @83s runs after a publish-only payload change all scored
45.0% strict attribution against a 55.0% Phase 0 median, which looked like the kill
criterion firing on the new code. The run-invariant diagnostics (dyadic ceiling, window
counts, remaps) were identical, and two runs with byte-identical role-decision timelines
had scored 45% vs 55% across phases - one Phase 0 history even contradicted its own
timeline's final mapping. The real cause: the role queue keeps accepting tail-batch
flips for seconds after WebSocket disconnect, and `fetch_history` ran before the settle
sleep, so the scored labels depended on a fetch-vs-flip race, not on the change under
test. Reordering the eval to fetch history after the role-timeline settle made the gate
deterministic (55.0/55.0/55.0). Churn can still straddle any fixed settle window, so
median-of-3 remains mandatory.

**Lesson:** when a gate metric moves right after a change that cannot mechanically
affect it, first check the gate's own sampling timing against asynchronous state
updates before blaming the change. Compare run-invariant diagnostics and look for
artifacts that contradict each other within one run (here: history role labels vs the
role timeline's final mapping) - a self-contradicting run proves a measurement race.

## Lesson: Free-assignment oracle metrics overstate reachable accuracy - constrain the assignment (2026-07-05)

**Created:** 2026-07-05
**Evidence:** `scripts/transcript-quality.py` (search: "def score_best_dyadic_mapping"), `.goat-flow/plans/0.3.0/M16-diarization-stability-role-confidence.md` (search: "Deep diagnosis (2026-07-05, second pass").

The M16 "speaker oracle accuracy" gave each emitted speaker ID its majority reference role
independently, so on fixtures where diarization mixed one voice across BOTH IDs the oracle
assigned DOCTOR to both (c03/c06/c08) or PATIENT to both (c07) - mappings no real
one-DOCTOR/one-PATIENT product can ship. The derived "role mapping gap" (+35.3pp on c07)
was read as role-mapping headroom, when the best VALID dyadic mapping could only recover
+13.7pp; the rest was diarization purity loss wearing a role-mapping costume.

**Lesson:** when an oracle/ceiling metric drives a which-layer-is-at-fault decision,
constrain the oracle to assignments the product can actually make (here: a role
bijection for dyads) and report both numbers. An unconstrained per-ID oracle is an upper
bound on a different system than the one being tuned.

## Lesson: Prompt-only role establishment hints need median replay proof (2026-07-05)

**Created:** 2026-07-05
**Evidence:** `strands_agents/api/role_agent_runtime.py` (search: "establishment_hint_guard"), `strands_agents/api/role_inference_queue.py` (search: "establishment_hint").

During M20 Phase 4, removing the role agent's `current_mapping` echo and adding cue-rich
representative rows looked mechanically correct, but c03 @83s immediately scored 45/45
strict on two runs. Adding opener cue counts and a prompt-level `establishment_hint` still
scored 50/50 on two of three runs because the model accepted the exact inverse mapping
from later seam-mixed rows.

**Lesson:** For sampled role-establishment work, do not accept prompt/evidence wording from
static tests or one replay. Use median-of-3 on the contested fixture, inspect the role
timeline when a run regresses, and make high-precision establishment evidence enforceable
when it is meant to prevent an exact dyadic inversion.

## Lesson: Region WER buckets need word-level allocation, not whole-interval exclusion (2026-07-05)

**Created:** 2026-07-05
**Evidence:** `scripts/transcript-quality.py` (search: "def timed_words_for_region"), `.goat-flow/plans/0.3.0/M17-transcript-accuracy-and-readability.md` (search: "Phase 0 baseline table").

During M17 Phase 0, the first clean-vs-overlap WER split assigned an entire TextGrid
interval or transcript row to the overlap bucket if it touched cross-talk at all. A full
fixture run made c07 clean WER print as `298.3%`: long reference intervals that barely
touched overlap were removed from the clean denominator while nearby transcript rows stayed
clean. The metric would have made later readability/transcription changes look worse or
better for bucket-boundary reasons instead of real word accuracy.

**Lesson:** For WER or other word-count metrics split by time regions, allocate words by
word timestamps when available, or by an explicit approximation such as token-center time.
Do not reuse segment-level "touches overlap" exclusion unless the numerator and denominator
are guaranteed to be bucketed the same way.

## Lesson: Log-derived timelines must count state changes, not echoed decisions (2026-07-05)

**Created:** 2026-07-05
**Evidence:** `scripts/role-timeline.py` (search: "Count the state-change log only"), `tests/python/test_role_timeline.py` (search: "role_mapping.flip_detected speakers=speaker_0,speaker_1").

During M16 diagnostics, the first role-timeline summary counted accepted flips whenever a
timeline row had `decision == "accepted_flip"`. A single visible flip appears twice in the
logs: once as the state-change row (`role_mapping.flip_detected`) and once as the downstream
published role-call row (`role_inference.completed` with `flip_detected: true`). The artifact
therefore doubled accepted-flip counts until the eval-run summaries were compared with
`session.quality`.

**Lesson:** For log-derived counters, identify the ownership event that mutates the state and
count only that event. Downstream publish/completed logs can repeat the decision for context,
but they should not increment the same summary counter unless the owning event is absent and
the fallback is documented in code and tests.

## Lesson: Original fixture metrics outrank plausible seam fixes (2026-07-05)

**Created:** 2026-07-05
**Evidence:** `scripts/eval-fixtures.sh` (search: "scripts/transcript-quality.py"), `scripts/transcript-quality.py` (search: "best dyadic mapping accuracy"), `tests/python/test_nemo_session.py` (search: "test_window_speaker_ids_follow_the_anchor").

A conservative speaker-anchor change for consultation-03 passed the focused
`tests/python/test_nemo_session.py` suite and matched the local theory that tiny context-tail
overlaps can trigger bad whole-visit speaker swaps. The original fixture replay rejected it:
consultation-03 at 83 seconds fell to 45.0% non-overlap attribution, while the reverted
runtime scored 55.0%. The unit test proved only one synthetic seam behavior, not the full
doctor/patient transcript users see.

**Lesson:** For transcription-quality work, keep the original fixture replay as the
acceptance gate. A mechanism that passes unit tests but worsens `scripts/transcript-quality.py`
on the reported fixture must be reverted or marked diagnostic-only, even when the hypothesis
still sounds mechanically plausible.

## Lesson: Held-tail speaker anchors need replay proof before adoption (2026-07-05)

**Created:** 2026-07-05
**Evidence:** `strands_agents/nemo_session.py` (search: "_overlap_speaker_map"), `.goat-flow/plans/0.3.0/M20-improve-doctor-patient-detection.md` (search: "held-tail anchor voting").

During M20 Phase 5, the per-window artifacts made held-tail speaker anchoring look like the
small seam mechanism M16 had left open: c03 @83s had 18 held rows and wrong rows clustered at
window starts, so using the prior window's canonicalized held rows as extra overlap-vote
references seemed safer than widening the NeMo/ASR window. The focused unit test passed and
the mechanism changed no audio slicing, no GPU model, and no timestamp mode. Runtime replay
rejected it anyway: c03 @83s strict attribution fell from the accepted 70.0 median to
65.0/65.0/65.0, with the same 18 windows, 18 remaps, 6 merges, 5 confidently wrong rows, and
2 uncertain rows each run. The patch was reverted and the restore smoke returned to 70.0.

**Lesson:** Treat held-tail or un-emitted transcript rows as an unproven speaker-identity
source, not a free continuity improvement. Even when a seam mechanism does not widen GPU
audio and passes a synthetic anchor test, run the reported fixture median before keeping it;
if it lowers strict attribution, revert rather than tuning a second seam rule in the same
phase.

## Lesson: Eval-generated session IDs must use route-valid UUIDs and avoid retry collisions (2026-07-05)

**Created:** 2026-07-05
**Evidence:** `scripts/eval-channel-ceiling.py` (search: "def channel_session_id"), `strands_agents/api/server.py` (search: "_validate_session_id").

During M17 channel-ceiling work, the first eval script posted human-readable session IDs such as `primock57-...-doctor` to `/transcribe/file`; FastAPI rejected them with `400 Bad Request` because the route validates caller-supplied session IDs. The next fix made deterministic UUIDs from fixture/role, but retries reused active in-memory session state during the reconnect grace window.

**Lesson:** Eval tooling that creates server sessions must either omit session IDs and capture the generated one, or generate valid UUIDs with a run-specific salt. After changing session identity behavior, run the server path that validates the ID rather than only testing local helper formatting.

## Lesson: Persisted fixture commands must use unique slugs (2026-07-10)

**Created:** 2026-07-10
**What happened:** M02's three-run command used `consultation03`, which had been unique when
the plan was drafted but matched four WAVs after the local corpus expanded. The resolver
correctly exited 2, while the outer loop repeated the same failure three times because its
example omitted `set -e`.
**Evidence:** `scripts/eval-corrected-fixtures.sh` (search: "matched multiple files"),
`.goat-flow/plans/0.4.0/M02-eval-vs-browser-live-gap.md` (search: "Phase 1 - zero-code
5-second/1x discriminator").
**Prevention:** Persist exact fixture slugs or direct paths in plans and handoffs, even when a
short consultation number is unique today. Put multi-run eval loops under `set -e`, and run
the resolver once before committing to an expensive GPU batch.

## Lesson: Grace-bound actions run before diagnostic settle sleeps (2026-07-10)

**Created:** 2026-07-10
**What happened:** Role-timeline instrumentation initially inserted an eight-second settle
before the corrected-fixture runner requested post-visit correction. The request then hung
past the documented 120-second budget while reconnect grace expired, even though the live
replay and timeline were already complete. M02 did not need correction to diagnose roles.
**Evidence:** `scripts/eval-corrected-fixtures.sh` (search: "request_correction" and
"write_role_timeline"), `.goat-flow/plans/0.4.0/M02-eval-vs-browser-live-gap.md` (search:
"Authoritative settled-role rerun").
**Prevention:** In a harness with a grace-bound post-stop action, invoke that action before
fixed diagnostic waits and give its client an explicit timeout. Use the live-only evaluator
for live-lane diagnostics instead of making unrelated correction success a prerequisite.

## Lesson: Targeted attribution deltas do not replace corpus-wide quality gates

**Created:** 2026-07-12
**What happened:** M04's stable two-identity alias passed two named same-span gates with nine
improvements, zero worsened spans, and no new identities, then reproduced all three flag-OFF
hashes. The canonical 20-fixture run still regressed corrected strict by 1.290 points and raised
incorrect-confident by 1.645 points, with large failures on two non-target c07 fixtures.
**Evidence:** `.goat-flow/plans/0.4.1/M04-crosstalk-bleed-mechanism.md` (search:
"Stable-alias candidate rejected and removed") and
`var/quality/m04-crosstalk-bleed-20260711T193941Z/phase1c-full-corpus-gate-verdict.md`.
**Prevention:** Treat named causal fixtures as mechanism proof, not release coverage. A visit-long
speaker policy must still pass the full corpus quality/identity gates even when targeted deltas
and default-OFF compatibility are exact.

## Lesson: A zero target count needs a causal guard event

**Created:** 2026-07-12
**What happened:** M05's exact guard-ON replay scored zero safe repeats, but logged no withheld row;
the retained 67-row/3-phantom target instead became 63 rows/10 phantoms and its original pair was
not reproduced. The zero could not be attributed to the guard or prove no word loss.
**Evidence:** `.goat-flow/plans/0.4.1/M05-dual-identity-duplicates.md` (search:
"duplicate_row_withheld event").
**Prevention:** Require the causal guard event plus a matched before/after target. A zero detector
count alone is not improvement when the input behavior or adjacent identity metrics changed.

## Lesson: Mixed JSONL metrics need exact event and phase filters

**Created:** 2026-07-13
**What happened:** M06's first delivery aggregation treated every JSONL object's `emitted_rows`
as a per-window count. The file also contained `window_continuity.summary`, whose 274-row session
total was then misreported as the largest browser burst; the real c01 maximum was 25 rows.
**Evidence:** `var/quality/m06-emission-starvation-20260712T211015Z/retained-delivery-and-density-baseline.log`
keeps the failed aggregation, while `retained-delivery-baseline-corrected.log` filters exact
`nemo_session.window_continuity` events with `phase=chunk`.
**Prevention:** Before reducing a mixed JSONL artifact, inspect its first and last object shapes
and filter the exact owning event plus phase. Exclude finalize and summary totals from per-window
delivery metrics unless the metric explicitly includes them.

## Lesson: Cadence-quantized bounds need startup corpus coverage

**Created:** 2026-07-13
**What happened:** M06 selected a 10-second stability hold from two long-turn fixtures and both
projected at or below a 15-second browser batch interval. The target and browser gates passed,
but the 20-fixture run found four 20-25-second first-batch intervals. The worst window had five
stable clock-ready rows yet only 9.8 seconds of measured hold, so a strict 10-second comparison
waited for the next five-second evaluation step.
**Evidence:** `var/quality/full-corpus-20260712T233933Z/m06-delivery-violation-details.txt` and
`.goat-flow/plans/0.4.1/M06-emission-starvation.md` (search: "CORPUS GATE REGRESSION").
**Prevention:** When a policy is evaluated only on a fixed cadence, project and test boundary
values just below the threshold as well as long established holds. Include time-to-first-row in
the corpus delivery gate; mid-consultation target fixtures do not cover startup quantization.
