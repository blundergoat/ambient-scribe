# Ambient Scribe

Real-time medical transcription system that captures clinical conversations, performs speaker diarization and automatic speech recognition, and infers DOCTOR/PATIENT roles -- delivering labeled transcripts to the browser in real time. After Stop, a second ASR pass corrects the transcript and an off-GPU agent drafts a citation-linked SOAP note for clinician review.

## Architecture

```
+-----------+  GET /scribe + helper proxies   +--------------+
|           | ------------------------------> |  Symfony App |
|           |                                 |  (PHP 8.3+)  |
|           |                                 +------+-------+
|           |                                        | proxied HTTP
|  Browser  |  WebSocket (16 kHz PCM audio)   +------v-------------------+
|           | ------------------------------> |  FastAPI NeMo agent      |
|           |                                 |  - Diarization (GPU)     |
|           |                                 |  - ASR Parakeet (GPU)    |
|           |                                 |  - Post-stop 2nd-pass ASR|
|           |                                 |  - Role inference        |
|           |                                 |    DOCTOR / PATIENT      |
|           |                                 |    (Strands, off-GPU)    |
|           |                                 |  - SOAP summary (off-GPU)|
|           |                                 +------+-------------------+
|           |                                        | publish raw/roles/summary
|           |  Mercure SSE (transcript events) +-----v--------+
|           | <------------------------------- |  Mercure Hub |
+-----------+                                  +--------------+
```

## Prerequisites

- **Docker** and **Docker Compose**
- **NVIDIA GPU** with CUDA support
- **NVIDIA Container Toolkit** installed on the host
- 16GB+ RAM recommended
- AWS credentials configured for Bedrock access

## Quick Start

```bash
cp .env.example .env
docker compose up --build
```

The application will be available at `http://localhost:48082`.

The copied `.env` selects AWS Bedrock for role inference and summaries
(`ROLE_AGENT_MODEL_PROVIDER=bedrock`), so Bedrock credentials must be present.
For a fully local CPU stack, set `ROLE_AGENT_MODEL_PROVIDER=ollama` and start
with the `ollama` Compose profile. `./scripts/start-dev.sh` is the guided
local-dev entry point: it enables the profile automatically and runs health
checks.

## Tech Stack

| Layer              | Technology                                           |
|--------------------|------------------------------------------------------|
| ASR + Diarization  | NVIDIA NeMo multitalker Parakeet + Sortformer (GPU)  |
| Post-visit ASR     | NeMo Parakeet second pass after Stop (GPU)           |
| Role Inference     | Strands SDK + AWS Bedrock (or CPU-only Ollama)       |
| SOAP Summary       | Strands SDK + AWS Bedrock (or CPU-only Ollama)       |
| Backend            | PHP 8.3+, Symfony 6.4                                |
| Audio Pipeline     | WebSocket (browser -> Python)                        |
| Transcript Delivery| Mercure Hub (SSE)                                    |
| Frontend           | Twig, Tailwind CSS, vanilla JS modules               |
| Infrastructure     | Docker Compose, NVIDIA Container Toolkit             |

## Development

```bash
composer test          # PHPUnit
composer analyse       # PHPStan level 10
composer cs:check      # PHP-CS-Fixer dry-run
composer preflight     # All quality checks in sequence
strands_agents/.venv/bin/pytest tests/python/ -q   # Python agent tests
```

## Documentation

- [README_summary.md](README_summary.md) - plain-English overview and model inventory
- [README_HOW_IT_WORKS.md](README_HOW_IT_WORKS.md) - detailed system walkthrough
- [README_STACK.md](README_STACK.md) - full model, service, and dependency inventory
- [README_CLINICAL_INTELLIGENCE.md](README_CLINICAL_INTELLIGENCE.md) - medical term normalisation and summary grounding
- [README_FIXTURES.md](README_FIXTURES.md) - demo/replay audio fixture setup
- [CHANGELOG.md](CHANGELOG.md) - feature-by-feature history

## Plans

See the [.goat-flow/plans/](.goat-flow/plans/) directory for project planning and progress tracking (contents are local workflow state; only the index is checked in).
