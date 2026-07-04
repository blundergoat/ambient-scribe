# Domain Reference — Ambient Scribe

Technical reference for project architecture, components, and conventions.
Migrated from CLAUDE.md and the old root AGENTS content to separate domain knowledge from workflow instructions.

## Project Overview

Ambient Scribe is a real-time medical transcription app that captures consultation audio, transcribes speech with speaker diarization (NeMo multitalker Parakeet), and attributes speakers as DOCTOR/PATIENT using a Strands AI agent. Built with Python/FastAPI (NeMo GPU inference + WebSocket), PHP/Symfony (UI + session management), and Mercure (real-time SSE streaming).

## Project Structure

- `src/Controller/`: Symfony routes and HTTP entrypoints.
- `src/Service/`: PHP orchestration and client wrappers.
- `templates/scribe/index.html.twig`: main UI plus inline browser streaming logic.
- `assets/controllers/`: frontend helper controllers when Stimulus wiring is needed.
- `public/index.php`: Symfony web entrypoint.
- `strands_agents/`: FastAPI agent, NeMo pipeline, session state, and role tools.
- `tests/Unit/`: PHPUnit tests mirroring PHP namespaces.
- `tests/python/`: Python tests for the agent stack.
- `scripts/`: localdev, health, install, deployment, and quality-gate scripts.

## Architecture

### Data Flow

```text
Browser (Twig UI :48082)
  -> GET /scribe (Symfony) -> Twig template with session config
  -> WebSocket /ws/transcribe/{session_id} (Python FastAPI :48101)
    -> NeMo multitalker Parakeet (GPU inference in ThreadPoolExecutor)
    -> Publish raw segments to Mercure (scribe/session/{id}/raw)
  -> Strands agent (async, sequential per-session queue)
    -> Publish role updates to Mercure (scribe/session/{id}/roles)
    -> Publish summaries to Mercure (scribe/session/{id}/summary)
  <- Browser receives segments, role updates, and summaries via Mercure SSE
```

### Key Design Decisions

- PHP does not touch the live audio hot path. Symfony serves the page and history/snapshot APIs only.
- NeMo owns the GPU exclusively. The Strands role agent must stay on Bedrock or CPU-only Ollama.
- Three Mercure topics per session keep hot-path transcription, slower role inference, and summary rendering independent.
- NeMo inference runs in `ThreadPoolExecutor(max_workers=2)` to avoid blocking the async event loop.
- Role inference is sequenced per session to avoid mapping races.

### Key Components

- PHP layer (`src/`): PSR-4 namespace `App\`, Symfony 6.4. `ScribeController` serves the UI plus history/role routes.
- Python agent (`strands_agents/`): FastAPI with WebSocket support. `nemo_pipeline.py` wraps NeMo models (singleton, shared). `nemo_session.py` manages per-WebSocket state. `agents/transcription_agent.py` handles role inference via Strands SDK. `api/server.py` is the HTTP + WebSocket layer.
- Frontend (`templates/scribe/index.html.twig`, `public/js/scribe.js`): Twig injects session config; browser JS handles PCM audio capture, WebSocket streaming, and Mercure SSE subscriptions.
- Infrastructure (`infra/terraform/`): ECS/Fargate task with app, agent, and Mercure sidecars plus DynamoDB scaffolding.

## Quality Standards

- PHPStan Level 10
- PHP-CS-Fixer with PSR-12
- PHPMD for design/codesize/unusedcode
- Cyclomatic complexity max 20
- Coverage minimum 80%
- Python tests via pytest (`tests/python/`)
- PHP uses `declare(strict_types=1);`, 4-space indentation, short arrays, ordered imports, and single quotes

## Environment

Copy `.env.example` to `.env`. Key variables:
- `NEMO_MODEL_PROVIDER`: NeMo model source (default `local`)
- `NEMO_STREAM_INPUT_FORMAT`: `pcm` for the current browser path; must match `nemo_session.py`
- `ROLE_AGENT_MODEL_PROVIDER`: `bedrock` (recommended) or `ollama` (CPU-only)
- `NEMO_WEBSOCKET_URL`: browser -> Python WebSocket URL
- `MERCURE_URL` / `MERCURE_PUBLIC_URL`: Mercure hub endpoints
- `MERCURE_JWT_SECRET`: JWT signing key for Mercure
- Local host defaults use uncommon dev ports: app `48082`, agent `48101`, Mercure `48137`
- For local stack bring-up, prefer `./scripts/start-dev.sh` and the health-check scripts over raw `docker compose`

## Plan Status

See `.goat-flow/plans/` for detailed task breakdowns:
- M0: shared infrastructure
- M1: NeMo validation + scaffold (complete)
- M2: audio pipeline end-to-end (in progress; code exists, final live verification pending)
- M3: role attribution + agent intelligence (in progress; live queue + Mercure role publishing implemented)
- M4: polish, blog, open source
