# Architecture Overview — Ambient Scribe

Real-time medical transcription system. Browser captures microphone audio, streams to Python via WebSocket, NeMo performs GPU-accelerated diarization + ASR, Strands agent assigns DOCTOR/PATIENT roles, results stream back via Mercure SSE.

## Major Components

| Component | Stack | Location | Purpose |
|-----------|-------|----------|---------|
| Web UI | Twig + inline JS | `templates/scribe/index.html.twig` | Audio capture (MediaRecorder), WebSocket streaming, Mercure SSE subscription |
| PHP backend | Symfony 6.4 | `src/` | Session page serving, role inference proxy, session history retrieval |
| Python agent | FastAPI | `strands_agents/` | WebSocket server, NeMo inference, role inference, Mercure publishing |
| NeMo pipeline | NeMo Parakeet | `strands_agents/nemo_pipeline.py` | GPU diarization + ASR (singleton, shared across sessions) |
| Role agent | Strands SDK | `strands_agents/agents/transcription_agent.py` | DOCTOR/PATIENT attribution via Bedrock or Ollama |
| SSE hub | Mercure | Docker service | Real-time event delivery to browser |
| Infrastructure | Terraform | `infra/terraform/` | AWS ECS Fargate, ALB, DynamoDB, WAF |

## Data Flow

```
Browser → GET /scribe (Symfony) → Twig renders page with session UUID
       → WS /ws/transcribe/{id} (FastAPI) → binary audio chunks
           → ThreadPoolExecutor → NemoPipeline.transcribe_file() (GPU)
           → Segment[] → publish to Mercure topic: scribe/session/{id}/raw
       → POST /session/{id}/roles/stream (FastAPI) → Strands agent → Bedrock/Ollama
           → RoleMappingState update → publish to Mercure topic: scribe/session/{id}/roles
       ← EventSource (Mercure SSE) ← browser receives raw segments + role updates
```

## Non-Obvious Constraints

1. **NeMo GPU exclusivity.** The NeMo model fills ~4-8GB VRAM. The Strands role agent MUST use Bedrock (AWS) or Ollama (CPU-only). Never co-locate a GPU LLM with NeMo on 16GB.
2. **ThreadPoolExecutor(max_workers=2).** Only 2 concurrent NeMo inference calls. Others queue. Hardcoded in `api/server.py`.
3. **All session state is in-memory.** Three independent stores (TranscriptionSession, SessionStore, RoleMappingState) keyed by session UUID. All lost on restart. DynamoDB created in Terraform but not yet integrated.
4. **Mercure JWT optional.** If not configured, publishes silently fail. No browser-visible error.
5. **PHP never touches audio.** PHP proxies role inference results only. Audio flows browser → Python directly.

## Deliberate Trade-Offs

- **In-memory over DynamoDB:** Faster iteration during M1-M2. DynamoDB integration planned for M3/M4.
- **Singleton NeMo over per-request:** GPU memory constraint. Recovery requires container restart.
- **Two Mercure topics over one:** Decouples hot path (NeMo) from cold path (role inference). Browser handles merge.
- **Sequential role inference per session:** Prevents race conditions on RoleMappingState. Throughput traded for correctness.

## Docker Services

| Service | Port | GPU | Purpose |
|---------|------|-----|---------|
| `nemo-agent` | 8000 (HTTP), 8001 (WS) | Yes (NVIDIA) | FastAPI + NeMo + Strands agent |
| `mercure` | 3701 | No | Caddy-based SSE hub |
| `app` | 8080/8082 | No | Symfony PHP application |

## TODOs

- [ ] DynamoDB integration for session persistence (currently in-memory only)
- [ ] Audio format detection (currently assumes 16kHz PCM; browser may send WebM/Opus)
- [ ] Coordinated session lifecycle across three state stores
- [ ] NeMo model health check and recovery mechanism
