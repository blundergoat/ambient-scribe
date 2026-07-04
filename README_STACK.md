# Ambient Scribe Stack

This file is the operational stack inventory for Ambient Scribe.
It documents the models, runtimes, services, ports, and feature toggles that a
developer or reviewer needs to understand before running or changing the app.
The focus is the user-visible transcription flow: record a consultation, see
speaker-labelled transcript cards, review a SOAP summary, and optionally see
assistive clinical hints.

Last checked: 2026-07-04 against the local repo.

## Short Version

Ambient Scribe is a Symfony + FastAPI + NeMo + Strands + Mercure medical scribe.
The browser captures microphone audio and streams 16 kHz PCM to FastAPI.
FastAPI owns the NeMo GPU pipeline, transcript storage, role inference,
summaries, replay, and Mercure publishing. Symfony serves the UI and injects
browser-facing config.

The core invariant is:

```text
NeMo owns the single NVIDIA GPU.
Role inference, summaries, clinical hints, and medical term correction do not use that GPU.
```

## Runtime Services

| Service | Default local URL | Main responsibility | Source |
| --- | --- | --- | --- |
| Symfony app | `http://localhost:48082` | Serves `/scribe`, injects session config, proxies history/role endpoints | `Dockerfile`, `src/`, `templates/` |
| FastAPI NeMo agent | `http://localhost:48101` | WebSocket audio ingest, NeMo inference, role queue, summaries, replay, Mercure publish | `strands_agents/`, `docker/nemo/Dockerfile` |
| Mercure hub | `http://localhost:48137/.well-known/mercure` | Browser SSE fan-out for transcript, role, summary, and hint events | `docker-compose.yml` |
| Ollama | `http://localhost:11434` | Optional CPU-only local LLM provider for role and summary agents | `docker-compose.yml` profile `local` |

## Model Inventory

| User-facing feature | Model or data source | Runtime | Default selector | Notes |
| --- | --- | --- | --- | --- |
| Speaker diarization | `nvidia/diar_streaming_sortformer_4spk-v2.1` via `SortformerEncLabelModel` | NeMo container, NVIDIA GPU | `NEMO_MODEL_PROVIDER=local` | Streaming Sortformer v2.1. The Dockerfile pre-downloads `diar_streaming_sortformer_4spk-v2.1.nemo`. |
| Automatic speech recognition | `nvidia/multitalker-parakeet-streaming-0.6b-v1` via `EncDecMultiTalkerRNNTBPEModel` | NeMo container, NVIDIA GPU | `NEMO_MODEL_PROVIDER=local` | Multitalker Parakeet 0.6B. The Dockerfile pre-downloads `multitalker-parakeet-streaming-0.6b-v1.nemo`. |
| Role inference | Strands Agent with `assign_roles` tool | AWS Bedrock or CPU-only Ollama | `ROLE_AGENT_MODEL_PROVIDER=ollama` in local env/Compose | Maps raw `spk_0`/`spk_1` labels to DOCTOR/PATIENT. Raw transcript still appears if this fails. |
| Local role/summary model | `qwen2.5:14b` through Ollama | CPU and system RAM | `ROLE_AGENT_OLLAMA_MODEL=qwen2.5:14b` | Recommended local model in `.env.example`; `qwen2.5:7b` is documented as faster but lower quality. |
| Bedrock role/summary model | `us.anthropic.claude-haiku-4-5-20251001-v1:0` in `.env.example` and agent code defaults | AWS Bedrock | `ROLE_AGENT_MODEL_PROVIDER=bedrock` plus `ROLE_AGENT_MODEL_ID` | Used when cloud credentials are provided. Compose has an older no-`.env` fallback of `us.anthropic.claude-sonnet-4-20250514-v1:0`; normal local setup copies `.env.example`. |
| Summary generation | Same Strands provider/model as role inference | AWS Bedrock or CPU-only Ollama | Same `ROLE_AGENT_*` env vars | Generates JSON SOAP-style sections and key points after the visit. Max tokens are 2048 in the summary agent. |
| Clinical summary grounding | Project-authored `strands_agents/data/clinical_knowledge.json` | CPU keyword retrieval | Always available to summary prompt when snippets match | Not an LLM or external RAG service. It adds short documentation reminders to the summary prompt. |
| Clinical hints sidebar | Rule-based hints in `strands_agents/clinical_hints.py` | CPU | `CLINICAL_HINTS_ENABLED=1` | Publishes non-blocking review suggestions to `scribe/session/{id}/hints`. |
| Medical term correction | `strands_agents/data/medical_lexicon.txt` | CPU post-ASR text normaliser | `MEDICAL_BOOST_ENABLED=0` by default | Exact word-boundary replacement only. NeMo decode-time phrase boosting remains GPU-pending. |
| Role fallback | Keyword heuristics in `strands_agents/api/role_heuristics.py` | CPU | Automatic after agent failure | Gives low-confidence labels when the configured Strands model is unavailable. |

## NeMo Speech Pipeline

The NeMo lane is loaded once when FastAPI starts. `NemoPipeline` loads both
speech models, moves them to CUDA when available, and keeps public transcription
methods synchronous so FastAPI can run them in a GPU worker pool.

Important files:

- `strands_agents/nemo_pipeline.py` loads `SortformerEncLabelModel` and
  `EncDecMultiTalkerRNNTBPEModel`.
- `strands_agents/nemo_session.py` buffers live audio and hands complete windows
  to the pipeline.
- `docker/nemo/Dockerfile` uses `nvcr.io/nvidia/nemo:26.02` and installs
  `nemo_toolkit[asr]==2.7.3`.
- `docker-compose.yml` reserves one NVIDIA GPU for `nemo-agent`.

Important env vars:

| Env var | Default | Meaning |
| --- | --- | --- |
| `NEMO_MODEL_PROVIDER` | `local` | `local` loads real NeMo models; `mock` skips model loading in tests. |
| `NEMO_STREAM_INPUT_FORMAT` | `pcm` | Browser/server audio contract. The current browser path sends 16 kHz 16-bit PCM. |
| `NEMO_MAX_WORKERS` | `2` | Thread pool size for GPU-bound work. Increasing this changes GPU concurrency. |
| `NEMO_BUFFER_MAX_DURATION` | `900` | Safety cap for live audio buffer duration in seconds. |

The Dockerfile VRAM budget reserves the GPU for Sortformer and Parakeet and
explicitly warns not to co-locate a GPU LLM in the same runtime.

## Strands Agents

There are two Strands agents:

| Agent | File | User-facing job | Tools | Max tokens |
| --- | --- | --- | --- | --- |
| Role inference | `strands_agents/agents/transcription_agent.py` | Decide which raw speaker is DOCTOR/PATIENT | `assign_roles` | 1024 |
| Summary | `strands_agents/agents/summary_agent.py` | Produce the post-visit SOAP note JSON | none | 2048 |

Both agents use the same provider selector:

```text
ROLE_AGENT_MODEL_PROVIDER=ollama|bedrock
ROLE_AGENT_OLLAMA_MODEL=qwen2.5:14b
ROLE_AGENT_MODEL_ID=us.anthropic.claude-haiku-4-5-20251001-v1:0
AWS_DEFAULT_REGION=ap-southeast-2
OLLAMA_HOST=http://host.docker.internal:11434
```

Local development defaults to Ollama so role inference can run without AWS.
The `ollama` Compose service is optional and CPU-only. If a host-installed
Ollama is used, the `nemo-agent` container reaches it through
`http://host.docker.internal:11434`.

Bedrock is opt-in. Set `ROLE_AGENT_MODEL_PROVIDER=bedrock`, provide
`ROLE_AGENT_MODEL_ID`, and inject AWS credentials or an AWS profile outside the
repo. Do not commit credentials into `.env`.

## Browser And Event Flow

The browser path is:

```text
GET /scribe
  -> Twig injects session id, WebSocket URL, Mercure URL, and topics
  -> browser captures microphone audio as 16 kHz PCM
  -> ws://localhost:48101/ws/transcribe/{session_id}
  -> NeMo publishes raw transcript segments
  -> role queue publishes DOCTOR/PATIENT updates
  -> summary endpoint publishes summary and optional hints
  -> Mercure streams updates back to the browser
```

Mercure topics:

| Topic | Payload purpose |
| --- | --- |
| `scribe/session/{id}/raw` | Immediate raw speaker transcript segments. |
| `scribe/session/{id}/roles` | Speaker-to-role mapping updates and relabelled segments. |
| `scribe/session/{id}/summary` | Completed summary payloads. |
| `scribe/session/{id}/hints` | Optional clinical-review suggestions. |

Browser source files:

- `public/js/scribe.js` owns shared UI state, roles, status, and safe DOM helpers.
- `public/js/scribe-streaming.js` owns Mercure streams, reconnects, and PCM capture.
- `public/js/scribe-transcript.js` owns transcript card rendering and relabelling.
- `public/js/scribe-output.js` owns replay, summary rendering, downloads, and hints.
- `public/js/scribe-dev.js` owns the local dev inspector panel.
- `public/js/scribe-fixtures.js` owns the dev-only Demo Audio picker for generated WAV replay.

## Clinical Assistance Lane

Clinical assistance is intentionally lightweight in this version.

- `CLINICAL_HINTS_ENABLED=1` enables the hints lane.
- `strands_agents/data/clinical_knowledge.json` is a project-authored PoC corpus,
  not clinical guideline authority.
- `retrieve_clinical_context()` uses simple keyword scoring to add short notes
  to the summary prompt.
- `generate_clinical_hints()` emits small review prompts such as missing
  objective documentation, follow-up reminders, or medication-review prompts.
- Hints never diagnose, prescribe, mutate the transcript, or block the summary.

## Medical Phrase Normalisation

`MEDICAL_BOOST_ENABLED=1` enables a conservative post-ASR correction fallback.
It loads `strands_agents/data/medical_lexicon.txt` and replaces exact
word-boundary variants with canonical clinical terms before text reaches the UI,
summary, or download.

This is not NeMo decode-time phrase boosting. The NeMo 2.7.x multitalker
decode-time API still needs a GPU-container proof before this fallback should be
replaced.

## Storage And Session State

| Setting | Default | Meaning |
| --- | --- | --- |
| `SESSION_STORAGE` | `memory` | In-process transcript storage for local development. |
| `SESSION_DB_PATH` | `/data/sessions.db` | SQLite path when `SESSION_STORAGE=sqlite`. |
| `MAX_SESSIONS` | `100` | Session-store safety limit. |
| `SESSION_TTL_SECONDS` | `7200` | Transcript retention window for cleanup. |

Role state is held by `strands_agents/tools/assign_roles.py` and cleaned with
session lifecycle teardown or orphan cleanup. Transcript durability comes from
the selected session storage backend, not from Mercure.

## Core Dependency Floors

| Area | Dependency floor or pin | Source |
| --- | --- | --- |
| PHP | `>=8.3 <8.5` | `composer.json` |
| Symfony | `^6.4` | `composer.json` |
| Strands PHP client | `^1.4` | `composer.json` |
| NeMo base image | `nvcr.io/nvidia/nemo:26.02` | `docker/nemo/Dockerfile` |
| NeMo toolkit | `nemo_toolkit[asr]==2.7.3` | `docker/nemo/Dockerfile` |
| Strands Agents Python | `strands-agents[ollama]>=1.45.0` | `strands_agents/requirements.txt` |
| FastAPI | `>=0.139.0` | `strands_agents/requirements.txt` |
| Uvicorn | `uvicorn[standard]>=0.49.0` | `strands_agents/requirements.txt` |
| Pydantic | `>=2.13.4` | `strands_agents/requirements.txt` |
| HTTPX | `>=0.28.1` | `strands_agents/requirements.txt` |
| websockets | `>=16.0` | `strands_agents/requirements.txt` |
| sse-starlette | `>=3.4.5` | `strands_agents/requirements.txt` |
| numpy | `>=1.26.0` | `strands_agents/requirements.txt` |
| soundfile | `>=0.14.0` | `strands_agents/requirements.txt` |
| Hugging Face Hub | `>=0.36.2,<1.0` | `strands_agents/requirements.txt` |
| GOAT Flow | `@blundergoat/goat-flow ^1.13.0` | `package.json` |
| gruff-ts | `@blundergoat/gruff-ts ^0.4.0` | `package.json` |
| Playwright | `@playwright/test ^1.58.2` | `package.json` |

## What Is Not In This Stack

- No GPU LLM runs inside the NeMo container.
- No browser audio is processed by PHP.
- No external vector database is used for clinical hints.
- No real guideline corpus is bundled; the clinical KB is project-authored PoC data.
- No NeMo decode-time phrase boosting is proven yet.
- No production login/auth layer is documented in the current local `/scribe` path.

## Verification Commands

Use these after changing model IDs, provider defaults, audio formats, Mercure
topics, or stack documentation:

```bash
docker compose config >/dev/null
strands_agents/.venv/bin/pytest tests/python/ -q
composer test
composer analyse
composer cs:check
node_modules/.bin/gruff-ts analyse .
strands_agents/.venv/bin/gruff-py analyse .
vendor/bin/gruff-php analyse
./scripts/preflight-checks.sh
```

Use these for GPU-specific changes:

```bash
./scripts/gpu-check.sh
docker compose up --build nemo-agent
```

Use these for learning-loop or instruction/doc routing changes:

```bash
node_modules/.bin/goat-flow index
node_modules/.bin/goat-flow stats --check
./scripts/context-validate.sh
```
