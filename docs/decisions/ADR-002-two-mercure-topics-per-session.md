# ADR-002: Two Mercure Topics Per Session (Raw + Roles)

**Date:** 2026-03-14 (documented; decision made during M2 design)
**Status:** Accepted

## Context

The transcription pipeline produces two types of output at different speeds: raw transcription segments (low-latency, from NeMo GPU) and role attribution updates (higher-latency, from Strands agent calling Bedrock/Ollama). Combining both on one topic would force the browser to wait for role inference before displaying text, adding 2-5 seconds of perceived latency.

## Decision

- Two Mercure topics per session: `scribe/session/{id}/raw` and `scribe/session/{id}/roles`
- Raw segments published immediately after NeMo inference — the hot path
- Role updates published asynchronously after Strands agent inference — decoupled from hot path
- Browser subscribes to both topics independently

## Consequences

- **Easier:** Hot path latency is NeMo inference only (~1-2s). Role inference can take as long as needed without blocking transcript display. Browser can show `spk_0`/`spk_1` immediately, then update to DOCTOR/PATIENT when roles arrive.
- **Harder:** Browser must handle out-of-order updates. Role updates may arrive after the user has already read the `spk_0` text. UI needs merge logic for retroactive role application.
