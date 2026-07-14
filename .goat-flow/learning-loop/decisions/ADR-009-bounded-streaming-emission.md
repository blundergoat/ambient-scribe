# ADR-009: Bound Stable Streaming-Row Holds Behind an Obsolete Frontier

**Status:** Accepted
**Date:** 2026-07-13
**Ticket/Context:** 0.4.0-slice-2 M06 long-turn emission starvation

## Context

The session-long streaming engine holds stable rows behind every speaker slot's oldest revisable
word so rows normally reach the browser in spoken order. Two 1x replays proved that an old word can
remain revisable while the slot's activity keeps refreshing, pinning that global frontier even when
many stable rows are clock-ready:

- c01 session `ca102d92-6d45-4d6c-971a-002b9fe92659` measured 50.0 seconds with no rows,
  55.0 seconds between batches, and a 25-row browser burst.
- c03 session `a6406c28-87a5-4f45-af13-33c205472ad2` measured 25.0 seconds with no rows,
  30.0 seconds between batches, and a 15-row largest burst.
- PHI-safe evidence shows up to 37 clock-ready c01 rows and 19 c03 rows blocked with zero engine
  release. This rejects decoder finality, a required speaker turn, and browser rendering as causes.

The evidence and threshold projection live under
`var/quality/m06-emission-starvation-20260712T211015Z/phase0-mechanism-verdict.log`.

## Decision

Add `NEMO_STREAMING_MAX_TRANSCRIPT_HOLD_SECONDS`, defaulting to `0` (disabled). When a positive
limit is enabled and the stability frontier has lagged the clock horizon by at least that limit,
the engine releases only already-stable, clock-ready rows. It does not emit mutable words, invent
timestamps, change the five-second browser cadence, or alter speaker identity policy.

Use `10` seconds for the M06 enabled gates. Captured evidence projects maximum c01/c03 batch
intervals of 15.0/10.0 seconds at that value. A 15-second stability limit projected a 20.0-second
c01 batch interval, missing the user-visible target by one browser window.

The release decision is observable as `release_bounded_stability_frontier` using count/time-only
diagnostics. Default-off row output must pass the canonical byte-identity trio before the candidate
can be promoted, and enabled output must pass targeted, corpus, and browser acceptance.

## Failure Mode Comparison

| Option | What fails | Why rejected or accepted |
| --- | --- | --- |
| Keep the unbounded frontier | A long answer can freeze the visible transcript for 50 seconds and then add 25 rows at once. | Rejected because both 1x fixtures reproduced stable clock-ready starvation. |
| Emit mutable tail words early | NeMo may revise wording the browser already treated as final, recreating truncation or duplicate risks. | Rejected; the bound never changes mutable-word emission. |
| Use a 15-second stability bound | Captured c01 evidence still projects a 20-second batch interval at five-second cadence. | Rejected because it misses the proposed delivery gate. |
| Release stable clock-ready rows after 10 seconds | A late older mutable row may appear after newer stable wording, matching the existing dormant-slot trade-off but with an explicit bound. | Accepted subject to byte, quality, corpus, and browser gates. |
| Change chunk cadence, model, or speaker policy | It broadens the blast radius without addressing the named release condition. | Rejected as unrelated to the measured mechanism. |

## Consequences

- Ordinary visits remain on strict chronological release while the setting is `0`.
- Enabled visits trade a bounded, visible ordering slip for continuous delivery during pathological
  active-tail refreshes. Timestamps remain honest, so stored transcript order can still be sorted by
  spoken time even when arrival order differs.
- The limit applies only when at least one stable clock-ready row is actually blocked. Silence and
  an entirely mutable tail do not fabricate a row merely to satisfy the clock.
- Corpus quality and browser behavior are acceptance gates, not assumptions of this decision.

## Reversibility

This is a two-way door. Set `NEMO_STREAMING_MAX_TRANSCRIPT_HOLD_SECONDS=0` and restart the agent to
restore the prior release policy without changing stored data or public payloads. Reject or revisit
the decision if enabled runs exceed the delivery bound, change canonical text/timing beyond the
approved stability trade-off, regress strict attribution or incorrect-confident rates, or show a
clinically confusing arrival-order slip in the browser.
