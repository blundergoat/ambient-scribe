---
category: runtime
last_reviewed: 2026-07-10
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
- **Update (2026-07-07, 0.4.0 M06 spike):** confidence mode is NOT similarly cursed - enabling `confidence_cfg` (preserve word+token) on the same multitalker model, with the runtime's CUDA-graph workaround mirrored, ran clean on two fixtures with non-degenerate values and an error-free follow-up eval (`scripts/probe-word-confidence.py`, evidence `var/quality/word-confidence-spike/`). The timestamp caution stands; do not generalize it to confidence.

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

- **Files:** `public/js/scribe-output.js` (search: "fetch(`/session/${requestedSessionId}/summary")
- **Files:** `public/js/scribe-actions.js` (search: "async function readJsonResponse")
- **Files:** `src/Controller/ScribeController.php` (search: "public function summary")
- **Files:** `strands_agents/api/server.py` (search: "async def generate_summary")
- **What breaks:** The browser posts summary requests to the app-origin `/session/{id}/summary` URL, but FastAPI owns the actual work. Without the Symfony proxy route, the browser can receive Symfony/PHP HTML errors and then show JSON parser failures such as `Unexpected token '<'`. (Replay uploads used to share this trap; demo replay now streams over the WebSocket and has no app-origin HTTP action.)
- **Evidence:** The browser fetch path is same-origin, FastAPI defines the summary endpoint, and Symfony must translate the app-origin request into a FastAPI call while preserving JSON error responses.
- **Prevention:** When adding or changing browser-to-FastAPI HTTP actions, add a Symfony same-origin proxy or explicitly prove browser CORS/config. Include a route smoke that checks `Content-Type: application/json` for failure states, not just happy-path API tests.
- **Update (2026-07-07):** the pattern held for the summary Transcript tab: `GET /session/{id}/corrected-transcript` gained a same-origin proxy (`src/Controller/ScribeController.php`, search: "public function correctedTranscript") with unit tests for forwarding and invalid-UUID rejection.

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

## Footgun: Row-scope role override after grace expiry publishes fabricated empty role state

**Status:** active | **Created:** 2026-07-07 | **Evidence:** OBSERVED

- **Files:** `strands_agents/api/server.py` (search: "_apply_row_role_override")
- **Files:** `strands_agents/tools/assign_roles.py` (search: "def get_or_create_state")
- **Files:** `strands_agents/session_lifecycle.py` (search: "cleanup_role_state")
- **Files:** `public/js/scribe-transcript.js` (search: "function handleRoleUpdate")
- **What breaks:** Both scopes of `/session/{id}/roles/override` call `get_or_create_state(session_id)` to include the speaker mapping and confidence in their Mercure publish. After lifecycle teardown runs `cleanup_role_state`, that call silently CREATES a fresh empty `RoleMappingState` (mapping `{}`, zero confidence) for the dead session and publishes it as an authoritative `role_update`. The browser guard `if (!roleUpdateEvent.mapping)` does not catch `{}` (an empty object is truthy in JS), so `confidence = roleUpdateEvent.confidence ?? 0` wipes the header badge while `applySpeakerRoleMapping({})` leaves labels alone. The row correction itself sticks because `sessions.set_row_role` writes to transcript storage, whose TTL is independent of lifecycle role state.
- **Evidence:** 2026-07-06 consult-03 manual test, session `fd2d7bfb-5d23-4e32-b0fa-e4f16cf0fed7`: grace expired `19:59:34Z`; a row override `seg-0002 -> PATIENT` at `20:07:34Z` published `{"type":"role_update","mapping":{},"row_overrides":{"seg-0002":"PATIENT"},...}` and the header badge dropped from `Roles identified (92%)` to `Speakers unclear (0%)` while the row chip flipped correctly.
- **Prevention:** Post-visit request paths must peek at existing role state rather than `get_or_create_state` before including mapping/confidence in a publish, and the browser must treat an empty mapping as "no mapping news" rather than letting a `manual_override` row-scope event overwrite session-level confidence. When adding any post-Stop interaction (overrides, review queues, edits), check what lifecycle teardown has already destroyed before republishing derived state.
- **Current state (2026-07-07):** the row-scope publish now peeks (`strands_agents/tools/assign_roles.py`, search: "def peek_state") and omits mapping/confidence for a finished visit, and `handleRoleUpdate` routes badge news through `applyMappingAndConfidenceNews` so `manual_override` events never move the earned badge (regressions: `tests/python/test_api.py`, search: "publishes_no_fabricated_mapping"; `tests/e2e/browser.spec.js`, search: "cannot wipe the badge"). RESOLVED for all three callers (2026-07-07 evening, 0.4.0 M04): the speaker-scope branch is liveness-gated (`strands_agents/api/server.py`, search: "_apply_speaker_role_override") - a live visit (active socket or reconnect grace) still creates state so a label clicked before the role worker's first update pins against later agent proposals, while a finished visit peeks, applies only the explicit `{speaker_id: role}` to stored rows and the corrected artifact, skips the auto-row re-judge, and publishes the partial mapping with no confidence key. `roles_snapshot` peeks and returns the honest empty shape. Regressions: `tests/python/test_api.py`, search: "speaker_override_after_role_state_cleanup", "snapshot_after_cleanup_does_not_resurrect", "before_first_role_update_pins_label".

## Footgun: Logger-level filters never see records propagated from module loggers

**Status:** active | **Created:** 2026-07-07 | **Evidence:** OBSERVED

- **Files:** `strands_agents/api/server.py` (search: "Correlation must be attached at the handler")
- **Files:** `strands_agents/logging_config.py` (search: "dictConfig")
- **What breaks:** Python runs a logger's filters only on records CREATED by that logger; records from child loggers propagate straight to ancestor HANDLERS without touching ancestor logger filters. The correlation-ID feature attached `CorrelationIdFilter` to the root logger with a comment claiming it covered "ALL log records" - but every module here logs via `logging.getLogger(__name__)`, so `correlation_id` was missing from essentially every JSON line and the observability feature could not join a clinician action across HTTP, WebSocket, and role-worker logs (found via PR #3 bot review).
- **Prevention:** Anything that must stamp every record (correlation IDs, session IDs, redaction) belongs on the HANDLER (`handler.addFilter(...)` after `configure_logging()`) or a `logging.setLogRecordFactory` - never on the root logger. Regressions: `tests/python/test_observability.py` (search: "corr-child-123") proves a child-logger record carries the ID; the config assertion checks handlers existentially because pytest injects its own root capture handlers (see lessons/verification.md, search: "pytest owns extra root log handlers").

## Footgun: Role overrides span three stores that must move together

**Status:** active | **Created:** 2026-07-07 | **Evidence:** OBSERVED

- **Files:** `strands_agents/api/server.py` (search: "confirmed_overrides.pop")
- **Files:** `public/js/scribe-transcript.js` (search: "manualOverrides.delete")
- **Files:** `strands_agents/api/server.py` (search: "corrected artifact snapshots roles")
- **Files:** `strands_agents/api/summary_request.py` (search: "if corrected_segments:")
- **What breaks:** A clinician role decision is recorded in three independent places: the server's `confirmed_overrides` (enforced over agent proposals by `_apply_confirmed_overrides`), the browser's `manualOverrides` set (blocks incoming `role_update` mappings client-side), and the corrected-transcript artifact (role snapshot taken at correction time, preferred by summaries). Any mutation path that touches only one diverges the rest. Two PR #3 review findings hit this: cycling a label back to Unknown (the documented undo) stored UNKNOWN as a PERMANENT confirmed override so the agent could never relabel - and even after the server fix, the browser's `manualOverrides` would still have blocked relabels until `cycleRole` also deleted its entry; separately, overrides after the first correction never reached `corrected_segments`, so retried summaries cited pre-fix roles.
- **Prevention:** Any new role-mutation feature (bulk relabel, undo stack, review queue) must decide explicitly for EACH of the three stores: update, invalidate, or deliberately skip - and say why. Current wiring: UNKNOWN pops the confirmed override AND the browser set; speaker overrides rewrite corrected rows in place; row overrides invalidate the corrected artifact so the next summary re-corrects (regressions: `tests/python/test_api.py`, search: "unknown_override_clears_confirmed_override", "updates_corrected_rows_for_retried_summaries", "invalidates_stale_corrected_artifact").

## Footgun: A hot-reload can silently move NeMo to CPU when WSL drops the GPU adapter

**Status:** active | **Created:** 2026-07-08 | **Evidence:** OBSERVED

- **Files:** `strands_agents/nemo_pipeline.py` (search: "torch.cuda.is_available()")
- **Files:** `scripts/eval-corrected-fixtures.sh` (search: "AGENT_HTTP_URL")
- **What breaks:** WSL2 can lose its GPU adapter mid-session while everything else keeps
  working: `/dev/dxg` still exists, `nvidia-smi` exits 0 while printing NOTHING, `/health`
  stays green, and the next uvicorn hot-reload loads both models on CPU because the pipeline
  falls back silently (`cuda if available else cpu`). Observed 2026-07-08: the first CPU load
  logged only NeMo warnings ("No conditional node support for Cuda ... CUDA is not
  available") at 08:35Z after ~25 hot-reloads and two probe processes; the M06 phase-2 gate
  eval then ran on CPU against the GPU M01 baseline and produced a false "regression" (c08
  live +6 words diverging from row 7; c02 role-mapping luck flip). Two same-code CPU runs
  were byte-identical to each other, proving device numerics - not code - moved the text.
  `docker compose restart nemo-agent` cannot recover this state: the nvidia runtime hook
  fails with "WSL environment detected but no adapters were found" and the container stays
  DOWN until a Windows-side `wsl --shutdown`.
- **Prevention:** Before ANY baseline-gated eval, verify device liveness in the running
  container - `docker exec ambient-scribe-nemo-agent-1 python -c "import torch;
  print(torch.cuda.is_available())"` must print True; env vars alone are NOT liveness
  (lessons/verification.md "Verify the running container's env" now has a device
  counterpart). Treat a byte-identity gate failure as UNATTRIBUTED until the run's device
  matches the baseline's device; re-run the same eval twice on the same device to separate
  code drift from hardware numerics before touching any code. After GPU restore, grep the
  agent log for "CUDA is not available" over the full session window before trusting any
  metrics recorded in it.

## Footgun: Second-pass correction failure silently swaps the summary's input lane

**Status:** active | **Created:** 2026-07-09 | **Evidence:** ACTUAL_MEASURED

- **Files:** `strands_agents/api/server.py` (search: "correction.unavailable")
- **Files:** `strands_agents/api/summary_request.py` (search: "corrected_segments")
- **What breaks:** The post-visit correction endpoint makes exactly ONE attempt; any second-pass failure returns HTTP 200 with `correction.unavailable` at WARNING, and the next summary request silently falls back from `corrected_segments` to `browser_visible_segments` (raw live rows: worse ASR, dual-identity duplicate rows, different row shapes for the fidelity checker) - stacking with the 8000-char head-truncation footgun (footguns/summary.md). The observed failure was transient: `CUDA driver error: device not ready` on the FIRST transcribe call, ~12s after `session_lifecycle.destroy_scheduled`, on the longest session of the evening (116 chunks), while the two shorter sessions' corrections succeeded minutes earlier and `torch.cuda.is_available()` printed True in-container immediately afterwards. That is consistent with racing the live session's GPU teardown (cudaErrorNotReady = async work still pending), NOT with the WSL adapter drop documented below. A single retry would very likely have succeeded; none exists.
- **Log-grep trap:** the failure is logged at WARNING with the wording "CUDA driver error", so sweeps for `ERROR`, `Traceback`, or the phrase "CUDA error" all miss it. Grep `correction.unavailable` explicitly when auditing a session.
- **Evidence:** 2026-07-08 (UTC) session `0a40e243-c813-48a5-b87f-e069da4def40`: `correction.unavailable ... duration_ms=12350 detail=Second-pass ASR failed for nvidia/parakeet-tdt-0.6b-v3: CUDA driver error: device not ready`, then `summary.requested source=browser_visible_segments` 14s later. Same evening, sessions `d97a9bde`/`203d1d35` logged `correction.completed` (17.4s / 11.5s) and `summary.requested source=corrected_segments`. Related but distinct root cause with the same downstream fallback: lessons/verification.md (search: "correction smoke tests must stay inside reconnect grace").
- **Prevention:** 0.4.0 M09 owns retry + fallback visibility. Until then, after every manual or e2e correction run, verify BOTH `correction.completed` AND `summary.requested source=corrected_segments` in the agent log before judging note quality - the existing grace-expiry lesson's check now has two root causes that trip it.
- **Update (2026-07-10, M08 c03 acceptance):** "a single retry would very likely have succeeded" is now REFUTED for long clips. Full-length c03 (9:04) corrections failed 3/3 today - once as an explicit CUDA OOM ("1.38 GiB ... 247 MiB free"), twice as "device not ready", including once on a freshly restarted agent with 10.6 GiB free - while 3:01-3:48 sessions succeeded on both days. GPU sampling during the clean-state run shows streaming stayed ~5.8 GiB and the correction's own transcribe spiked to 15.7 GiB in ~10s before failing, then stayed cached at 15.7 GiB, so same-lifetime retries inherit near-zero headroom. Treat full-length one-shot second-pass transcribe as over-capacity on this 16 GB card with the streaming stack resident; the failure is deterministic at ~9 min, not a teardown race. Full table and design implications: `.goat-flow/plans/0.4.0/M09-correction-resilience.md` (search: "length-correlated capacity failure").

## Footgun: Phantom-speaker merging can fold short real interjections into the other speaker's row

**Status:** active | **Created:** 2026-07-09 | **Evidence:** OBSERVED

- **Files:** `strands_agents/nemo_session.py` (search: "phantom_speaker_merged")
- **What breaks:** The streaming engine folds window-local marginal speaker slots into a canonical speaker (the hallucination-scale guard from the cache-aware integration entry above). When a real patient interjection is short enough to look marginal inside one window, the fold assigns those words to the OTHER speaker's canonical stream, and they render inside that speaker's row - the cross-talk bleed family seen in every manual acceptance run.
- **Evidence:** OBSERVED correlation, not yet a confirmed mechanism: 2026-07-08 (UTC) session `203d1d35` (day3-consultation01) logged six `phantom_speaker_merged window_speaker_id=speaker_2 canonical_speaker_id=speaker_0` events at exactly the wall-clock timestamps where the patient's consent answer ("I think I am. Yeah,") rendered inside the doctor's secure-location row. Same family, other direction observed in day5-consultation09: only 3 phantom merges while the patient's audio ran under TWO kept identities (speaker_0 + speaker_3) that both emitted text for the same spans - whole utterances duplicated, both mapped PATIENT.
- **Prevention:** Before touching the fold threshold, confirm the mechanism: replay a bleed fixture with per-window slot shares logged and check whether the bled words' window slot was folded. Any threshold change is streaming-engine territory (Ask First: `strands_agents/nemo_session.py`) and must pass the M01-style byte-identity gates plus a bleed-specific fixture check.

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
