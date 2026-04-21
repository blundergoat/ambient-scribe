---
category: runtime
last_reviewed: 2026-04-22
---

# Runtime / Session / Mercure Footguns

## Footgun: Mercure publish failure only surfaces as a browser banner
**Status:** active | **Created:** 2026-03-21 | **Evidence:** ACTUAL_MEASURED

- **Files:** `strands_agents/api/server.py:335-395`
- **Files:** `strands_agents/api/server.py:710-725`
- **Files:** `public/js/scribe.js:392-395`
- **Files:** `public/js/scribe.js:940-944`
- **What breaks:** If Mercure is down, transcription can still run on the server but live segments stop appearing in the browser. The user gets a one-time `system_error` banner, not a fallback delivery path.
- **Evidence:** `publish_to_mercure()` returns `False` after retries, the WebSocket handler emits a `system_error` frame on the first failed raw publish, and the browser only renders that message into `#systemBanner`.

## Footgun: Session lifecycle split across active sessions, transcript storage, and role state
**Status:** active | **Created:** 2026-03-21 | **Evidence:** ACTUAL_MEASURED

- **Files:** `strands_agents/session_lifecycle.py:68-98`
- **Files:** `strands_agents/api/server.py:240-285`
- **Files:** `strands_agents/tools/assign_roles.py:170-179`
- **Files:** `strands_agents/session.py:104-133`
- **What breaks:** `SessionLifecycle` owns active WebSocket sessions, `assign_roles` owns role state, and `SessionStore` owns transcript TTL eviction. Those stores are coordinated but not unified, so cleanup timing can diverge.
- **Evidence:** `destroy()` cleans active sessions plus role state, `_periodic_cleanup()` separately reaps orphaned queue/mode/event-id entries, and `SessionStore` independently expires transcript data on access.

## Footgun: NeMo is a fixed-capacity singleton with no in-process recovery
**Status:** active | **Created:** 2026-03-21 | **Evidence:** ACTUAL_MEASURED

- **Files:** `strands_agents/api/server.py:198-202`
- **Files:** `strands_agents/api/server.py:217-237`
- **Files:** `docker-compose.yml:48-54`
- **Files:** `docker-compose.yml:59-61`
- **What breaks:** GPU exhaustion, model-load failure, or degraded model state requires a process restart. The executor is capped by `NEMO_MAX_WORKERS` and the service reserves exactly one GPU regardless of host capacity.
- **Evidence:** FastAPI creates one `NemoPipeline()` during lifespan startup, one shared `ThreadPoolExecutor`, and the Compose service reserves a single NVIDIA device.

## Footgun: Reconnect grace window keeps session state alive after disconnect
**Status:** active | **Created:** 2026-03-21 | **Evidence:** ACTUAL_MEASURED

- **Files:** `strands_agents/session_lifecycle.py:41-67`
- **Files:** `strands_agents/session_lifecycle.py:100-138`
- **Files:** `strands_agents/api/server.py:250-253`
- **Files:** `strands_agents/api/server.py:661-678`
- **Files:** `strands_agents/api/server.py:781-788`
- **What breaks:** During the grace period, audio buffers, `_session_modes`, `_mercure_event_ids`, and role state remain alive so a reconnect can resume. Long grace windows trade resilience for memory growth.
- **Evidence:** `register()` cancels pending destroys, `schedule_destroy()` stores delayed tasks in `_pending_destroys`, the server treats pending destroys as live sessions, and `transcribe_stream()` schedules cleanup instead of destroying immediately.
