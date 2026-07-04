---
category: runtime
last_reviewed: 2026-07-04
---

# Runtime / Session / Mercure Footguns

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

## Footgun: App-origin replay and summary routes must stay proxied to FastAPI
**Status:** active | **Created:** 2026-07-04 | **Evidence:** OBSERVED

- **Files:** `public/js/scribe-output.js` (search: "fetch(`/session/${CONFIG.sessionId}/replay")
- **Files:** `public/js/scribe-output.js` (search: "fetch(`/session/${CONFIG.sessionId}/summary")
- **Files:** `public/js/scribe-actions.js` (search: "async function readJsonResponse")
- **Files:** `src/Controller/ScribeController.php` (search: "public function replay")
- **Files:** `src/Controller/ScribeController.php` (search: "public function summary")
- **Files:** `strands_agents/api/server.py` (search: "async def replay_file")
- **Files:** `strands_agents/api/server.py` (search: "async def generate_summary")
- **What breaks:** The browser posts replay and summary requests to app-origin `/session/{id}/...` URLs, but FastAPI owns the actual work. Without Symfony proxy routes, the browser can receive Symfony/PHP HTML errors and then show JSON parser failures such as `Unexpected token '<'`.
- **Evidence:** The browser fetch path is same-origin, FastAPI defines the replay/summary endpoints, and Symfony must translate those app-origin requests into FastAPI calls while preserving JSON error responses.
- **Prevention:** When adding or changing browser-to-FastAPI HTTP actions, add a Symfony same-origin proxy or explicitly prove browser CORS/config. Include a route smoke that checks `Content-Type: application/json` for failure states, not just happy-path API tests.

## Footgun: Replay transcription does not make demo audio audible
**Status:** active | **Created:** 2026-07-04 | **Evidence:** OBSERVED

- **Files:** `public/js/scribe-fixtures.js` (search: "new File([audioBlob], audioFixture.filename")
- **Files:** `public/js/scribe-output.js` (search: "async function startReplayAudioPlayback")
- **Files:** `public/js/scribe-output.js` (search: "async function syncStoppedReplayOnServer")
- **Files:** `strands_agents/api/replay_session.py` (search: "replay_tasks: dict[str, asyncio.Task")
- **Files:** `strands_agents/api/server.py` (search: "async def stop_replay_file")
- **What breaks:** A replay upload can produce transcript text without any browser audio playback if the selected WAV is only posted to FastAPI. Even with audible playback, transcript text can appear faster than speech if server-side replay pacing is independent of the browser audio clock.
- **Evidence:** The browser smoke for `primock57-day1-consultation02-i-have-sore-red-skin.wav` first showed transcript replay and a visible `#replayAudio` control after wiring the fixture blob into an `<audio>` element; the next fix moved visible transcript reveal to `revealReplaySegmentsUpToAudioTime()` and synced stopped replay rows through `/session/{id}/replay/stop`.
- **Prevention:** Replay UI changes must verify four browser-visible states together: audio controls have a playable source, transcript rows advance only as the audio clock advances, Stop pauses local audio and sends visible rows to the replay-stop proxy, and Summarise uses that visible transcript snapshot.

## Footgun: PriMock replay WAVs exceed PHP's default upload ceiling
**Status:** active | **Created:** 2026-07-04 | **Evidence:** OBSERVED

- **Files:** `Dockerfile` (search: "upload_max_filesize=128M")
- **Files:** `scripts/e2e-test.sh` (search: "-d upload_max_filesize=128M")
- **Files:** `tests/fixtures/audio/generated-manifest.json` (search: "primock57-day1-consultation02")
- **Files:** `public/js/scribe-output.js` (search: "Replay service returned an unreadable response")
- **What breaks:** PHP's built-in server defaults `post_max_size` to 8M, but generated PriMock WAVs are around 15-27M. Oversized uploads can prepend an HTML PHP warning before JSON, causing the browser to report a replay parser failure or treat a malformed 200 as success.
- **Evidence:** Posting `primock57-day1-consultation02-i-have-sore-red-skin.wav` through the app route produced `POST Content-Length ... exceeds the limit of 8388608 bytes` before the JSON body.
- **Prevention:** Keep Docker and local/e2e PHP launch paths at `upload_max_filesize=128M` and `post_max_size=128M`. Smoke one real fixture upload after changing demo audio size, PHP startup commands, or the replay proxy.

## Footgun: Full-file PriMock replay can exceed NeMo GPU memory
**Status:** active | **Created:** 2026-07-04 | **Evidence:** OBSERVED

- **Files:** `strands_agents/api/replay_session.py` (search: "segments = await _transcribe_replay_upload")
- **Files:** `strands_agents/api/replay_session.py` (search: "services.pipeline.transcribe_file")
- **Files:** `strands_agents/nemo_pipeline.py` (search: "def transcribe_file")
- **Files:** `tests/fixtures/audio/generated-manifest.json` (search: "primock57-day1-consultation02-i-have-sore-red-skin.wav")
- **What breaks:** Replay upload currently transcribes the entire selected WAV before background pacing starts. Long PriMock fixtures can fit through PHP but still push the multitalker ASR forward pass over available GPU memory, returning a JSON 500 instead of streamed transcript text.
- **Evidence:** After raising PHP upload limits, posting the 18 MB `primock57-day1-consultation02-i-have-sore-red-skin.wav` reached FastAPI and failed in `NemoPipeline.transcribe_file()` with `torch.OutOfMemoryError: CUDA out of memory`.
- **Prevention:** Keep replay transcription bounded by chunking long uploads or by enforcing fixture durations proven under the GPU memory budget. Any demo-audio change should smoke one real PriMock WAV through `/session/{id}/replay`, not only check that `/scribe/demo-audio` returns `audio/wav`.

## Footgun: Defaulted mode maps can hide hard fallback keys
**Status:** active | **Created:** 2026-07-04 | **Evidence:** OBSERVED

- **Files:** `strands_agents/agents/transcription_agent.py` (search: "MEDICAL_ROLE_PROMPT")
- **Files:** `strands_agents/agents/summary_agent.py` (search: "MEDICAL_SUMMARY_PROMPT")
- **Files:** `strands_agents/api/server.py` (search: "MEDICAL_ROLE_INSTRUCTION")
- **What breaks:** A one-value migration that keeps `PROMPTS.get(mode, PROMPTS["general"])` can still crash when the fallback key is deleted. The default argument is evaluated before `.get()` returns, so deleting `["general"]` without flattening the map reintroduces a `KeyError`.
- **Evidence:** Before 0.3.0 the role and summary factories used mode prompt dictionaries with a hard-indexed `"general"` fallback. 0.3.0 removed the trap by replacing those dictionaries with medical constants and argless factories.
- **Prevention:** When a user-facing selector collapses to one supported behavior, collapse the data structure to a named constant and remove the selector parameter at every call site.
