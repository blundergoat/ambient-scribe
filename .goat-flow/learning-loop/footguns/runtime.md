---
category: runtime
last_reviewed: 2026-07-06
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

## Footgun: Separated-channel full-corpus eval can destabilize NeMo
**Status:** active | **Created:** 2026-07-05 | **Evidence:** ACTUAL_MEASURED

- **Files:** `scripts/eval-channel-ceiling.py` (search: "allow-unstable-full-run")
- **Files:** `strands_agents/nemo_pipeline.py` (search: "def transcribe_file")
- **Files:** `strands_agents/api/streaming_session.py` (search: "websocket.error")
- **What breaks:** M17 tried to quantify an overlap ceiling by transcribing PriMock57 doctor/patient source channels independently. Full-file batch transcription hit `torch.OutOfMemoryError` on c04, and browser-like WebSocket streaming of all separated channels later produced `websocket.error` rows from NeMo internals (`KeyError` in Sortformer diarization, then ASR `Cannot unfreeze partially without first freezing the module with freeze()`). After those errors, the shared singleton model needed a `docker compose restart nemo-agent` before normal eval could be trusted.
- **Evidence:** `scripts/eval-channel-ceiling.py --all` failed during the full-corpus run; `docker compose logs nemo-agent --since '2026-07-05T04:59:00Z'` showed `websocket.error` for separated-channel sessions and stack traces through `nemo_session.py` -> `nemo_pipeline.py` -> NeMo Sortformer/RNNT. The script now requires named fixtures by default and gates full-corpus reproduction behind `--allow-unstable-full-run`.
- **Prevention:** Do not run separated-channel full-corpus eval as a routine quality gate. Use named fixtures only, grep server logs after every run, and restart `nemo-agent` after any NeMo internal error before trusting later metrics. Treat a channel-separated "ceiling" as unproven until it uses a model/path designed for single-speaker source channels.

## Footgun: Role confidence does not prove speaker identity stayed stable
**Status:** active | **Created:** 2026-07-05 | **Evidence:** ACTUAL_MEASURED

- **Files:** `strands_agents/nemo_session.py` (search: "_continue_anchor_speakers")
- **Files:** `strands_agents/session_quality.py` (search: "speaker_anchor_remaps")
- **Files:** `scripts/transcript-quality.py` (search: "best dyadic mapping accuracy")
- **What breaks:** The browser badge can show high role confidence while the visible transcript is still mislabeled, because role inference maps `speaker_0`/`speaker_1` after NeMo canonicalization has already decided which voice each ID represents. If `_continue_anchor_speakers` drifts at window seams, the role agent may keep a stable high-confidence mapping for unstable speaker IDs; accepting or suppressing a role flip only relabels whole IDs and cannot split mixed segments.
- **Evidence:** A consultation-03 replay session `aeeef2f2-0d2f-4ebc-9563-bd01471d2a29` finalized with `final_confidence=0.904`, `error_count=0`, `role_truncation_events=0`, `speaker_anchor_remaps=17`, and `phantom_speaker_merges=5`. Scoring its saved `/session/{id}/history` against `tests/fixtures/audio/primock57-day1-consultation03-i-have-terrible-headache.{doctor,patient}.TextGrid` gave non-overlap attribution `45.5%` and best valid dyadic mapping only `54.5%`, so the failure was speaker identity drift, not a recoverable role-agent label choice.
- **Prevention:** For any doctor/patient mix-up report, fetch the stored history promptly, run `scripts/transcript-quality.py` with the matching TextGrids, and compare visible attribution to `best dyadic mapping accuracy` before tuning role prompts or damping. Treat high `speaker_anchor_remaps` with high role confidence as a speaker-canonicalization investigation.

## Footgun: Multitalker ASR timestamp mode can crash CUDA in chunked replay
**Status:** active | **Created:** 2026-07-05 | **Evidence:** ACTUAL_MEASURED

- **Files:** `strands_agents/nemo_pipeline.py` (search: "return_hypotheses=True")
- **Files:** `scripts/eval-fixtures.sh` (search: "session.quality JSONL row")
- **What breaks:** NeMo's installed transcribe API advertises `timestamps=True`, but enabling it for `EncDecMultiTalkerRNNTBPEModel` during live chunk replay can terminate `nemo-agent` before the session quality row is emitted. The browser/eval then sees a missing `session.quality` artifact instead of a clean transcription result.
- **Evidence:** A consult-03 83s replay session `f14375fd-a803-46b0-a38a-1fea08688bc6` with `timestamps=True` added to `_asr_model.transcribe(...)` failed with `error: no session.quality JSONL row found`. `docker compose logs nemo-agent --tail 250` showed `terminate called after throwing an instance of 'c10::AcceleratorError'` and `CUDA error: an illegal memory access was encountered` immediately after NeMo logged `Timestamps requested`.
- **Prevention:** Do not enable multitalker ASR timestamps as a quick word-to-speaker fix. Treat it as a GPU spike requiring an isolated process, log grep, and restart after failure; keep the proportional word splitter unless a timestamp path completes `scripts/eval-fixtures.sh --all` without CUDA errors.

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
- **Files:** `src/Controller/ScribeController.php` (search: "rawurlencode($filename)")
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

## Footgun: Visit topics must share one multiplexed EventSource
**Status:** active | **Created:** 2026-07-05 | **Evidence:** OBSERVED

- **Files:** `public/js/scribe-streaming.js` (search: "class StreamOrchestrator")
- **Files:** `public/js/scribe-recording.js` (search: "streams.connect(topics")
- **What breaks:** Browsers cap HTTP/1.1 connections at ~6 per host, and the Mercure hub on `http://localhost` cannot negotiate HTTP/2. When each visit topic (raw/roles/summary/hints) opened its own EventSource, a couple of open scribe tabs exhausted the pool and every new tab's streams hung silently in CONNECTING: the dev panel showed "disconnected" and 0 segments while the server logged `mercure.publish.succeeded` for every event and `curl` against the hub returned a healthy SSE stream.
- **Evidence:** A demo replay produced continuous publish-succeeded logs while the fresh browser tab rendered nothing; the hub answered probes with 200 + correct CORS throughout. Consolidating to one EventSource carrying all topics (events routed by payload `type`) restored delivery.
- **Prevention:** New Mercure topics must be added to the shared stream's topic list and the type→handler map - never as an additional EventSource. When "hub works but browser is silent", count open SSE connections across ALL tabs before blaming the hub or a stale page.

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
- **Files:** `strands_agents/agents/transcription_agent.py` (search: "MEDICAL_ROLE_INSTRUCTION")
- **What breaks:** A one-value migration that keeps `PROMPTS.get(mode, PROMPTS["general"])` can still crash when the fallback key is deleted. The default argument is evaluated before `.get()` returns, so deleting `["general"]` without flattening the map reintroduces a `KeyError`.
- **Evidence:** Before 0.3.0 the role and summary factories used mode prompt dictionaries with a hard-indexed `"general"` fallback. 0.3.0 removed the trap by replacing those dictionaries with medical constants and argless factories.
- **Prevention:** When a user-facing selector collapses to one supported behavior, collapse the data structure to a named constant and remove the selector parameter at every call site.

## Footgun: Browser stop teardown can race server finalize publishes

**Status:** active | **Created:** 2026-07-06 | **Evidence:** ACTUAL_MEASURED

- **What breaks:** Any browser stop path that closes the Mercure EventSource in the same tick as the transcription WebSocket loses the server's finalize output: `_finalize_after_disconnect` (`strands_agents/api/streaming_session.py`, search: "_finalize_after_disconnect") is *triggered by* the socket close and publishes the held-back tail rows, the `finalized` event, and enqueues tail role inference - all after the browser stopped listening. The visible transcript and the auto-generated summary then miss the final utterances.
- **Evidence:** 2026-07-06 fake-mic live-stop repro, session `7b627fbb-8c0e-4119-9f8a-03600e290b78`: browser froze at 8 rows while the server stored 11; the quality record and `finalized` event were published to nobody. The replay path never hit this because `enterReplayDrain` (`public/js/scribe-output.js`, search: "enterReplayDrain") already waits for `finalized` with a bounded timeout.
- **Prevention:** Every stop path must drain, not tear down: close the WebSocket, keep the EventSource open until the `finalized` event or a bounded timeout, then summarize (`stopRecording`/`endLiveStop` mirror the replay drain since M21). Only explicit discard paths (`resetSession`, replay error cleanup) may disconnect immediately. Server-side, summary POSTs merge by `segment_id` (`merge_browser_segments`) instead of replacing history, so even a raced or stale browser can no longer shrink the stored transcript. When adding a new stop/teardown path, classify it drain-wait vs discard-immediate before wiring `disconnectMercureStreams()`.

## Footgun: NeMo cache-aware streaming integration has three silent alignment traps

**Status:** active | **Created:** 2026-07-06 | **Evidence:** ACTUAL_MEASURED

- **What breaks:** Integrating `SpeakerTaggedASR` + `CacheAwareStreamingAudioBuffer` for live audio (M22 streaming engine) fails silently in three distinct ways that all present as "mysteriously bad transcript quality" with zero errors: (1) `append_audio`'s first-stream create branch returns `stream_id=-1`, so passing the returned id back on the next append pads a NEW stream - the batch grows per chunk until the diar streaming state throws a tensor-size mismatch; (2) `drop_extra_pre_encoded` must be `0` on step 0 and `encoder.streaming_cfg.drop_extra_pre_encoded` on every later step - passing 0 forever misaligns encoder outputs and quietly destroys recall (~30% observed); (3) per-instance hypothesis `timestamp` frames count that speaker's own voiced/decoded frames, NOT session time - `offset + ts*0.08` produces row times beyond the audio, and downstream cutoff filters then silently drop the rows.
- **Evidence:** M22 Phase 2 GPU-contact debugging, 2026-07-06 (`.goat-flow/plans/0.3.0/M22-session-long-streaming-diarization.md`, search: "GPU-contact findings"). Per-step instrumentation dump proved cumulative slot texts with frozen bursts and voiced-frame timestamps.
- **Prevention:** Pin `stream_id = max(0, returned_id)` after the first append; mirror the reference CLI's per-step `drop_extra_pre_encoded` computation verbatim. When streaming quality looks wrong with clean logs, instrument per-step hypothesis facts (slot, n_words, head/tail, ts0/tsN, offset) before touching emission logic.
- **Three more traps found at REAL-TIME pacing (2026-07-06, invisible at accelerated eval pacing):** (4) `CacheAwareStreamingAudioBuffer.__iter__` yields PARTIAL chunks near the buffer end and advances the cursor a full shift regardless - at 1x pacing a step loop drains the buffer every feed, truncating AND skipping audio four times per 5s chunk (garbled words, speaker fragmentation). Gate stepping on `frames_available >= full_chunk_frames` and consume partials only at flush. (5) Deriving word times from the decode/step clock is FICTION under decode lag: rows carry compressed times, the time-overlap scorer and the role layer both read garbage, and eval "attribution" numbers become unmeasurable. True times come from inverting the diarizer's own activity stream: per-slot (cumulative voiced frames -> wall seconds) ledger, then map each token timestamp through it. (6) An eager "pin the first N slots to establish" speaker cap folds a genuine voice into another slot when one speaker's audio spans two early cache slots (c03's doctor does) - fold only hallucination-scale marginal slots (share-based) and let substantial slots through; the role mapping labels them anyway.
- **Verification rule this taught:** accelerated-pacing evals CANNOT stand in for real-time behavior on streaming integrations. Any cache-aware streaming change must pass a 1x browser replay (row order, live cadence, text sanity) in addition to eval gates.

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
