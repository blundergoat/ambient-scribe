# ADR-002: Mercure Topics Per Session (Raw + Roles + Summary)

**Date:** 2026-03-14 (documented; decision made during M2 design)
**Status:** Accepted; role-label wording superseded by 0.3.0 medical-only UI

## Context

The transcription pipeline produces outputs at different speeds: raw transcription segments (low-latency, from NeMo GPU), role attribution updates (higher-latency, from Strands agent calling Bedrock/Ollama), and end-of-session summaries. Combining them on one topic would couple unrelated latency and rendering concerns.

## Decision

- Three Mercure topics per session: `scribe/session/{id}/raw`, `scribe/session/{id}/roles`, and `scribe/session/{id}/summary`
- Raw segments published immediately after NeMo inference - the hot path
- Role updates published asynchronously after Strands agent inference - decoupled from hot path
- Summary updates published when the session summary endpoint completes
- Browser subscribes to topics independently

## Consequences

- **Easier:** Hot path latency is NeMo inference only (~1-2s). Role inference and summary generation can take as long as needed without blocking transcript display. Browser can show `spk_0`/`spk_1` immediately, then update to medical Doctor/Patient labels when roles arrive. This wording supersedes the original multi-mode label note; the three-topic decision still stands.
- **Harder:** Browser must handle out-of-order updates. Role updates may arrive after the user has already read the `spk_0` text, and summary updates may arrive after the transcript is complete. UI needs merge logic for retroactive role application and a separate summary render path.
