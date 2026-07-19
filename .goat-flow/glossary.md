# Glossary

Project-specific domain terms a new contributor needs to recognise when reading the code.

## Transcription pipeline

- **NeMo** - NVIDIA's speech framework. Runs Parakeet (multi-talker ASR) and Sortformer (diarization) on the single GPU inside `strands_agents/nemo_pipeline.py`.
- **Parakeet** - `EncDecMultiTalkerRNNTBPEModel`, the ASR model loaded by the single-GPU NeMo pipeline.
- **Sortformer** - `SortformerEncLabelModel`, the diarization model (who-spoke-when) loaded alongside Parakeet.
- **PcmStreamer** - Browser-side capture/resampler in `public/js/scribe-streaming.js`. Always emits 16 kHz 16-bit PCM; the server trusts `NEMO_STREAM_INPUT_FORMAT` to match.
- **StreamOrchestrator** - Browser-side Mercure EventSource manager in `public/js/scribe-streaming.js`. Tracks topic subscriptions, reconnect backoff, and Last-Event-ID resume state.
- **Raw segment** - Pre-role-attribution transcript chunk published to `scribe/session/{id}/raw`.
- **Roles topic** - Post-inference speaker → role mapping published to `scribe/session/{id}/roles`.
- **Summary topic** - End-of-session summary update published to `scribe/session/{id}/summary`.
- **Replay** - Browser/FastAPI flow that decodes a generated demo WAV locally and streams heard 16 kHz PCM over the live WebSocket so transcript text does not outrun audible speech.

## Session / lifecycle

- **SessionLifecycle** - `strands_agents/session_lifecycle.py`. Owns active WebSocket sessions and per-session cleanup with reconnect grace window.
- **SessionStore** - `strands_agents/session.py`. In-memory transcript history with independent TTL eviction; it can delegate persistence to a storage backend.
- **SqliteBackend** - `strands_agents/storage.py`. Persistent transcript backend stored at `SESSION_DB_PATH` when `SESSION_STORAGE=sqlite`.
- **Medical role taxonomy** - Ambient Scribe now uses one consultation profile: DOCTOR/PATIENT role inference plus SOAP-style summaries. The previous profile selector was removed in 0.3.0.
- **Role inference** - Bedrock or CPU Ollama agent (`strands_agents/agents/transcription_agent.py`) that calls `assign_roles` as a tool. Never runs on the NeMo GPU.

## Runtime / infra

- **Mercure** - SSE fan-out hub. FastAPI publishes with JWT credentials; local Compose permits anonymous browser subscriptions, while deployed subscriber authentication is environment-specific.
- **Strands** - Agent SDK used to wire Ollama/Bedrock + `assign_roles` tool. Configured via `config/packages/strands.yaml`.
- **Preflight** - `./scripts/preflight-checks.sh`: local umbrella quality gate for PHP validation, PHPStan L10, coding style dry-run, PHPMD/complexity, PHPUnit, coverage, Docker Compose config, and optional Ruff when installed.
