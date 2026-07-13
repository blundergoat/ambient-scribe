# ADR-010: Decouple Evidence-Backed Speaker Repairs Without Weakening Flip Damping

**Status:** Rejected (revision 2 repeated the c07 spot non-regression failure)
**Date:** 2026-07-14
**Updated:** 2026-07-14 (approved isolated c07 retry reproduced the failed gate)
**Ticket/Context:** 0.4.1 M07 role-map bifurcation

## Context

At 60 seconds, three c08 browser-cadence replays produced byte-identical
`text,start,end,speaker_id` rows and an 84.4% role-free oracle, but visible strict attribution
split **84.4 / 59.4 / 84.4%**. The bad session
`c69e00d3-d4a2-439a-87f3-a14ef5ccb414` never proposed a repair. A current full-corpus c08
session did propose one, but coupled the strong `speaker_1 -> DOCTOR` repair with a contradicted
`speaker_2 -> PATIENT` flip. Whole-map damping correctly held that mixed proposal.

The current 20-fixture corpus contains eight suppressed multi-speaker proposals across three
fixtures. Only one proposal, in c08, combines a majority-truth repair and regression. Aggregate
cue-count automation is not safe enough to replace the role model: a margin of three is 100%
precise across current corpus speaker IDs but does not fire for c08 `speaker_1`; lower margins
produce wrong anchors. Evidence and analyzers are retained under
`var/quality/m07-role-map-bifurcation-20260713T202336Z/`.

## Decision

Require the role tool to receive an independent assessment for every bounded-evidence speaker.
Every ID in `role_evidence.speakers` must occur in both the proposed map and assessments; an
incomplete call returns the missing IDs for a retry without changing UI role state. Each
assessment names the proposed role, a per-speaker confidence, and start-time citations selected
from that speaker's bounded representative/recent evidence.

The server validates an assessment before it may decouple a proposal:

- the assessed role must match the proposed mapping;
- confidence must be at least 0.80;
- at least one cited start time must belong to that speaker's bounded evidence; and
- production cue counts must favour the assessed role over its opposite.

When a multi-speaker role permutation contains a proper subset of validated changed speakers,
accept only that subset and keep the other speakers on their current UI labels. If every changed
speaker validates, or none does, send the complete proposal through the existing repeat/confidence
damping unchanged. Clinician-confirmed overrides are never eligible for automatic decoupling.

This is an internal role-tool contract only. The role agent remains Bedrock or CPU-only Ollama;
NeMo keeps sole ownership of the GPU. No browser event or PHP/Python API contract changes.

## Failure Mode Comparison

| Option | What fails | Decision |
| --- | --- | --- |
| Keep whole-map proposals only | One weak speaker decision can block a strong repair; a bad run may also repeat its initial map without reviewing speakers independently. | Rejected by c08 59.4% draws. |
| Automatically trust aggregate cue margins | Safe margins do not identify c08 `speaker_1`; useful lower margins mislabel corpus speaker IDs. | Rejected by threshold mining. |
| Remove or lower whole-map damping | A confident but wrong complete swap can relabel every existing transcript card immediately. | Rejected; damping is retained unchanged. |
| Run a second model verification on every multi-speaker update | It doubles model latency/cost and adds another stochastic decision before proving one structured call is insufficient. | Deferred unless the cited single-call design misses acceptance. |
| Validate per-speaker assessments and accept only a supported subset | Strong repairs can land without carrying a contradicted companion flip, while complete swaps still face existing damping. | Accepted for M07 verification. |

## Consequences

- The model must inspect every speaker independently rather than infer a balanced whole map.
- Missing, malformed, weak, uncited, or cue-contradicted assessments preserve existing behavior.
- Role timelines must record decoupled speakers so acceptance evidence distinguishes subset repair
  from an accepted or suppressed whole-map flip.
- The design can improve a no-repair draw only if the strengthened structured prompt makes the
  model propose the independently supported speaker role; five consecutive c08 replays are the
  release gate for that stochastic boundary.

## Reversibility

This is a two-way door. Removing the assessment argument, validation/decoupling branch, and prompt
instruction restores whole-map behavior without changing stored transcript data or public payloads.
Reject the design if five consecutive c08 replays do not reach at least 84% strict attribution, any
of four role-churn controls regress, confirmed overrides move, or the role agent touches the GPU.

## Acceptance outcome: revision 1

Rejected on the first c08 candidate replay. Session
`bf459d4c-3d28-452e-b4a9-bcc27ff8600c` ended with the correct mapping for its three mapped
speakers, but omitted a newly observed fourth speaker ID. Three clean rows remained UNKNOWN, so
strict attribution was **78.1% (25/32)** against an unchanged **84.4%** role-free oracle. The
timeline recorded one suppressed flip and no decoupled event. Runs 2-5 were cancelled.

This failure does not show that proper-subset decoupling relabelled a speaker incorrectly; that
branch did not fire. It shows the selected contract is incomplete because "assess every mapped
speaker" does not require mapping every speaker present in bounded evidence. Any revision must be
a new candidate with an explicit mapping-completeness rule and a fresh five-run gate.

## Acceptance outcome: revision 2

The approved completeness revision passed 43 focused tests, but its first c08 score was sampled
too early to decide the product result. Session `be6f808c-0856-4c02-bdcf-b5081288e6ce` had the
same 78.1% scorer snapshot after the configured 8-second role settle. The final queued role call
then completed 11.578 seconds after quality finalization with the complete correct four-speaker
map, including `speaker_3:DOCTOR`; the evaluator had fetched history 3.427 seconds earlier.

Per the corpus-gate stop rule, runs 2-5 were cancelled. Revision 2 is neither accepted nor
rejected on this evidence. A timing-only replay with enough settling for the observed tail call
must still pass five consecutive >=84% runs, followed by the role-churn spot gate, before this
decision can become Accepted.

The approved 20-second-settle gate subsequently passed five consecutive c08 runs at 84.4% strict
with complete correct maps. In the full-length spot gate, c02/c03/c08 each improved strict by
0.1/0.5/0.3pp, but c07 scored 86.8% against its 87.2% accepted-corpus baseline. The run stopped
before day5-c08 completed. C07's canonical row hash changed and its best-dyadic score declined
0.5pp, so the -0.4pp visible movement is not proven role-lane harm; nevertheless the literal
non-regression gate was not satisfied.

The one explicitly approved unchanged c07 retry, session
`267f1cf8-74e9-4282-be21-235e57f9cb0b`, exactly reproduced the first failed draw's canonical
row hash and **86.8% (184/212)** strict score. Its final role map was correct and its best-dyadic
ceiling remained 86.3%, but the declared comparison is still **-0.4pp** against 87.2%. Per the
pre-authorized decision rule, revision 2 is rejected; day5-c08 and broad suites do not proceed.

The user approved no-ship closure. The role prompt, runtime/tool contract, decoupled timeline, and
focused tests were restored exactly to HEAD. This ADR and its evidence remain as a rejected design
record; none of the described behavior is active in the product.
