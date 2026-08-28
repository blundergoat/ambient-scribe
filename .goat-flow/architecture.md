# Architecture

Ambient Scribe is a browser-to-FastAPI live transcription system with Symfony serving the UI and Mercure carrying transcript events back to the browser. PHP owns page rendering, config injection, and proxy-style history/role endpoints; Python owns live audio ingest, NeMo inference, role inference, storage, summaries, and Mercure publishing.

## System Overview

- Browser UI shell (`templates/scribe/index.html.twig`, `public/js/scribe.js`, `public/js/scribe-fixtures.js`) creates a session and shared page state.
- Browser streaming modules (`public/js/scribe-streaming.js`, `public/js/scribe-recording.js`) capture microphone audio with `PcmStreamer`, stream generated demo WAVs with `WavPcmStreamer`, send PCM chunks to the FastAPI WebSocket, and subscribe to Mercure topics through `StreamOrchestrator`.
- Symfony app (`src/Controller/ScribeController.php`, `src/Service/RoleInferenceService.php`) renders `/scribe`, redirects `/`, proxies transcript history from Python, and exposes current role mappings.
- FastAPI agent (`strands_agents/api/server.py`) exposes batch transcription (test-only), live WebSocket transcription, history, role override/snapshot, post-stop correction (`/session/{id}/correction`, `/session/{id}/corrected-transcript`), summary, and health endpoints.
- NeMo pipeline (`strands_agents/nemo_pipeline.py`, `strands_agents/nemo_session.py`) owns the single GPU and performs diarization/ASR; role inference never uses that GPU. `NEMO_SESSION_ENGINE` selects the live engine: `windowed` (per-window re-diarization, Compose default) or `streaming` (`strands_agents/nemo_streaming_engine.py`, session-long Sortformer speaker cache; `start-dev.sh` default).
- Role and summary agents (`strands_agents/agents/transcription_agent.py`, `strands_agents/agents/summary_agent.py`, `strands_agents/tools/assign_roles.py`) run medical speaker role attribution and structured SOAP-style end-of-session summaries.
- Storage (`strands_agents/session.py`, `strands_agents/storage.py`) is either in-memory `SessionStore` or SQLite `SqliteBackend`, selected by `SESSION_STORAGE`.
- Mercure (`docker-compose.yml`) fans out per-session raw, roles, and summary events under `scribe/session/{id}/{raw|roles|summary}`.
- Observability (`strands_agents/logging_config.py`, `src/Logging/JsonLineLogger.php`, `src/Observability/StrandsClientTelemetry.php`) emits JSON-line process logs with `session_id`/`correlation_id` join keys when `LOG_FORMAT=json`.

## Request Flow

1. Browser requests `GET /scribe`; `ScribeController::index` generates a UUID and injects WebSocket URL, Mercure URL, raw/roles/summary topics, and dev audio fixture options into Twig.
2. `public/js/scribe-streaming.js` captures and downsamples audio to 16 kHz 16-bit PCM, while `public/js/scribe-recording.js` opens `ws://.../ws/transcribe/{session_id}`.
3. `strands_agents/api/server.py::transcribe_stream` validates the UUID, registers the session in `SessionLifecycle`, buffers chunks in `TranscriptionSession`, and runs NeMo through `ThreadPoolExecutor`.
4. FastAPI stores raw segments, publishes `segment` events to `scribe/session/{id}/raw`, queues role inference, then publishes `role_update` events to `scribe/session/{id}/roles`.
5. Browser EventSource handlers merge live raw and role events into the transcript timeline; demo replay decodes the WAV locally and streams PCM over the same WebSocket, paced by the browser audio clock, so its segments arrive through the identical Mercure path.
6. On end session, FastAPI can generate a summary on `scribe/session/{id}/summary`.
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
- Demo replay is a live session fed from a decoded WAV: the browser audio element paces PCM chunk streaming, so segments, role inference, and summary context flow through the same streaming session storage as a microphone visit.
- After Stop, the correction endpoint re-runs ASR (`strands_agents/post_visit_correction.py`, second-pass Parakeet on the GPU executor) over retained session audio and stores corrected rows in a separate corrected-transcript lane; summary generation prefers corrected rows and validates their `segment_id` citations.
- Mercure carries JSON events only; it is the browser-facing event fan-out, not the source of durable transcript state.

## Deployment / Operations

- Local runtime is `docker-compose.yml` with `nemo-agent`, `app`, `mercure`, and optional CPU-only `ollama` profile.
- `docker/nemo/Dockerfile` builds the NeMo FastAPI image from NVIDIA's NeMo 26.02 base image and pins `nemo_toolkit[asr]==2.7.3`; `Dockerfile` builds the Symfony app container.
- `scripts/start-dev.sh`, `scripts/health-check-localdev.sh`, `scripts/gpu-check.sh`, and `scripts/preflight-checks.sh` are the main local operator commands.
- This checkout has no repository CI workflow; Terraform production scaffolding lives under `infra/terraform/environments/prod/` and modules under `infra/terraform/modules/`.

## Local Data and Evidence Budget

- Committed architecture, code-map, glossary, decision, and learning-loop files are durable orientation, but agents still re-read live code and rerun commands before claiming current behaviour.
- `.goat-flow/skill-docs/playbooks/` contains the committed top-level tool guidance: browser-use.md, changelog.md, code-comments.md, gruff-code-quality.md, hook-policy-testing.md, naming-and-placement.md, observability.md, page-capture.md, release-notes.md, skill-playbook-authoring-sync.md, test-selection.md, writing-sentence-diagnostics.md, writing-structure-diagnostics.md, and writing-style.md.
- `.goat-flow/plans/`, `.goat-flow/scratchpad/`, `.goat-flow/logs/sessions/`, `.goat-flow/logs/quality/`, `.goat-flow/logs/events/`, `.goat-flow/logs/critiques/`, `.goat-flow/logs/review/`, and `.goat-flow/logs/security/` are gitignored checkout-local state. They may resume work or explain prior evidence, but cannot prove current behaviour or authorize commits, pushes, external messages, or other side effects.
- Local artifacts contain only the minimum paths, commands, redacted summaries, and pass/fail evidence needed for continuity. Never copy credentials, environment contents, raw clinical audio, transcripts, or patient-identifying data into them; session and handoff text goes through `goat-flow redact` before persistence.
- Promote only a re-verified durable conclusion into `.goat-flow/learning-loop/` or `.goat-flow/learning-loop/decisions/`, citing live files with semantic anchors rather than the local artifact. Goat-flow does not purge local artifacts automatically; the user owns retention and removal.

## Constraints

- NeMo owns the GPU; role inference must stay on Bedrock or CPU-only Ollama.
- `NEMO_STREAM_INPUT_FORMAT` is a browser/server contract and is not auto-detected.
- `NEMO_MAX_WORKERS` gates concurrent GPU-bound inference work in FastAPI.
- Mercure publish failures log at ERROR and return `False`; the WebSocket path emits a `system_error` frame for browser-visible failures.
- Browser-facing URLs in `.env.example`, `docker-compose.yml`, and Twig-injected config must stay aligned for local versus remote clients.

## Trade-Offs

- Separate Mercure topics keep raw transcription, role inference, and summary delivery independently recoverable.
- In-memory storage keeps local iteration simple; SQLite adds persistence without matching Terraform's DynamoDB-oriented production scaffold.
- A singleton NeMo pipeline minimizes GPU churn but process restart is the recovery path for a poisoned model state.
- PHP retains non-live role/history endpoints while the main live UI path uses Mercure events.
