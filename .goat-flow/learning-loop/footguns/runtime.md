---
category: runtime
last_reviewed: 2026-07-04
---

# Runtime / Session / Mercure Footguns

## Footgun: The NeMo image's /opt/venv botocore shadows strands' boto3 and crashes the agent at import

**Status:** active | **Created:** 2026-07-04 | **Evidence:** ACTUAL_MEASURED

- **Files:** `docker/nemo/Dockerfile` (search: "boto3==1.42.61")
- **Files:** `strands_agents/agents/transcription_agent.py` (search: "from strands import Agent")
- **What breaks:** The `nvcr.io/nvidia/nemo` base image ships an older `botocore` in its runtime venv (`/opt/venv`), while `strands-agents` installs a newer boto3/botocore into `/usr/local/lib/python3.12/dist-packages`. `/opt/venv` wins on `sys.path`, so `import boto3` - pulled in unconditionally by `strands.models.bedrock` even when the provider is Ollama - fails with `cannot import name 'DocumentModifiedShape' from 'botocore.docs.utils'`. The FastAPI agent then crashes at import, the container never passes `/health`, and `setup-initial.sh` fails at the "Starting containers" step ("dependency ... is unhealthy"). Because the image's `pip` targets `/usr/local` (NOT the runtime `/opt/venv`), pinning in `requirements.txt` does NOT fix it.
- **aiobotocore constraint:** The base image's `aiobotocore` pins `botocore<1.42.62`, so you cannot just upgrade botocore to match the newer boto3 - install the matched **1.42.61** pair (which is `<1.42.62`) directly INTO `/opt/venv`.
- **Fix:** `RUN /opt/venv/bin/python -m pip install --no-cache-dir "boto3==1.42.61" "botocore==1.42.61"`, placed AFTER the model-download layer so a rebuild keeps the multi-GB model cache. Bump the pair only alongside the pinned `nemo:26.02` base image. Diagnose with `docker compose exec nemo-agent /opt/venv/bin/python -c "import boto3"`.

## Footgun: Mercure publish failure only surfaces as a browser banner
**Status:** active | **Created:** 2026-03-21 | **Evidence:** ACTUAL_MEASURED

- **Files:** `strands_agents/api/server.py` (search: "async def publish_to_mercure")
- **Files:** `strands_agents/api/streaming_session.py` (search: "async def _warn_live_streaming_failure")
- **Files:** `public/js/scribe-recording.js` (search: "if (socketMessage.type === 'system_error')")
- **Files:** `public/js/scribe-recording.js` (search: "function showSystemBanner")
- **What breaks:** If Mercure is down, transcription can still run on the server but live segments stop appearing in the browser. The user gets a one-time `system_error` banner, not a fallback delivery path.
- **Evidence:** `publish_to_mercure()` returns `False` after retries, the streaming workflow emits a `system_error` frame on the first failed raw publish, and the browser only renders that message into `#systemBanner`.

## Footgun: Session lifecycle split across active sessions, transcript storage, and role state
**Status:** active | **Created:** 2026-03-21 | **Evidence:** ACTUAL_MEASURED

- **Files:** `strands_agents/session_lifecycle.py` (search: "async def destroy")
- **Files:** `strands_agents/api/server.py` (search: "async def _periodic_cleanup")
- **Files:** `strands_agents/tools/assign_roles.py` (search: "def cleanup_session")
- **Files:** `strands_agents/session.py` (search: "def _evict_expired")
- **What breaks:** `SessionLifecycle` owns active WebSocket sessions, `assign_roles` owns role state, and `SessionStore` owns transcript TTL eviction. Those stores are coordinated but not unified, so cleanup timing can diverge.
- **Evidence:** `destroy()` cleans active sessions plus role state, `_periodic_cleanup()` separately reaps orphaned queue/event-id entries, and `SessionStore` independently expires transcript data on access.

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
- **Files:** `strands_agents/session_lifecycle.py` (search: "register() cancels the pending task")
- **Files:** `strands_agents/api/streaming_session.py` (search: "await services.lifecycle.schedule_destroy")
- **What breaks:** During the grace period, audio buffers, `_mercure_event_ids`, and role state remain alive so a reconnect can resume. Long grace windows trade resilience for memory growth.
- **Evidence:** `register()` cancels pending destroys, `schedule_destroy()` stores delayed tasks in `_pending_destroys`, the server treats pending destroys as live sessions, and `transcribe_stream_session()` schedules cleanup instead of destroying immediately.

## Footgun: Live transcription contracts are split across Docker, Twig, JS, and FastAPI
**Status:** active | **Created:** 2026-07-04 | **Evidence:** ACTUAL_MEASURED
**Source:** git history (auto-seeded)
**hallucination-risk:** high

- **Files:** `docker-compose.yml` (search: "NEMO_WEBSOCKET_URL=ws://localhost:${AGENT_PORT:-48101}")
- **Files:** `templates/scribe/index.html.twig` (search: "const CONFIG =")
- **Files:** `public/js/scribe-recording.js` (search: "new WebSocket(`${CONFIG.wsUrl}/ws/transcribe/")
- **Files:** `strands_agents/api/server.py` (search: "async def transcribe_stream")
- **Git evidence:** `35bceb4` touched `docker-compose.yml`, `strands_agents/api/server.py`, `strands_agents/nemo_pipeline.py`, `strands_agents/nemo_session.py`, `templates/scribe/index.html.twig`, and `tests/python/test_api.py` to restore live transcription and healthcheck contracts.
- **Git evidence:** `d045b6c` touched `docker-compose.yml`, `scripts/start-dev.sh`, `templates/scribe/index.html.twig`, and scenarios to harden the dev workflow and UI.
- **Git evidence:** churn scan over the last 50 commits found `templates/scribe/index.html.twig` in 12 commits, `strands_agents/api/server.py` in 10 commits, and `docker-compose.yml` in 10 commits.
- **What breaks:** A local-looking change to ports, injected config, WebSocket URL construction, or FastAPI route handling can silently break the live path because no single schema owns the browser-to-agent contract.
- **Evidence:** The session URL is composed from Docker/Symfony/Twig-provided config in the browser, while FastAPI separately owns route validation and session handling.

## Footgun: PHP built-in server bypasses Symfony for dotted dynamic assets
**Status:** active | **Created:** 2026-07-04 | **Evidence:** OBSERVED

- **Files:** `Dockerfile` (search: "Start PHP's built-in web server")
- **Files:** `src/Controller/ScribeController.php` (search: "public function demoAudio")
- **Files:** `src/Controller/ScribeController.php` (search: "'url' => '/scribe/demo-audio?filename='")
- **What breaks:** A Symfony route that puts a dotted filename such as `.wav` in the path can return the PHP built-in server's static-file 404 before Symfony sees the request. The Demo Audio picker must use a non-dotted path with the filename in the query string, or the dev server command needs an explicit router script.
- **Evidence:** `GET /scribe/demo-audio/primock57-day1-consultation01.wav` was handled as a missing static file, while `GET /scribe/demo-audio?filename=primock57-day1-consultation01.wav` reached `ScribeController::demoAudio()` and served `audio/wav`.

## Footgun: App-origin summary route must stay proxied to FastAPI
**Status:** active | **Created:** 2026-07-04 | **Evidence:** OBSERVED

- **Files:** `public/js/scribe-output.js` (search: "fetch(`/session/${CONFIG.sessionId}/summary")
- **Files:** `public/js/scribe-actions.js` (search: "async function readJsonResponse")
- **Files:** `src/Controller/ScribeController.php` (search: "public function summary")
- **Files:** `strands_agents/api/server.py` (search: "async def generate_summary")
- **What breaks:** The browser posts summary requests to the app-origin `/session/{id}/summary` URL, but FastAPI owns the actual work. Without the Symfony proxy route, the browser can receive Symfony/PHP HTML errors and then show JSON parser failures such as `Unexpected token '<'`. (Replay uploads used to share this trap; demo replay now streams over the WebSocket and has no app-origin HTTP action.)
- **Evidence:** The browser fetch path is same-origin, FastAPI defines the summary endpoint, and Symfony must translate the app-origin request into a FastAPI call while preserving JSON error responses.
- **Prevention:** When adding or changing browser-to-FastAPI HTTP actions, add a Symfony same-origin proxy or explicitly prove browser CORS/config. Include a route smoke that checks `Content-Type: application/json` for failure states, not just happy-path API tests.

## Footgun: Replay transcription must stay coupled to audible browser audio
**Status:** active | **Created:** 2026-07-04 | **Evidence:** OBSERVED

- **Files:** `public/js/scribe-fixtures.js` (search: "new File([audioBlob], audioFixture.filename")
- **Files:** `public/js/scribe-output.js` (search: "async function startReplayAudioPlayback")
- **Files:** `public/js/scribe-streaming.js` (search: "class WavPcmStreamer")
- **Files:** `public/js/scribe-output.js` (search: "function enterReplayDrain")
- **What breaks:** Demo replay can produce transcript text without any browser audio playback, or transcript text decoupled from what the user has heard, if replay PCM is fed to NeMo independently of the audible `#replayAudio` element. `WavPcmStreamer` sends chunks only as the audio clock advances; bypassing it (or timing chunks off wall-clock instead of `currentTime`) silently reintroduces the decoupling.
- **Evidence:** The original batch replay showed transcript rows with no audible audio until the fixture blob was wired into an `<audio>` element; the design has since been reworked so the audio clock drives PCM streaming itself (`WavPcmStreamer._heardBytes` derives the send offset from `currentTime`).
- **Prevention:** Replay UI changes must verify four browser-visible states together: audio controls have a playable source, PCM chunks stream only as the audio clock advances, transcript rows arrive over Mercure like a live visit, and Stop flushes only heard audio then waits for the backend `finalized` event before enabling Summarise.

## Footgun: Defaulted mode maps can hide hard fallback keys
**Status:** active | **Created:** 2026-07-04 | **Evidence:** OBSERVED

- **Files:** `strands_agents/agents/transcription_agent.py` (search: "MEDICAL_ROLE_PROMPT")
- **Files:** `strands_agents/agents/summary_agent.py` (search: "MEDICAL_SUMMARY_PROMPT")
- **Files:** `strands_agents/api/server.py` (search: "MEDICAL_ROLE_INSTRUCTION")
- **What breaks:** A one-value migration that keeps `PROMPTS.get(mode, PROMPTS["general"])` can still crash when the fallback key is deleted. The default argument is evaluated before `.get()` returns, so deleting `["general"]` without flattening the map reintroduces a `KeyError`.
- **Evidence:** Before 0.3.0 the role and summary factories used mode prompt dictionaries with a hard-indexed `"general"` fallback. 0.3.0 removed the trap by replacing those dictionaries with medical constants and argless factories.
- **Prevention:** When a user-facing selector collapses to one supported behavior, collapse the data structure to a named constant and remove the selector parameter at every call site.

## Resolved Entries

## Footgun: PriMock replay WAVs exceed PHP's default upload ceiling
**Status:** resolved | **Created:** 2026-07-04 | **Evidence:** OBSERVED

- **Files:** `Dockerfile` (search: "upload_max_filesize=128M")
- **Files:** `scripts/e2e-test.sh` (search: "-d upload_max_filesize=128M")
- **What breaks:** PHP's built-in server defaults `post_max_size` to 8M, but generated PriMock WAVs are around 15-27M. Oversized uploads prepended an HTML PHP warning before JSON, causing browser replay parser failures.
- **Resolution:** Demo replay no longer uploads WAVs through PHP at all — the browser decodes the WAV locally and streams 16 kHz PCM over the FastAPI WebSocket (`public/js/scribe-streaming.js`, search: "class WavPcmStreamer"). The raised PHP upload limits remain configured but no user flow depends on them.

## Footgun: Full-file PriMock replay can exceed NeMo GPU memory
**Status:** resolved | **Created:** 2026-07-04 | **Evidence:** OBSERVED

- **Files:** `strands_agents/nemo_pipeline.py` (search: "def transcribe_file")
- **Files:** `strands_agents/api/server.py` (search: "async def transcribe_file")
- **What breaks:** Batch replay transcribed the entire selected WAV in one NeMo forward pass; the 18 MB `primock57-day1-consultation02` fixture failed with `torch.OutOfMemoryError: CUDA out of memory`.
- **Resolution:** Demo replay now streams chunked PCM through the same `TranscriptionSession` path as live recording, so no full-file forward pass happens for demo audio. Residual risk: the test-only `POST /transcribe/file` batch endpoint still runs `transcribe_file` on whole files — keep its inputs short.
