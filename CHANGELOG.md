# Changelog

All notable changes to Ambient Scribe are documented in this file.

Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

Dev workflow hardening, UI polish, client-side flip detection, and
multi-mode scenario coverage.

### Added

- **Docker hot reload** — volume-mount `.:/app` in `docker-compose.yml`
  so template, asset, and PHP changes are reflected without rebuilding
  the container
- **Footgun FG-9** — documented "template changes require container
  rebuild" pitfall and the volume-mount mitigation
- **5 multi-mode test scenarios** — Meeting Daily Standup, Technical
  Interview, TV Panel Discussion, Intro to CS Lecture, 3+ Speakers;
  exercises mode-aware role inference for non-medical modes

### Changed

- **`start-dev.sh` simplified** — removed `--build`/`--no-logs` flags
  and all flag-parsing logic; `dc up -d` always used (volume mount
  removes the need for rebuild-on-edit); help text updated
- **Role flip detection** — moved from server-side `flip_detected` flag
  to client-side previous/current mapping comparison; works identically
  in live and scenario modes
- **Mode start labels** — TV mode: "Start Recording" to "Start
  Broadcast"; General mode: "Start Recording" to "Start Transcription"
- **`getRoleLabel()` fallback** — unknown backend roles are now
  title-cased with underscores replaced (was raw backend string)
- **`applyMode()` call order** — moved after state variable declarations
  to prevent reference errors on `relabelSegments()`
- **Dev panel text truncation** — segment log and raw log use ellipsis
  at 57/77 chars instead of hard substring cuts; raw log entries show
  expand indicator for long payloads

### Fixed

- **Confidence badge pulse** — `recording-pulse` class now cleared on
  both `stopRecording()` and post-scenario cleanup (was left animating)
- **Session reset** — now clears dev panel segment/raw/Mercure logs and
  `segmentsBySpeaker`/`manualOverrides` maps (was leaving stale state)
- **Scenario runner progress** — progress bar and counter shown for
  single-scenario runs (was only shown during `runAll`)
- **Scenario progress counter** — initialized to `0/N` on render
  instead of showing `0/0` until first run
- **Post-scenario reset button** — shown unconditionally after scenario
  ends (was gated on `segmentIndex > 0`)
- **Disabled button opacity** — `.dev-panel__btn:disabled` changed from
  0.35 to 0.5 for better readability
- **Dev panel titles** — "Scenarios" to "Demo Scenarios", "Inspector"
  to "Dev Panel" for consistency
- **Failed button tooltip** — added `title="No failed scenarios to
  re-run"` for accessibility

## [0.2.0] - 2026-03-16

Multi-mode role inference, local-first defaults, SQLite persistence,
manual speaker overrides, and comprehensive hardening across the full stack.

### Added

- **Mode-aware role inference** — 6 mode-specific system prompts
  (Medical, Meeting, Interview, TV/Media, Lecture, General) with tailored
  role names and reasoning signals; mode passed from browser via WebSocket
  query parameter; agent cached per mode (`@lru_cache(maxsize=6)`)
- **3-tier role inference fallback** — LLM agent (Ollama/Bedrock) →
  heuristic keyword classifier → None (graceful degradation); heuristic
  uses mode-specific keyword matching for DOCTOR/PATIENT, ORGANISER/PARTICIPANT,
  INTERVIEWER/CANDIDATE, or SPEAKER_A/B
- **Manual speaker override** — click speaker label to cycle roles,
  override published to Mercure, locks mapping as ground truth
  (`confirmed_overrides`), agent respects locked speakers; checkmark
  icon on overridden segments; `POST /session/{id}/roles/override` endpoint
- **SQLite persistence backend** — `StorageBackend` protocol with
  `SqliteBackend` and in-memory `SessionStore` implementations; factory
  via `SESSION_STORAGE=sqlite|memory` env var; WAL mode, thread-safe;
  Docker volume mount for data persistence across container restarts
- **WebSocket reconnect grace period** — `schedule_destroy()` delays
  session cleanup by 30s (configurable via `SESSION_RECONNECT_GRACE_SECONDS`);
  reconnecting client resumes existing `TranscriptionSession` with audio
  buffer and transcript state intact
- **Mercure Last-Event-ID** — per-session monotonic event IDs on all
  Mercure publishes; browser passes `lastEventId` on SSE reconnect to
  resume from where it dropped
- **Speaker hallucination filter** — suppresses speakers with < 5% total
  frame activity from Sortformer diarisation output
- **Cold start animation** — segments fade from grey to colour-coded with
  0.6s ease-out animation when roles are identified
- **Flip notification toast** — "Speaker labels corrected" amber toast
  when `flip_detected=true`
- **Audio quality feedback** — RMS energy monitoring in PcmStreamer;
  "Low audio level" warning after 3 consecutive low-energy chunks;
  "Audio clipping detected" when amplitude saturates; colour-coded
  indicator dot (green/yellow/red)
- **Keyboard shortcuts** — Space toggles recording, Esc stops, D downloads;
  hints shown on buttons; `aria-live="polite"` and `role="log"` on
  transcript container for screen reader support
- **Periodic orphan cleanup** — background task every 5 minutes removes
  entries from `_inference_queues`, `_inference_workers`, `_session_modes`,
  `_session_states` for sessions not in `lifecycle._active`
- **Segment event protocol** — `segment_id`, `revision`, `supersedes`
  fields on `Segment` dataclass; server assigns IDs for future
  reconciliation support
- **Ollama Docker service** — optional CPU-only Ollama container via
  `docker compose --profile local up`; `ollama_data` volume for model
  persistence
- **Tailwind bundled locally** — `public/js/tailwind.js` replaces CDN
  dependency; fully offline-capable
- **Hot-reload for Python** — `--reload` flag on uvicorn in docker-compose
  dev mode
- **26 SQLite backend tests** — roundtrip, replace, role mapping, transcript
  text, cleanup, persistence across connections
- **16 role inference tests** — flip detection (3 tests), 3-speaker mapping,
  confidence EWMA, normalize mapping, mode-specific prompts (7 tests),
  heuristic classifier (9 tests)
- **5 speaker hallucination filter tests**
- **20 nemo_session tests** — rewrote stale `_webm_accumulator` tests for
  current `AudioBuffer` API

### Changed

- **Default role inference provider** — `ROLE_AGENT_MODEL_PROVIDER`
  defaults to `ollama` (was `bedrock`); local-first, no AWS credentials
  needed
- **Confidence calculation** — last-5-readings window replaces lifetime
  simple average (early low-confidence readings no longer permanently
  drag down the score)
- **Transcript context window** — role inference agent receives first 500 +
  last 3000 chars (was last 2000); consultation opening preserved for
  stronger role signals
- **Agent JSON parsing** — regex fallback extracts JSON from agent response
  when `json.loads()` fails on preamble text
- **Mapping history** — capped to last 5 entries in agent prompt (was
  unbounded)
- **Role inference queue** — `maxsize=50` with `put_nowait`; drops batch
  on overflow instead of blocking
- **`asyncio.get_event_loop()`** — replaced with `get_running_loop()`
  everywhere (deprecated in Python 3.10+)
- **Persistent httpx client** — single `AsyncClient` created in lifespan,
  shared across all Mercure publishes (was per-publish connection churn)
- **`AudioBuffer`** — uses `collections.deque` for O(1) popleft (was
  `list.pop(0)` O(n))
- **`relabelSegments()` performance** — tracks segments by speaker_id in
  a Map; only updates changed speakers on role update (was O(n) full
  DOM traversal)
- **`SessionLifecycle` simplified** — removed SSE consumer tracking
  (deleted alongside legacy SSE endpoint); destroy is now always atomic

### Removed

- **Legacy PHP SSE role path** — deleted `POST /scribe/{id}/roles/stream`
  endpoint, `RoleInferenceService::streamRoleInference()`,
  `RoleInferenceResult` class, and `fetchAuthoritativeSnapshot()`.
  Mercure queue is the single live role delivery path (footgun FG-2
  resolved)
- **Python `/session/{id}/roles/stream` SSE endpoint** — deleted, no
  callers remain
- **`sse-starlette` import** — no longer needed
- **SSE consumer tracking** in `SessionLifecycle` — `sse_consumer_start()`,
  `sse_consumer_end()`, `_sse_consumers` dict removed
- **`docker-compose.no-gpu.yml`** — unnecessary; GPU is required for the
  application to function

### Fixed

- **`test_api.py`** — added missing imports (`ThreadPoolExecutor`, `httpx`,
  `asyncio`, `WebSocketDisconnect`), added `client` fixture, updated all
  session IDs to valid UUIDs
- **`test_nemo_session.py`** — replaced stale `_webm_accumulator`
  references with current `AudioBuffer` API (7 → 20 tests)
- **Path traversal in `/transcribe/file`** — replaced manual
  `/tmp/scribe_{session_id}.wav` with `tempfile.NamedTemporaryFile`
- **Session ID validation** — all endpoints validate UUID format; rejects
  path traversal and injection attempts with HTTP 400
- **Error message leaking** — Mercure error publishes use generic
  "Transcription error occurred" instead of raw `str(e)`
- **Log privacy** — role inference error logs truncated to 200 chars with
  `error_type` instead of full stack traces that could contain transcript
- **`pcmStreamer` implicit global** — declared with `let`
- **Health check log spam** — `start-dev.sh` log filter excludes
  `GET /health` lines
- **`start-dev.sh` hardcoded ports** — ready banner uses `${APP_PORT}`,
  `${AGENT_PORT}`, `${MERCURE_PORT}`

### Security

- Session ID validated as UUID on all API endpoints
- Temp files use secure `NamedTemporaryFile` (not user-controlled paths)
- Error messages sanitised before publishing to Mercure
- Transcript content stripped from application logs

## [0.1.0] - 2026-03-15

First release. Real-time audio transcription with speaker diarisation,
role inference, and a developer-facing scenario runner for testing
without GPU hardware.

### Added

- **Transcription UI** — single-page Twig template with live transcript,
  recording controls, timer, download (JSON + plain text), and session reset
- **Mode selector** — switch between Medical, Meeting, Interview, TV/Media,
  Lecture, and General modes; each mode maps backend roles to
  context-appropriate labels and avatars
- **Theme support** — light/dark toggle with sun/moon icons, persisted to
  localStorage, respects `prefers-color-scheme`
- **StreamOrchestrator** — Mercure SSE client with auto-reconnect,
  exponential backoff (1 s to 30 s), and multi-topic management
- **PcmStreamer** — browser-side 16 kHz mono PCM capture via Web Audio API
  with configurable chunk interval
- **Reconnect logic** — automatic WebSocket reconnect (3 attempts) with
  segment preservation and manual reconnect fallback
- **Role inference** — Strands agent streaming role updates via
  `RoleInferenceService`, retroactive segment relabelling, three-tier
  confidence badge (green >=80%, amber >=50%, grey <50%)
- **Dev panel** (APP_ENV=dev only, zero prod bytes) —
  - 3-column layout: scenario panel (left), transcript (centre),
    inspector (right)
  - Inspector tabs: Segments, Pipeline, Mercure, WebSocket, State, Raw
  - Raw tab ring buffer (200 entries) with collapsible JSON and Copy All
  - State tab auto-refreshes every 500 ms
- **Scenario runner** — 8 fixture-driven scenarios injected client-side
  (happy path, role flip, reconnect recovery, high-volume stress,
  empty session, single speaker, late role update, permanent disconnect)
  with end-state validation, batch execution, progress bar, and JSON export
- **ScribeController** — `GET /scribe` (UI), `GET /scribe/{id}/history`,
  `GET /scribe/{id}/roles`; loads scenario fixtures in dev mode
- **Python API server** — FastAPI with WebSocket audio ingest,
  NeMo diarisation pipeline, Mercure publishing, session lifecycle
- **NeMo session management** — GPU singleton, ThreadPoolExecutor,
  model configuration, session history
- **Role assignment tooling** — Strands agent tool for speaker role mapping
- **Infrastructure** — Terraform config for self-contained deployment,
  Docker Compose with NVIDIA GPU support, Mercure hub
- **Scripts** — `setup-initial.sh`, `start-dev.sh`, `preflight-checks.sh`,
  `health-check-localdev.sh`, `api-load-test.sh`, `e2e-test.sh`,
  `context-validate.sh`
- **Quality gates** — `composer preflight` (PHPStan L10, PHP-CS-Fixer,
  PHPMD, cyclomatic complexity, coverage, Twig lint, Python syntax,
  Docker Compose validation)
- **Test suites** — PHPUnit (controller, role inference service), pytest
  (API, NeMo pipeline, role inference, concurrent sessions, cleanup races,
  Mercure failures, inference queue)
- **E2E scaffolding** — Playwright config and test stubs
- **Documentation** — architecture overview, domain reference, footguns
  index, local development guide, milestone plans, NeMo API notes

### Fixed

- `start-dev.sh` crashes on launch — unbound variables and undefined
  functions under `set -uo pipefail`
- Dev panel inspector Segments tab never updated retroactively
- StreamOrchestrator `_active` flag ordering bug
- Docker volume mount path for Python hot-reload

[Unreleased]: https://github.com/user/ambient-scribe/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/user/ambient-scribe/releases/tag/v0.2.0
[0.1.0]: https://github.com/user/ambient-scribe/releases/tag/v0.1.0
