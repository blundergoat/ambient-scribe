# Architecture

Ambient Scribe is a browser-to-FastAPI live transcription system with Symfony serving the UI and Mercure carrying transcript events back to the browser. PHP owns page rendering, config injection, and proxy-style history/role endpoints; Python owns live audio ingest, NeMo inference, role inference, storage, summaries, and Mercure publishing.

## System Overview

- Browser UI (`templates/scribe/index.html.twig`, `public/js/scribe.js`, `public/js/scribe-fixtures.js`) creates a session, captures microphone audio with `PcmStreamer`, replays generated demo WAVs, sends PCM chunks to the FastAPI WebSocket, and subscribes to Mercure topics through `StreamOrchestrator`.
- Symfony app (`src/Controller/ScribeController.php`, `src/Service/RoleInferenceService.php`) renders `/scribe`, redirects `/`, proxies transcript history from Python, and exposes current role mappings.
- FastAPI agent (`strands_agents/api/server.py`) exposes batch transcription, live WebSocket transcription, history, role override/snapshot, summary, replay, and health endpoints.
- NeMo pipeline (`strands_agents/nemo_pipeline.py`, `strands_agents/nemo_session.py`) owns the single GPU and performs diarization/ASR; role inference never uses that GPU.
- Role and summary agents (`strands_agents/agents/transcription_agent.py`, `strands_agents/agents/summary_agent.py`, `strands_agents/tools/assign_roles.py`) run medical speaker role attribution and structured SOAP-style end-of-session summaries.
- Storage (`strands_agents/session.py`, `strands_agents/storage.py`) is either in-memory `SessionStore` or SQLite `SqliteBackend`, selected by `SESSION_STORAGE`.
- Mercure (`docker-compose.yml`) fans out per-session raw, roles, summary, and hint events under `scribe/session/{id}/{raw|roles|summary|hints}`.
- Observability (`strands_agents/logging_config.py`, `src/Logging/JsonLineLogger.php`, `src/Observability/StrandsClientTelemetry.php`) emits JSON-line process logs with `session_id`/`correlation_id` join keys when `LOG_FORMAT=json`.

## Request Flow

1. Browser requests `GET /scribe`; `ScribeController::index` generates a UUID and injects WebSocket URL, Mercure URL, raw/roles/summary/hints topics, and dev audio fixture options into Twig.
2. `public/js/scribe.js` captures audio, downsamples to 16 kHz 16-bit PCM, and opens `ws://.../ws/transcribe/{session_id}`.
3. `strands_agents/api/server.py::transcribe_stream` validates the UUID, registers the session in `SessionLifecycle`, buffers chunks in `TranscriptionSession`, and runs NeMo through `ThreadPoolExecutor`.
4. FastAPI stores raw segments, publishes `segment` events to `scribe/session/{id}/raw`, queues role inference, then publishes `role_update` events to `scribe/session/{id}/roles`.
5. Browser EventSource handlers merge live raw and role events into the transcript timeline; demo replay receives batch segments from FastAPI and reveals them from the browser audio clock before summary generation.
6. On end session, FastAPI can generate a summary and optional hints on `scribe/session/{id}/summary` and `scribe/session/{id}/hints`.
7. PHP non-live paths call Python history and role endpoints through `StrandsClient` and `RoleInferenceService`.

## Auth / Trust Boundaries

- The local `/scribe` UI is served by Symfony without app-level login in the current code.
- Mercure publish auth uses `MERCURE_JWT` or an HS256 token derived from `MERCURE_JWT_SECRET`; browser subscribe is anonymous in local Docker through Mercure directives.
- Session IDs crossing browser, Symfony, FastAPI, and Mercure are UUIDs; FastAPI rejects malformed session IDs before storage or inference.
- Environment and secret values belong in `.env`, `.env.example`, Docker Compose, Terraform secrets, and deployment settings, not committed runtime credentials.

## Data Flow

- Live audio moves directly from browser to FastAPI WebSocket; PHP does not process live audio frames.
- Transcript segments are written to `SessionStore` memory or `SqliteBackend` at `/data/sessions.db` inside the `session_data` Docker volume.
- `SessionLifecycle` tracks active WebSocket sessions, reconnect grace, and teardown; storage TTL and cleanup are separate from active WebSocket state.
- Role state is kept per session by `assign_roles` and is cleaned with lifecycle teardown or periodic orphan cleanup.
- Demo replay stores batch-transcribed segments for role inference and summary context, but visible transcript pacing follows the browser audio element.
- Mercure carries JSON events only; it is the browser-facing event fan-out, not the source of durable transcript state.

## Deployment / Operations

- Local runtime is `docker-compose.yml` with `nemo-agent`, `app`, `mercure`, and optional CPU-only `ollama` profile.
- `docker/nemo/Dockerfile` builds the NeMo FastAPI image from NVIDIA's `nvcr.io/nvidia/nemo:26.02` base image and pins `nemo_toolkit[asr]==2.7.3`; `Dockerfile` builds the Symfony app container.
- `scripts/start-dev.sh`, `scripts/health-check-localdev.sh`, `scripts/gpu-check.sh`, and `scripts/preflight-checks.sh` are the main local operator commands.
- GitHub Actions live under `.github/workflows/`; Terraform production scaffolding lives under `infra/terraform/environments/prod/` and modules under `infra/terraform/modules/`.

## Constraints

- NeMo owns the GPU; role inference must stay on Bedrock or CPU-only Ollama.
- `NEMO_STREAM_INPUT_FORMAT` is a browser/server contract and is not auto-detected.
- `NEMO_MAX_WORKERS` gates concurrent GPU-bound inference work in FastAPI.
- Mercure publish failures log at ERROR and return `False`; the WebSocket path emits a `system_error` frame for browser-visible failures.
- Browser-facing URLs in `.env.example`, `docker-compose.yml`, and Twig-injected config must stay aligned for local versus remote clients.

## Trade-Offs

- Separate Mercure topics keep raw transcription, role inference, summary delivery, and hints independently recoverable.
- In-memory storage keeps local iteration simple; SQLite adds persistence without matching Terraform's DynamoDB-oriented production scaffold.
- A singleton NeMo pipeline minimizes GPU churn but process restart is the recovery path for a poisoned model state.
- PHP retains non-live role/history endpoints while the main live UI path uses Mercure events.
