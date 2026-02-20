# Milestone 2 — Audio Pipeline End-to-End

**Timeline:** Weekend 2 (~5-6 hours across 2 sessions)
**Status:** Not Started (stub files + initial tests created during M1 scaffold)
**Dependencies:** Milestone 1 complete (NeMo validated on RTX 5080, API surface documented, scaffold in place)

---

## Objective

Live mic audio from browser -> WebSocket -> NeMo -> transcript segments back to browser via Mercure. Working end-to-end demo with `spk_0`/`spk_1` labels (no role inference yet).

---

## Tasks

### 2.1 Offline Batch Pipeline First (Session A, ~1.5 hours)

> **Important:** Build the offline pipeline before adding WebSocket streaming. This isolates NeMo integration issues from WebSocket plumbing.

- [ ] Create `strands_agents/nemo_pipeline.py` — wraps NeMo models based on Milestone 1 API discovery:
  ```python
  class NemoPipeline:
      """Loaded once at startup, shared across sessions.

      The actual NeMo API calls here should match what was discovered
      in Milestone 1's nemo_test.py script. Do NOT assume independent
      sortformer.diarize() / parakeet.transcribe() calls — NeMo's
      multitalker pipeline is a composite recipe.
      """
      def __init__(self):
          # Load models per docs/nemo-api-notes.md findings
          ...

      def transcribe_file(self, audio_path: str) -> list[Segment]:
          """Process a complete audio file. Returns speaker-attributed segments."""
          ...
  ```
- [ ] Add a test HTTP endpoint: `POST /transcribe/file` (accepts WAV upload, returns JSON segments)
- [ ] Verify end-to-end: upload test WAV -> get back speaker-attributed segments
- [ ] Publish segments to Mercure from Python:
  ```python
  async def publish_to_mercure(session_id: str, data: dict):
      async with httpx.AsyncClient() as client:
          await client.post(MERCURE_HUB_URL, data={
              "topic": f"scribe/session/{session_id}",
              "data": json.dumps(data),
          }, headers={"Authorization": f"Bearer {MERCURE_JWT}"})
  ```
- [ ] Verify browser receives segments via Mercure SSE subscription

### 2.2 Chunked Processing Wrapper (Session A, ~1 hour)

- [ ] Create `strands_agents/nemo_session.py` — stateful session wrapper:
  ```python
  class TranscriptionSession:
      """Manages audio buffer and NeMo processing across chunks.

      Uses the buffer strategy decided in Milestone 1:
      - Growing buffer: re-process all audio each chunk (best consistency)
      - Sliding window: process last N seconds (constant cost)
      - Hybrid: full re-process every Nth chunk, cached otherwise
      """
      def __init__(self, session_id: str, pipeline: NemoPipeline):
          self.session_id = session_id
          self.pipeline = pipeline  # shared, NOT loaded per session
          self.buffer = AudioBuffer()

      def process_chunk(self, raw_audio: bytes) -> list[Segment]:
          """Synchronous — runs in executor thread to avoid blocking event loop."""
          pcm = decode_audio(raw_audio)
          self.buffer.append(pcm)
          return self.pipeline.transcribe_buffer(self.buffer.current_window())

      def finalize(self) -> list[Segment]:
          """Final processing on session end. Returns complete transcript."""
          return self.pipeline.transcribe_file_from_buffer(self.buffer.full_audio())
  ```
- [ ] Key design: `NemoPipeline` is a singleton loaded at startup; `TranscriptionSession` holds per-session state (audio buffer, accumulated transcript) and references the shared pipeline
- [ ] Handle audio format conversion (per Milestone 1 spike decision):
  - If PyAV: in-process WebM/Opus -> PCM 16kHz mono
  - If AudioWorklet: expect raw PCM Float32 from browser
  - If ffmpeg: persistent subprocess with pipe I/O (not per-chunk spawn)

### 2.3 FastAPI WebSocket Endpoint (Session B, ~1 hour)

- [ ] Add WebSocket endpoint to FastAPI:
  ```python
  import asyncio
  from concurrent.futures import ThreadPoolExecutor

  # Thread pool for GPU-bound NeMo inference — prevents blocking the async event loop
  nemo_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="nemo")

  @app.websocket("/ws/transcribe/{session_id}")
  async def transcribe_stream(websocket: WebSocket, session_id: str):
      await websocket.accept()
      session = TranscriptionSession(session_id, pipeline=app.state.nemo_pipeline)
      try:
          while True:
              audio_chunk = await websocket.receive_bytes()

              # Run GPU-bound NeMo inference in thread pool to avoid blocking event loop.
              # Without this, all other WebSocket connections and /health freeze during inference.
              loop = asyncio.get_event_loop()
              segments = await loop.run_in_executor(
                  nemo_executor,
                  session.process_chunk,
                  audio_chunk
              )

              for segment in segments:
                  await publish_to_mercure(f"scribe/session/{session_id}", {
                      "speaker": segment.speaker_id,
                      "text": segment.text,
                      "start": segment.start,
                      "end": segment.end,
                      "is_interim": segment.is_interim,
                  })
      except WebSocketDisconnect:
          # Run finalize in executor too
          await loop.run_in_executor(nemo_executor, session.finalize)
  ```
- [ ] Load NeMo models at FastAPI startup (not per-request):
  ```python
  @app.on_event("startup")
  async def load_models():
      app.state.nemo_pipeline = NemoPipeline()
  ```
- [ ] Keep existing `/health` endpoint for Docker healthchecks

> **Why `run_in_executor`?** NeMo inference is synchronous GPU-bound work. Running it directly inside an `async def` WebSocket handler blocks the entire FastAPI event loop. Every other WebSocket connection, the `/health` endpoint, and Mercure publishing all freeze during inference. The thread pool executor runs NeMo in a separate thread, keeping the event loop responsive.

### 2.4 Structured Logging and Observability

> **A multi-service streaming system is nearly impossible to debug without structured logging.** Add this early, not as polish.

- [ ] Add correlation IDs (UUID v7) per session, propagated through all log lines
- [ ] Log key events with timing:
  ```python
  import structlog
  logger = structlog.get_logger()

  # In WebSocket handler:
  logger.info("chunk_received", session_id=session_id, chunk_bytes=len(audio_chunk))
  logger.info("nemo_complete", session_id=session_id, segments=len(segments), duration_ms=elapsed)
  logger.info("mercure_published", session_id=session_id, topic=topic)
  ```
- [ ] Log VRAM usage periodically (every 10th chunk):
  ```python
  import subprocess
  def log_vram():
      result = subprocess.run(["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader"],
                              capture_output=True, text=True)
      logger.info("vram_usage", used=result.stdout.strip())
  ```
- [ ] Track end-to-end latency per chunk: timestamp at receive -> timestamp at Mercure publish

### 2.5 Browser Audio Capture (Session B, ~1 hour)

- [ ] **Decision: inline JS vs Stimulus**
  - If adding Stimulus: add `@hotwired/stimulus`, configure build step (Vite or Webpack Encore), add `package.json` — document this as explicit new scope
  - If staying inline (matching Summit pattern): write audio capture as plain JS in the Twig template
  - **Recommendation:** Stay inline for the PoC. Adding a JS build pipeline is scope creep for a 4-weekend project. Revisit in Milestone 4 if needed.
- [ ] Implement audio capture:
  ```javascript
  // Core logic (plain JS, no framework)
  const stream = await navigator.mediaDevices.getUserMedia({
      audio: { sampleRate: 16000, channelCount: 1 }
  });
  const ws = new WebSocket(`${wsUrl}/ws/transcribe/${sessionId}`);
  const recorder = new MediaRecorder(stream, {
      mimeType: 'audio/webm;codecs=opus'
  });
  recorder.ondataavailable = (e) => {
      if (e.data.size > 0 && ws.readyState === WebSocket.OPEN) {
          ws.send(e.data);
      }
  };
  recorder.start(5000); // 5-second chunks
  ```
- [ ] Add start/stop controls to Twig template
- [ ] Subscribe to Mercure SSE for transcript segments
- [ ] Render segments as they arrive: `spk_0` / `spk_1` with timestamps

### 2.6 Transcript UI — Basic Version

- [ ] Create `templates/scribe/index.html.twig`:
  - Start/Stop recording buttons
  - Recording status indicator
  - Transcript container with auto-scroll
  - Segments rendered as: `[spk_0 00:03] "What brings you in today?"`
- [ ] Basic styling (can reuse Summit's dark theme or keep minimal)

### 2.7 Docker Compose Integration

- [ ] Update `docker-compose.yml` with all services:
  ```yaml
  services:
    nemo-agent:
      build: ./docker/nemo
      deploy:
        resources:
          reservations:
            devices:
              - driver: nvidia
                count: 1
                capabilities: [gpu]
      environment:
        - MERCURE_HUB_URL=http://mercure/.well-known/mercure
        - MERCURE_JWT=${MERCURE_PUBLISHER_JWT}
      ports:
        - "8001:8001"
    mercure:
      image: dunglas/mercure
      # Same config as Summit
    php:
      build: ./docker/php
      # Updated env for scribe
  ```
- [ ] Verify all 3 services start and communicate
- [ ] Test full flow: `docker compose up --build` -> open browser -> record -> see transcript

### 2.8 Tests for NeMo Pipeline Wrapper

- [x] Create `tests/python/test_nemo_pipeline.py` (structural tests from scaffold — integration tests with real NeMo TBD):
  - [x] Test `Segment` dataclass creation and dict serialization
  - [x] Test `AudioBuffer` append, duration, max cap, empty state
  - [x] Test `TranscriptionSession` creation, process_chunk counter, finalize
  - [ ] Test `NemoPipeline.transcribe_file()` with fixture WAV → verify output structure (needs real NeMo)
  - [ ] Test audio format conversion (input bytes → PCM output)
- [x] Create `tests/python/test_api.py` (basic endpoint tests from scaffold):
  - [x] Test `/health` endpoint returns 200
  - [x] Test `/session/{id}/history` returns empty for nonexistent session
  - [ ] Test `POST /transcribe/file` endpoint with fixture WAV (needs real NeMo)
  - [ ] Test `/ws/transcribe/{session_id}` WebSocket connection lifecycle (needs real NeMo)
- [x] Add `pytest` and `pytest-asyncio` to `requirements-dev.txt`
- [x] All 21 tests passing

---

## Exit Criteria

- [ ] Browser captures mic audio and sends chunks over WebSocket
- [ ] FastAPI receives chunks, feeds NeMo pipeline, gets transcript segments
- [ ] **NeMo inference runs in thread pool** — does not block the async event loop
- [ ] Segments published to Mercure, rendered in browser in real-time
- [ ] Can see `spk_0` and `spk_1` labelled text appearing as you speak
- [ ] End-to-end latency under ~5 seconds (chunk interval + NeMo processing)
- [ ] NeMo models loaded once at startup, shared across sessions
- [ ] **Structured logging with correlation IDs and timing in place**
- [ ] **Python tests pass for NeMo pipeline wrapper and API endpoints**

---

## Key Risks for This Milestone

| Risk | Mitigation |
|---|---|
| NeMo streaming API not stable for chunked processing | Start with the offline batch endpoint (2.1) first. If chunked fails, fall back to processing accumulated audio as a growing file. |
| WebM/Opus chunks not independently decodable | WebM container headers must be re-injected per chunk, or accumulate a growing buffer. PyAV handles this better than raw ffmpeg. |
| `getUserMedia` requires HTTPS (secure context) | Use `localhost` for dev (exempt from HTTPS requirement). For non-localhost testing, use a self-signed cert or ngrok tunnel. |
| Audio buffer grows unboundedly | Use strategy from Milestone 1 buffer spike. If growing buffer, cap at configurable max (e.g. 15 minutes). |
| NeMo blocks event loop | `run_in_executor` with ThreadPoolExecutor. Validate by hitting `/health` during active transcription. |

---

## Architecture: PHP/Python Coordination Sequence

```
1. Browser → GET /scribe (Symfony)
   ← HTML page with session_id, WebSocket URL, Mercure topics

2. Browser → EventSource(mercure_hub_url?topic=scribe/session/{id})
   ← SSE connection established (ready to receive)

3. Browser → WebSocket /ws/transcribe/{session_id} (Python FastAPI)
   ← Connection accepted

4. Browser → sends audio chunks over WebSocket
   Python → NeMo inference → publish to Mercure
   Browser ← receives segments via SSE

5. Browser → clicks "End Consultation"
   Browser → closes WebSocket
   Python → session.finalize()
   Browser → GET /scribe/{id}/history (Symfony → Python)
   ← Full transcript JSON
```

Symfony serves the page and provides session history. **PHP does not touch the live audio hot path.** All real-time data flows through Python and Mercure.
