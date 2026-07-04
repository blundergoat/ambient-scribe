---
applyTo: 'strands_agents/**/*.py'
---

# Python Conventions - Ambient Scribe Agent

## Language & Framework

- Python 3.12+
- FastAPI for the HTTP + WebSocket layer (`api/server.py`)
- Pydantic v2 for request/response validation
- Strands SDK for role inference agent (AWS Bedrock)
- NVIDIA NeMo Parakeet for GPU-accelerated diarization + ASR

## Project Layout

```
strands_agents/
├── api/
│   ├── __init__.py
│   └── server.py              # FastAPI - /health, /transcribe/file, /ws/transcribe/{id}, /session/{id}/history
├── agents/
│   ├── __init__.py            # Agent package
│   └── transcription_agent.py # Strands role inference agent (Bedrock or Ollama)
├── tools/
│   └── assign_roles.py        # RoleMapping, RoleMappingState, per-session store
├── nemo_pipeline.py           # NeMo Parakeet wrapper - Segment, TranscriptionResult, NemoPipeline
├── nemo_session.py            # AudioBuffer, TranscriptionSession
├── session.py                 # In-memory SessionStore for transcript history
└── requirements.txt
```

## Style

- 4-space indentation
- Double quotes for docstrings, single or double for other strings (follow surrounding code)
- Type hints on all function signatures: `def create_role_inference_agent() -> Agent:`
- Use `| None` syntax over `Optional[]`: `segment_text: str | None = None`
- Module-level docstrings explaining what the file does and how it fits into the system
- Blank line after module docstrings, between top-level definitions, and before `return` in long functions

## Naming

- `snake_case` for functions, methods, variables, and modules
- `PascalCase` for classes and Pydantic models
- `UPPER_SNAKE_CASE` for module-level constants
- Descriptive names - `session_store` not `ss`, `transcription_result` not `tr`

## Architecture Patterns

- **NeMo singleton**: One `NemoPipeline` instance created at import time, shared across all WebSocket sessions
- **Per-session state**: Each WebSocket connection gets its own `TranscriptionSession` with an `AudioBuffer`
- **Role inference**: The Strands agent uses Bedrock (or Ollama CPU) to assign medical DOCTOR/PATIENT roles to diarized speaker labels.
- **Role mapping state**: `RoleMappingState` in `tools/assign_roles.py` maintains per-session speaker-to-role mappings
- **Mercure publishing**: Transcription results are published to Mercure SSE topics for real-time browser delivery

## API Contract

- `POST /transcribe/file` - Upload a WAV file, returns batch transcription result
- `WS /ws/transcribe/{session_id}` - Live audio streaming via WebSocket; binary audio frames in, transcription events out
- `GET /session/{id}/history` - Returns transcript history for a session
- `GET /health` - Returns `{ "status": "ok" }` (Docker healthcheck)

Changes to Pydantic models or WebSocket message formats MUST be coordinated with the Symfony frontend (Twig template WebSocket/SSE handling).

## Environment Variables

| Variable | Default | Used by |
|----------|---------|---------|
| `NEMO_MODEL_PROVIDER` | `local` | `nemo_pipeline.py` |
| `ROLE_AGENT_MODEL_PROVIDER` | `bedrock` | `agents/transcription_agent.py` |
| `NEMO_WEBSOCKET_URL` | `ws://nemo-agent:8001` | Symfony-injected browser WebSocket URL |
| `MERCURE_HUB_URL` | `http://mercure:3701/.well-known/mercure` | `server.py` |
| `MERCURE_JWT` | (empty) | `server.py` |
| `AWS_DEFAULT_REGION` | `ap-southeast-2` | Bedrock region |

## Validation

```bash
python3 -m py_compile strands_agents/api/server.py
python3 -m py_compile strands_agents/agents/__init__.py
python3 -m py_compile strands_agents/nemo_pipeline.py
python3 -m py_compile strands_agents/agents/transcription_agent.py
```
