---
category: streaming
last_reviewed: 2026-07-26
---

# Streaming Footguns

## Footgun: A minimum-duration floor turns missing word timing into a plausible measurement

**Status:** active | **Created:** 2026-07-26 | **Evidence:** ACTUAL_MEASURED
**Decision changed:** Treat any row whose span equals a code floor as timing-absent, not timing-known. Before drawing a conclusion from live row spans, count the floor-valued rows first.
**Trigger phase:** VERIFY

- **Files:** `strands_agents/nemo_streaming_engine.py` (search: `def _appended_word_entries`)
- **Files:** `scripts/transcript-quality.py` (search: `--row-diagnostics-json`)
- **What breaks:** `_appended_word_entries` shares a decode step's new token timestamps across the words appended in that step by proportional index. When a step appends more words than it received tokens, several words resolve to the same token index, `token_hi - 1 == token_lo`, and both ends of the span derive from one token. `end=max(word_end, word_start + 0.05)` then stamps each of those words with an identical 0.05-second span. Nothing errors. The rows look ordinary, carry a real-looking `start`, and flow into every span-overlap consumer — role attribution, the TextGrid scorer, overlap classification, and any fold or repair policy that reasons about time. The harm is not the fake duration; it is that every word in the group is asserted at one instant, so near a speaker change words land on the wrong side of the boundary and inherit the wrong speaker.
- **Evidence:** Measured 2026-07-26 on five captured consult-1.2 browser runs of fixture ordinal 1 in `tests/fixtures/audio/development-corpus-0.5.0.json` (search: `"fixture_id"`), scored with `scripts/transcript-quality.py` at a 557-second cutoff against that fixture's two official TextGrids. Working evidence lives in the gitignored 0.5.2 plan tree; the measurements are recorded here because that tree is local state. 42.1-42.3% of rows carry the exact 0.05-second span in every run (130-131 of 309-310). Scoring at each row's `start` only, so the fabricated `end` cannot influence the result, and stratifying by proximity to a true turn boundary: floor-valued rows score 81.4% in clean regions and 38.7-40.0% within 250 ms of a boundary, while real-span rows score 98.7% and 94.1-100% in the same regions. Boundaries are therefore not inherently hard; only the token-starved path collapses there. Two competing explanations were tested and both fail: re-scoring at `start` alone preserves the gap (69.0% vs 98.8%), so it is not a scorer artifact, and the real-span near-boundary result refutes acoustic difficulty.
- **Prevention:** When the engine cannot place words individually, keep them together under the interval it did measure, or mark the span estimated — never synthesize a plausible number. Do not interpolate per-word times inside a starved group; `.goat-flow/learning-loop/lessons/verification.md` (search: `## Lesson: Full-clip proportional word timings drift`) records a proportional estimate driving strict attribution from 83.3% to 78.9%. Before trusting any timing-derived metric on live rows, count rows whose span equals the floor; above a few percent, treat span-based conclusions as unsafe until the mapping is fixed. A floor is a tell that evidence was missing, not that timing was recovered.
- **Relationship to the alignment-traps entry below:** the earlier trap (5) correctly established that step-clock word times are fiction, but its per-slot voiced-frame remedy was disproved by M02 Phase A on 2026-07-26. Phase B then disproved the replacement claim that stable accumulated token frames were session-relative. In the installed parallel path, cache gating advances only active speaker ASR states; `hypothesis.timestamp` therefore uses a per-speaker ASR-active-step clock. On the Phase A probe, every hypothesis had token timestamps and the voiced mapper still produced 3,058 clamps versus two interpolations. On the one-shot Phase B candidate, all 1,530 words had internally bounded observed spans, yet the stored timeline ended at 381.52 seconds while diarizer activity reached 556.88 seconds and strict row placement fell from 86.85% to 53.78%. The floor therefore hides token grouping errors and coordinate-system errors; diagnose them separately.
- **Consequence for prior decisions:** ADR-010's two-witness span-replacement policy depended on a structural witness reasoning over these spans. Its flag-on arm scored 38.7% near boundaries, identical to flag-off, and gained rows only in clean regions. Do not read that rejection as evidence the two-witness idea is unsound without first re-testing on honest timings.

## Footgun: NeMo cache-aware streaming integration has three silent alignment traps

**Status:** active | **Created:** 2026-07-06 | **Evidence:** ACTUAL_MEASURED
**Decision changed:** Resolve timestamp coordinates from the active installed NeMo call path before building a mapper; never infer them from value shape or an older probe.
**Trigger phase:** READ

- **What breaks:** Integrating `SpeakerTaggedASR` + `CacheAwareStreamingAudioBuffer` for live audio fails silently in three distinct ways that all present as "mysteriously bad transcript quality" with zero errors: (1) `append_audio`'s first-stream create branch returns `stream_id=-1`, so passing the returned id back on the next append pads a NEW stream - the batch grows per chunk until the diar streaming state throws a tensor-size mismatch; (2) `drop_extra_pre_encoded` must be `0` on step 0 and `encoder.streaming_cfg.drop_extra_pre_encoded` on every later step - passing 0 forever misaligns encoder outputs and quietly destroys recall (~30% observed); (3) timestamp values can be numerically plausible while interpreted in the wrong frame coordinate. The installed NeMo 2.8.0rc0 parallel path computes `offset_seconds + hypothesis.timestamp * 0.08`, but cache gating calls `conformer_stream_step` and updates ASR state only for active speakers. The raw timestamp is therefore local to the speaker's admitted ASR steps. A cumulative diarizer-positive-frame ledger advances too slowly and clamps; treating the raw value as session-relative omits inactive session gaps and compresses the timeline.
- **Evidence:** M22 Phase 2 GPU-contact debugging, 2026-07-06 (`.goat-flow/plans/0.3.0/M22-session-long-streaming-diarization.md`, search: "GPU-contact findings") established the first two traps and exposed timestamp ambiguity. M02 Phase A records 1,443/1,443 hypotheses with token timestamps and 3,058 clamp calls in `var/quality/0.5.2-span-fidelity/phase-a-decision.json`. M02 Phase B's sole full candidate records zero estimates but a 381.52-second final row against activity through 556.88 seconds and a strict-placement fall from 86.85% to 53.78% in `var/quality/0.5.2-span-fidelity/phase-b-candidate/candidate-verdict.json`. The installed source receipt is `var/quality/0.5.2-span-fidelity/installed-nemo-parallel-timestamp-source.txt` (search: `start_time = offset`); the installed producer path uses the semantic anchors `perform_parallel_streaming_stt_spk`, `active_speakers`, and `update_asr_state`.
- **Prevention:** Pin `stream_id = max(0, returned_id)` after the first append; mirror the reference CLI's per-step `drop_extra_pre_encoded` computation verbatim. Before touching emission timing, inspect the active installed parallel path and prove not only whether a prefix accumulates, but which events advance its clock. For this path, map local token frames through exact per-slot ASR-active-step output ranges to session output ranges. Do not use the current chunk offset for old tokens, a diarizer-positive-frame ledger, or raw frames as absolute time. Prefix stability and “inside processed audio” are necessary checks, not coordinate proofs.
- **Three more traps found at REAL-TIME pacing (2026-07-06, invisible at accelerated eval pacing):** (4) `CacheAwareStreamingAudioBuffer.__iter__` yields PARTIAL chunks near the buffer end and advances the cursor a full shift regardless - at 1x pacing a step loop drains the buffer every feed, truncating AND skipping audio four times per 5s chunk (garbled words, speaker fragmentation). Gate stepping on `frames_available >= full_chunk_frames` and consume partials only at flush. (5) Deriving word times from the decode/step clock is FICTION under decode lag: rows carry compressed times, the time-overlap scorer and role layer both read garbage. Do not replace that fiction with a voiced-frame mapper or raw token-frame clock. The current parallel path needs an ASR-active-step-to-session ledger because speaker-local ASR state pauses during cache-gated inactive steps; tokenizer-aware grouping solves cardinality, not coordinates. (6) An eager "pin the first N slots to establish" speaker cap folds a genuine voice into another slot when one speaker's audio spans two early cache slots (c03's doctor does) - fold only hallucination-scale marginal slots (share-based) and let substantial slots through; the role mapping labels them anyway.
- **Verification rule this taught:** accelerated-pacing evals CANNOT stand in for real-time behavior on streaming integrations. Any cache-aware streaming change must pass a 1x browser replay (row order, live cadence, text sanity) in addition to eval gates.
- **Update (2026-07-10, 0.4.0-slice-1 M02 diagnosis complete):** the browser contract is 5000ms,
  not 250ms (`public/js/scribe.js`, search: "PCM_CHUNK_MS = 5000"). The old 250ms/1x eval
  changed both cadence and chunk shape and caused real ASR loss; `scripts/eval-fixtures.sh`
  now supports `EVAL_PACE=1x` with the same 5000ms default and saves role/window artifacts.
  Three c03+c08 runs at a pinned 60s produced byte-identical `text,start,end,speaker_id`
  rows to M01. Against saved browser controls, free-oracle differences were 0.0pp/+1.6pp
  and clean-WER differences +1.2pp/+0.6pp, so the generic eval is decision-grade for the
  ASR/diarization lane; no recorded fixture cadence is needed.
- **Role-lane split:** c08 settled strict attribution was 59.4/84.4/59.4 while its role-free
  rows and 84.4% free oracle stayed invariant. Bad runs initially assigned `speaker_1` to
  PATIENT; a later proposal coupled that repair to an incorrect `speaker_2` flip, so whole-map
  damping suppressed it. Use median replays plus `role-timeline.jsonl` for visible-role work;
  do not select an audio fix from strict attribution alone. Browser replay remains the
  clinician-facing reference when visible role labels, rather than ASR/diarization, are the gate.
- **Long-turn hold is separate:** the full day3 consultation 01 replay under the corrected
  5000ms/1x eval reproduced windows 19-28 at `emitted_rows=0`, held rows reaching 44, and a
  25-row flush at window 29. Since the hold survives the generic schedule that closes the
  role-free browser gap, investigate the streaming emission gate separately; recorded browser
  cadence and role-agent changes do not explain it.
- **M06 bounded-release result (2026-07-14):** a repeatedly refreshed mutable tail can pin the
  global stability frontier behind rows whose wording and clock horizon are already stable. The
  default-off `NEMO_STREAMING_MAX_TRANSCRIPT_HOLD_SECONDS` policy may release only those stable,
  clock-ready rows before the next fixed five-second browser tick would overshoot the configured
  hold; it must never release mutable or future wording. The second 20-fixture 1x corpus had zero
  causal violations, at most one tick of stable-ready wait, and quality movement inside the prior
  noise envelope. Keep ordinary visits at `0` until a separate promotion decision. A tracked
  Compose fallback does not prove the effective value because protected `.env` interpolation can
  override it; after every evidence run, verify the non-secret flag with container `printenv`
  alongside health/CUDA instead of reading or inferring the local environment file.
