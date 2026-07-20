# ADR-010: Two-Witness Span Replacement for the Corrected Transcript

**Status:** Proposed (implementation approved 2026-07-21; moves to Accepted or Rejected on the M05 corpus verdict)
**Date:** 2026-07-21
**Ticket/Context:** post-stop-rediarization M03

## Context

ADR-009 closed time-local fold rules; the fallback plan rebuilds speaker
structure from full retained audio at correction time. Spikes proved the
rebuild corrects every deterministic harmful fold but wholesale replacement
regresses clean visits, the plain role agent mislabels a pure slot on one
control fixture, and rebuilt slots can be locally impure. The replacement
policy therefore cannot trust any single witness.

## Decision

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

## Consequences

- The thresholds (3.0s, 2.0x, 0.25s) live in this ADR; code reads them and
  in-campaign tuning is prohibited - a failed gate kills the candidate.
- M04 integration must retain each session's fold spans unconditionally
  (today they are only logged under the evidence flag) and run one role-agent
  pass over rebuilt rows per correction to supply the wording witness.
- Truthless spans (silence/crosstalk) can be replaced when both witnesses
  agree; measured effect is state-neutral, and the M05 corpus gates own the
  final word-loss/attribution verdict.
- Rejection of this policy at any later gate ends the candidate line per the
  plan's kill discipline; no second in-plan policy may be tuned against the
  same corpus.
