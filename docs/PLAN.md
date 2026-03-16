# Next Steps Plan

**Created:** 2026-02-20
**Updated:** 2026-03-16
**Current state:** M0–M2 complete, M3 code-complete (tool wired, Ollama default fixed, tests pass), live verification pending

---

## Priority 0: Unblock Preflight (stop-the-line)

`composer preflight` fails: PHP coverage 0% (0/132 lines, threshold 80%). This blocks every future task per the stop-the-line rule.

- [ ] Create `tests/php/Controller/ScribeControllerTest.php`
  - Test `GET /` redirects to `/scribe`
  - Test `GET /scribe` renders template with session_id, ws_url, mercure topics
  - Test `GET /scribe/{id}/history` returns JSON (mock StrandsClient)
  - Test `GET /scribe/{id}/history` handles AgentErrorException → 502
  - Test `GET /scribe/{id}/history` handles StrandsException → 503
  - Test `POST /scribe/{id}/roles/stream` returns streamed JSON (mock RoleInferenceService)
  - Test `GET /scribe/{id}/roles` returns mapping JSON (mock RoleInferenceService)
- [ ] Create `tests/php/Service/RoleInferenceServiceTest.php`
  - Test `streamRoleInference()` with mocked StrandsClient SSE
  - Test `getCurrentMapping()` with mocked StrandsClient
  - Test error handling paths
- [ ] `composer preflight` passes (all green)

**Estimate:** ~1 session. This is the most important task — everything else is blocked by a red preflight.

---

## ~~Priority 1: GPU Verification Session (M2 sign-off)~~ DONE (2026-02-26)

Verified via `scripts/m2-verify.sh` + `scripts/m2-debug-live.sh` + manual browser test.

- [x] `docker compose up --build` — all 3 services start (nemo-agent, mercure, app)
- [x] NeMo models load at startup (Sortformer + Parakeet restored from cache)
- [x] `POST /transcribe/file` — 87 segments, 7.56s inference, two speakers
- [x] Browser → record audio → WebSocket connects, chunks stream every 5s
- [x] Segments appear in browser via Mercure SSE (75 segments in 1m42s session)
- [x] `/health` responds during active inference (0ms, event loop not blocked)
- [x] VRAM at 38% after inference (6302/16303 MB)

**Bugs found and fixed:**
- [x] **StreamOrchestrator `_active` ordering bug** — first EventSource topic (`/raw`) silently skipped because `_active` was `false` when `_connect()` ran. Fix: set `_active = true` before `_connect()` in `subscribe()`.

**Bugs found, deferred:**
- [ ] Duplicate segments: growing buffer re-publishes full transcript each chunk — browser appends all, needs dedup/replace
- [ ] NeMo concurrency error: `"Cannot unfreeze partially"` when sessions overlap — needs mutex around model inference
- [ ] Python logging invisible: `logging.getLogger(__name__)` with no `basicConfig(level=INFO)` — all INFO calls swallowed
- [ ] `websocket.chunk_e2e` latency logs not visible (consequence of logging level issue)

---

## Priority 2: M3 — Role Attribution + Agent Intelligence

This is the demo's centrepiece. Prerequisites: preflight green, M2 GPU-verified (DONE).

### Session A (~2 hours): Agent + Queue

- [ ] Fill `strands_agents/transcription_agent.py` stub — Strands Agent with Bedrock Haiku 4.5
  - System prompt for DOCTOR/PATIENT reasoning (medical terminology, question patterns, greeting conventions)
  - Edge case handling: monologues, silence, Sortformer label flips
- [ ] Implement per-session inference queue in server.py
  - `asyncio.Queue` per session — sequential processing, no race conditions
  - Worker auto-cleans after 60s idle
  - Wire into WebSocket handler: raw segments published immediately, role inference enqueued
- [ ] Fill `assign_roles` tool — programmatic state management (not LLM reasoning)
  - Persist mapping across invocations
  - Flip detection via mapping history comparison
  - Running confidence average
- [ ] Tests for inference queue: sequential processing, worker cleanup, concurrent sessions

### Session B (~2 hours): Progressive UX + PHP Wiring

- [ ] Progressive confidence UX in `index.html.twig`
  - Cold start: `SPEAKER 1` / `SPEAKER 2` with muted styling + "Identifying speakers..." indicator
  - Threshold crossed (confidence >= 0.8): switch to `DOCTOR` / `PATIENT`, retroactive relabeling
  - Flip correction: swap labels, brief notification
- [ ] Colour-coded transcript styling (blue=DOCTOR, green=PATIENT, grey=UNKNOWN) + dark mode
- [ ] Wire `ScribeController` to pass both Mercure topics
- [ ] Browser subscribes to both `raw` and `roles` SSE topics
- [ ] Tests for role inference: flip detection, progressive confidence, single-speaker stability
- [ ] `composer preflight` green

---

## Priority 3: M4 — Polish + Open Source (stretch)

Only after M3 is working end-to-end:

- [ ] Replay demo mode (self-recorded WAV, simulated real-time playback)
- [ ] Clinical summary generation at consultation end
- [ ] UI polish: mobile responsive, error states, export transcript
- [ ] Open source prep: LICENSE, CONTRIBUTING.md, audit fixtures for licensing
- [ ] Blog article: "Building a Real-Time Ambient Medical Scribe in 4 Weekends"

---

## Quick Reference: What's Blocking What

```
Preflight green ←── PHP tests (Priority 0)        ✔ DONE
      │
      ▼
M2 sign-off ←────── GPU verification (Priority 1) ✔ DONE
      │
      ▼
M3 build ←───────── Agent + queue + UX (Priority 2) ✔ CODE COMPLETE — live verification pending
      │
      ▼
M4 polish ←──────── Demo mode + blog (Priority 3)  ← NEXT
```
