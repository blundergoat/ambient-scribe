# ADR-002: Mercure Topics Per Session (Raw + Roles + Summary + Hints)

**Date:** 2026-03-14 (documented; decision made during Mercure transport design)
**Status:** Implemented; role-label wording superseded by 0.3.0 medical-only UI
**Updated:** 2026-07-05

## Context

The transcription pipeline produces outputs at different speeds: raw transcription segments (low-latency, from NeMo GPU), role attribution updates (higher-latency, from Strands agent calling Bedrock/Ollama), end-of-session summaries, and optional clinical hints. Combining them on one concern topic would couple unrelated latency, rendering, and failure concerns.

Browser connection pressure also matters. The Mercure hub on the local `http://localhost` path cannot rely on HTTP/2 multiplexing, and browsers cap HTTP/1.1 connections per host. Opening one EventSource per topic exhausted the browser connection pool across a few open scribe tabs, so topic separation now pairs with one browser EventSource that subscribes to every visit topic and routes events by payload `type`.

## Decision

- Four Mercure topics per session: `scribe/session/{id}/raw`, `scribe/session/{id}/roles`, `scribe/session/{id}/summary`, and `scribe/session/{id}/hints`
- Raw segments published immediately after NeMo inference - the hot path
- Role updates published asynchronously after Strands agent inference - decoupled from hot path
- Summary updates published when the session summary endpoint completes
- Clinical hints publish separately after summary generation when hints are enabled and available
- Browser subscribes to all visit topics through one shared EventSource and dispatches handlers by event `type`

## Consequences

- **Easier:** Hot path latency is NeMo inference only (~1-2s). Role inference, summary generation, and clinical hints can take as long as needed without blocking transcript display. Browser can show `spk_0`/`spk_1` immediately, then update to medical Doctor/Patient labels when roles arrive. This wording supersedes the original multi-mode label note; the per-concern topic decision still stands.
- **Harder:** Browser must handle out-of-order updates. Role updates may arrive after the user has already read the `spk_0` text, and summary or hint updates may arrive after the transcript is complete. UI needs merge logic for retroactive role application and separate summary/hints render paths.
- **Operational constraint:** New visit concerns should add a sibling topic and handler on the shared EventSource, not a second EventSource.
