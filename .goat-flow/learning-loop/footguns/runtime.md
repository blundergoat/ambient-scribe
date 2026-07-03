---
category: runtime
last_reviewed: 2026-07-04
---

# Runtime / Session / Mercure Footguns

## Footgun: Mercure publish failure only surfaces as a browser banner
**Status:** active | **Created:** 2026-03-21 | **Evidence:** ACTUAL_MEASURED

- **Files:** `strands_agents/api/server.py` (search: "async def publish_to_mercure")
- **Files:** `strands_agents/api/server.py` (search: "system_error")
- **Files:** `public/js/scribe.js` (search: "if (msg.type === 'system_error') showSystemBanner")
- **Files:** `public/js/scribe.js` (search: "const banner = document.getElementById('systemBanner')")
- **What breaks:** If Mercure is down, transcription can still run on the server but live segments stop appearing in the browser. The user gets a one-time `system_error` banner, not a fallback delivery path.
- **Evidence:** `publish_to_mercure()` returns `False` after retries, the WebSocket handler emits a `system_error` frame on the first failed raw publish, and the browser only renders that message into `#systemBanner`.

## Footgun: Session lifecycle split across active sessions, transcript storage, and role state
**Status:** active | **Created:** 2026-03-21 | **Evidence:** ACTUAL_MEASURED

- **Files:** `strands_agents/session_lifecycle.py` (search: "async def destroy")
- **Files:** `strands_agents/api/server.py` (search: "async def _periodic_cleanup")
- **Files:** `strands_agents/tools/assign_roles.py` (search: "def cleanup_session")
- **Files:** `strands_agents/session.py` (search: "def _evict_expired")
- **What breaks:** `SessionLifecycle` owns active WebSocket sessions, `assign_roles` owns role state, and `SessionStore` owns transcript TTL eviction. Those stores are coordinated but not unified, so cleanup timing can diverge.
- **Evidence:** `destroy()` cleans active sessions plus role state, `_periodic_cleanup()` separately reaps orphaned queue/mode/event-id entries, and `SessionStore` independently expires transcript data on access.

## Footgun: NeMo is a fixed-capacity singleton with no in-process recovery
**Status:** active | **Created:** 2026-03-21 | **Evidence:** ACTUAL_MEASURED

- **Files:** `strands_agents/api/server.py` (search: "NEMO_MAX_WORKERS = int")
- **Files:** `strands_agents/api/server.py` (search: "app.state.nemo_pipeline = NemoPipeline()")
- **Files:** `docker-compose.yml` (search: "devices:")
- **Files:** `docker-compose.yml` (search: "NEMO_MAX_WORKERS=${NEMO_MAX_WORKERS:-2}")
- **What breaks:** GPU exhaustion, model-load failure, or degraded model state requires a process restart. The executor is capped by `NEMO_MAX_WORKERS` and the service reserves exactly one GPU regardless of host capacity.
- **Evidence:** FastAPI creates one `NemoPipeline()` during lifespan startup, one shared `ThreadPoolExecutor`, and the Compose service reserves a single NVIDIA device.

## Footgun: Reconnect grace window keeps session state alive after disconnect
**Status:** active | **Created:** 2026-03-21 | **Evidence:** ACTUAL_MEASURED

- **Files:** `strands_agents/session_lifecycle.py` (search: "async def register")
- **Files:** `strands_agents/session_lifecycle.py` (search: "async def schedule_destroy")
- **Files:** `strands_agents/api/server.py` (search: "pending_ids = {sid for sid in lifecycle._pending_destroys}")
- **Files:** `strands_agents/api/server.py` (search: "register() cancels pending destroys")
- **Files:** `strands_agents/api/server.py` (search: "await lifecycle.schedule_destroy")
- **What breaks:** During the grace period, audio buffers, `_session_modes`, `_mercure_event_ids`, and role state remain alive so a reconnect can resume. Long grace windows trade resilience for memory growth.
- **Evidence:** `register()` cancels pending destroys, `schedule_destroy()` stores delayed tasks in `_pending_destroys`, the server treats pending destroys as live sessions, and `transcribe_stream()` schedules cleanup instead of destroying immediately.

## Footgun: Live transcription contracts are split across Docker, Twig, JS, and FastAPI
**Status:** active | **Created:** 2026-07-04 | **Evidence:** ACTUAL_MEASURED
**Source:** git history (auto-seeded)
**hallucination-risk:** high

- **Files:** `docker-compose.yml` (search: "NEMO_WEBSOCKET_URL=ws://localhost:${AGENT_PORT:-48101}")
- **Files:** `templates/scribe/index.html.twig` (search: "const CONFIG =")
- **Files:** `public/js/scribe.js` (search: "new WebSocket(`${CONFIG.wsUrl}/ws/transcribe/")
- **Files:** `strands_agents/api/server.py` (search: "async def transcribe_stream")
- **Git evidence:** `35bceb4` touched `docker-compose.yml`, `strands_agents/api/server.py`, `strands_agents/nemo_pipeline.py`, `strands_agents/nemo_session.py`, `templates/scribe/index.html.twig`, and `tests/python/test_api.py` to restore live transcription and healthcheck contracts.
- **Git evidence:** `d045b6c` touched `docker-compose.yml`, `scripts/start-dev.sh`, `templates/scribe/index.html.twig`, and scenarios to harden the dev workflow and UI.
- **Git evidence:** churn scan over the last 50 commits found `templates/scribe/index.html.twig` in 12 commits, `strands_agents/api/server.py` in 10 commits, and `docker-compose.yml` in 10 commits.
- **What breaks:** A local-looking change to ports, injected config, WebSocket URL construction, mode query parameters, or FastAPI route handling can silently break the live path because no single schema owns the browser-to-agent contract.
- **Evidence:** The session URL is composed from Docker/Symfony/Twig-provided config in the browser, while FastAPI separately owns route validation and mode handling.
