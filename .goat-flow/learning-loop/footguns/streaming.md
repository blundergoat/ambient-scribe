---
category: streaming
last_reviewed: 2026-07-14
---

# Streaming Footguns

## Footgun: NeMo cache-aware streaming integration has three silent alignment traps

**Status:** active | **Created:** 2026-07-06 | **Evidence:** ACTUAL_MEASURED

- **What breaks:** Integrating `SpeakerTaggedASR` + `CacheAwareStreamingAudioBuffer` for live audio (M22 streaming engine) fails silently in three distinct ways that all present as "mysteriously bad transcript quality" with zero errors: (1) `append_audio`'s first-stream create branch returns `stream_id=-1`, so passing the returned id back on the next append pads a NEW stream - the batch grows per chunk until the diar streaming state throws a tensor-size mismatch; (2) `drop_extra_pre_encoded` must be `0` on step 0 and `encoder.streaming_cfg.drop_extra_pre_encoded` on every later step - passing 0 forever misaligns encoder outputs and quietly destroys recall (~30% observed); (3) per-instance hypothesis `timestamp` frames count that speaker's own voiced/decoded frames, NOT session time - `offset + ts*0.08` produces row times beyond the audio, and downstream cutoff filters then silently drop the rows.
- **Evidence:** M22 Phase 2 GPU-contact debugging, 2026-07-06 (`.goat-flow/plans/0.3.0/M22-session-long-streaming-diarization.md`, search: "GPU-contact findings"). Per-step instrumentation dump proved cumulative slot texts with frozen bursts and voiced-frame timestamps.
- **Prevention:** Pin `stream_id = max(0, returned_id)` after the first append; mirror the reference CLI's per-step `drop_extra_pre_encoded` computation verbatim. When streaming quality looks wrong with clean logs, instrument per-step hypothesis facts (slot, n_words, head/tail, ts0/tsN, offset) before touching emission logic.
- **Three more traps found at REAL-TIME pacing (2026-07-06, invisible at accelerated eval pacing):** (4) `CacheAwareStreamingAudioBuffer.__iter__` yields PARTIAL chunks near the buffer end and advances the cursor a full shift regardless - at 1x pacing a step loop drains the buffer every feed, truncating AND skipping audio four times per 5s chunk (garbled words, speaker fragmentation). Gate stepping on `frames_available >= full_chunk_frames` and consume partials only at flush. (5) Deriving word times from the decode/step clock is FICTION under decode lag: rows carry compressed times, the time-overlap scorer and the role layer both read garbage, and eval "attribution" numbers become unmeasurable. True times come from inverting the diarizer's own activity stream: per-slot (cumulative voiced frames -> wall seconds) ledger, then map each token timestamp through it. (6) An eager "pin the first N slots to establish" speaker cap folds a genuine voice into another slot when one speaker's audio spans two early cache slots (c03's doctor does) - fold only hallucination-scale marginal slots (share-based) and let substantial slots through; the role mapping labels them anyway.
- **Verification rule this taught:** accelerated-pacing evals CANNOT stand in for real-time behavior on streaming integrations. Any cache-aware streaming change must pass a 1x browser replay (row order, live cadence, text sanity) in addition to eval gates.
- **Update (2026-07-10, 0.4.0 M02 diagnosis complete):** the browser contract is 5000ms,
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
