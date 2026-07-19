# Ambient Scribe Stack

This file is the operational stack inventory for Ambient Scribe.
It documents the models, runtimes, services, ports, and feature toggles that a
developer or reviewer needs to understand before running or changing the app.
The focus is the user-visible transcription flow: record a consultation, see
speaker-labelled transcript cards, and review a SOAP summary.

Last checked: 2026-07-20 against the local repo.

## Short Version

Ambient Scribe is a Symfony + FastAPI + NeMo + Strands + Mercure medical scribe.
The browser captures microphone audio and streams 16 kHz PCM to FastAPI.
FastAPI owns the NeMo GPU pipeline, transcript storage, role inference,
summaries, replay, and Mercure publishing. Symfony serves the UI and injects
browser-facing config.

The core invariant is:

```text
NeMo owns the single NVIDIA GPU.
Role inference, summaries, clinical context retrieval, and medical term correction do not use that GPU.
```

## Runtime Services

| Service | Default local URL | Main responsibility | Source |
| --- | --- | --- | --- |
| Symfony app | `http://localhost:48082` | Serves `/scribe`, injects session config, proxies history/role endpoints | `Dockerfile`, `src/`, `templates/` |
| FastAPI NeMo agent | `http://localhost:48101` | WebSocket audio ingest, NeMo inference, role queue, summaries, replay, Mercure publish | `strands_agents/`, `docker/nemo/Dockerfile` |
| Mercure hub | `http://localhost:48137/.well-known/mercure` | Browser SSE fan-out for transcript, role, and summary events | `docker-compose.yml` |
| Ollama | in-network `http://ollama:11434` | CPU-only local LLM provider for role and summary agents when `ROLE_AGENT_MODEL_PROVIDER=ollama`; starts through the `ollama` Compose profile, no host port published | `docker-compose.yml` service `ollama` |

## Model Inventory

| User-facing feature | Model or data source | Runtime | Default selector | Notes |
| --- | --- | --- | --- | --- |
| Speaker diarization | `nvidia/diar_streaming_sortformer_4spk-v2.1` via `SortformerEncLabelModel` | NeMo container, NVIDIA GPU | `NEMO_MODEL_PROVIDER=local` | Streaming Sortformer v2.1. The Dockerfile pre-downloads `diar_streaming_sortformer_4spk-v2.1.nemo`. |
| Automatic speech recognition | `nvidia/multitalker-parakeet-streaming-0.6b-v1` via `EncDecMultiTalkerRNNTBPEModel` | NeMo container, NVIDIA GPU | `NEMO_MODEL_PROVIDER=local` | Multitalker Parakeet 0.6B. The Dockerfile pre-downloads `multitalker-parakeet-streaming-0.6b-v1.nemo`. |
| Post-visit transcript correction | `nvidia/parakeet-unified-en-0.6b` second-pass ASR via `strands_agents/post_visit_correction.py`, checkpoint pinned by revision and SHA-256 | NeMo container, NVIDIA GPU (runs in the NeMo executor after Stop) | `POST_VISIT_ASR_MODEL` code default | Re-transcribes retained session audio into corrected rows stored beside the live transcript. Unified became loadable via an empty validation-loader config and is the accepted default after manual review. |
| Role inference | Strands Agent with `assign_roles` tool | AWS Bedrock or CPU-only Ollama | `.env.example` and agent code default to `ROLE_AGENT_MODEL_PROVIDER=bedrock`; bare Compose falls back to `ollama` only when no env overrides it | Maps raw `spk_0`/`spk_1` labels to DOCTOR/PATIENT. Raw transcript still appears if this fails. |
| Local role/summary model | `qwen3.5:9b` through Ollama | CPU and system RAM; zero VRAM | `ROLE_AGENT_OLLAMA_MODEL` and `SUMMARY_AGENT_OLLAMA_MODEL` | Exact local tag for both user flows. Compose and `scripts/install-ollama.sh` hide NVIDIA, ROCm, and Vulkan GPUs so NeMo keeps the only GPU. |
| Bedrock role model | `au.anthropic.claude-haiku-4-5-20251001-v1:0` | AWS Bedrock in `ap-southeast-2` | `ROLE_AGENT_MODEL_PROVIDER=bedrock` plus `ROLE_AGENT_MODEL_ID` | `.env.example`, Compose, Python, and production Terraform agree on AU Haiku 4.5. |
| Bedrock summary model | `au.anthropic.claude-haiku-4-5-20251001-v1:0` | AWS Bedrock in `ap-southeast-2` | `SUMMARY_AGENT_MODEL_ID`; provider normally inherits the role provider | Haiku 4.5 is retained deliberately for lower note-generation cost. Sonnet is deferred rather than silently selected by a fallback. |
| Summary generation | Independent Strands summary agent | AWS Bedrock or CPU-only Ollama | `SUMMARY_AGENT_*`, with provider/model inheritance from effective `ROLE_AGENT_*` values | Generates JSON SOAP-style sections and key points after the visit. Max tokens default to 8192 (`SUMMARY_AGENT_MAX_TOKENS`). |
| Clinical summary grounding | Governed `strands_agents/data/clinical_knowledge.json` (`ambient-scribe-clinical-knowledge/v1`) | CPU keyword retrieval | Off by default; only an internal caller that explicitly enables context can add matched cards | Not an LLM or external RAG service. Reviewed documentation-checklist reminders, validated fail-closed before any prompt use. |
| Medical term correction | `strands_agents/data/medical_lexicon.txt` | CPU post-ASR text normaliser | On by default; `MEDICAL_BOOST_ENABLED=0` opts out | Exact word-boundary replacement only. NeMo decode-time phrase boosting remains GPU-pending. |
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
- `strands_agents/nemo_streaming_engine.py` is the M22 session-long streaming
  engine (`NEMO_SESSION_ENGINE=streaming`): one Sortformer speaker cache owns
  speaker identity for the whole visit instead of per-window re-diarization.
- `strands_agents/post_visit_correction.py` runs the post-stop second-pass ASR
  (default `nvidia/parakeet-unified-en-0.6b`, pinned by revision and SHA-256,
  cached under `POST_VISIT_MODEL_CACHE_DIR`) over retained session audio in
  the same GPU executor and writes corrected rows to the corrected-transcript
  storage lane. Long audio is corrected in ordered ~180-second chunks with one
  transient-failure retry.
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
| `NEMO_SPEAKER_CAP` | `2` | Maximum visible speaker IDs before window-local extras merge back into stable IDs; `0` allows all detected speakers. |
| `NEMO_SESSION_ENGINE` | `streaming` (`.env.example` and `start-dev.sh`); `windowed` is the Compose fallback for env-less checkouts and CI | Live transcription engine. `windowed` re-diarizes each window and stitches speaker IDs; `streaming` keeps one session-long Sortformer speaker cache (M22). |
| `NEMO_STREAMING_MAX_TRANSCRIPT_HOLD_SECONDS` | `0` | Optional bound on how long stable rows may wait behind an old revisable word; `0` keeps strict spoken-order release. |
| `NEMO_STREAMING_SLOT_EVIDENCE` | `0` | Operator-only QA logging of speaker-slot evidence (IDs, counts, timings - never words). |
| `NEMO_STREAMING_CROSSTALK_GUARD` | `0` | Rejected QA guard; keep off because guarded corpus attribution regressed. |
| `POST_VISIT_ASR_MODEL` | `nvidia/parakeet-unified-en-0.6b` | Pinned post-stop second-pass checkpoint. |

The Dockerfile VRAM budget reserves the GPU for Sortformer and Parakeet and
explicitly warns not to co-locate a GPU LLM in the same runtime.

## Strands Agents

There are two Strands agents:

| Agent | File | User-facing job | Tools | Max tokens |
| --- | --- | --- | --- | --- |
| Role inference | `strands_agents/agents/transcription_agent.py` | Decide which raw speaker is DOCTOR/PATIENT | `assign_roles` | 2048 (`ROLE_AGENT_MAX_TOKENS`) |
| Summary | `strands_agents/agents/summary_agent.py` | Produce the post-visit SOAP note JSON | none | 8192 (`SUMMARY_AGENT_MAX_TOKENS`) |

Role and summary settings resolve independently, while the summary normally inherits the role
provider and model:

```text
ROLE_AGENT_MODEL_PROVIDER=ollama|bedrock
ROLE_AGENT_OLLAMA_MODEL=qwen3.5:9b
ROLE_AGENT_MODEL_ID=au.anthropic.claude-haiku-4-5-20251001-v1:0
SUMMARY_AGENT_MODEL_PROVIDER=<optional; inherits role provider>
SUMMARY_AGENT_OLLAMA_MODEL=qwen3.5:9b
SUMMARY_AGENT_MODEL_ID=au.anthropic.claude-haiku-4-5-20251001-v1:0
AWS_DEFAULT_REGION=ap-southeast-2
OLLAMA_HOST=http://ollama:11434
```

The checked-in `.env.example` defaults to Bedrock, matching the Python agent's
no-env fallback. Offline local development uses Ollama by setting
`ROLE_AGENT_MODEL_PROVIDER=ollama`; `scripts/start-dev.sh` then enables the
`ollama` Compose profile, and `nemo-agent` reaches it in-network at
`http://ollama:11434`. On first run, pull the model into the persistent
`ollama_data` volume:

```bash
docker compose exec ollama ollama pull qwen3.5:9b
```

`scripts/install-ollama.sh` is the host-side setup path. It defaults to `qwen3.5:9b`, never edits
`.env`, starts new servers with GPU visibility disabled, and requires `/api/ps` to report
`size_vram == 0` after its one-token smoke. An already-running GPU-backed Ollama is reported with
manual CPU-only restart guidance and is never killed automatically.

Inside Compose, `OLLAMA_HOST` is pinned to `http://ollama:11434` in
`docker-compose.yml` and deliberately cannot be overridden from `.env`
(`host.docker.internal` is unreachable from the agent container on some
Docker / WSL2 setups and a stale value silently broke role inference).
A host-installed Ollama is only reachable when running FastAPI outside
Docker, where the `OLLAMA_HOST` env var applies normally.

Bedrock requires credentials. Keep `ROLE_AGENT_MODEL_PROVIDER=bedrock`, provide
`ROLE_AGENT_MODEL_ID`, and inject AWS credentials or an AWS profile outside the repo. Summary
configuration normally inherits that provider; `SUMMARY_AGENT_MODEL_PROVIDER` is available only
when an operator deliberately separates the note provider. Production Terraform emits these
canonical role/summary keys directly and defaults the region to `ap-southeast-2`. Do not commit
credentials into `.env`.

The fixture-only `scripts/eval-second-pass.sh` uses the same
`nvidia/parakeet-unified-en-0.6b` default as a stopped visit; pass `--model`
to evaluate a different checkpoint.

## Browser And Event Flow

The browser path is:

```text
GET /scribe
  -> Twig injects session id, WebSocket URL, Mercure URL, and topics
  -> browser captures microphone audio as 16 kHz PCM (pause drops audio, never pads silence)
  -> ws://localhost:48101/ws/transcribe/{session_id}
  -> NeMo publishes raw transcript segments
  -> role queue publishes DOCTOR/PATIENT updates
  -> Stop finalizes, captures a terminal source attestation, and auto-starts correction
  -> correction endpoint stores corrected rows (pinned second-pass ASR)
  -> on-demand summary prefers settled corrected rows, runs fidelity checks, publishes the note
  -> Mercure streams updates back to the browser
```

Mercure topics:

| Topic | Payload purpose |
| --- | --- |
| `scribe/session/{id}/raw` | Immediate raw speaker transcript segments, the finalize event, and the end-of-session quality record. |
| `scribe/session/{id}/roles` | Speaker-to-role mapping updates and relabelled segments. |
| `scribe/session/{id}/summary` | Completed summary payloads. |

Browser source files:

- `public/js/scribe.js` owns shared UI state, roles, status, and safe DOM helpers.
- `public/js/scribe-streaming.js` owns Mercure streams, reconnects, and PCM capture.
- `public/js/scribe-recording.js` owns start/stop/pause, the WebSocket lifecycle, and reconnect backoff.
- `public/js/scribe-transcript.js` owns transcript card rendering and relabelling.
- `public/js/scribe-output.js` owns replay, correction settling, and on-demand note generation.
- `public/js/scribe-actions.js` owns post-visit action visibility, summary-panel state text, safe JSON response parsing, and keyboard shortcuts.
- `public/js/scribe-stitch.js` merges adjacent same-speaker corrected rows into utterance blocks (display only).
- `public/js/scribe-flow.js` classifies silence gaps and talking-over for the transcript layout.
- `public/js/scribe-summary-tabs.js` owns the Note/Transcript tab switch and the Transcript tab body.
- `public/js/scribe-provenance.js` owns per-section citation popovers with an "Open in transcript" action.
- `public/js/scribe-confidence.js` owns low-confidence review cues for transcript rows and note wording.
- `public/js/scribe-copy.js` owns Copy transcript / Copy draft note plain-text serialization.
- `public/js/scribe-dev.js` owns the local dev inspector panel.
- `public/js/scribe-fixtures.js` owns the dev-only Demo Audio picker for generated WAV replay.

## Clinical Summary Grounding

Clinical context assistance is intentionally lightweight in this version.

- `strands_agents/data/clinical_knowledge.json` is a governed project-authored
  asset (`ambient-scribe-clinical-knowledge/v1`), not clinical guideline
  authority; each card cites `docs/clinical-documentation-checklists.md` and
  carries review identity, hard negatives, and privacy exclusions.
- Context stays off by default: cards are retrieved only when an internal
  caller explicitly enables it, and schema-invalid assets fail closed.
- `retrieve_clinical_context()` uses simple keyword scoring to add short notes
  to the summary prompt.
- The retrieval helper never diagnoses, prescribes, mutates the transcript, or
  blocks the summary.

## Medical Phrase Normalisation

A conservative post-ASR correction fallback runs by default
(`MEDICAL_BOOST_ENABLED=0` opts out). It loads
`strands_agents/data/medical_lexicon.txt` and replaces exact word-boundary
variants with canonical clinical terms before text reaches the UI, summary, or
download.

`strands_agents/data/medical_lexicon_review.json` is the v2 pair ledger: one
row per canonical/variant pair with category, hard negatives, source artifact,
review identity, and safety rationale, plus a SHA-256 binding of the exact
runtime `.txt` bytes. `python3 scripts/evaluate-medical-boost.py` prints the
CPU-only before/after table and verifies the binding without loading NeMo;
`scripts/clinical-data-audit.py` is the full governance gate.

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
| `SESSION_POST_VISIT_AUDIO_RETENTION_SECONDS` | `900` | How long a finalized visit's audio stays correctable before the clinician clicks Generate summary. |

Role state is held by `strands_agents/tools/assign_roles.py` and cleaned with
session lifecycle teardown or orphan cleanup. Transcript durability comes from
the selected session storage backend, not from Mercure.

Both backends also keep a separate corrected-transcript lane: the post-stop
correction pass writes corrected rows beside (never over) the live rows, and
`GET /session/{id}/corrected-transcript` exposes them for QA scoring.

## Core Dependency Floors

| Area | Dependency floor or pin | Source |
| --- | --- | --- |
| PHP | `>=8.3 <9.0` | `composer.json` |
| Symfony | `^6.4` | `composer.json` |
| Strands PHP client | `dev-dev` (locked at `a4e30ff`) | `composer.json` / `composer.lock` |
| NeMo base image | `nvcr.io/nvidia/nemo:26.02` | `docker/nemo/Dockerfile` |
| NeMo toolkit | `nemo_toolkit[asr]==2.7.3` | `docker/nemo/Dockerfile` |
| Strands Agents Python | `strands-agents[ollama]>=1.45.0` | `strands_agents/requirements.txt` |
| FastAPI | `>=0.139.0` | `strands_agents/requirements.txt` |
| Uvicorn | `uvicorn[standard]>=0.49.0` | `strands_agents/requirements.txt` |
| Pydantic | `>=2.13.4` | `strands_agents/requirements.txt` |
| HTTPX | `>=0.28.1` | `strands_agents/requirements.txt` |
| PyJWT | `>=2.8.0` | `strands_agents/requirements.txt` |
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
- No external vector database is used for clinical summary grounding.
- No real guideline corpus is bundled; the clinical KB is project-authored PoC data.
- No NeMo decode-time phrase boosting is live; an inactive reviewed-phrase hook exists in the post-visit lane only.
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
vendor/bin/gruff-php analyse --baseline=gruff-php-baseline.json
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
```
