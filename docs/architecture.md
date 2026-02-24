# Architecture

System design with WHY explanations. For code locations, see `docs/code-map.md`. For NeMo API details, see `docs/nemo-api-notes.md`.

## System Overview

```mermaid
graph TB
    subgraph Browser
        UI["Twig UI :8082"]
    end

    subgraph Docker["Docker Compose (or ECS Fargate task)"]
        App["Symfony :8080<br/>Session init, history, roles"]
        NemoAgent["FastAPI :8001<br/>WebSocket, NeMo GPU, Strands agent"]
        Mercure["Mercure :3701<br/>SSE hub"]
    end

    subgraph AWS
        Bedrock["Amazon Bedrock<br/>Claude Haiku 4.5"]
    end

    UI -->|"GET /scribe"| App
    UI -->|"WebSocket /ws/transcribe/{id}"| NemoAgent
    NemoAgent -->|"publish events"| Mercure
    NemoAgent -->|"role inference"| Bedrock
    Mercure -->|"SSE subscription"| UI
    App -->|"session history"| NemoAgent
```

**WHY three services?** PHP serves the initial page and manages session state — it's the right tool for server-rendered HTML with Twig. Python owns the GPU and real-time WebSocket — asyncio + NeMo is the only viable option for streaming inference. Mercure decouples publishing from subscribing so the browser gets updates without polling.

**WHY PHP does NOT touch the audio hot path?** Audio flows browser → Python via WebSocket directly. Adding PHP as a relay would double latency and add a serialization boundary for binary audio data that PHP can't process anyway (NeMo requires Python).

## Audio Pipeline Flow

```mermaid
sequenceDiagram
    participant B as Browser
    participant WS as FastAPI WebSocket
    participant S as TranscriptionSession
    participant FF as ffmpeg subprocess
    participant NP as NemoPipeline (GPU)
    participant M as Mercure Hub

    B->>WS: WebSocket connect /ws/transcribe/{session_id}
    loop Every ~1s audio chunk
        B->>WS: Binary WebM/Opus chunk
        WS->>S: accumulate WebM bytes
        S->>FF: convert accumulated WebM → 16kHz mono WAV
        FF-->>S: WAV file path
        S->>NP: process_chunk(wav_path) via ThreadPoolExecutor
        NP->>NP: Sortformer diarize → speaker segments
        NP->>NP: Parakeet ASR → transcribed text
        NP-->>S: list[Segment]
        S-->>WS: segments (with spk_0/spk_1 labels)
        WS->>M: publish to scribe/session/{id}/raw
        M-->>B: SSE event (segment data)
    end
    B->>WS: WebSocket close
    WS->>M: publish finalized event
```

**WHY WebM/Opus from browser + ffmpeg conversion?** Opus compression reduces WebSocket bandwidth ~10x vs raw PCM. ffmpeg conversion adds <50ms per chunk — negligible next to NeMo inference time. Alternative (AudioWorklet sending raw PCM) was rejected for bandwidth cost.

**WHY growing buffer (re-process full audio each chunk)?** NeMo's Sortformer diarizer needs full-session context for accurate speaker attribution. Benchmarks show linear RTF scaling (0.020→0.022 from 30s to 520s) — time is not the bottleneck. VRAM is: flush when approaching 15 GB.

**WHY ThreadPoolExecutor?** NeMo inference is synchronous GPU-bound work. Without `run_in_executor`, it blocks the FastAPI async event loop, freezing `/health` checks and all other WebSocket connections during inference.

## Role Inference Flow

```mermaid
sequenceDiagram
    participant WS as FastAPI WebSocket
    participant Q as Per-Session Queue
    participant SA as Strands Agent
    participant BK as Bedrock (Haiku 4.5)
    participant M as Mercure Hub

    WS->>Q: enqueue segments for role inference
    Q->>SA: process sequentially (per-session)
    SA->>BK: "Given these segments, who is DOCTOR and who is PATIENT?"
    BK-->>SA: role mapping (spk_0 → DOCTOR, spk_1 → PATIENT)
    SA->>SA: validate against existing mapping (detect flips)
    SA-->>M: publish to scribe/session/{id}/roles
    M-->>WS: (browser receives via SSE)
```

**WHY two Mercure topics per session?** `raw` delivers spk_0/spk_1 segments immediately (low latency hot path). `roles` delivers DOCTOR/PATIENT attribution asynchronously (Bedrock adds 1-3s latency). Separating them means the UI can show text immediately and retroactively apply role labels.

**WHY sequential per-session role inference?** The Strands agent maintains a role mapping (spk_0→DOCTOR). Fire-and-forget would create race conditions where two concurrent inferences could produce contradictory mappings.

## Session Lifecycle

```mermaid
stateDiagram-v2
    [*] --> PageLoad: GET /scribe
    PageLoad --> Connected: WebSocket open
    Connected --> Streaming: first audio chunk
    Streaming --> Streaming: accumulate + process chunks
    Streaming --> Disconnected: WebSocket close / error
    Disconnected --> Connected: reconnect
    Disconnected --> Finalized: session complete
    Finalized --> [*]

    state Streaming {
        [*] --> AccumulateWebM
        AccumulateWebM --> ConvertToWAV: chunk received
        ConvertToWAV --> NeMoInference: ffmpeg success
        ConvertToWAV --> AccumulateWebM: ffmpeg failure (skip)
        NeMoInference --> PublishSegments: segments found
        NeMoInference --> AccumulateWebM: no new segments
        PublishSegments --> EnqueueRoleInference
        EnqueueRoleInference --> AccumulateWebM
    }
```

**Session ID coupling:** PHP generates UUID (`Uuid::v4()`) → passes to Twig template → JS uses it in WebSocket URL `/ws/transcribe/{session_id}` → Python uses it in Mercure topics `scribe/session/{id}/raw` and `scribe/session/{id}/roles` → browser subscribes to those same topics. All four layers must agree on the same ID.

## Deployment Architecture

```mermaid
graph TB
    subgraph Internet
        Browser
    end

    subgraph AWS["AWS (ap-southeast-2)"]
        R53["Route 53"]
        WAF["WAF v2"]

        subgraph VPC["VPC (self-contained or shared)"]
            subgraph Public["Public Subnets"]
                ALB["ALB :443"]
            end

            subgraph Private["Private Subnets"]
                subgraph Task["ECS Fargate Task (sidecar pattern)"]
                    AppC["App :8080"]
                    AgentC["NeMo Agent :8000"]
                    MercureC["Mercure :3701"]
                end
            end
        end

        Bedrock["Bedrock"]
        DDB["DynamoDB"]
        ECR["ECR"]
    end

    Browser --> R53 --> WAF --> ALB
    ALB -->|"/* → :8080"| AppC
    ALB -->|"/.well-known/mercure → :3701"| MercureC
    AppC -->|"localhost:8000"| AgentC
    AgentC --> Bedrock
    AgentC --> DDB
```

**WHY sidecar pattern (all three in one ECS task)?** The services communicate over localhost with no network hop. Mercure publishes happen in-process. Scaling is per-GPU anyway (one NeMo instance per card), so there's no benefit to independent scaling.

For full Terraform module layout and deployment commands, see `docs/deployment.md` and `docs/terraform.md`.

## Data Contracts

### Mercure Event Types

| Topic | Event Type | Payload | Producer | Consumer |
|---|---|---|---|---|
| `scribe/session/{id}/raw` | `segment` | `{type, speaker, start_time, end_time, words}` | `api/server.py` | Browser SSE |
| `scribe/session/{id}/raw` | `finalized` | `{type, session_id}` | `api/server.py` | Browser SSE |
| `scribe/session/{id}/roles` | `role_update` | `{type, mapping: {spk_0: "DOCTOR", spk_1: "PATIENT"}}` | Strands agent | Browser SSE |

### WebSocket Protocol

| Direction | Format | Content |
|---|---|---|
| Browser → Server | Binary (ArrayBuffer) | WebM/Opus audio chunks (~1s intervals) |
| Server → Browser | (none — segments go via Mercure SSE) | — |

### HTTP Endpoints

| Method | Path | Service | Purpose |
|---|---|---|---|
| GET | `/scribe` | Symfony | Serve UI with session config |
| GET | `/scribe/{id}/history` | Symfony | Session transcript history |
| POST | `/scribe/{id}/roles/stream` | Symfony | SSE role inference stream |
| POST | `/transcribe/file` | FastAPI | File-based transcription |
| GET | `/session/{id}/history` | FastAPI | Session transcript history |
| GET | `/session/{id}/roles` | FastAPI | Current role mapping |
| WS | `/ws/transcribe/{session_id}` | FastAPI | Real-time audio streaming |
| GET | `/health` | FastAPI | Health check |
