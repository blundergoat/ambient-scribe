---
category: architecture
last_reviewed: 2026-07-07
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

## Pattern: New classic-script module instead of growing a page script past the gruff ceiling

**Created:** 2026-07-07

**Context:** A frontend feature belongs with `public/js/scribe-output.js`, but that file sits
near gruff-ts's file-length threshold (was 948 lines before the last split; the same trap fired earlier
for `strands_agents/post_visit_correction.py` at 1023 lines).

**Approach:** Put the feature in a new classic script (`scribe-summary-tabs.js`,
`scribe-provenance.js`), loaded via an ordered `<script>` tag after its dependencies; classic
scripts share globals, so cross-file calls need only a `typeof fn === 'function'` guard for
isolated test pages. Add `if (typeof module !== 'undefined' && module.exports)` at the bottom
when node-based unit tests need to require the file (`scribe-stitch.js` + `npm run test:js`).
Deleting superseded legacy code from the old file at the same time (no-backwards-compat rule)
can shrink it back below the ceiling - one such split took scribe-output.js from 948 to 799 lines.
