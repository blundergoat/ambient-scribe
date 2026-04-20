# Architecture

Ambient Scribe is a browser -> Python -> Mercure -> browser transcription system. Symfony serves the UI and session APIs; the live audio path bypasses PHP.

## Components

- Browser UI (`templates/scribe/index.html.twig`): captures microphone audio with `PcmStreamer`, opens the WebSocket, and subscribes to Mercure `raw` and `roles` topics.
- Symfony app (`src/`): renders `/scribe`, exposes history and role snapshot/proxy endpoints, and owns app config.
- FastAPI agent (`strands_agents/api/server.py`): handles WebSocket ingest, batch upload, session history, legacy role SSE, and Mercure publishing.
- NeMo pipeline (`strands_agents/nemo_pipeline.py`, `strands_agents/nemo_session.py`): singleton GPU diarization/ASR plus per-session audio buffering.
- Role inference (`strands_agents/agents/transcription_agent.py`, `strands_agents/tools/assign_roles.py`): sequential per-session DOCTOR/PATIENT mapping.
- Summary generation (`strands_agents/agents/summary_agent.py`): mode-specific structured summaries (SOAP, action items, etc.) on session end.
- Mercure (`docker-compose.yml`): fan-out for three topics per session — raw segments, role assignments, and end-of-session summary (topic template `scribe/session/<id>/<concern>`, concern ∈ {raw, roles, summary}).
- Terraform (`infra/terraform/`): ECS/Fargate, ALB, Mercure, secrets, and DynamoDB scaffolding.

## Primary Flows

1. Browser loads `/scribe`; Symfony injects session id, WebSocket URL, Mercure URL, and topic names.
2. Browser streams PCM audio to `ws://.../ws/transcribe/{session_id}`.
3. FastAPI runs NeMo inside `ThreadPoolExecutor(max_workers=2)`, stores transcript state, and publishes raw segments to Mercure.
4. A per-session async queue runs role inference and publishes role updates to Mercure.
5. Browser merges the `raw` and `roles` topics into one transcript timeline.
6. PHP can still fetch history plus current/streamed role data for non-live paths.

## Constraints

- NeMo owns the GPU; the role model must stay on Bedrock or CPU-only Ollama.
- `NEMO_STREAM_INPUT_FORMAT` is a browser/server contract, not auto-detected.
- Live state is coordinated by `SessionLifecycle` (wraps active sessions + role state cleanup). `SessionStore` transcript data has independent TTL eviction.
- Mercure publish failures return `False`, log at ERROR, and send a `system_error` WebSocket frame to the browser.
- Browser-facing URLs in `.env.example` and `docker-compose.yml` use local-host defaults and must be overridden for remote clients.

## Trade-Offs

- Two Mercure topics keep the NeMo hot path independent from role-inference latency.
- In-memory state keeps iteration fast but loses data on restart and diverges from Terraform's DynamoDB scaffold.
- A singleton NeMo pipeline minimizes GPU churn but requires process restart for recovery.
- Legacy PHP role-stream endpoints remain even though the live UI now prefers Mercure role topics.
