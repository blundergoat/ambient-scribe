# Footguns

Cross-domain architectural landmines with real coupling and file:line evidence.

---

Each entry names the files involved, what breaks, and the evidence.

**Symptoms:** ASR inference crashes with `ValueError: not enough values to unpack (expected 6, got 5)`.

### 1. Mercure publish failure ~~is log-only~~ (MITIGATED)
- **Files:** `strands_agents/api/server.py:236-269`, `templates/scribe/index.html.twig`
- **Status:** `publish_to_mercure()` now returns `bool`, logs at ERROR, and sends a `system_error` WebSocket text frame to the browser on first failure. The template shows a persistent amber banner. The UI still depends on Mercure for segment delivery, but silent data loss is now surfaced.
- **Remaining risk:** No fallback data path — if Mercure is down, segments are transcribed but not displayed until reconnection.

### 2. ~~Live role updates have two competing delivery paths~~ (RESOLVED)
- **Files:** `strands_agents/api/server.py`, `src/Service/RoleInferenceService.php`, `src/Controller/ScribeController.php`
- **Status:** The PHP SSE proxy endpoint (`POST /scribe/{id}/roles/stream`) has been deleted. `RoleInferenceService::streamRoleInference()` and the `RoleInferenceResult` class have been deleted. The Mercure queue is the single live role delivery path. `RoleInferenceService` now only provides `getCurrentMapping()` for snapshot lookups.
- **Remaining risk:** None. Single delivery path.

### 3. Session lifecycle ~~is split across three in-memory stores~~ (MITIGATED)
- **Files:** `strands_agents/session_lifecycle.py`, `strands_agents/api/server.py:217-218`, `strands_agents/tools/assign_roles.py`
- **Status:** `SessionLifecycle` class now coordinates registration and teardown with per-session `asyncio.Lock`. `lifecycle.destroy()` atomically cleans up active session + role state under lock. `assign_roles._session_states` access is protected by `threading.Lock`.
- **Remaining risk:** `SessionStore` transcript data still has its own TTL eviction independent of lifecycle — long-idle sessions may have transcript data evicted while role state persists.

### 4. Stream input format is a cross-layer contract ~~not auto-detected~~ (MITIGATED)
- **Files:** `strands_agents/nemo_session.py:_validate_audio_format`, `.env.example`, `templates/scribe/index.html.twig`
- **Status:** First-chunk format validation now rejects WebM/WAV bytes when configured for PCM (raises `ValueError` with clear message). WebM mode warns if magic bytes are missing. The browser still hardcodes PCM via `PcmStreamer`.
- **Remaining risk:** Misconfiguration is detected on first chunk but causes session failure rather than auto-correction.

### 5. Browser-facing WebSocket and Mercure URLs are passed through unchanged
- **Files:** `.env.example:17-24`, `.env.example:53-55`, `docker-compose.yml:109-111`, `src/Controller/ScribeController.php:54-64`, `templates/scribe/index.html.twig:228-235`
- **What breaks:** Host-only defaults such as `localhost:48101` and `localhost:48137` work for the developer machine but fail for remote clients or alternate hostnames unless explicitly overridden end to end.
- **Evidence:** Symfony injects the configured URLs directly into the browser config object; the browser then uses them as-is for WebSocket and Mercure connections.

### 6. NeMo is a fixed-capacity singleton with no in-process recovery
- **Files:** `strands_agents/api/server.py:123-127`, `strands_agents/api/server.py:180-201`, `docker-compose.yml:48-54`, `docker-compose.yml:83-88`
- **What breaks:** GPU exhaustion, model-load failure, or degraded model state requires a process/container restart. Only two concurrent NeMo executor workers are allowed, regardless of hardware.
- **Evidence:** The NeMo pipeline is created once during lifespan startup and shared for all sessions; the executor and GPU reservation are hardcoded.

### 7. WebSocket reconnect grace period keeps sessions alive after disconnect
- **Files:** `strands_agents/session_lifecycle.py:schedule_destroy`, `strands_agents/api/server.py:transcribe_stream`, `strands_agents/api/server.py:_periodic_cleanup`
- **Status:** On WebSocket disconnect, `schedule_destroy()` delays session cleanup by `SESSION_RECONNECT_GRACE_SECONDS` (default 30). If the same `session_id` reconnects within the window, `register()` cancels the pending destroy and the existing `TranscriptionSession` (audio buffer + transcript state) is reused.
- **What could break:** During the grace window, `_session_modes`, `_mercure_event_ids`, and role state remain alive. The periodic cleanup treats sessions with pending destroys as "live" to avoid premature eviction. If the grace period is set very long, memory usage grows because audio buffers are not released.
- **Evidence:** `lifecycle._pending_destroys` dict holds `asyncio.Task` objects for each scheduled destroy.

### 8. Terraform provisions DynamoDB, but runtime session state is still memory-only
- **Files:** `infra/terraform/environments/prod/main.tf:82-90`, `infra/terraform/environments/prod/main.tf:142-146`, `strands_agents/session.py:28-29`, `strands_agents/session.py:37-39`
- **What breaks:** Production infrastructure implies persisted session storage, but live code still uses an in-memory `SessionStore` with TTL/LRU limits and loses data on restart.
- **Evidence:** Terraform exports a DynamoDB table name to the agent container, while `SessionStore` keeps transcript data in a Python `OrderedDict` only.
