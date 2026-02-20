# GEMINI.md - Ambient Scribe

## Project Overview
**Ambient Scribe** is a real-time medical transcription system that captures clinical conversations, performs speaker diarization and automatic speech recognition using NVIDIA NeMo, and infers DOCTOR/PATIENT roles via a Strands agent backed by AWS Bedrock. Transcripts are delivered to the browser in real time via Mercure SSE.

It is designed to demonstrate GPU-accelerated speech processing, multi-speaker diarization, and intelligent role inference using the Strands SDK.

### Core Technologies
- **Backend (App):** PHP 8.2+ with Symfony 6.4.
- **Backend (NeMo Pipeline):** Python 3.12+ with NeMo multitalker Parakeet for diarization + ASR (GPU-accelerated).
- **Backend (Agent):** Python 3.12+ with Strands SDK (`strands-agents`) for DOCTOR/PATIENT role inference via AWS Bedrock.
- **Real-time:** Mercure Hub (SSE) for transcript segment streaming.
- **Audio Input:** WebSocket pipeline (browser -> Python).
- **Frontend:** Twig templates with Tailwind CSS and Stimulus/AssetMapper.

---

## Architecture
The system follows a containerized microservices architecture with GPU support:

1.  **App (Symfony):** Handles the web UI and orchestrates transcript delivery.
    - `src/Controller/ScribeController.php`: Main entry point for scribe sessions.
    - `src/Service/ScribeStreamOrchestrator`: Manages real-time transcript streaming via Mercure.
2.  **NeMo Pipeline (Python/GPU):** Processes audio for diarization and speech recognition.
    - `nemo_pipeline.py`: Core pipeline that runs NeMo multitalker Parakeet for simultaneous diarization and ASR.
    - `nemo_session.py`: Session state management for concurrent transcription sessions.
3.  **Transcription Agent (Python/Strands):** Performs DOCTOR/PATIENT role inference.
    - `transcription_agent.py`: Strands agent that classifies diarized speaker segments into DOCTOR or PATIENT roles using AWS Bedrock.
4.  **Mercure:** Acts as the real-time hub, relaying transcript segments from the PHP app to the browser.

### Data Flow
1. Browser captures audio via the microphone and sends it over a WebSocket connection.
2. The NeMo pipeline receives audio frames, performs diarization (speaker separation) and ASR (speech-to-text) on GPU.
3. Diarized transcript segments are passed to the transcription agent for DOCTOR/PATIENT role inference via Strands/Bedrock.
4. The PHP app receives labeled transcript segments and publishes them to Mercure.
5. The browser receives transcript segments via `EventSource` (SSE) and renders them in real time, organized by speaker role.

---

## Building and Running

### Prerequisites
- Docker and Docker Compose.
- NVIDIA GPU with CUDA support.
- NVIDIA Container Toolkit installed on the host.
- 16GB+ RAM recommended.
- AWS credentials configured for Bedrock access.

### Commands
```bash
# Setup environment
cp .env.example .env

# Start the stack
docker compose up --build

# Run all quality checks (Preflight)
composer preflight

# Run tests
composer test

# Fix code style
composer cs:fix

# Static analysis
composer analyse
```

---

## Development Conventions

### Quality Gates
The project maintains a high quality bar, enforced by `./scripts/preflight-checks.sh`:
- **PHPStan:** Level 10 (Maximum strictness).
- **Complexity:** Cyclomatic complexity must be <= 20 per method.
- **Testing:** Minimum 80% line coverage for PHP code.
- **Style:** PSR-12/Symfony standards via PHP-CS-Fixer.
- **Python:** Syntax checks for pipeline and agent code.

### Audio Processing Pipeline
- **WebSocket Ingestion:** Audio frames arrive from the browser in real time and are buffered for NeMo processing.
- **NeMo Diarization + ASR:** The multitalker Parakeet model performs simultaneous speaker diarization and speech recognition on GPU.
- **Role Inference:** The Strands transcription agent classifies speakers as DOCTOR or PATIENT using contextual cues and Bedrock.
- **Session Management:** `nemo_session.py` tracks per-session state for concurrent transcription sessions.

### Streaming Pattern
Transcript segments are published to Mercure topics as they are produced by the pipeline. The browser subscribes via `EventSource` and renders segments in real time. The `ScribeController` manages session lifecycle and Mercure topic assignment.

---

## Key Files
- `src/Controller/ScribeController.php`: Main entry point for scribe sessions.
- `src/Service/ScribeStreamOrchestrator.php`: Logic for relaying transcript segments to Mercure.
- `strands_agents/nemo_pipeline.py`: NeMo multitalker Parakeet diarization + ASR pipeline.
- `strands_agents/nemo_session.py`: Session state management for concurrent transcription sessions.
- `strands_agents/transcription_agent.py`: Strands agent for DOCTOR/PATIENT role inference.
- `strands_agents/api/server.py`: FastAPI server implementation.
- `templates/scribe.html.twig`: Main UI and frontend audio capture / transcript display.
- `docker-compose.yml`: Defines the local development environment (with GPU passthrough).
