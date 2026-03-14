# Footguns Index

Cross-domain architectural landmines — real coupling and hidden dependencies with file:line evidence.

## Format

Each entry: date, title, files involved, what breaks, evidence.

## Entries

### 1. Mercure JWT silent publish failure
- **Files:** `strands_agents/api/server.py` (`publish_to_mercure()`), `.env.example`
- **What breaks:** If `MERCURE_JWT_SECRET` is not configured or Mercure is unreachable, `publish_to_mercure()` logs a warning but silently drops segments. Browser receives nothing via SSE — no error visible to user.
- **Evidence:** `server.py` wraps Mercure publish in try/except with `logger.warning()` only.
- **Created:** 2026-03-14

### 2. Session cleanup races with role inference
- **Files:** `strands_agents/api/server.py` (WebSocket disconnect handler), `strands_agents/tools/assign_roles.py` (`RoleMappingState`)
- **What breaks:** WebSocket disconnect triggers `cleanup_session()` which deletes `RoleMappingState`. If PHP is still streaming role inference via `/session/{id}/roles/stream`, the endpoint crashes or returns stale data mid-iteration.
- **Evidence:** `cleanup_session()` called in `except WebSocketDisconnect` block; `roles_stream()` reads same state concurrently.
- **Created:** 2026-03-14

### 3. NeMo GPU singleton has no recovery
- **Files:** `strands_agents/nemo_pipeline.py` (NemoPipeline), `strands_agents/api/server.py` (startup)
- **What breaks:** NeMo models loaded once at FastAPI startup as singleton on `app.state.nemo_pipeline`. If GPU memory error or model crash, no reload mechanism. Container restart required. `max_workers=2` in ThreadPoolExecutor is hardcoded — no config to tune for GPU capacity.
- **Evidence:** `NemoPipeline` instantiated once in `lifespan()`, shared via `app.state`.
- **Created:** 2026-03-14

### 4. PHP↔Python API contract not validated
- **Files:** `src/Service/RoleInferenceService.php` (`streamRoleInference()`), `strands_agents/api/server.py` (`roles_stream()`)
- **What breaks:** PHP expects SSE events with `mapping` and `confidence` keys. Python endpoint produces these, but neither side validates the contract. A format change in Python silently breaks PHP's `$onUpdate` callback.
- **Evidence:** `RoleInferenceService::streamRoleInference()` accesses `$event['mapping']` without validation.
- **Created:** 2026-03-14

### 5. Three independent session state buckets
- **Files:** `strands_agents/nemo_session.py` (TranscriptionSession), `strands_agents/session.py` (SessionStore), `strands_agents/tools/assign_roles.py` (RoleMappingState)
- **What breaks:** Same session UUID keys three separate in-memory stores with no coordinated lifecycle. Partial cleanup (e.g., WebSocket disconnect cleans TranscriptionSession but not SessionStore) leaves orphaned state. All lost on container restart.
- **Evidence:** Each file has independent dict/store keyed by `session_id`.
- **Created:** 2026-03-14

### 6. Audio format assumed, not detected
- **Files:** `strands_agents/nemo_session.py` (AudioBuffer), `templates/scribe/index.html.twig` (MediaRecorder)
- **What breaks:** `AudioBuffer` hardcodes 16kHz 16-bit PCM. Browser MediaRecorder may send Opus/WebM depending on browser. No format detection or conversion — wrong format produces garbage transcriptions silently.
- **Evidence:** `nemo_session.py` AudioBuffer constructor sets `sample_rate=16000`, `sample_width=2`.
- **Created:** 2026-03-14

### 7. DynamoDB provisioned in Terraform but unused in code
- **Files:** `infra/terraform/environments/prod/main.tf` (DynamoDB module), `strands_agents/session.py` (SessionStore)
- **What breaks:** Production Terraform creates DynamoDB table for session storage, but code uses in-memory `SessionStore` with `MAX_SESSIONS=100` and `SESSION_TTL_SECONDS=7200`. Production will hit memory limits; DynamoDB sits empty.
- **Evidence:** `session.py` uses Python dict; no DynamoDB SDK import or integration.
- **Created:** 2026-03-14

### 8. NEMO_WEBSOCKET_URL environment mismatch
- **Files:** `.env.example`, `templates/scribe/index.html.twig`, `docker-compose.yml`
- **What breaks:** `.env.example` defaults `NEMO_WEBSOCKET_URL=ws://localhost:8001`. In Docker with external browser access, `localhost` doesn't resolve to the container. WebSocket silently fails with CORS error — no user-facing feedback.
- **Evidence:** Twig template reads `nemo_websocket_url` from Symfony config to construct WebSocket connection.
- **Created:** 2026-03-14

## Propagation

- Entries 1-6 → `strands_agents/CLAUDE.md` (local)
- Entry 7 → `infra/CLAUDE.md` (local)
- Entry 8 → root-level (spans .env, docker-compose.yml, templates/) — no single directory qualifies for local CLAUDE.md
