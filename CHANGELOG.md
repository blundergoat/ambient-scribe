# Changelog

All notable changes to Ambient Scribe are documented in this file.

Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

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
  `POST /scribe/{id}/roles/stream`, `GET /scribe/{id}/roles`; loads
  scenario fixtures in dev mode
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
  index, local development guide, milestone plans (1-3), NeMo API notes

### Fixed

- `start-dev.sh` crashes on launch — unbound `OLLAMA_HOST`,
  `MODEL_PROVIDER`, `NEMO_MODEL_PROVIDER`, and `ERRORS` variables under
  `set -uo pipefail`; undefined `select_available_port`,
  `start_compose_stack`, `follow_agent_logs`, `show_compose_failure_details`,
  and `export_aws_profile_credentials` functions (dead code from prior
  refactor removed)
- Ready banner in `start-dev.sh` showed hardcoded old ports (`8082`,
  `8001`, `3701`) instead of configured `APP_PORT`, `AGENT_PORT`,
  `MERCURE_PORT`
- Dev panel inspector Segments tab never updated retroactively when roles
  were assigned — entries now relabel in sync with the main transcript
- High-volume stress scenario had no `role_update`, so `relabelSegments()`
  across 50 DOM nodes was never exercised
- StreamOrchestrator `_active` flag ordering bug that prevented clean
  reconnection after page-level teardown
- Docker volume mount path for Python hot-reload

[0.1.0]: https://github.com/user/ambient-scribe/releases/tag/v0.1.0
