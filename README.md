# Ambient Scribe

Real-time medical transcription system that captures clinical conversations, performs speaker diarization and automatic speech recognition, and infers DOCTOR/PATIENT roles -- delivering labeled transcripts to the browser in real time.

## Architecture

```
+-----------+  GET /scribe, summary/history   +--------------+
|           | ------------------------------> |  Symfony App |
|           |                                 |  (PHP 8.3+)  |
|           |                                 +------+-------+
|           |                                        | proxied HTTP
|  Browser  |  WebSocket (16 kHz PCM audio)   +------v-------------------+
|           | ------------------------------> |  FastAPI NeMo agent      |
|           |                                 |  - Diarization (GPU)     |
|           |                                 |  - ASR Parakeet (GPU)    |
|           |                                 |  - Role inference        |
|           |                                 |    DOCTOR / PATIENT      |
|           |                                 |    (Strands, off-GPU)    |
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

## Tech Stack

| Layer              | Technology                                           |
|--------------------|------------------------------------------------------|
| ASR + Diarization  | NVIDIA NeMo multitalker Parakeet (GPU)               |
| Role Inference     | Strands SDK + AWS Bedrock (or CPU-only Ollama)       |
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
```

## Plans

See the [.goat-flow/plans/](.goat-flow/plans/) directory for project planning and progress tracking.
