---
category: evidence-artifacts
last_reviewed: 2026-07-30
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

## Lesson: Preserve partial order when aligning parallel transcript channels

**Created:** 2026-07-26
**Decision changed:** Align each role's ordered reference channel independently against the display
stream, then reject cross-role conflicts; never discard a whole reference interval or physically
coalesce rows merely because timing makes their global order uncertain.
**Trigger phase:** VERIFY
**What happened:** M01's first timestamp-independent evaluator marked every word in any Doctor or
Patient interval touching opposite-role speech as order-ambiguous. That excluded 1,282 of 1,741
reference words and left only 22.2% display-word coverage. Its structural-null implementation also
physically merged same-slot/same-start rows; four groups were non-contiguous in display order, so
the transform moved words and changed the ownership result. Independent forced alignment within
each role, followed by a cross-role conflict filter, raised coverage to 81.6% while leaving
ambiguous words unscored. Recasting the structural null as row-count arithmetic preserved the
original word stream and restored the planned 22-versus-17 accounting. A final reread also found
that a repeated display word ambiguous in both role channels was safely unscored but mislabeled as
within-role ambiguity; the classifier and regression now report that uncertainty as cross-role.
**Evidence:** `scripts/transcript_alignment.py` (search: `def align_role_channels`) preserves each
channel's known order and rejects cross-role candidates;
`scripts/streaming-timing-diagnostics.py` (search: `def _structural_nulls`) labels the null as a
row-count projection and hashes the unchanged word stream.
`tests/python/test_transcript_alignment.py` (search:
`test_role_channels_accept_interleaving_without_fabricating_reference_order`) and
`tests/python/test_transcript_alignment.py` (search:
`test_role_channels_classify_ambiguity_in_both_roles_as_cross_role`) plus
`tests/python/test_streaming_timing_diagnostics.py` (search:
`test_structural_nulls_distinguish_real_rows_and_preserve_ownership`) pin these corrections.
**Prevention:** Treat parallel speaker channels as a partial-order problem. Prove coverage and
timestamp invariance on one real capture before a corpus pass, reject only lexical candidates whose
ownership is actually ambiguous, and keep a structural null word-preserving by definition instead
of implementing it as a transcript rewrite.

This bucket records mistakes that can corrupt an evidence bundle without changing its visible meaning.
Use it before duplicating hash-bound fixtures or sealing a write-once artifact manifest.

## Lesson: Isolate operational rollback patches from mutable evidence

**Created:** 2026-07-26
**Decision changed:** Seal candidate behavior and append-only evidence as
separate artifacts against an explicit accepted base, then execute every
nested identity check before a one-shot attempt sentinel; never use a full
dirty worktree diff as an operational rollback.
**Trigger phase:** VERIFY
**Incident count:** 4
**Latest occurrence:** 2026-07-27

**What happened:** M02 sealed one full-worktree patch that combined accepted
diagnostic tooling, two successive timing candidates, generated indexes, and
learning text. After the decoded-state probe was rejected and its lesson was
updated, the exact reverse check stopped applying. A narrower reversal of only
the current unstaged behavior was also unsafe because its target was the
already-rejected raw-frame candidate rather than the accepted pre-M02 runtime.
The preflight caught both conditions and no rollback ran.

The approved replacement validated the prevention:
`m02-runtime-rollback.patch` covered only eight behavior/direct-contract
paths, preserved the index receipt and four M01 evaluator hashes, restored
every path to its accepted base blob, and recreated a healthy
host/container-matched service.

The later index-only reconciliation exposed a second mismatch. Four staged
paths held earlier rejected-candidate blobs rather than the sealed runtime
patch postimages, so a cached reverse-check failed before mutation. A proposed
readable recovery patch was then passed through the required durable-text
scrubber; it replaced an environment-looking source expression and no longer
applied. The invalid artifact was removed. The real index remained untouched
until a mode/blob/path receipt recreated the original target receipt in a
temporary index.

M05 exposed the same identity mistake from the evidence-consumer side. Its
historical live campaign and all 207 artifact hashes still verified, but the
campaign's three sealed runtime hashes belonged to the temporary M02
diagnostic implementation that was later rejected and rolled back. Treating
its 29.509147% clean WER as the current-runtime baseline would have compared
future candidates across different sources. The Phase A preflight stopped on
the hash mismatch, retained that campaign only as a historical comparator,
and required Phase B's same-run live lanes to establish the accepted rollback
runtime beside their corrected lanes.

The approved M05 Phase B attempt exposed the composed-contract form of the
same mistake. Phase A added a clinical identity helper to the scorer and froze
that edited SHA-256 in its metric contract, while the simultaneously pinned
development manifest still required the scorer's pre-edit size and SHA-256.
The selected tests passed because they omitted the real-manifest unit test and
the shell smoke deliberately substituted a corpus-helper stub. The one-shot
runner verified its reverse patch but wrote the attempt sentinel before the
restoration wrapper executed the nested scorer-manifest check. The wrapper
then rejected `scorer_size_drift` before fixture 1, restored all ten stereo
fixtures, and left zero replay or correction artifacts; the approved contract
still forbade a retry once the sentinel existed.

**Evidence:** `var/quality/0.5.2-span-fidelity/m02-active-step-candidate.patch`
(search: `diff --git`) includes source, tests, generated indexes, and learning
files. `.goat-flow/learning-loop/lessons/source-semantics.md` (search:
`corrected decoded-state probe`) records the runtime falsification that made
the rollback preflight necessary. The completed operational proof is
`var/quality/0.5.2-span-fidelity/m02-runtime-rollback-receipt.json` (search:
`index_preservation`). The index-only course correction is sealed in
`var/quality/0.5.2-span-fidelity/m02-index-reconciliation-receipt.json`
(search: `course_correction`). The later baseline reconciliation is sealed in
`var/quality/0.5.2-asr-accuracy/m05-baseline/metric-contract.json`
(search: `historical_comparator_source_sha256`) and binds the accepted hashes
to the same rollback receipt. The terminal composed-contract failure is
`var/quality/0.5.2-asr-accuracy/m05-baseline/phase-b/phase-b-failure-report.md`
(search: `The frozen contracts were internally incompatible`).

**Prevention:** Before implementation, record an explicit base tree or base
blob hash for every behavior file. Seal separate patches for candidate
behavior and append-only evidence/docs. Re-run the behavior patch's forward
and reverse checks after each implementation edit, but do not include mutable
indexes, plans, lessons, or evidence in the operational rollback. Before
applying any rollback, inspect the resulting source identity and prove it is an
accepted baseline, not merely the current index or previous candidate. Before
reconciling a dirty index, compare every staged blob with the rollback patch
postimage. If candidate generations differ, record mode, blob, and path entries
and prove their restoration in a temporary index. Do not treat a scrubbed
source patch as byte-faithful recovery evidence; reject it when its bytes or
apply-check change. When reusing campaign evidence, compare its sealed runtime
hashes with the accepted current runtime before calling it a baseline. A
verified mismatch does not erase the old measurement: relabel it as a
historical comparator and establish a matched current-runtime baseline before
candidate selection. Before sealing a one-shot runner, execute every nested
CPU-only authorization check against the exact proposed worktree, including
tracked-manifest tests that a stubbed smoke cannot cover. A reverse patch and
two individually valid hashes do not prove their composed contracts agree.
Write the attempt sentinel only after all deterministic nested preflights have
passed, unless the approved policy explicitly intends preflight rejection to
spend the attempt.

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
**Incident count:** 3
**Latest occurrence:** 2026-07-30
**Decision changed:** Separate candidate-on truth classification from cross-arm
candidate causality, and adjudicate heuristic review findings before freezing
any raw-count correctness gate.
**Trigger phase:** VERIFY
**What happened:** The first corpus-quality disposition build tried to load a corpus-off corrected transcript
for every source-chip alert and stopped on d2c09. That transcript is intentionally absent and already recorded as a historical
legacy gap. The d2c09 alerts in scope were independently truth-aligned candidate-on heuristic false
positives, so clearing those heuristic alerts did not require an off-lane row. The corrected adjudicator
records the baseline as unavailable: candidate-on truth may clear a false positive, while any role-error or
unscored causality claim without a comparison remains unverified. The failed build stopped before creating
the evidence root.

On 2026-07-28, the M05 replacement contract froze the raw source-chip finding
maximum at zero even though this lesson already recorded truth-aligned lexical
false positives. The sole approved campaign completed all ten replays and ten
correction requests, then terminally failed on 28 findings. Of the 28 current
findings, 27 exactly match historical fixture, segment, code, text-hash,
visible-role, and timing payloads: 21 were already adjudicated as lexical
heuristic false positives, three as confirmed role errors, and three as
reference gaps. One is new and unadjudicated. The approved zero gate was
correctly enforced and must not be waived after results; the mistake was
freezing absence of heuristic alerts as if it were equivalent to absence of
adjudicated role errors.

On 2026-07-30, the first M05 disposition evaluator applied the supported
Doctor/Patient-role prerequisite to every corrected row before selecting the
source-chip findings. Historical corpus-off therefore stopped on an unresolved
non-finding row even though that row was not evidence for any requested
classification. The evaluator was corrected to require complete row-index,
role, and timing agreement across the lane while applying supported-role and
truth-class requirements only to the flagged findings. Its synthetic fixture
now includes an aligned unresolved non-finding row.
**Evidence:** `scripts/verify-rediarization-corpus-quality-disposition.py` (search:
`def classify_source_finding`) separates truth status from candidate causality, and
`tests/python/test_rediarization_corpus_quality_disposition_verifier.py` (search:
`test_truth_aligned_alert_does_not_require_historical_off_lane`) pins the known-missing-lane case.

`var/quality/0.5.2-asr-accuracy/m05-baseline/replacement-campaign/terminal-failure-summary.json`
(search: `diagnostic_cross_map`) records the text-free recurrence evidence, and
`var/quality/rediar-m05-acceptance/arms/corpus-on/source-chip-findings-adjudication.json`
(search: `classifications`) records the prior 22/3/4 truth split.
`scripts/m05-source-chip-disposition.py` (search: `def _validate_aligned_rows`)
now separates whole-lane identity checks from finding classification, and
`tests/python/test_m05_source_chip_disposition.py` (search:
`"role": "UNRESOLVED"`) pins the non-finding case.
**Prevention:** Before cross-mapping alerts, identify which fields prove truth status and which prove
candidate causality. Load only available frozen lanes, encode missing comparison evidence explicitly, and
never turn an already-declared historical absence into a prerequisite for an independent truth
classification.

Before freezing a heuristic finding maximum, cross-map stable identities,
define which adjudicated truth classes are blockers, and preserve unknowns as
review work rather than silently equating severity with ground truth. If a raw
zero threshold is already approved, enforce it and preserve the terminal
failure; seek a new pre-results contract through a separate plan instead of
waiving the gate post hoc. Validate full-lane alignment with the broadest
values the lane contract permits, then apply narrower classification
prerequisites only to rows that actually participate in that classification.

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

## Lesson: Keep shell tracing out of machine-readable campaign logs

**Created:** 2026-07-26
**Decision changed:** Never launch a write-once campaign under `bash -x` unless
xtrace has a dedicated file descriptor; monitor files must accept only records
that match their declared schema, and live guards must read the latest valid
record rather than the last physical line.
**Trigger phase:** ACT
**Incident count:** 1
**Latest occurrence:** 2026-07-26

**What happened:** Three detached launch attempts exited before the campaign
body, so `bash -x` was used to diagnose the launch. That diagnostic invocation
unexpectedly became the one successful 16-session baseline run. Bash tracing
propagated into background subshells; the GPU and watchdog blocks redirected
stderr with stdout, so `+ docker ...` and `+ sleep 10` lines were mixed with
CSV/TSV samples. The watchdog's `tail -n 1` GPU check could therefore parse a
trace line as zero and weaken its live memory threshold. The campaign itself
remained valid: direct GPU polling stayed below the limit, health/container/
suspend/fatal guards remained active, the final peak calculation still saw the
real numeric field, and post-run validation extracted 944 timestamped GPU rows
and 952 numeric watchdog rows. The raw traced files were retained and sealed
rather than silently cleaned.

**Evidence:** `var/quality/0.5.2-corpus-validation/fresh-baseline/monitors/`
(search the GPU and watchdog files for `+ sleep 10`) and
`var/quality/0.5.2-corpus-validation/fresh-baseline-summary.json` (search:
`real_sample_count`).

**Prevention:** Debug launch/preflight separately from the write-once payload.
If tracing is essential, set `BASH_XTRACEFD` to a dedicated trace file before
starting any redirected monitor. Give stdout records an exact JSON/CSV/TSV
shape, route command diagnostics elsewhere, and make both live and final
validators reject or skip nonconforming lines explicitly. Select the latest
valid GPU record, not `tail -n 1`, and pin that behavior with a shell smoke test
before a long campaign.

## Lesson: Optional diagnostics must not create runtime prerequisites

**Created:** 2026-07-26
**Decision changed:** Gate optional diagnostic calculations on the evidence they
observe, and tolerate intentionally partial CPU test engines at that boundary.
**Trigger phase:** VERIFY
**Incident count:** 1
**Latest occurrence:** 2026-07-26

**What happened:** Phase A timing diagnostics calculated the processed-audio
boundary before checking whether a hypothesis supplied compatible token
timestamps. Four word-confidence tests construct a deliberately partial
`StreamingSessionEngine` with `object.__new__`; those tests do not need diarizer
state, so the new observer raised `AttributeError` for `_diar_frames_seen`.
Focused timing tests passed, but the full Python suite caught the expanded
runtime prerequisite. Production engines had the field and the completed
baseline campaign was unaffected.

**Evidence:** the sealed rejected implementation patch
`var/quality/0.5.2-span-fidelity/m02-active-step-candidate.patch` (search:
`diagnostic_frame_len_sec`) records the optional diagnostic state with total
defaults, and `tests/python/test_word_confidence_persistence.py` (search:
`make_bookkeeping_engine`) preserves the partial-engine contract.

**Prevention:** A diagnostics-only observer must not require state that the
underlying behavior path does not require. Compute only after confirming the
relevant evidence exists, or use explicit total defaults at the diagnostic
boundary. Run the full CPU suite after adding observers, even when their focused
contracts pass, because lightweight test doubles reveal accidental coupling to
production initialization.

## Lesson: Project fields before searching minified clinical JSON

**Created:** 2026-07-29
**Decision changed:** Inspect structured clinical evidence with an explicit
field whitelist; never run broad content searches across one-line transcript
JSON.
**Trigger phase:** READ
**Incident count:** 1
**Latest occurrence:** 2026-07-29

**What happened:** During the M05 post-failure investigation, a read-only search
across a broad quality-artifact root matched a minified corrected-transcript
JSON record. Because the file was one line, the tool expanded the entire record
into its output even though only stable identity fields were needed. Nothing
was written, and the investigation immediately switched to sanitized
projections and hashes, but the read exceeded the intended evidence boundary.

**Evidence:** `.goat-flow/architecture.md` (search: `Local artifacts contain only`)
restricts durable evidence to minimal, non-clinical content. The M05 recovery
investigation required only fixture identity, segment identity, role, timing,
classification, and text hash.

**Prevention:** Use `rg --files` to identify candidate JSON files, inspect their
schema, and then use `jq` to whitelist non-clinical fields before searching or
comparing values. Prefer hashes for text identity. Do not run broad `rg`
content searches over minified clinical artifacts; when a textual search is
unavoidable, constrain it to known non-clinical files or an exact bounded
projection.

## Lesson: Run relative checksum manifests from their declared root

**Created:** 2026-07-30
**Decision changed:** Inspect a checksum file's record paths before invoking
`sha256sum -c`, then run it from the root those paths declare; do not assume
that an adjacent checksum file is relative to its own directory.
**Trigger phase:** VERIFY
**Incident count:** 1
**Latest occurrence:** 2026-07-30

**What happened:** The final M05 disposition evidence recheck first ran from
the campaign directory. Both checksum files contain repository-root-relative
records, so `sha256sum` reported the sealed files as unreadable even though
their bytes had not changed. Rerunning from the workspace root verified all 83
manifest records and the terminal summary.

**Evidence:**
`var/quality/0.5.2-asr-accuracy/m05-baseline/source-chip-disposition-campaign/terminal-evidence-manifest.sha256`
and the adjacent `terminal-failure-summary.sha256` retain their root-relative
record paths and pass from the controlling workspace root.

**Prevention:** Read one record before verification, resolve its base against
the artifact contract, and set the command working directory explicitly.
Classify `FAILED open or read` as a path-resolution failure, not a digest
mismatch; preserve the manifest and correct only the invocation.
