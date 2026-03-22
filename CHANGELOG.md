# Changelog

All notable changes to Ambient Scribe are documented in this file.

Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

M3 completion (tool wiring, Ollama fix), M4 features (summaries, replay,
transcript grouping, scenario gate), plus prior JS extraction and UI polish.

### Added

- **Agent-neutral instruction layer** — `ai/instructions/base.md` (stack,
  architecture, hard constraints), `ai/instructions/code-review.md` (review
  priorities, approval criteria, anti-patterns from footguns), `ai/instructions/
  git-commit.md` (commit format, project-specific areas and examples),
  `ai/README.md` routing table; usable by any AI coding agent without
  depending on Claude Code or Codex runtime files
- **CI: Router table validation** — new workflow step parses CLAUDE.md Router
  table and verifies every backtick-wrapped path exists on disk (skips globs)
- **CI: Skills directory validation** — new workflow step ensures each
  `.claude/skills/goat-*/` contains a `SKILL.md` file
- **`.github/git-commit-instructions.md`** — quick-reference commit format
  summary pointing to full `ai/instructions/git-commit.md`

### Changed

- **CLAUDE.md Ask First** — expanded generic area names to real file paths;
  replaced nonexistent `config/packages/security.yaml` with verified
  `config/packages/framework.yaml`
- **`.github/instructions/commit-messages.instructions.md`** — replaced
  Forge/WSL boilerplate areas and examples with project-specific ones
  (Backend, Agent, NeMo, Frontend, Infra, Scripts, CI)
- **Agent evals** (8 files) — restructured from flat bullets to headed
  sections (Bug Description, Replay Prompt, Expected Outcome, Failure Mode
  Tested) with numbered pass/fail steps
- **`docs/lessons.md`** — expanded terse bullets into full narrative entries
  with root cause, evidence, and cross-references
- **`tasks/.gitignore`** — switched from explicit file ignores to allowlist
  pattern (keep only `.gitignore` and `handoff-template.md`)

### Fixed

- **Package name in code-review instructions** — `blundergoat/strands-client`
  → `blundergoat/strands-php-client` (matches `composer.json`)
- **Footgun cross-reference** — `docs/lessons.md` audio format entry now
  correctly references footgun #3 (was #6)
- **CI path triggers** — added missing paths (`.github/git-commit-instructions.md`,
  `ai/**`, `.claude/skills/**`) so workflow runs on instruction file changes

### Added (continued from above)

- **`@tool` assign_roles** — Strands `@tool`-decorated function for
  programmatic role state management; passed to `Agent(tools=[assign_roles])`;
  dual-path worker detects tool invocation vs free-text JSON fallback
- **Session summary generation** — `summary_agent.py` with 6 mode-specific
  prompts: Medical (SOAP note), Meeting (action items/decisions),
  Interview (strengths/concerns), TV (key moments), Lecture (concepts),
  General (key points); `POST /session/{id}/summary` endpoint; published
  to Mercure topic `scribe/session/{id}/summary`; collapsible UI panel
  auto-triggered on session end; included in transcript export
- **Replay demo mode** — `POST /session/{id}/replay` accepts WAV upload,
  processes through NeMo `transcribe_file()`, replays segments to Mercure
  with real-time pacing (adjustable speed 0.25x–10x); "Demo" button with
  file picker; progress bar + timer; role inference runs automatically
- **Transcript segment grouping** — consecutive same-speaker segments
  merge into unified chat blocks with flowing text; new speaker starts a
  new block; relabeling and download handle grouped structure
- **Scenario timing assertions** — `maxDurationMs` in `expectedEndState`;
  high-volume stress must complete within 5s budget
- **Scenario content assertions** — `contentCheck.firstSegmentText` /
  `lastSegmentText` verifies rendered DOM text matches injected data
- **Scenario fixture validation** — `test_scenarios.py` (12 tests)
  validates JSON structure, unique IDs, event types, segment data,
  expectedEndState consistency, contentCheck accuracy
- **Footgun FG-10** — Ollama model must support tool calling for
  `assign_roles`; recommends `qwen2.5:14b`, notes `qwen3:14b` alternative
- **JS extracted from Twig template** — `public/js/scribe.js` (core app)
  and `public/js/scribe-dev.js` (dev panel, zero prod bytes)
- **Dev Panel WebSocket instrumentation** — `_instrumentWs()` wraps
  `ws.send`/`onmessage` to track frame counts and byte totals
- **Docker hot reload** — volume-mount `.:/app` in `docker-compose.yml`
- **Footgun FG-9** — documented template rebuild pitfall
- **5 multi-mode test scenarios** — Meeting, Interview, TV, Lecture, 3+ Speakers
- **37 new Python tests** — 4 tool tests, 10 summary tests, 5 replay
  tests, 12 scenario validation tests, 6 summary prompt tests

### Changed

- **Ollama model default** — `llama3.1:8b` → `qwen2.5:14b`; aligns with
  `docker-compose.yml`, `.env.example`, and what is actually pulled locally
- **Role inference worker** — snapshots `mapping_history` length before
  agent call; if history grew (tool invoked), skips duplicate
  `apply_role_mapping_result`; `last_flip_detected` field on
  `RoleMappingState` for accurate Mercure reporting
- **System prompt** — instructs agent to call `assign_roles` tool with
  JSON fallback for models without tool support
- **Agent constructor** — `tools=[assign_roles]` (was `tools=[]`)
- **`start-dev.sh` simplified** — removed `--build`/`--no-logs` flags
- **Role flip detection** — moved to client-side mapping comparison
- **Mode start labels** — TV: "Start Broadcast"; General: "Start Transcription"

### Removed

- **`ROLE_INFERENCE_SYSTEM_PROMPT`** — stale backwards-compat alias deleted
  (no users, per project policy)

### Fixed

- **Confidence badge pulse** — cleared on both stop and post-scenario cleanup
- **Session reset** — clears dev panel logs, speaker maps, summary panel,
  replay state
- **Download fallback** — gathers text from all `.segment__text` spans in
  grouped blocks (was querying single `.segment__text`)
- **E2E session IDs** — changed from `e2e-xxx-{hex}` to proper UUIDs
  (UUID validation rejects non-UUID format)
- **E2E stale endpoint reference** — replaced deleted `/roles/stream` with
  `/summary` endpoint check
- **E2E JS extraction assertions** — `test_scribe_has_reconnect_and_download`
  checks for `scribe.js` reference instead of inline function names

### Tests

- **230 Python unit tests** (61 new): dual-path worker (tool vs free-text,
  flip detection, attributed segments), assign_roles tool edges (dict/list
  inputs, missing fields, invalid JSON, 3-speaker flip, confidence boundaries),
  agent creation (provider selection, mode fallbacks, prompt content),
  summary generation (mode passthrough, JSON extraction, agent failures),
  replay (mode defaults, speed boundaries, duration, validation),
  heuristic (tv/lecture fallback, 3-speaker general, no-keyword medical),
  scenario fixture integrity (12 structural validations)
- **25 E2E contract tests** — agent health, sessions, WebSocket, file
  transcription, PHP proxy, Mercure pub/sub, session lifecycle, cross-service
  shape matching

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
