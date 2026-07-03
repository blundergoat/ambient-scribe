# Changelog

All notable changes to Ambient Scribe are documented in this file.

Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

## [0.2.0] - 2026-03-16

Release covering tool-based role mapping, summaries, replay, transcript grouping, scenario gates, JS extraction, UI polish, developer guidance, multi-mode role inference, local-first defaults, SQLite persistence, manual speaker overrides, and full-stack hardening.

### Added

- **Gruff quality analyzers** - added TypeScript, Python, and PHP dev analyzers: `@blundergoat/gruff-ts`, `gruff-py`, and `blundergoat/gruff-php`.
- **Agent-neutral instruction layer** - added reusable AI guidance in `ai/instructions/` plus routing docs for agents that do not depend on Claude Code or Codex runtime files.
- **CI validation** - added router-table and skills-directory checks, plus a quick-reference commit instruction file.
- **`@tool` role assignment** - added Strands tool support for role state management while keeping the free-text JSON fallback.
- **Session summaries** - added six mode-specific summary prompts, `POST /session/{id}/summary`, Mercure summary publishing, UI display, and transcript export support.
- **Replay demo mode** - added WAV upload replay through NeMo with paced Mercure events, speed control, progress UI, and automatic role inference.
- **Transcript grouping** - merges consecutive same-speaker segments into chat blocks that relabel and download correctly.
- **Scenario assertions** - added duration, content, and fixture-structure validation for the scenario runner.
- **Ollama tool-calling footgun** - documented models that support `assign_roles`, including `qwen2.5:14b`.
- **Frontend extraction** - moved production code to `public/js/scribe.js` and dev-only panel code to `public/js/scribe-dev.js`.
- **Developer instrumentation** - added WebSocket frame/byte counters, Docker hot reload, template rebuild guidance, five multi-mode scenarios, and 37 Python tests.
- **Mode-aware role inference** - added six mode-specific prompts, browser-passed mode, context labels, and per-mode agent caching.
- **Role inference fallback** - falls back from LLM agent to mode-specific heuristics, then to graceful no-role output.
- **Manual speaker override** - lets users cycle roles, publishes overrides to Mercure, locks confirmed speakers, and exposes `POST /session/{id}/roles/override`.
- **SQLite persistence** - added `StorageBackend`, SQLite and memory backends, `SESSION_STORAGE=sqlite|memory`, WAL mode, and Docker data persistence.
- **Reconnect support** - added WebSocket reconnect grace, session resume, and Mercure Last-Event-ID event IDs.
- **Speaker and role UX** - added hallucination filtering, cold-start animation, flip toast, audio-level feedback, clipping warnings, keyboard shortcuts, and transcript accessibility attributes.
- **Runtime cleanup and protocol fields** - added orphan cleanup plus `segment_id`, `revision`, and `supersedes` fields for future reconciliation.
- **Local runtime support** - added optional CPU Ollama service, bundled Tailwind, Python hot reload, and expanded SQLite, role inference, hallucination, and session tests.

### Changed

- **BREAKING: PHP baseline is now 8.3+.** Upgrade local, CI, and deployment PHP from 8.2 to 8.3 before running Composer; this has no deprecation window because the PHP Gruff dev tool requires PHP 8.3.
- **Ollama default model** - changed `llama3.1:8b` to `qwen2.5:14b` to match local pulls, `.env.example`, and Docker Compose.
- **Role inference worker** - detects tool invocation via mapping-history growth and avoids duplicate role mapping application.
- **Role inference prompt and agent setup** - instructs tool calling with JSON fallback and passes `assign_roles` in the agent tool list.
- **Role flip detection** - moved client-side so Mercure reporting reflects visible mapping changes.
- **Developer scripts and labels** - simplified `start-dev.sh` flags and renamed TV/General start labels.
- **Agent guidance** - made Ask First paths, commit areas, evals, and lessons more project-specific.
- **Default role provider** - changed `ROLE_AGENT_MODEL_PROVIDER` from `bedrock` to `ollama` for local-first startup.
- **Confidence scoring** - uses a rolling last-five window instead of lifetime average.
- **Transcript context** - sends the first 500 and last 3000 characters to preserve opening context.
- **Agent parsing and prompt state** - extracts JSON from preamble text and caps mapping history to five entries.
- **Inference queue** - uses `maxsize=50` with non-blocking enqueue and drops overflow batches.
- **Async/runtime internals** - replaced deprecated event-loop access, reused one Mercure `httpx.AsyncClient`, switched `AudioBuffer` to `deque`, optimized relabeling by speaker map, and simplified session destruction.

### Removed

- **`ROLE_INFERENCE_SYSTEM_PROMPT`** - removed the unused backwards-compatibility alias.
- **Legacy live role SSE path** - removed the PHP `/roles/stream` endpoint, `RoleInferenceService::streamRoleInference()`, `RoleInferenceResult`, `fetchAuthoritativeSnapshot()`, and the Python `/session/{id}/roles/stream` endpoint.
- **Unused SSE support** - removed `sse-starlette` imports and SSE consumer tracking.
- **`docker-compose.no-gpu.yml`** - removed the unused no-GPU compose file because the app requires GPU transcription.

### Fixed

- **Instruction drift** - fixed the `blundergoat/strands-php-client` package name, footgun cross-reference, and CI instruction-file triggers.
- **Session cleanup** - clears confidence pulse, dev panel logs, speaker maps, summaries, and replay state.
- **Download fallback** - collects text from grouped segment spans instead of a single segment node.
- **E2E contracts** - uses UUID session IDs, checks the `/summary` endpoint, and asserts the extracted `scribe.js` reference.
- **Python tests** - repaired stale imports, fixtures, UUIDs, and `AudioBuffer` API expectations.
- **File upload security** - replaced user-shaped temp paths with `NamedTemporaryFile`.
- **Session ID validation** - rejects malformed IDs with HTTP 400 on all endpoints.
- **Error privacy** - publishes generic Mercure errors and truncates role inference logs with `error_type`.
- **Frontend/runtime issues** - declared `pcmStreamer`, filtered health-check log spam, and made the ready banner use configured ports.

### Tests

- **230 Python unit tests** cover tool/free-text role mapping, flip detection, agent creation, summaries, replay, heuristics, and scenario fixtures.
- **25 E2E contract tests** cover agent health, sessions, WebSocket, file transcription, PHP proxy, Mercure pub/sub, lifecycle, and cross-service shape matching.

### Security

- Session IDs are UUID-validated on all API endpoints.
- Temp files use secure generated paths.
- Mercure error messages are sanitized.
- Transcript content is stripped from application logs.

## [0.1.0] - 2026-03-15

First release: real-time audio transcription with speaker diarisation, role inference, and a developer scenario runner that works without GPU hardware.

### Added

- **Transcription UI** - added the Twig page with live transcript, recording controls, timer, JSON/text download, and reset.
- **Modes and theme** - added Medical, Meeting, Interview, TV/Media, Lecture, and General modes plus persisted light/dark theme.
- **Streaming clients** - added Mercure `StreamOrchestrator`, browser-side `PcmStreamer`, and WebSocket reconnect logic.
- **Role inference** - added Strands role updates, retroactive relabeling, and confidence badges.
- **Dev panel** - added dev-only scenario, transcript, inspector, pipeline, Mercure, WebSocket, state, and raw-event views.
- **Scenario runner** - added eight fixture-driven scenarios with validation, batch execution, progress, and JSON export.
- **Backend and agent APIs** - added ScribeController routes, FastAPI WebSocket ingest, NeMo diarisation, Mercure publishing, session lifecycle, and role assignment tooling.
- **Infrastructure and tooling** - added Terraform, GPU Docker Compose, Mercure, setup/start/preflight/health/load/e2e/context scripts, quality gates, PHPUnit, pytest, Playwright scaffolding, and project docs.

### Fixed

- `start-dev.sh` no longer crashes on unbound variables or undefined functions.
- Dev panel segment data updates retroactively.
- StreamOrchestrator `_active` flag ordering is correct.
- Python hot-reload uses the correct Docker volume mount path.

[Unreleased]: https://github.com/user/ambient-scribe/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/user/ambient-scribe/releases/tag/v0.2.0
[0.1.0]: https://github.com/user/ambient-scribe/releases/tag/v0.1.0
