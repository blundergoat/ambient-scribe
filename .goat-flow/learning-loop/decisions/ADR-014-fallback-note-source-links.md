# ADR-014: Notes Built From the Live Transcript Carry No Source Links

**Status:** Accepted
**Date:** 2026-08-29
**Ticket/Context:** Post-visit provenance work, decided after the corrected lane was restored

## Context

A note is normally written from the corrected transcript and every claim can link to the rows that
support it. When post-visit correction cannot run, the note is instead written from the live rows and
those lanes pass `allow_citations=False`, so the clinician reads "No cited evidence" on every claim
(`strands_agents/api/summary_request.py`, search: `allow_citations`).

Those rows are mechanically citable. They carry stable segment ids, and `build_source_units` would
group them exactly as it groups corrected rows, so switching the flag would light up source links
across the fallback note with no other change. The question is whether it should.

Two hazards make the fallback rows different in kind rather than merely rougher. Live rows can repeat
a contiguous phrase under a second speaker identity, so a link can point at wording the other person
said. And the note generator is already recorded as producing medication names that were never spoken
(`.goat-flow/learning-loop/footguns/summary.md`, search: `silently repairs garbled drug names`), while
correction failure silently moves a note onto live rows in the first place
(`.goat-flow/learning-loop/footguns/runtime.md`, search: `Second-pass correction failure silently swaps`).
A source link beside a claim is read as confirmation, and confirmation is the one thing these rows
cannot offer.

This decision is separable from quote truthfulness, and that separation now exists in code. Quoted
wording in an uncited note is verified against the selected visit rows and flagged only when those rows
do not support it (`strands_agents/api/summary_generation.py`, search: `def _claim_quote_state`).
Truthful quote badges therefore do not require citations, which removes the main practical argument for
turning links on.

## Decision

Notes built from the live transcript emit no source links. Both fallback lanes keep
`allow_citations=False`, `citation_segments` stays empty, and no citation chips or transcript deep links
render for those notes. The clinician keeps the existing persistent notice that the note came from the
live transcript.

A source link in this product means one thing only: the cited wording is present in the reviewed
transcript that passed correction. It never means the claim is clinically approved, and it is never
offered for wording that was not reviewed. Conformance is checkable: no lane other than the corrected
one may pass `allow_citations=True`.

This changes no runtime behaviour. It records why the current behaviour must not be "fixed" by a later
maintainer who notices the ids are right there.

## Failure Mode Comparison

| Option | What fails | Why rejected or accepted |
| --- | --- | --- |
| Keep refusing fallback citations | A degraded note stays untraceable even though its rows have usable ids, so a clinician who wants to check a claim must read the transcript themselves. | Accepted. The cost falls on effort, not on trust. Nothing on screen claims more certainty than the transcript earned. |
| Allow terminal live-row provenance | A link can point at a duplicated fragment attributed to the wrong speaker, and a link beside an ungrounded medication name reads as confirmation of it. | Rejected. The failure is silent and lands on the exact classes already recorded as unsafe, and it appears only when correction has already failed. |

## Consequences

- The uncited fallback note keeps its existing copy, and no browser or summary production code changed.
- "Open in transcript" stays unavailable on fallback notes, because it resolves cited row ids.
- Quote warnings on fallback notes remain meaningful, since they are checked against the selected rows.
- Anyone widening citations must revisit this record rather than treat the empty arrays as an oversight.

## Reversibility

A two-way door. Turning links on is a small change to the two fallback call sites, and the pinned policy
test asserts the current behaviour, so a future change is a deliberate edit rather than a silent drift.

Revisit when either hazard is measured away, not before:

- cross-speaker repeat risk in live rows is quantified against corrected rows on a real corpus and is
  low enough to accept, using the exact-overlap and grounded-repeat metrics the duplicate scorer defines
  (`scripts/duplicate-transcript-score.py`); or
- the interface can carry a limitation that survives wherever a link is interpreted, including the
  transcript view a link opens, so a live-row link can never be mistaken for reviewed evidence.

Restoring correction is the better fix for an uncited note and is now the supported path: a stopped
visit that corrects successfully produces a note with working source links.
