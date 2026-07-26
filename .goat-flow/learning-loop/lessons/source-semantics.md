---
category: source-semantics
last_reviewed: 2026-07-26
---

# Source-semantics lessons

## Lesson: An accumulated timestamp prefix can still use an instance-local clock

**Created:** 2026-07-26
**Decision changed:** Do not call a stable accumulated timestamp session-relative until the producer's skip/advance behavior is traced and a reference-timeline placement gate passes.
**Trigger phase:** VERIFY
**Incident count:** 4
**Latest occurrence:** 2026-07-26

**What happened:** M02 Phase A initially carried forward the claim that
`previous_hypothesis.timestamp` counted per-speaker voiced frames. The probe
therefore compared raw token values with a cumulative voiced-frame ledger and
the plan marked native shape inspection complete. The approved full replay
reported 3,058 post-ledger clamps versus two interpolations even though all
1,443 hypotheses carried token timestamps. Reading the installed NeMo 2.8.0rc0
parallel display helper showed its formula:
`offset_seconds + hypothesis.timestamp * 0.08`, but that helper alone did not
prove the upstream object's coordinate system. The separately approved
stability probe showed the raw prefix stable in 103/103 comparisons while the
offset-shifted prefix changed in 103/103. The agent then made a second,
different inference error: because the hypothesis was accumulated and every
first-seen raw frame fell inside processed audio, it labeled the raw clock
session-relative.

The one-shot Phase B candidate falsified that label. Its provenance closed with
all 1,530 words observed and bounded, but the stored timeline ended at 381.52
seconds while diarizer activity reached 556.88 seconds; strict non-overlap row
placement fell from 86.85% to 53.78%. Re-reading the installed producer, not
only its display consumer, showed the missing condition:
`perform_parallel_streaming_stt_spk` calls `conformer_stream_step` and
`update_asr_state` only for cache-gated `active_speakers`. An inactive
speaker's accumulated hypothesis is retained without advancing. The timestamp
is therefore accumulated on a per-speaker ASR-active-step clock, not the
session clock.

The first ASR-active-step replacement then selected the wrong adjacent field:
`Hypothesis.length`. Its bounded probe recorded only one 14-frame interval per
slot, classified all 166 words as estimates, and caught a final length
regression. The configured label-looping decoder does offset each continuation
by cumulative `decoded_lengths`, and `Hypothesis.merge_` initially adds the
continuation length, but the later `pack_hypotheses()` call overwrites
`Hypothesis.length` with the current chunk length. The cumulative value that
survives the producer call is `hypothesis.dec_state.decoded_length`; its
post-step delta identifies the local frame range that actually advanced. The
adjacent serial helper's count normalization remains valid for grouping; it
never proved coordinate identity.

The corrected decoded-state probe then disproved another conversion
assumption. `decoded_length` is a cumulative count of frames advanced by the
slot, but the implementation projected each positive delta densely from the
session step start. Runtime provenance closed for all 166 words with zero final
estimates while strict placement fell from 100% to 82.35%; non-overlap rows 0,
11, and 18 were misplaced, including a span from 31.76 to 52.72 seconds across
a long inactive gap. A cumulative count plus a step boundary proves
cardinality, not the absolute within-step position of each frame.

**Evidence:** the sealed rejected implementation patch
`var/quality/0.5.2-span-fidelity/m02-active-step-candidate.patch` (search:
`def _word_timing_candidates`),
`var/quality/0.5.2-span-fidelity/phase-a-decision.json` (search:
`accumulated_hypothesis_semantics`),
`var/quality/0.5.2-corpus-validation/fresh-baseline-summary.json` (search:
`c03_repeatability`), and
`var/quality/0.5.2-span-fidelity/phase-b-candidate/candidate-verdict.json`
(search: `m01_timestamp_placement_regressed`), plus
`var/quality/0.5.2-span-fidelity/phase-b-active-step-short/` (search:
`asr_active_step_length_regression`), and
`var/quality/0.5.2-span-fidelity/phase-b-active-step-decoded-short/` (search:
`candidate_coordinate_projection_falsified`). The installed NeMo semantic
anchors are `perform_parallel_streaming_stt_spk`, `active_speakers`,
`update_asr_state`, `rnnt_label_looping.py` (search:
`fix timestamps for iterative decoding`), `Hypothesis.merge_`,
`pack_hypotheses`, and
`update_sessionwise_seglsts_for_parallel`.

**Prevention:** Before encoding timing units or coordinates, trace from the
configured entry point through both the producer's state lifecycle and the
consumer helper. Record whether an object is accumulated or chunk-local and
which inference events advance or pause its clock. Trace post-decoder packing
and state-copy operations too: a field can be cumulative mid-call and be
rewritten before the caller observes it. Compare the same prefix before and
after every proposed conversion, but also compare projected end times against
independent activity endpoints and run the reference-timeline placement
scorer. “Inside processed audio” only proves a bound; it does not prove the
same coordinate. Test count normalization separately on a fixture that
exercises duplicates. A runtime mapper must fail closed when a token frame
cannot be joined to an exact producer interval, and no timing arm may be
promoted on provenance accounting alone. Treat decoded-state deltas as
advance/count evidence only: do not construct a measured wall-clock interval
from `session_start + delta` unless the producer supplies the corresponding
absolute output-frame offset or masked encoder-frame indices.
