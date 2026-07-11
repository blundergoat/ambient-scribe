# ADR-008: Sustained Voice Evidence Guards Marginal Speaker Slots

**Status:** Accepted
**Date:** 2026-07-12
**Ticket/Context:** 0.4.1 M04

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

## Decision

Add an OFF-by-default `NEMO_STREAMING_CROSSTALK_GUARD` policy. When enabled, a cache slot stays
under its own visible speaker identity after both conditions are true:

- at least 50 cumulative voiced frames (about four seconds at the engine's 80 ms frame step);
- at least 1.5% of cumulative voiced frames across the visit.

The existing stable-word duration threshold still admits substantial slots and still folds
weaker slots. The guard never guesses which established speaker owns a marginal row: retaining
the engine's stable origin is safer than silently assigning the words to the dominant voice.
Flag-off behavior and transcript bytes remain the release rollback.

## Failure Mode Comparison

| Option | What fails | Decision |
| --- | --- | --- |
| Stable-word threshold only | Real speech can have substantial acoustic evidence before delayed stable words cross the growing 5% duration threshold, so the UI shows it under the dominant speaker. | Rejected by both Phase 0 specimens. |
| Sustained voiced-frame + share guard | A cache slot that contains a benign duplicate after carrying real speech can retain that duplicate under its origin identity; day2 window 96 is the known control. | Accepted behind a flag; targeted and corpus gates must show net benefit without new phantom identities. |
| Defer every marginal row for another window | Truly short answers may never emit another row, delaying the UI and still folding on timeout. | Rejected for live cadence and the day3 consent span. |
| Fold with origin metadata for later re-splitting | Requires a new correction/API attribution contract outside M04's approved boundary. | Deferred to a separately approved contract change. |

## Reversibility

This is a two-way door. Leave `NEMO_STREAMING_CROSSTALK_GUARD=0` or unset to restore the exact
fold policy. Remove the flag and guarded qualification if the byte-identity trio differs while
off, targeted benign controls regress more than harmful folds improve, the corpus does not halve
its source-chip count, strict attribution/incorrect-confident means regress, or new phantom
speaker identities appear.
