# strands_agents/ — Local Context

## Footguns (from docs/footguns.md)

- **Mercure JWT silent failure:** `publish_to_mercure()` silently drops segments if JWT not configured. No browser error.
- **Session cleanup race:** WebSocket disconnect `cleanup_session()` can delete `RoleMappingState` while `/roles/stream` is still reading it.
- **NeMo singleton no recovery:** GPU model loaded once at startup. Crash = container restart. `max_workers=2` hardcoded.
- **PHP↔Python contract unvalidated:** SSE event format (`mapping`, `confidence`) not validated on either side. Changes break silently.
- **Three session state buckets:** `nemo_session.py`, `session.py`, `tools/assign_roles.py` — independent stores, no coordinated lifecycle.
- **Audio format assumed:** `AudioBuffer` hardcodes 16kHz PCM. Browser may send WebM/Opus.

## Conventions

- NeMo inference MUST use `run_in_executor` — never call directly in async context.
- Role inference uses Bedrock or Ollama only — never the GPU.
- All Pydantic models for API contracts live in `api/server.py`.
