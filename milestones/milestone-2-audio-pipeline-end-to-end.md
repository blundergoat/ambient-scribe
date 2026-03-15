# Milestone 2 — Audio Pipeline End-to-End

**Timeline:** Weekend 2 (~5-6 hours across 2 sessions)
**Status:** In Progress (core code paths implemented; real end-to-end verification still pending)
**Dependencies:** Milestone 1 complete (NeMo validated on RTX 5080, API surface documented, scaffold in place)

---

## Objective

Live mic audio from browser -> WebSocket -> NeMo -> transcript segments back to browser via Mercure. Working end-to-end demo with `spk_0`/`spk_1` labels (no role inference yet).

> **Implementation snapshot (2026-03-15):** The main M2 code already exists in `strands_agents/nemo_pipeline.py`, `strands_agents/nemo_session.py`, `strands_agents/api/server.py`, `templates/scribe/index.html.twig`, and `docker-compose.yml`. The remaining work is running the full flow against real services/hardware, adding fixture-backed verification, and resolving the audio-format drift noted below.

---

## Tasks

### 2.1 Offline Batch Pipeline First (Session A, ~1.5 hours)

> **Important:** Build the offline pipeline before adding WebSocket streaming. This isolates NeMo integration issues from WebSocket plumbing.

- [x] Create `strands_agents/nemo_pipeline.py` — wraps NeMo models based on Milestone 1 API discovery:
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
- [x] Add a test HTTP endpoint: `POST /transcribe/file` (accepts WAV upload, returns JSON segments)
- [ ] Verify end-to-end with a real WAV fixture + loaded NeMo models: upload test WAV -> get back speaker-attributed segments
- [x] Publish segments to Mercure from Python:
  ```python
  async def publish_to_mercure(session_id: str, data: dict):
      async with httpx.AsyncClient() as client:
          await client.post(MERCURE_HUB_URL, data={
              "topic": f"scribe/session/{session_id}",
              "data": json.dumps(data),
          }, headers={"Authorization": f"Bearer {MERCURE_JWT}"})
  ```
- [ ] Verify browser receives segments via Mercure SSE subscription in a real `docker compose` run

### 2.2 Chunked Processing Wrapper (Session A, ~1 hour)

- [x] Create `strands_agents/nemo_session.py` — stateful session wrapper:
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
- [x] Key design: `NemoPipeline` is a singleton loaded at startup; `TranscriptionSession` holds per-session state (audio buffer, accumulated transcript) and references the shared pipeline
- [ ] Handle audio format conversion (per Milestone 1 spike decision):
  - **Current implementation:** browser streams 16kHz PCM directly via Web Audio; `TranscriptionSession` supports `pcm` input and includes a fallback WebM decode path via ffmpeg
  - **Gap:** Milestone 1 recommended `MediaRecorder` WebM/Opus with server-side conversion; the planned persistent ffmpeg pipe is not implemented
  - **Decision to make before calling M2 done:** keep browser-side PCM as the PoC path, or switch back to WebM/Opus and validate chunk decoding properly

### 2.3 FastAPI WebSocket Endpoint (Session B, ~1 hour)

- [x] Add WebSocket endpoint to FastAPI:
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
- [x] Load NeMo models at FastAPI startup (not per-request) via FastAPI lifespan:
  ```python
  @asynccontextmanager
  async def lifespan(app: FastAPI):
      app.state.nemo_pipeline = NemoPipeline()
      yield
  ```
- [x] Keep existing `/health` endpoint for Docker healthchecks

> **Why `run_in_executor`?** NeMo inference is synchronous GPU-bound work. Running it directly inside an `async def` WebSocket handler blocks the entire FastAPI event loop. Every other WebSocket connection, the `/health` endpoint, and Mercure publishing all freeze during inference. The thread pool executor runs NeMo in a separate thread, keeping the event loop responsive.

### 2.4 Structured Logging and Observability

> **A multi-service streaming system is nearly impossible to debug without structured logging.** Add this early, not as polish.

- [x] Add correlation IDs (UUID v7) per session, propagated through all log lines
- [x] Log key events with timing:
  ```python
  import structlog
  logger = structlog.get_logger()

  # In WebSocket handler:
  logger.info("chunk_received", session_id=session_id, chunk_bytes=len(audio_chunk))
  logger.info("nemo_complete", session_id=session_id, segments=len(segments), duration_ms=elapsed)
  logger.info("mercure_published", session_id=session_id, topic=topic)
  ```
- [x] Log VRAM usage periodically (every 10th chunk):
  ```python
  import subprocess
  def log_vram():
      result = subprocess.run(["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader"],
                              capture_output=True, text=True)
      logger.info("vram_usage", used=result.stdout.strip())
  ```
- [x] Track end-to-end latency per chunk: timestamp at receive -> timestamp at Mercure publish

> **Current state:** request/WebSocket correlation IDs exist, chunk timing is partially logged, and periodic chunk stats are emitted. VRAM logging and end-to-end latency tracking are still missing.

### 2.5 Browser Audio Capture (Session B, ~1 hour)

- [x] **Decision: inline JS vs Stimulus**
  - If adding Stimulus: add `@hotwired/stimulus`, configure build step (Vite or Webpack Encore), add `package.json` — document this as explicit new scope
  - If staying inline (matching Summit pattern): write audio capture as plain JS in the Twig template
  - **Chosen path:** Stay inline for the PoC. No JS build pipeline added.
- [x] Implement audio capture:
  ```javascript
  // Current implementation: browser-side PCM capture via Web Audio
  // (16kHz mono, chunked every ~5 seconds)
  ```
- [x] Add start/stop controls to Twig template
- [x] Subscribe to Mercure SSE for transcript segments
- [x] Render segments as they arrive: `spk_0` / `spk_1` with timestamps

### 2.6 Transcript UI — Basic Version

- [x] Create `templates/scribe/index.html.twig`:
  - Start/Stop recording buttons
  - Recording status indicator
  - Transcript container with auto-scroll
  - Segments rendered as: `[spk_0 00:03] "What brings you in today?"`
- [x] Basic styling implemented (light/dark theme, segment cards, timer, status badge)

### 2.7 Docker Compose Integration

- [x] Update `docker-compose.yml` with all services:
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
        - "48101:8000"
    mercure:
      image: dunglas/mercure
      # Same config as Summit
    php:
      build: ./docker/php
      # Updated env for scribe
  ```
- [ ] Verify all 3 services start and communicate in a real run
- [ ] Test full flow: `docker compose up --build` -> open browser -> record -> see transcript

### 2.8 Tests for NeMo Pipeline Wrapper

- [x] Create `tests/python/test_nemo_pipeline.py` (wrapper tests + structural coverage; real NeMo integration still TBD):
  - [x] Test `Segment` dataclass creation and dict serialization
  - [x] Test `AudioBuffer` append, duration, max cap, empty state
  - [x] Test `TranscriptionSession` creation, process_chunk counter, finalize
  - [x] Test `NemoPipeline.transcribe_file()` wrapper logic with monkeypatched diarization + ASR output
  - [ ] Test `NemoPipeline.transcribe_file()` with fixture WAV + real NeMo → verify output structure
  - [ ] Test audio format conversion (input bytes → PCM output)
- [x] Create `tests/python/test_api.py` (basic endpoint tests with stub pipelines):
  - [x] Test `/health` endpoint returns 200
  - [x] Test `/session/{id}/history` returns empty for nonexistent session
  - [x] Test `POST /transcribe/file` endpoint with a stub pipeline
  - [x] Test `/ws/transcribe/{session_id}` WebSocket connection lifecycle with a stub pipeline
  - [ ] Test `POST /transcribe/file` endpoint with fixture WAV + real NeMo
  - [ ] Test `/ws/transcribe/{session_id}` WebSocket lifecycle with real NeMo
- [x] Add `pytest` and `pytest-asyncio` to `requirements-dev.txt`
- [ ] Re-run the Python suite in an environment with `pytest` installed and record the current pass count

---

## Exit Criteria

- [ ] Browser captures mic audio and sends chunks over WebSocket in a real browser session
- [ ] FastAPI receives chunks, feeds NeMo pipeline, gets transcript segments with loaded models
- [x] **NeMo inference runs in thread pool** — does not block the async event loop
- [ ] Segments published to Mercure, rendered in browser in real-time during a verified compose run
- [ ] Can see `spk_0` and `spk_1` labelled text appearing as you speak
- [ ] End-to-end latency under ~5 seconds (chunk interval + NeMo processing)
- [x] NeMo models loaded once at startup, shared across sessions
- [ ] **Structured logging with correlation IDs and timing in place**
- [ ] **Python tests pass for NeMo pipeline wrapper and API endpoints in a runnable pytest environment**

---

## Next Slice (2026-03-15)

1. Run `docker compose up --build` and verify the live browser -> WebSocket -> NeMo -> Mercure -> browser loop on real services.
2. Add or source a legal local WAV fixture under `tests/fixtures/audio/` so the batch and WebSocket paths can be verified against real NeMo output.
3. Decide whether browser-side PCM is the accepted PoC path or whether to restore the Milestone 1 WebM/Opus plan before calling M2 complete.
4. Add the missing observability pieces: end-to-end latency, VRAM logging, and one or two explicit health/flow checks during active transcription.

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
