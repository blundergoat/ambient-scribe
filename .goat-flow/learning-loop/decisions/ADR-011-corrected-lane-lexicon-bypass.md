# ADR-011: The corrected lane bypasses the medical lexicon; note safety rests on the review-reason lane

**Date:** 2026-07-15
**Status:** Accepted (M04 lane question, resolved by decisive trace)

## Context

M04 (harden clinical-term safety) adds lexicon canonical/variant rows for the consult-1.2
medication terms. The two parent analyses disputed whether post-visit corrected rows receive the same
`correct_medical_terms` normalization as live rows — one asserted a bypass, the other treated
it as unvalidated. M04 required a decisive trace before any lexicon row is added, because the
answer decides what a new lexicon entry actually fixes.

## Trace (decisive)

- `strands_agents/nemo_pipeline.py` (search: "def visible_text") is the ONLY lexicon seam:
  "Both emission paths (windowed and streaming engine) must route text through this seam." It
  applies `correct_medical_terms` when `MEDICAL_BOOST_ENABLED` and phrases are loaded.
- `strands_agents/post_visit_correction.py` contains ZERO references to `medical_lexicon`,
  `correct_medical_terms`, `nemo_pipeline`, or `visible_text` (repo grep, 2026-07-15).
- The correction lane's transcription is self-contained: `run_post_visit_correction` →
  `transcriber = transcribe_audio_file or transcribe_audio_with_nemo` (search: "transcriber =") →
  `transcribe_audio_with_nemo` loads its own restored model via
  `nemo_asr.models.ASRModel.from_pretrained` (search: "_load_post_visit_asr_model") and
  recombines chunk text with no normalization step before rows are built.

**Conclusion: post-visit corrected rows NEVER receive lexicon normalization.** The bypass
assertion was correct.

## Decision

1. New lexicon canonical/variant rows added by M04 fix LIVE transcript text only. That is
   accepted and documented behavior, not a defect to wire around silently.
2. Runtime note safety for non-canonical clinical terms in the corrected lane rests entirely on
   the `source_low_confidence` review-reason route M04 builds (corrected-lane confidence < 0.78
   plus relevant-row linkage → machine-readable review reason consumed by M05/M06).
3. Wiring `visible_text`/lexicon normalization into the correction lane is NOT done here: it
   would change corrected-row wording produced from retained audio, touches the
   `nemo_pipeline.py` Ask First boundary, and would need its own approval, fixtures, and
   regression evidence (corrected-row hashes bind to M02 attestations — silent wording changes
   would also churn correction-output identity).

## Consequences

- A term like `Fexaphenidine` can appear verbatim in the corrected transcript even after its
  variant row exists; the note-side guarantee is the review reason, never silent rewriting.
- If a future milestone wants corrected-lane normalization, it must present the wording-change
  blast radius (row identity hashes, stored artifacts, existing fixtures) at the Ask First gate.
