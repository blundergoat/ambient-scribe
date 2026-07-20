# ADR-009: Time-Local Pairwise Slot Evidence Cannot Separate Harmful From Benign Folds

**Status:** Rejected
**Date:** 2026-07-20
**Ticket/Context:** 0.5.1 M02

## Context

After ADR-008 rejected both sustained-voice fold policies, v0.5.1 M01 froze a
deterministic flag-off baseline: the reported harmful speaker fold reproduces
identically in every cold 1x replay (c02: origin slot folded into the visible
Patient stream at 66.64s with TextGrid owner DOCTOR, 6/6 runs; day3 c01: three
harmful folds, 3/3 runs; day1 c03: six benign same-role folds, 0 harmful, 3/3
runs). M02 then added behavior-neutral pairwise co-activity counters inside the
streaming engine's existing diarizer sampling point
(`strands_agents/nemo_streaming_engine.py`, search: "_count_pairwise_slot_activity")
with flag-gated per-window deltas
(`strands_agents/nemo_session.py`, search: "_pairwise_slot_evidence"),
to test whether when-two-slots-speak-together evidence could distinguish a
harmful real-turn fold from a benign duplicate or churn fold.

## Decision

Reject any time-local pairwise co-activity/exclusive-frame fold rule. The
v0.5.1 fold-candidate line (M03 policy, M04 corpus gates, M05 promotion) does
not proceed. The harmful-fold repair routes to a separate System plan for
post-stop full-audio speaker re-diarization (0.5.1 backlog); live preview keeps
the frozen flag-off fold policy meanwhile. The diagnostic counters remain in
the engine, default-absent under `NEMO_STREAMING_SLOT_EVIDENCE`, because they
are proven byte-neutral and future mechanism work will need them.

## Measured basis

Twelve unique fold specimens from evidence-on cold replays of the four M01
targets, deterministic frame-for-frame across repeated runs:

- All six benign folds show zero co-activity with any slot at 0s and 2s join
  margins: they are one voice moving across cache slots sequentially during
  early-session establishment, not simultaneous-speech echoes.
- Harmful folds split two with zero co-activity and two with co-activity (5
  and 8 frames); the zero-co-activity harmful folds match every benign fold on
  every measured axis.
- Origin-exclusive shares overlap across classes (harmful 0.14-1.0, benign
  0.37-0.65; closest gap 0.6875 vs 0.6491).
- Compatibility and cost of the retained diagnostics: flag-off HASH_MATCHES=6/6
  (canonical transcript plus full continuity-row shape), peak cardinality six
  pairs per window with no growth over the visit, counting cost 0.287ms per
  150s replay by microbenchmark.

Evidence: `var/quality/0.5.1-m02-derivation/` (decision packet, derivation
table, joins, any-slot reanalysis) and
`var/quality/0.5.1-m02-flag-off-baseline/flag-off-compatibility.txt`.

## Consequences

- Do not re-attempt a fold policy from slot co-activity, exclusivity, shares,
  or any conjunction of them without new evidence that the benign
  sequential-churn class has become separable; a "protect zero-co-activity
  folds" rule mass-flags benign folds (the universal-deferral family ADR-008
  already rejected), and share thresholds have no credible margin.
- The counters and `pairwise_slot_evidence` window field are diagnostic lineage
  for future mechanism work, not a policy input; `NEMO_STREAMING_CROSSTALK_GUARD`
  stays 0 per ADR-008.
- The fallback plan owns the next attempt at repairing harmful folds and must
  target the corrected transcript lane (post-stop full-audio re-diarization),
  not the live preview.
