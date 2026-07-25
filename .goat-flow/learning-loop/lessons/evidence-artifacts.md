---
category: evidence-artifacts
last_reviewed: 2026-07-26
---

# Evidence Artifact Lessons

## Lesson: A simulated fix can only measure what the artifacts persisted

**Created:** 2026-07-26
**Decision changed:** Before planning to rehearse a fix on captured evidence, confirm the artifacts
contain the field the fix would repair. If they do not, bound the prize and say so; do not emit a
simulated result.
**Trigger phase:** SCOPE
**What happened:** A milestone was drafted to validate a streaming-timing fix offline by transforming
captured `live-history.json` rows — merging rows that shared a fabricated span and re-scoring — and
reporting the attribution gain as the fix's expected ceiling. That number would have been meaningless.
The defect corrupts each word's `start`, and true per-word starts exist only in the NeMo token
timestamps consumed inside the streaming engine and never written to the history artifact. The
transform could rearrange rows but could not move a word to the time it was actually spoken, so the
measurement would have reflected the merge heuristic rather than the repair. The milestone was
rewritten to characterize the defect and bound an upper limit instead, with an explicit instruction
not to produce a simulated-fix figure.
**Evidence:** `strands_agents/nemo_streaming_engine.py` (search: `def _appended_word_entries`) is the
code that holds the token timestamps; no field in the emitted history carries them, which is what made
the simulation underdetermined. The milestone that was rewritten and the bound that replaced it live in
the gitignored 0.5.2 plan tree, so the constraint is stated here rather than linked.
**Prevention:** Ask which field the fix writes, then grep an artifact for it. A transform over captured
evidence measures the transform unless the artifact carries the repaired quantity. When it does not,
an upper bound derived from a healthy comparison class is honest and still decision-grade — label it a
bound, not a forecast. A confident number from an underdetermined simulation is worse than no number,
because it survives into the plan as a target.

This bucket records mistakes that can corrupt an evidence bundle without changing its visible meaning.
Use it before duplicating hash-bound fixtures or sealing a write-once artifact manifest.

## Lesson: Reference byte-bound fixtures instead of text-patch copying them

**Created:** 2026-07-19
**What happened:** A manual-test evidence bundle copied two official TextGrid files through
`apply_patch`. The visible text survived, but newline normalization changed both byte sizes
and SHA-256 values. Verification rejected and removed the copies before the manifest was sealed.
**Evidence:** `var/quality/0.5.0-manual-consult53-20260718T231930Z/verification/truth-copy-normalization-failure.md`.
**Prevention:** When fixture identity is defined by bytes, keep the verified original path and
hash as the evidence reference. If a duplicate is required, use a byte-preserving approved
mechanism and verify size plus SHA-256 before any scorer or manifest consumes it.

## Lesson: Validate manifest record shape before counting or sealing

**Created:** 2026-07-19
**What happened:** A cold-campaign verifier initially counted the TSV header as an artifact record, and an
early identity draft briefly contained malformed placeholder hash lines. Both errors were caught and removed
before the new packet was sealed or any runtime action occurred, but count/hash comparison alone would not
have rejected every malformed record shape.
**Evidence:** `var/quality/0.5.0-m02-transcription-accuracy-no-game-20260719T080330Z/identity/`
`cold-worktree-and-old-seal.txt` records the draft correction, and `commands/m02-campaign-runner.sh` validates
the exact header, timestamp, SHA-256, byte count, and root-confined relative path before reading artifacts.
**Prevention:** Before counting or hashing a delimited evidence manifest, assert its exact header, skip that
header explicitly, reject zero or malformed records and path traversal, and reject non-64-hex hashes or
placeholder tokens. Only then compare duplicates, presence, sizes, hashes, and write-once permissions.

## Lesson: Evidence equivalence needs semantic fields and authoritative bounds

**Created:** 2026-07-25
**Decision changed:** Define comparison fields and the authoritative measurement boundary before running a
promotion verifier; preserve a failed raw result when correcting only verifier semantics.
**Trigger phase:** VERIFY
**What happened:** The recovery-only replacement replay produced corrected and live segment arrays exactly
equal to the prior accepted full flag-off replay. Its first verifier nevertheless rejected the final
corrected row because it
used the artifact's rounded `duration_seconds=430.1` instead of the frozen input clip's 432.24-second
boundary; the row ended at 430.13 seconds. A second comparison then reported different scorer findings only
because each otherwise identical finding embedded its run-specific `artifact_path`. Both failed results
were preserved, adjudicated, and corrected without another GPU call.
**Evidence:** `var/quality/rediar-m05-acceptance/diagnostics/2026-07-25_d2c09-d3-recovery-only-replay1/diagnostic-verification-attempt1-adjudication.json`
(search: `"frozen_input_duration_seconds": 432.24`) and
`var/quality/rediar-m05-acceptance/diagnostics/2026-07-25_d2c09-d3-recovery-only-replay1/source-chip-findings-adjudication-attempt2.json`
(search: `"raw_difference_class": "artifact_path_only"`).
**Prevention:** Use the frozen input duration for clip-containment gates and artifact-declared duration only
for the contract it actually owns. Compare semantic payload fields after excluding an explicit allowlist of
transport metadata such as run-root paths; never normalize row identity, role, timing, text, severity, or
evidence fields. Preserve the raw failed comparison and record why the corrected verifier added no runtime
spend.

## Lesson: Cross-map only evidence available in each frozen lane

**Created:** 2026-07-25
**Decision changed:** Separate candidate-on truth classification from cross-arm candidate causality; require
a historical comparison lane only for claims that actually depend on it.
**Trigger phase:** VERIFY
**What happened:** The first corpus-quality disposition build tried to load a corpus-off corrected transcript
for every source-chip alert and stopped on d2c09. That transcript is intentionally absent and already recorded as a historical
legacy gap. The d2c09 alerts in scope were independently truth-aligned candidate-on heuristic false
positives, so clearing those heuristic alerts did not require an off-lane row. The corrected adjudicator
records the baseline as unavailable: candidate-on truth may clear a false positive, while any role-error or
unscored causality claim without a comparison remains unverified. The failed build stopped before creating
the evidence root.
**Evidence:** `scripts/verify-rediarization-corpus-quality-disposition.py` (search:
`def classify_source_finding`) separates truth status from candidate causality, and
`tests/python/test_rediarization_corpus_quality_disposition_verifier.py` (search:
`test_truth_aligned_alert_does_not_require_historical_off_lane`) pins the known-missing-lane case.
**Prevention:** Before cross-mapping alerts, identify which fields prove truth status and which prove
candidate causality. Load only available frozen lanes, encode missing comparison evidence explicitly, and
never turn an already-declared historical absence into a prerequisite for an independent truth
classification.

## Lesson: CPU config fakes must preserve runtime-only value types

**Created:** 2026-07-19
**Incident count:** 2
**Latest occurrence:** 2026-07-24
**Trigger phase:** VERIFY
**Decision changed:** Put decoder-config contracts behind the established CPU framework shim, and exercise the
real framework only in the pinned runtime.
**What happened:** The M02 phrase contracts returned only primitive values from their fake
`OmegaConf.to_container`, so 77 CPU tests passed. The pinned NeMo runtime instead retained a nested
`BlankLMScoreMode` enum in the effective decoder mapping. Slot 1 completed its audio inference, then failed
while JSON-serializing that required evidence; the nonzero evaluator exit left the slot unscoreable and ended
the write-once campaign. On 2026-07-24, a new decoder-helper contract directly imported `omegaconf` even
though the CPU test venv intentionally omits it; the focused gate stopped after 101 passes. The assertion was
moved into the existing phrase-accuracy fixture that supplies the repository's CPU-only OmegaConf shim.
**Evidence:** `tests/python/test_post_visit_phrase_accuracy.py` (search: "def _plain_value") returns unknown
objects unchanged; `strands_agents/post_visit_correction.py` (search: "def _effective_post_visit_decoding_config")
accepts any dictionary returned by OmegaConf; and `scripts/second_pass_asr.py` (search: "def write_json") sends
that dictionary directly to `json.dumps`.
**Prevention:** For evidence produced from framework configuration, add a CPU contract containing the
runtime's enum/object value shapes and prove the exact final serializer. Reuse an established CPU framework
shim instead of importing a runtime-only dependency into a unit-test lane, then run a pinned-runtime
serialization-only probe before spending the first write-once audio slot. A mapping type check alone does not
prove that every nested value is JSON-safe.

## Lesson: Console log session filters can erase source-selection evidence

**Created:** 2026-07-19
**What happened:** A manual-test capture first filtered NeMo logs by the consultation UUID. That retained
fidelity events whose formatted message includes `session_id`, but silently removed `summary.requested` and
`summary.completed`: their UUID exists only in structured `extra`, which the local console formatter drops.
The incomplete capture was caught during artifact reread and replaced before its manifest was sealed.
**Evidence:** `strands_agents/api/server.py` (search: `"summary.requested source=%s`) and
`strands_agents/api/server.py` (search: `"summary.completed source=%s`) put `session_id` in `extra` but not in
the formatted message; `strands_agents/api/summary_generation.py` (search:
`"summary.fidelity_draft_selected session_id=%s`) includes it in both.
**Prevention:** For console-log evidence, filter a bounded session time window by required event names rather
than UUID alone, then assert that source selection, completion, and any failure/citation events are present or
explicitly absent before sealing. Prefer structured JSON logs for named QA captures when available.

## Lesson: Test the mutation-to-bookkeeping gap in artifact transactions

**Created:** 2026-07-20
**What happened:** M04C's first fail-closed pass handled ordinary output conflicts and one-case catalog
merges, but review found two negative-space gaps. A valid selector plus a typo still ignored the typo, and an
interrupt after `os.link` created a final but before the path was appended could evade rollback. Both were
caught before the milestone gate and frozen in `tests/python/test_development_corpus.py` (search:
`test_mixed_valid_and_mistyped_selectors_stop_before_generation`) and
`tests/python/test_clinical_data_audit_acceptance.py` (search:
`test_output_pair_rolls_back_an_interrupted_post_link_window`).
**Prevention:** For multi-artifact or selected-refresh transactions, prove that every requested identity
matched before the first mutation. Inject failure immediately after the kernel mutation and before local
bookkeeping, then recover ownership from durable identity such as the staged inode rather than only an
in-memory success list.
