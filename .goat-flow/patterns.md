# Patterns

Repeatable approaches that have worked more than once in this codebase. Unlike lessons (which record mistakes) or footguns (which record traps), patterns record *what to do*.

## Pattern: Thin vertical slice on contract changes

When a change crosses PHP ↔ Python ↔ browser (e.g. a new Mercure topic, a new WebSocket frame type, a role payload field), implement the thinnest end-to-end slice first — one file per layer — and prove it with a smoke test before fanning out to additional fields, modes, or error paths.

**Why:** Cross-boundary changes fail silently (see `.goat-flow/lessons/verification.md` — audio format mismatch). A vertical slice exercises the full contract before investment grows.

**How to apply:** Touch `src/Controller/`, `strands_agents/api/server.py`, and the Twig/JS at once for a single path; skip unrelated modes, error recovery, and tests for edge cases until the happy path is observable end-to-end.

## Pattern: ThreadPoolExecutor for all NeMo calls

Every NeMo inference entry point wraps the synchronous call in `loop.run_in_executor(nemo_executor, fn, ...)` so the FastAPI event loop never blocks on GPU work.

**Why:** GPU inference holds the CUDA stream for seconds; direct `await` inside an async handler would freeze all concurrent WebSocket sessions.

**How to apply:** When adding a new NeMo entry point, mirror `TranscriptionSession.process_chunk()` usage in `strands_agents/api/server.py` — reuse the shared `nemo_executor`, don't create a new pool.

## Pattern: Mercure topic per concern, not per session

Session state is fanned out across three sibling Mercure topics (`…/raw`, `…/roles`, `…/summary`) instead of multiplexing into one. Browser subscriptions pick the topics it cares about.

**Why:** The raw path must not be blocked by role-inference latency; the summary path fires once at session end. Separate topics keep each hop's tail-latency independent. See `.goat-flow/decisions/ADR-002-mercure-topics-per-session.md`.

**How to apply:** When adding a new stream (e.g. word-level confidences, speaker switches), publish a new topic under `scribe/session/{id}/<concern>` rather than overloading `raw`.
