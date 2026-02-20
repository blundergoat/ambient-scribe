# Next Steps Plan

**Created:** 2026-02-20
**Current state:** M0 code-complete, M1 complete, M2 code-complete (GPU verification pending)

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

## Priority 1: GPU Verification Session (M2 sign-off)

All M2 code is written. These items need `docker compose up --build` on the GPU machine:

- [ ] `docker compose up --build` — verify all 3 services start (nemo-agent, mercure, app)
- [ ] Verify NeMo model loading at startup (watch logs for `server.startup.nemo_models_loaded`)
- [ ] Upload test WAV via `POST /transcribe/file` — verify speaker-attributed segments returned
- [ ] Open browser → record audio → verify WebSocket connection + chunk streaming
- [ ] Verify segments appear in browser via Mercure SSE
- [ ] Verify `websocket.chunk_e2e` latency logs are emitted
- [ ] Hit `/health` during active transcription — verify it responds (event loop not blocked)
- [ ] Run WebSocket lifecycle test: connect → send chunks → disconnect → verify finalize runs

**Outcome:** Mark remaining M2 exit criteria as done, or log specific issues to fix.

---

## Priority 2: M3 — Role Attribution + Agent Intelligence

This is the demo's centrepiece. Prerequisites: preflight green, M2 GPU-verified.

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
Preflight green ←── PHP tests (Priority 0)
      │
      ▼
M2 sign-off ←────── GPU verification (Priority 1)
      │
      ▼
M3 build ←───────── Agent + queue + UX (Priority 2)
      │
      ▼
M4 polish ←──────── Demo mode + blog (Priority 3)
```
