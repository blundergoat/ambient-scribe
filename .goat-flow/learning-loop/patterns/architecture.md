---
category: architecture
last_reviewed: 2026-07-04
---

# Architecture Patterns

## Pattern: Thin vertical slice on contract changes

**Context:** A change crosses PHP, Python, and browser surfaces, such as a new Mercure topic, WebSocket frame type, role payload field, or session config value.

**Approach:** Implement the smallest observable end-to-end path first across `src/Controller/`, `strands_agents/api/server.py`, `templates/scribe/index.html.twig`, and `public/js/scribe.js`. Prove that one happy path with a smoke check before adding more fields, modes, or error branches.

## Pattern: ThreadPoolExecutor for all NeMo calls

**Context:** NeMo inference is synchronous GPU work inside an async FastAPI process.

**Approach:** Wrap each NeMo inference entry point in `loop.run_in_executor(nemo_executor, fn, ...)` and reuse the shared executor in `strands_agents/api/server.py`. Do not run GPU inference directly on the event loop or create per-request executors.

## Pattern: Mercure topic per concern, not per session

**Context:** Raw transcription, role attribution, and summary generation have different latency and failure profiles.

**Approach:** Publish separate sibling topics under `scribe/session/{id}/<concern>` for each concern, as captured by `.goat-flow/learning-loop/decisions/ADR-002-mercure-topics-per-session.md`. Do not overload the raw topic with higher-latency role or summary payloads.

## Pattern: Collapse removed selectors to constants

**Created:** 2026-07-04

**Context:** A user-facing selector is removed and only one behavior remains, such as the 0.3.0 collapse from multiple scribe profiles to medical-only transcription.

**Approach:** Remove the selector parameter at every ingress, replace prompt/config maps with named constants, and update browser labels directly. Verify with a narrow stale-contract sweep for removed symbols plus a broader documentation sweep that records justified historical keeps.
