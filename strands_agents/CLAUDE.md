# strands_agents/ — Local Context

## Footguns (see `.goat-flow/footguns/`)

- ~~**Mercure JWT silent failure:**~~ MITIGATED — `publish_to_mercure()` returns `bool`, logs ERROR, sends `system_error` WebSocket frame. Browser shows amber banner.
- ~~**Session cleanup race:**~~ MITIGATED — `SessionLifecycle` class with per-session `asyncio.Lock`. `lifecycle.destroy()` is atomic. `assign_roles._session_states` has `threading.Lock`.
- **NeMo singleton no recovery:** GPU model loaded once at startup. Crash = container restart. `max_workers=2` hardcoded.
- **PHP↔Python contract:** `/history` and `/roles` now accept both GET and POST. Empty mapping serializes as `{}` on both sides.
- ~~**Three session state buckets:**~~ MITIGATED — `session_lifecycle.py` coordinates active session + role state cleanup. `SessionStore` TTL eviction is still independent.
- ~~**Audio format assumed:**~~ MITIGATED — First-chunk validation rejects WebM/WAV bytes when PCM configured. Raises `ValueError` with clear message.

## Conventions

- NeMo inference MUST use `run_in_executor` — never call directly in async context.
- Role inference uses Bedrock or Ollama only — never the GPU.
- All Pydantic models for API contracts live in `api/server.py`.
