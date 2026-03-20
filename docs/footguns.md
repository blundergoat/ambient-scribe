# Footguns

Cross-domain architectural landmines with real coupling and file:line evidence.

Each entry names the live files involved, what breaks, and the evidence.

### 1. Mercure publish failure only surfaces as a browser banner
- **Files:** `strands_agents/api/server.py:335-395`, `strands_agents/api/server.py:710-725`, `public/js/scribe.js:392-395`, `public/js/scribe.js:940-944`
- **What breaks:** If Mercure is down, transcription can still run on the server but live segments stop appearing in the browser. The user gets a one-time `system_error` banner, not a fallback delivery path.
- **Evidence:** `publish_to_mercure()` returns `False` after retries, the WebSocket handler emits a `system_error` frame on the first failed raw publish, and the browser only renders that message into `#systemBanner`.

### 2. Session lifecycle is still split across active sessions, transcript storage, and role state
- **Files:** `strands_agents/session_lifecycle.py:68-98`, `strands_agents/api/server.py:240-285`, `strands_agents/tools/assign_roles.py:170-179`, `strands_agents/session.py:104-133`
- **What breaks:** `SessionLifecycle` owns active WebSocket sessions, `assign_roles` owns role state, and `SessionStore` owns transcript TTL eviction. Those stores are coordinated but not unified, so cleanup timing can diverge.
- **Evidence:** `destroy()` cleans active sessions plus role state, `_periodic_cleanup()` separately reaps orphaned queue/mode/event-id entries, and `SessionStore` independently expires transcript data on access.

### 3. Audio format is a browser/env/Python contract
- **Files:** `public/js/scribe.js:193-255`, `public/js/scribe.js:367-383`, `.env.example:28-33`, `docker-compose.yml:57-63`, `strands_agents/nemo_session.py:250-268`
- **What breaks:** The browser always streams 16 kHz PCM through `PcmStreamer`, while the server trusts `NEMO_STREAM_INPUT_FORMAT`. If env/config drifts to `webm`, live sessions fail on the first chunk.
- **Evidence:** `PcmStreamer` down-samples and emits 16-bit PCM bytes, Docker and `.env.example` expose `NEMO_STREAM_INPUT_FORMAT`, and `_validate_audio_format()` rejects mismatched magic bytes after the session starts.

### 4. Browser-facing WebSocket and Mercure URLs are passed straight through
- **Files:** `.env.example:19-23`, `.env.example:79-81`, `docker-compose.yml:119-123`, `src/Controller/ScribeController.php:54-56`, `src/Controller/ScribeController.php:81-90`, `templates/scribe/index.html.twig:825-833`, `public/js/scribe.js:373-380`, `public/js/scribe.js:530-535`
- **What breaks:** Host-only defaults like `localhost:48101` and `localhost:48137` work on the developer machine but fail for remote clients or alternate hostnames unless every layer is overridden together.
- **Evidence:** Symfony injects `ws_url` and `mercure_url` directly into `CONFIG`, and `public/js/scribe.js` uses those values as-is for the WebSocket and Mercure subscriptions.

### 5. NeMo is a fixed-capacity singleton with no in-process recovery
- **Files:** `strands_agents/api/server.py:198-202`, `strands_agents/api/server.py:217-237`, `docker-compose.yml:48-54`, `docker-compose.yml:59-61`
- **What breaks:** GPU exhaustion, model-load failure, or degraded model state requires a process restart. The executor is capped by `NEMO_MAX_WORKERS` and the service reserves exactly one GPU regardless of host capacity.
- **Evidence:** FastAPI creates one `NemoPipeline()` during lifespan startup, one shared `ThreadPoolExecutor`, and the Compose service reserves a single NVIDIA device.

### 6. The reconnect grace window keeps session state alive after disconnect
- **Files:** `strands_agents/session_lifecycle.py:41-67`, `strands_agents/session_lifecycle.py:100-138`, `strands_agents/api/server.py:250-253`, `strands_agents/api/server.py:661-678`, `strands_agents/api/server.py:781-788`
- **What breaks:** During the grace period, audio buffers, `_session_modes`, `_mercure_event_ids`, and role state remain alive so a reconnect can resume. Long grace windows trade resilience for memory growth.
- **Evidence:** `register()` cancels pending destroys, `schedule_destroy()` stores delayed tasks in `_pending_destroys`, the server treats pending destroys as live sessions, and `transcribe_stream()` schedules cleanup instead of destroying immediately.

### 7. Terraform still advertises DynamoDB while runtime persists only memory or SQLite
- **Files:** `infra/terraform/environments/prod/main.tf:82-90`, `infra/terraform/environments/prod/main.tf:142-146`, `strands_agents/api/server.py:298-310`, `strands_agents/session.py:28-29`, `strands_agents/storage.py:54-76`
- **What breaks:** Production infrastructure exports a DynamoDB table name, but runtime persistence only switches between the in-memory `SessionStore` and local SQLite. There is no DynamoDB-backed runtime path.
- **Evidence:** Terraform sets `DYNAMODB_TABLE`, `create_storage_backend()` chooses only `SessionStore` or `SqliteBackend`, and the in-memory store still documents restart data loss.

### 8. Bind-mounted local dev can hide image-only runtime issues
- **Files:** `Dockerfile:36-43`, `docker-compose.yml:84-87`, `docker-compose.yml:108-114`
- **What breaks:** Local Compose uses bind mounts for both the app and agent, so hot reload can look healthy even when the built images would still ship stale files or broken import paths.
- **Evidence:** The Dockerfile bakes the app into `/app`, the agent image expects `/app`, and Compose overrides both services with host mounts during local development.

### 9. The Ollama role model must support tool calling
- **Files:** `.env.example:67-74`, `docker-compose.yml:73-77`, `strands_agents/agents/transcription_agent.py:76-84`, `strands_agents/agents/transcription_agent.py:205-215`, `strands_agents/agents/transcription_agent.py:235-240`, `strands_agents/tools/assign_roles.py:207-244`
- **What breaks:** `assign_roles` is wired as a Strands tool. Models without tool/function calling support fall back to free-text JSON behaviour, which is slower and less reliable.
- **Evidence:** The agent prompt explicitly says it MUST call `assign_roles`, the agent constructor passes `tools=[assign_roles]`, and the default Ollama model is pinned in both `.env.example` and `docker-compose.yml`.
