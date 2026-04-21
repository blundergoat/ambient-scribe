# Glossary

Project-specific domain terms a new contributor needs to recognise when reading the code.

## Transcription pipeline

- **NeMo** — NVIDIA's speech framework. Runs Parakeet (multi-talker ASR) and Sortformer (diarization) on the single GPU inside `strands_agents/nemo_pipeline.py`.
- **Parakeet** — `EncDecMultiTalkerRNNTBPEModel`, the ASR model. ~4.5 GB VRAM.
- **Sortformer** — `SortformerEncLabelModel`, the diarization model (who-spoke-when). ~1.2 GB VRAM.
- **PcmStreamer** — Browser-side capture/resampler in `public/js/scribe.js`. Always emits 16 kHz 16-bit PCM; the server trusts `NEMO_STREAM_INPUT_FORMAT` to match.
- **Raw segment** — Pre-role-attribution transcript chunk published to `scribe/session/{id}/raw`.
- **Roles topic** — Post-inference speaker → role mapping published to `scribe/session/{id}/roles`.
- **Summary topic** — End-of-session summary update published to `scribe/session/{id}/summary`.

## Session / lifecycle

- **SessionLifecycle** — `strands_agents/session_lifecycle.py`. Owns active WebSocket sessions and per-session cleanup with reconnect grace window.
- **SessionStore** — `strands_agents/session.py`. In-memory or SQLite transcript history with independent TTL eviction.
- **Mode** — One of 6 capture profiles: Medical, Meeting, Interview, TV/Media, Lecture, General. Drives role taxonomy and summary prompt.
- **Role inference** — Bedrock or CPU Ollama agent (`strands_agents/agents/transcription_agent.py`) that calls `assign_roles` as a tool. Never runs on the NeMo GPU.

## Runtime / infra

- **Mercure** — SSE fan-out hub. JWT-authed publish from FastAPI; browser subscribes with a short-lived JWT.
- **Strands** — Agent SDK used to wire Ollama/Bedrock + `assign_roles` tool. Configured via `config/packages/strands.yaml`.
- **Preflight** — `./scripts/preflight-checks.sh`: local umbrella quality gate for PHP validation, PHPStan L10, coding style dry-run, PHPMD/complexity, PHPUnit, coverage, Docker Compose config, and optional Ruff when installed.
