# Ambient Scribe

Real-time medical transcription system that captures clinical conversations, performs speaker diarization and automatic speech recognition, and infers DOCTOR/PATIENT roles -- delivering labeled transcripts to the browser in real time.

## Architecture

```
+------------------+         WebSocket (audio)         +----------------------+
|                  | ----------------------------------> |                      |
|   Browser        |                                    |  NeMo Pipeline (GPU) |
|   (Audio Capture |         Mercure SSE (transcripts)  |  - Diarization       |
|    + Transcript  | <---------------------------------- |  - ASR (Parakeet)    |
|    Display)      |                                    |                      |
|                  |                                    +----------+-----------+
+------------------+                                               |
        ^                                                          | Diarized segments
        |                                                          v
        |  Mercure SSE                                  +----------------------+
        |                                               |                      |
        +---------------------------------------------- |  Transcription Agent |
                                                        |  (Strands / Bedrock) |
                           +-------------------+        |  - Role inference    |
                           |                   |        |  (DOCTOR / PATIENT)  |
                           |  Symfony App      | <------+----------------------+
                           |  (PHP 8.2)        |
                           |  - ScribeController        +----------------------+
                           |  - Orchestrator   | -----> |  Mercure Hub (SSE)   |
                           |                   |        +----------------------+
                           +-------------------+
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

The application will be available at `http://localhost:8080`.

## Tech Stack

| Layer              | Technology                                           |
|--------------------|------------------------------------------------------|
| ASR + Diarization  | NVIDIA NeMo multitalker Parakeet (GPU)               |
| Role Inference     | Strands SDK + AWS Bedrock                            |
| Backend            | PHP 8.2, Symfony 6.4                                 |
| Audio Pipeline     | WebSocket (browser -> Python)                        |
| Transcript Delivery| Mercure Hub (SSE)                                    |
| Frontend           | Twig, Tailwind CSS, Stimulus / AssetMapper           |
| Infrastructure     | Docker Compose, NVIDIA Container Toolkit             |

## Development

```bash
composer test          # PHPUnit
composer analyse       # PHPStan level 10
composer cs:check      # PHP-CS-Fixer dry-run
composer preflight     # All quality checks in sequence
```

## Milestones

See the [milestones/](milestones/) directory for project planning and progress tracking.
