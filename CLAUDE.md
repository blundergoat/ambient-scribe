# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Ambient Scribe is a real-time ambient medical scribe that captures doctor-patient consultations via microphone, transcribes speech with speaker diarization (NeMo multitalker Parakeet), and attributes speakers as DOCTOR or PATIENT using a Strands AI agent. Built with Python/FastAPI (NeMo GPU inference + WebSocket), PHP/Symfony (UI + session management), and Mercure (real-time SSE streaming).

Scaffolded from the-summit-chatroom (a multi-persona chat demo), adapted into a medical transcription pipeline.

## Commands

```bash
# Run all quality checks (tests, lint, analysis, coverage)
composer preflight

# PHP Tests
composer test                          # Run PHPUnit tests
composer test:coverage                 # Tests with HTML + clover coverage

# Python Tests
cd strands_agents && pip install -r ../tests/python/requirements-dev.txt
pytest ../tests/python/                # Run Python tests

# Static analysis
composer analyse                       # PHPStan Level 10
composer analyse:complexity            # Cyclomatic complexity (max 20)
composer analyse:messdetector          # PHPMD

# Code style
composer cs:check                      # Dry-run PHP-CS-Fixer
composer cs:fix                        # Auto-fix code style (PSR-12)

# Docker (full stack — requires NVIDIA GPU)
docker compose up --build              # Build and start all 3 services

# NeMo test script (inside NeMo container)
python scripts/nemo_test.py tests/fixtures/audio/test.wav

# Dependencies
./scripts/dependencies-install.sh      # Install from lock files
./scripts/dependencies-update.sh       # Update to latest within constraints
```

## Architecture

### Data Flow

```
Browser (Twig UI :8082)
  → GET /scribe (Symfony) → Twig template with session config
  → WebSocket /ws/transcribe/{session_id} (Python FastAPI :8001)
    → NeMo multitalker Parakeet (GPU inference, in ThreadPoolExecutor)
    → Publish raw segments to Mercure (scribe/session/{id}/raw)
  → Strands agent (async, sequential per-session queue)
    → Publish role updates to Mercure (scribe/session/{id}/roles)
  ← Browser receives segments + role updates via Mercure SSE
```

### Key Design Decisions

- **PHP does NOT touch the live audio hot path.** Symfony serves the initial page and provides session history. Live audio flows directly from browser to Python via WebSocket.
- **NeMo owns the GPU exclusively.** The Strands role inference agent uses Bedrock or CPU-only Ollama. Do not co-locate a GPU LLM with NeMo on the same 16GB card.
- **Two Mercure topics per session:** `raw` (immediate spk_0/spk_1 segments) and `roles` (async DOCTOR/PATIENT attribution). This keeps the hot path latency-free from agent inference.
- **NeMo inference runs in ThreadPoolExecutor.** GPU-bound work is synchronous — without `run_in_executor`, it blocks the async event loop and freezes all connections.
- **Role inference is sequenced per session** (not fire-and-forget) to prevent race conditions on shared mapping state.

### Key Components

- **PHP layer** (`src/`): PSR-4 namespace `App\`, Symfony 6.4. `ScribeController` serves the UI and session history.
- **Python agent** (`strands_agents/`): FastAPI with WebSocket support. `nemo_pipeline.py` wraps NeMo models (singleton, shared). `nemo_session.py` manages per-WebSocket state. `transcription_agent.py` handles role inference via Strands SDK. `api/server.py` is the HTTP + WebSocket layer.
- **Frontend** (`templates/scribe/index.html.twig`): Single Twig template with inline JS for MediaRecorder audio capture, WebSocket streaming, and Mercure SSE subscription.
- **Infrastructure** (`infra/terraform/`): AWS deployment — consumes shared VPC/subnets from SSM Parameter Store (`blundergoat-infra`).

### Docker Services (docker-compose.yml)

3 services: `nemo-agent` (GPU, FastAPI + NeMo) → `mercure` (SSE hub) → `app` (Symfony). The nemo-agent requires NVIDIA Container Toolkit.

## Quality Standards

- PHPStan Level 10 (strictest)
- PHP-CS-Fixer with PSR-12
- PHPMD for design/codesize/unusedcode
- Cyclomatic complexity max 20
- Coverage minimum 80% (enforced by `preflight:coverage`)
- Python tests via pytest (`tests/python/`)

## Environment

Copy `.env.example` to `.env`. Key variables:
- `NEMO_MODEL_PROVIDER`: NeMo model source (default: `local`)
- `ROLE_AGENT_MODEL_PROVIDER`: `bedrock` (recommended) or `ollama` (CPU-only)
- `NEMO_WEBSOCKET_URL`: WebSocket URL for browser → Python connection
- `MERCURE_URL` / `MERCURE_PUBLIC_URL`: Mercure hub endpoints
- `MERCURE_JWT_SECRET`: JWT signing key for Mercure (must be ≥ 32 chars)

## Milestone Status

See `milestones/` directory for detailed task breakdowns:
- **M0**: Shared infrastructure (Terraform, VPC, ECR)
- **M1**: NeMo validation + project scaffold (current)
- **M2**: Audio pipeline end-to-end
- **M3**: Role attribution + agent intelligence
- **M4**: Polish, blog, open source

NeMo API discovery notes: `docs/nemo-api-notes.md`

## Workflow Rules

### Debugging: read first, fix second

When debugging issues, ALWAYS read the actual code and configuration files before proposing a fix. Trace the actual code path through ScribeController → Python WebSocket → NeMo pipeline → Mercure first.

### Full-stack awareness

This is a full-stack platform with a PHP/Symfony backend, Python/FastAPI agent layer (with GPU inference), Twig frontend, Docker Compose orchestration, and Mercure real-time streaming. When making changes, consider the impact across all layers.

### Feature completeness checklist

After implementing any feature, verify completeness by checking:
1. PHP service wiring (`config/services.yaml`, `config/packages/strands.yaml`)
2. Python agent endpoint contracts (Pydantic models in `api/server.py`)
3. Symfony route registration (controller attributes)
4. Twig template updates (`templates/scribe/index.html.twig`)
5. Docker Compose environment variables if new config is needed
6. PHPUnit tests covering the new PHP code path
7. Python tests covering the new Python code path
8. PHPStan passes at Level 10

### Preflight before done

Always run `composer preflight` BEFORE reporting that a task is complete. Fix any failures before declaring success.

### Stop-the-line

If tests, builds, or analysis break during a task — stop adding features immediately. Fix before continuing.

### Control scope

If a change reveals deeper issues, fix only what is necessary for correctness. Log follow-ups as TODOs.

### Git hygiene

Keep commits atomic and describable — one logical change per commit.
