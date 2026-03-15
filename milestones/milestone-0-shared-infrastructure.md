# Milestone 0 — Local Dev Gold Path

**Timeline:** ~2 hours
**Status:** In Progress
**Dependencies:** Docker, NVIDIA GPU (optional — mock mode available)

---

## Objective

A developer can clone the repo, run one command, and have a working local stack with no cloud credentials. GPU owners get live transcription; non-GPU developers get mock mode for UI/agent development.

> **Note:** Terraform and cloud deployment have moved to M5. This milestone is purely about the local development experience.

---

## Tasks

### 0.1 Local Defaults

- [ ] Default `ROLE_AGENT_MODEL_PROVIDER=ollama` in `.env.example` (not Bedrock)
- [ ] Default `NEMO_MODEL_PROVIDER=local` when GPU detected, `mock` when not
- [ ] `start-dev.sh` auto-detects GPU and sets `NEMO_MODEL_PROVIDER` accordingly
- [ ] Document a tested Ollama model for role inference with benchmarked latency + quality
- [x] `.env.example` documents all required variables with sensible local defaults
- [x] Port scheme documented: APP=48082, AGENT=48101, MERCURE=48137

### 0.2 Ollama Integration

- [ ] Add optional `ollama` service to `docker-compose.yml` (CPU-only, Docker profile)
- [ ] `start-dev.sh` auto-pulls the configured Ollama model on first run
- [x] `start-dev.sh` detects running Ollama and reuses it
- [x] Ollama host dockerized to `host.docker.internal` for container access

### 0.3 Zero-CDN Local Assets

- [ ] Bundle Tailwind CSS locally instead of `cdn.tailwindcss.com`
- [ ] No external script/stylesheet dependencies for the UI

### 0.4 Clean Clone Verification

- [ ] `cp .env.example .env && ./scripts/start-dev.sh` works from a fresh clone
- [ ] Mock mode (no GPU): UI loads, scenarios run, dev panel works
- [ ] GPU mode: full transcription pipeline operational
- [x] `scripts/setup-initial.sh` handles first-time setup
- [x] `scripts/start-dev.sh` handles daily startup

### 0.5 Developer Diagnostics

- [x] `scripts/health-check-localdev.sh` — checks GPU, Docker, ports, Ollama, ffmpeg
- [ ] Clear error messages when GPU/Ollama/Mercure are unavailable
- [ ] Add `--reload` to uvicorn in docker-compose dev mode for Python hot-reload

---

## Exit Criteria

- [ ] Fresh clone with no AWS account works end-to-end (mock or live depending on GPU)
- [ ] Role inference works locally via Ollama (no Bedrock required)
- [ ] No CDN dependencies — fully offline-capable
- [ ] `start-dev.sh` exits cleanly with actionable error if prerequisites are missing

---

## Notes

- Terraform infrastructure (VPC, ECS, ECR, ALB, DynamoDB) is code-complete and validated but deferred to M5
- 12 Terraform modules exist under `infra/terraform/` for future cloud deployment
- The local stack is: NeMo agent (Docker + GPU) + Mercure (Docker) + PHP app (Docker) + Ollama (host or Docker, CPU)
