# ADR-008: Sustained Voice Evidence Routes Marginal Slots to Canonical Identities

**Status:** Rejected
**Date:** 2026-07-12
**Updated:** 2026-07-12 (corpus quality regressed; keep both candidates disabled)
**Ticket/Context:** 0.4.0-slice-2 M04

## Context

The streaming speaker cap currently decides whether a cache slot is real from stable emitted
word duration alone (`strands_agents/nemo_session.py`, search: "marginal_below"). M04 Phase 0
confirmed that this folds real Patient turns into the visible Doctor stream: day3 window 15
and day2 windows 98/101 in
`var/quality/m04-crosstalk-bleed-20260711T193941Z/phase0-verdict.md`.

The engine already measures cumulative voiced frames independently of delayed/stabilized words
(`strands_agents/nemo_streaming_engine.py`, search: "speaker_slot_voiced_frame_counts"). At the
confirmed folds, the marginal origin slots carried 141 frames/20.41% voice share on day3 and
97 frames/1.87-1.93% on day2. A retained-evidence simulation found that a 50-frame and 1.5%
minimum would protect 11 of 13 unique harmful folds. It would also stop one known benign day2
fold, so corpus and benign-control checks remain mandatory.

The first candidate kept every proven slot under its raw cache identity. Targeted results
improved, but the full corpus exposed eight additional visible identities across six fixtures.
The source-chip proxy also moved only 62 -> 55 because it reported seven known false positives
on day2 and missed the confirmed day3 bleed. The user approved retaining that failure as
default-OFF history and replacing its proxy gate with the grounded fold scorer.

## Decision

Reject both guarded fold policies for user visits. Keep
`NEMO_STREAMING_CROSSTALK_GUARD=0`; the 50-frame/1.5% path remains controlled QA diagnostic
history only, and the failed visit-long origin-to-chip alias is removed from the product and
focused tests. Any replacement is a new Ask First decision with fresh corpus acceptance.

The diagnostic threshold candidate identifies a sustained voice after both conditions are true:

- at least 50 cumulative voiced frames (about four seconds at the engine's 80 ms frame step);
- at least 1.5% of cumulative voiced frames across the visit.

The first rejected candidate keeps a qualifying slot visible; the second rejected candidate
pinned an additional qualifying slot to the less-spoken canonical identity for the visit. Their
measured outcomes below explain why neither policy is release behavior. The existing stable-word
fold remains the user-facing path while the flag is off.

## Failure Mode Comparison

| Option | What fails | Decision |
| --- | --- | --- |
| Stable-word threshold only | Real speech can have substantial acoustic evidence before delayed stable words cross the growing 5% duration threshold, so the UI shows it under the dominant speaker. | Rejected by both Phase 0 specimens. |
| Keep sustained origin visible | Full-corpus strictness improves, but raw cache identities become extra source chips: 10 -> 18 phantom identities. | Rejected by the first Phase 2 corpus. |
| Alias sustained marginal origin to the less-spoken canonical identity for the visit | It fixes the named bleed and removes the third chip, but a cache identity later carries minority Doctor spans that the stable Patient alias misroutes. | Rejected by day3 grounded acceptance: 14 harmful folds vs combined target <=6. |
| Defer every marginal row for another window | Truly short answers may never emit another row, delaying the UI and still folding on timeout. | Rejected for live cadence and the day3 consent span. |
| Fold with origin metadata for later re-splitting | Requires a new correction/API attribution contract outside M04's approved boundary. | Deferred to a separately approved contract change. |

## Reversibility

The stable-alias delta has been removed. Leave `NEMO_STREAMING_CROSSTALK_GUARD=0` or unset so user
visits retain the exact release fold policy. The remaining guarded threshold path is diagnostic
only and must not be enabled for user visits. Source-chip counts remain diagnostic rather than a
release gate.

## Acceptance outcome

The first candidate is not accepted for release. The full corrected 1x corpus improved strict mean by
1.410 points and incorrect-confident mean by 1.080 points, but source-chip findings moved only
62 -> 55 (target <=31) and phantom-speaker total regressed 10 -> 18. The default remains OFF.
The user approved the grounded-metric/two-identity revision on 2026-07-12. Focused tests passed
25/25, but completed day3 acceptance found 14 harmful folds (12 later Doctor spans routed to the
Patient chip), exceeding the combined <=6 target even though strict stayed 90.4%, incorrect-
confident stayed 9.6%, and phantom identities fell to zero. Day2 stopped at 90 s; flag-off hashes
and corpus were not run. A causal comparison found all 14 spans already had the same wrong role in
Phase 0 and candidate one; the alias created no new wrong-role outcome there, but the approved raw
fold gate still fails. Evidence: `phase1b-targeted-gate-verdict.md`. Both candidates remain OFF.

The user then approved a causal attribution-delta gate. Each candidate fold span is compared with
the Phase 0 visible role at the same time using TextGrid truth; correct outranks unresolved, which
outranks wrong. Acceptance requires zero worsened spans, zero newly confident wrong spans, at
least one improvement, unchanged strict/incorrect-confident rates, and no new identity. Raw fold
and source-chip totals remain diagnostic. This metric refinement does not accept the policy;
the completed day3 replay passes with 3 improved spans, zero worsened spans, and zero newly
confident wrong spans. Strict attribution improves 90.0% -> 90.4%, incorrect-confident falls
10.0% -> 9.6%, and phantom identities fall 1 -> 0. Fresh day2 passes with 6 improved spans,
zero worsened/newly confident wrong, strict 91.5% -> 94.0%, incorrect-confident 8.1% -> 5.6%,
and no identity increase. The 277-span combined target result is 9 improved, zero worsened, and
zero newly confident wrong. A fresh default-OFF c02/c03/c08 trio is byte-identical at
`HASH_MATCHES=3/3`. The final 20-fixture corrected corpus rejects the policy: strict mean falls
88.985% -> 87.695% and incorrect-confident rises 10.595% -> 12.240%, despite phantom identities
improving 10 -> 7. The stable-alias product/test delta was removed after this kill; the PHI-safe
scorer and failure evidence remain for future mechanism work. Both candidates remain disabled;
evidence: `phase1c-full-corpus-gate-verdict.md`.
