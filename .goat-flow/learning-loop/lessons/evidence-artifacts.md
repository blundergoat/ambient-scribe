---
category: evidence-artifacts
last_reviewed: 2026-07-19
---

# Evidence Artifact Lessons

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

## Lesson: CPU config fakes must preserve runtime-only value types

**Created:** 2026-07-19
**What happened:** The M02 phrase contracts returned only primitive values from their fake
`OmegaConf.to_container`, so 77 CPU tests passed. The pinned NeMo runtime instead retained a nested
`BlankLMScoreMode` enum in the effective decoder mapping. Slot 1 completed its audio inference, then failed
while JSON-serializing that required evidence; the nonzero evaluator exit left the slot unscoreable and ended
the write-once campaign.
**Evidence:** `tests/python/test_post_visit_phrase_accuracy.py` (search: "def _plain_value") returns unknown
objects unchanged; `strands_agents/post_visit_correction.py` (search: "def _effective_post_visit_decoding_config")
accepts any dictionary returned by OmegaConf; and `scripts/second_pass_asr.py` (search: "def write_json") sends
that dictionary directly to `json.dumps`.
**Prevention:** For evidence produced from framework configuration, add a CPU contract containing the
runtime's enum/object value shapes and prove the exact final serializer, then run a pinned-runtime
serialization-only probe before spending the first write-once audio slot. A mapping type check alone does not
prove that every nested value is JSON-safe.
