# ADR-010: Two-Witness Span Replacement for the Corrected Transcript

**Status:** Rejected (M05 corpus verdict; human-approved 2026-07-25)
**Date:** 2026-07-21
**Resolved:** 2026-07-25
**Ticket/Context:** post-stop-rediarization M03-M05E

## Context

ADR-009 closed time-local fold rules; the fallback plan rebuilds speaker
structure from full retained audio at correction time. Spikes proved the
rebuild corrects every deterministic harmful fold but wholesale replacement
regresses clean visits, the plain role agent mislabels a pure slot on one
control fixture, and rebuilt slots can be locally impure. The replacement
policy therefore cannot trust any single witness.

## Decision

Reject the two-witness span-replacement policy as a promotable correction
policy. The M05 corpus gate confirmed substantive candidate regressions, so
the candidate line ends without threshold tuning, a default flip, or a
supplemental comparison. `NEMO_CORRECTION_REDIARIZATION` remains 0 in the
restored operator state. The experimental implementation remains default-off
pending any separately scoped removal decision; its presence is not
acceptance or release authorization.

M05B's `PASS_HYBRID_FLAG_OFF` and M05C's `PASS_CORPUS_ON_EXECUTION` remain
limited execution receipts. They do not override M05D's human-approved
`REJECT_CANDIDATE_KEEP_FLAG_OFF` quality disposition.

## Evaluated policy

Correction-time replacement operates only at fold-suspect spans (the spans the
live session itself folded into another chip) and changes only row-level roles
through the existing row_exceptions lane. A span is replaced only when two
independent, product-available witnesses agree, under frozen thresholds:

1. **Structural witness:** the dominant rebuilt slot at the span links, by
   clean-time overlap (excluding all fold-suspect spans), to a live chip other
   than the fold target. Linkage needs >= 3.0s overlap and a >= 2.0x lead over
   the runner-up chip; row-to-span matching uses a 0.25s margin.
2. **Wording witness:** the role agent's label for that rebuilt voice
   (span-local rows first, slot-wide otherwise) equals the linked chip's own
   settled live role.

The replacement role is the linked live chip's settled role - never a model
label directly, never reference truth. Outcomes: same-voice linkage, absent or
insufficient or ambiguous linkage, witness absence or disagreement, and
already-agreeing roles all keep the live transcript untouched; a linked chip
without a settled role routes the span to the review lane as UNKNOWN.

## Measured basis (offline, frozen specimens)

- Clean control (c03-83, six benign churn folds): six keep-live decisions,
  zero row exceptions - the two worsenings of the pre-witness draft are
  eliminated by the wording witness, whose label is locally right exactly
  where the structural link is locally wrong.
- Harmful specimens: c02-150's span wrong->correct; d3c01-87's three spans
  wrong->correct, wrong->correct, wrong->review. Zero worsened, zero newly
  confident wrong anywhere.
- Decision table pinned by 12 focused tests
  (tests/python/test_rediar_span_comparer.py); policy code in
  scripts/rediar-span-comparer.py; grading ledger in
  var/quality/rediar-m03-policy/.

## M05 corpus verdict

- On c03, corrected row index 220 / `corrected-0221` is time- and
  fingerprint-aligned across arms. It changes from expected and visible DOCTOR
  in the off arm to visible PATIENT in the on arm. This is one causal
  regression represented by the worsened-row and newly-confident-wrong gates,
  not two independent failures.
- On c08, strict attribution falls from 196/216 to 187/216, while
  incorrect-confident attribution rises from 20/216 to 29/216. Those two
  per-fixture metric regressions independently block the candidate; the
  separate corrected-row text mismatch remains unverified and is not used to
  strengthen or rescue the verdict.
- Available aggregate rates improve, but the frozen campaign contract
  prohibits aggregate improvement from waiving a causal-row or per-fixture
  regression.
- The sealed M05D packet records `promotion_authorized=false`,
  `full_campaign_pass=false`, and `runtime_calls_added=0`. The human approved
  its rejection disposition on 2026-07-25.

Evidence:
`var/quality/rediar-m05-acceptance/adjudication/2026-07-25_m05d-quality1/`.

## Consequences

- Do not promote or enable the two-witness correction lane. The restored
  runtime and release posture stay flag-off.
- Retaining default-off implementation and QA evidence does not make the
  policy supported behavior; removal or reuse requires a separate scope and
  approval.
- No second in-plan policy or threshold may be tuned against the consumed
  corpus. A future mechanism starts from a new plan and new evidence.
- Preserve M05B `PASS_HYBRID_FLAG_OFF`, M05C `PASS_CORPUS_ON_EXECUTION`, and
  M05D `REJECT_CANDIDATE_KEEP_FLAG_OFF` as distinct verdicts.
