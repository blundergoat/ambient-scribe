# Milestone 2 — Audio Pipeline End-to-End

**Timeline:** Weekend 2 (~5-6 hours)
**Status:** In Progress (live flow verified 2026-03-15; accuracy + long-session upgrades in M2.5)
**Dependencies:** Milestone 1 complete

---

## Objective

Live mic audio from browser → WebSocket → NeMo → transcript segments back to browser via Mercure. Working end-to-end with `spk_0`/`spk_1`/`spk_N` labels (role inference is M3).

---

## Tasks

### 2.1 Batch Pipeline — COMPLETE

- [x] `NemoPipeline` wraps Sortformer + Parakeet with `transcribe_file()` and `transcribe_buffer()`
- [x] Returns `TranscriptionResult` (segments list + raw output for debugging)
- [x] `POST /transcribe/file` endpoint for batch mode
- [x] `publish_to_mercure()` with 3-retry exponential backoff, JWT resolution, boolean return
- [ ] Verify batch endpoint with a real WAV fixture + loaded NeMo models

### 2.2 Chunked Processing — COMPLETE

- [x] `TranscriptionSession` wraps per-session audio buffer + deduplication
- [x] `AudioBuffer` with byte-based safety cap (`NEMO_BUFFER_MAX_DURATION`)
- [x] PCM input validated on first chunk (rejects WebM/WAV magic bytes when PCM configured)
- [x] WebM fallback path via ffmpeg (`NEMO_STREAM_INPUT_FORMAT=webm`)
- [x] **Decision: Browser-side PCM is the PoC path** (lowest latency, no ffmpeg in hot path)

### 2.3 WebSocket Endpoint — COMPLETE

- [x] `WS /ws/transcribe/{session_id}` accepts binary PCM chunks
- [x] NeMo inference in `ThreadPoolExecutor(max_workers=2)` — does not block event loop
- [x] NeMo models loaded once at startup via FastAPI lifespan, shared across sessions
- [x] Segments published to Mercure immediately after NeMo processing
- [x] Role inference queue integration (enqueue after raw segment publish)
- [x] Final transcription pass on WebSocket disconnect
- [x] Error events published to Mercure on failure
- [x] `/health` endpoint returns 200 (ok) or 503 (degraded, model load failed)
- [x] Verified live: browser → WebSocket → NeMo → Mercure → browser (2026-03-15)

### 2.4 Observability — COMPLETE

- [x] Correlation IDs per session (ContextVar + middleware), propagated through all logs
- [x] Per-chunk timing: inference_ms, total_ms, segment count
- [x] Periodic VRAM logging (every 10th chunk via `nvidia-smi`)
- [x] End-to-end latency tracking (chunk receive → Mercure publish)
- [x] Standard `logging` module (structlog was planned but not adopted)

### 2.5 Browser Audio Capture — COMPLETE

- [x] `PcmStreamer` class: 16kHz mono PCM via Web Audio API, 5-second chunk interval
- [x] Start/Stop recording controls
- [x] Mercure SSE subscription for transcript segments via `StreamOrchestrator`
- [x] Auto-reconnect with exponential backoff (1s to 30s)
- [x] Multi-topic management (raw segments + role updates)
- [x] Segments rendered with speaker labels and timestamps

### 2.6 Transcript UI — COMPLETE

- [x] 6 interaction modes: Medical, Meeting, Interview, TV/Media, Lecture, General
- [x] Mode-specific role labels and avatars (e.g. Doctor/Patient, Host/Guest, Speaker A/B)
- [x] Light/dark theme with persistence
- [x] Download as JSON and plain text
- [x] Timer, segment counter, session reset
- [x] System error banner on Mercure publish failure

### 2.7 Docker Compose — COMPLETE

- [x] 3 services: nemo-agent (GPU), app (PHP), mercure (SSE hub)
- [x] Health checks with appropriate start periods (60s for NeMo model loading)
- [x] Hot-reload volume mounts for dev
- [x] 16 environment variables for nemo-agent configuration
- [x] Verified: all 3 services start and communicate (2026-03-15)

### 2.8 Session State Management (unplanned, emerged during implementation)

- [x] `SessionStore` — in-memory transcript storage with TTL, LRU eviction, max segments per session
- [x] `SessionLifecycle` — atomic session registration/teardown with per-session async locks
- [x] SSE consumer tracking to prevent cleanup during active reads
- [x] `assign_roles` state management: `RoleMappingState` with mapping history, flip detection, confidence tracking

### 2.9 Tests

- [x] `test_nemo_pipeline.py` — Segment, AudioBuffer, TranscriptionSession, pipeline parsing
- [x] `test_nemo_session.py` — session creation, chunk processing (needs update: references stale `_webm_accumulator`)
- [x] `test_cleanup_race.py` — lifecycle destroy with SSE consumers
- [x] `test_concurrent_sessions.py` — multi-session isolation
- [x] `test_mercure_failures.py` — publish retry and failure handling
- [x] `test_inference_queue.py` — sequential processing, role publication
- [x] **Fix `test_api.py`** — missing imports (`ThreadPoolExecutor`, `httpx`, `WebSocketDisconnect`, `asyncio`) and undefined `client` fixture
- [x] **Fix `test_nemo_session.py`** — references `_webm_accumulator` which no longer exists
- [x] PHPUnit: 28 tests, 116 assertions passing. PHPStan Level 10 clean.

---

## Exit Criteria

- [x] Browser captures mic audio and sends chunks over WebSocket
- [x] FastAPI receives chunks, feeds NeMo pipeline, gets transcript segments
- [x] NeMo inference runs in thread pool — does not block the async event loop
- [x] Segments published to Mercure, rendered in browser in real-time
- [x] Can see `spk_0`, `spk_1`, `spk_2` labelled text appearing as you speak
- [x] NeMo models loaded once at startup, shared across sessions
- [x] Structured logging with correlation IDs and timing in place
- [x] **Python tests pass** (98 tests all green)

---

## Known Limitations (addressed in M2.5)

- Growing buffer re-processes entire audio each chunk — O(n^2) total work, falls behind real-time at ~4 minutes
- Proportional word-to-speaker distribution is a heuristic — wrong when speech density varies across speakers
- No VRAM guard — buffer can exceed GPU memory on long sessions
- No sliding window — VRAM and processing time grow unboundedly
- `is_interim` field exists on Segment but is never set to `true`
