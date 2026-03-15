# Milestone 0 — Local Dev Gold Path

**Timeline:** ~2 hours
**Status:** Complete
**Dependencies:** Docker, NVIDIA GPU (optional — mock mode available)

---

## Objective

A developer can clone the repo, run one command, and have a working local stack with no cloud credentials. GPU owners get live transcription; non-GPU developers get mock mode for UI/agent development.

> **Note:** Terraform and cloud deployment have moved to M5. This milestone is purely about the local development experience.

---

## Tasks

### 0.1 Local Defaults

- [x] Default `ROLE_AGENT_MODEL_PROVIDER=ollama` in `.env.example` (not Bedrock)
- [x] `start-dev.sh` requires GPU for `NEMO_MODEL_PROVIDER=local` — exits with clear error if no NVIDIA GPU found
- [x] `NEMO_MODEL_PROVIDER=mock` available as explicit opt-in for test/development only
- [x] Document tested Ollama model (`qwen2.5:14b` default, ~30s CPU; `qwen2.5:7b` lighter, ~15s)
- [x] `.env.example` documents all required variables with sensible local defaults
- [x] Port scheme documented: APP=48082, AGENT=48101, MERCURE=48137

### 0.2 Ollama Integration

- [x] Add optional `ollama` service to `docker-compose.yml` (CPU-only, `--profile local`)
- [x] `start-dev.sh` auto-pulls the configured Ollama model on first run
- [x] `start-dev.sh` detects running Ollama and reuses it
- [x] Ollama host dockerized to `host.docker.internal` for container access

### 0.3 Zero-CDN Local Assets

- [x] Bundle Tailwind CSS locally (`public/js/tailwind.js`, 407KB)
- [x] No external script/stylesheet dependencies for the UI

### 0.4 Clean Clone Verification

- [x] `cp .env.example .env && ./scripts/start-dev.sh` works from a fresh clone
- [x] Mock mode (no GPU): `docker-compose.no-gpu.yml` override, nvidia-smi warns instead of failing
- [x] GPU mode: full transcription pipeline operational (verified 2026-03-15)
- [x] `scripts/setup-initial.sh` handles first-time setup
- [x] `scripts/start-dev.sh` handles daily startup

### 0.5 Developer Diagnostics

- [x] `scripts/health-check-localdev.sh` — checks GPU, Docker, ports, Ollama, ffmpeg
- [x] Clear error messages when GPU/Ollama/Mercure are unavailable
- [x] Add `--reload` to uvicorn in docker-compose dev mode for Python hot-reload

---

## Exit Criteria

- [x] Fresh clone with no AWS account works end-to-end (mock or live depending on GPU)
- [x] Role inference works locally via Ollama (no Bedrock required)
- [x] No CDN dependencies — fully offline-capable
- [x] `start-dev.sh` exits cleanly with actionable error if prerequisites are missing

---

## Notes

- Terraform infrastructure (VPC, ECS, ECR, ALB, DynamoDB) is code-complete and validated but deferred to M5
- 12 Terraform modules exist under `infra/terraform/` for future cloud deployment
- The local stack is: NeMo agent (Docker + GPU) + Mercure (Docker) + PHP app (Docker) + Ollama (host or Docker, CPU)
