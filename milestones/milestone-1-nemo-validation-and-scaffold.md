# Milestone 1 — NeMo Validation + Project Scaffold

**Timeline:** Weekend 1 (~5-7 hours across 2 sessions)
**Status:** Complete (all tasks done, pending commit)
**Dependencies:** Milestone 0 complete, RTX 5080 accessible in WSL, NVIDIA Container Toolkit installed

---

## Objective

Prove NeMo multitalker Parakeet runs on the RTX 5080 and produces speaker-attributed transcripts from a test audio file. Understand the actual NeMo API surface. Set up the project skeleton by forking The Summit.

---

## Tasks

### 1.1 Project Scaffold (Session A, ~1 hour) — COMPLETE

- [x] Clone `the-summit-chatroom` into `ambient-scribe`
- [x] Rename/rebrand references (Summit -> Scribe, chatroom -> transcript)
- [x] Delete Summit-specific code:
  - `persona_router.py`, persona tools, persona prompts
  - `persona_objectives.py` / sabotage engine
  - Summit-specific Twig template content
- [x] Stub out new files:
  - `strands_agents/nemo_pipeline.py`
  - `strands_agents/nemo_session.py`
  - `strands_agents/transcription_agent.py`
  - `strands_agents/tools/assign_roles.py`
  - `src/Controller/ScribeController.php`
  - `templates/scribe/index.html.twig`
- [x] Update `docker-compose.yml` — add NeMo GPU service placeholder
- [x] Update `docker/nemo/Dockerfile` — NeMo base image with model downloads
- [x] Update `.env.example` — NeMo + Bedrock + WebSocket config
- [x] Update `CLAUDE.md`, `README.md`, `AGENTS.md`, `GEMINI.md` — full rewrite for scribe
- [x] Update all 14 shell scripts — branding, URLs, resource names
- [x] Update all 6 GitHub instruction files — architecture, API contracts, examples
- [x] Update `config/packages/strands.yaml` — agent renamed to scribe
- [x] Update `config/packages/framework.yaml` — added nemo_websocket_url parameter
- [x] Update `.github/workflows/deploy-prod.yml` — resource names, URLs
- [x] Update `.github/instructions/code-review.instructions.md` — full rewrite for scribe
- [x] Fix FastAPI lifespan — migrated deprecated `@app.on_event("startup")` to `lifespan` context manager
- [x] Verified: PHPStan Level 10 passes (0 errors)
- [x] Verified: PHP-CS-Fixer passes (0 fixable files)
- [x] Verified: Python tests pass (21/21, 0 warnings)
- [ ] Commit: `scaffold: fork summit for ambient scribe poc`

### 1.2 Acquire Test Audio

- [ ] **Primary:** Record a 2-3 minute GP consultation roleplay (two speakers, clear audio)
- [ ] **Secondary:** Download OSCE history-taking videos via `yt-dlp -x --audio-format wav --audio-quality 0 <URL>`
  - Search: `"OSCE history taking" chest pain` (Geeky Medics, Zero to Finals, OSCE Sense)
- [ ] **Baseline:** Download NeMo sample: `https://dldata-public.s3.us-east-2.amazonaws.com/2086-149220-0033.wav` (single-speaker, for basic ASR validation only)
- [ ] Store test audio in `tests/fixtures/audio/`

> **Legal note:** YouTube-extracted audio is fine for local development and testing but **must not be committed to the repo** if you plan to open-source. Use self-recorded audio or NeMo's sample files for any fixtures that ship with the repo. Add `tests/fixtures/audio/*.wav` to `.gitignore` except for self-recorded or explicitly licensed files.

### 1.3 GPU Prerequisites Check (Session B, first 15 min) — COMPLETE

- [x] Verify GPU accessible in WSL: RTX 5080 Laptop GPU, 16303 MiB VRAM, Driver 591.74, CUDA 13.1
- [x] Verify NVIDIA Container Toolkit: Docker GPU passthrough confirmed
- [x] Created `scripts/gpu-check.sh` for repeatable verification

> **Blackwell risk:** The RTX 5080 is very new silicon. If CUDA compatibility issues appear here, **stop and switch to the AWS spot fallback** (g5.2xlarge, ~$0.36/hr) rather than burning the weekend debugging driver issues. You can always revisit local GPU support later.

### 1.4 NeMo API Discovery Spike (Session B, ~2-3 hours) — COMPLETE

> **This is the most critical task in the entire project.** The original plan's code sketches treat Sortformer and Parakeet as independently callable models (`sortformer.diarize()`, `parakeet.transcribe(audio, speaker_mask)`). In practice, NeMo's multitalker pipeline is a **composite recipe** with its own inference API — you don't manually pass speaker masks between models. The actual API surface needs to be discovered and documented before any wrapper code is written.

- [x] Create `docker/nemo/Dockerfile` — base: `nvcr.io/nvidia/nemo:25.09` + NeMo main branch (v2.8.0rc0)
- [x] Build container: `docker build -t ambient-scribe-nemo ./docker/nemo`
- [x] Use modern Docker Compose GPU syntax (not legacy `runtime: nvidia`)
- [x] **Explore the actual NeMo multitalker API:**
  - Models are independently loadable (`SortformerEncLabelModel` + `EncDecMultiTalkerRNNTBPEModel`)
  - Diarization: `diar_model.diarize(audio=path)` → `[['start end speaker_id', ...]]`
  - Diarization tensors: `include_tensor_outputs=True` → shape `[B, T, 4]` speaker probabilities
  - ASR: `asr_model.transcribe([path])` → text or Hypothesis objects
  - Multi-speaker kernel injection requires custom dataloader (not usable via `transcribe()` alone)
  - PoC approach: independent diarize + ASR, align by timestamps
- [x] **Write minimal test scripts:**
  - `scripts/nemo_test.py` — standalone model validation
  - `scripts/nemo_explore_api.py` — API surface exploration
  - `scripts/nemo_multispeaker_test.py` — combined pipeline test
- [x] **Document findings** in `docs/nemo-api-notes.md` — comprehensive API surface, output formats, VRAM, timing, gotchas

### 1.5 VRAM Profiling — COMPLETE

- [x] Monitor VRAM while models are loaded
- [x] Actual measurements:
  - CUDA context overhead: 5,670 MiB
  - Sortformer: +1,163 MiB (total: 6,833 MiB)
  - Parakeet: +4,506 MiB (total: 11,339 MiB)
  - **Peak: 11,339 MiB (69% of 16 GB) — well within budget**
  - Headroom: ~5 GB for audio buffers and Python overhead
- [x] No need for Parakeet CTC fallback or sequential processing

> **Hard constraint: NeMo owns the GPU.** The Strands role inference agent (Milestone 3) must use Bedrock or a CPU-only Ollama model. Do not attempt to co-locate NeMo and a GPU-accelerated LLM on the same 16GB card. The 64GB system RAM is generous for everything else (audio buffers, Python overhead, Docker, Symfony), but GPU VRAM is the bottleneck.

### 1.6 Audio Buffer Strategy Spike — COMPLETE

> **This decision determines whether the pipeline can sustain a 15-minute consultation or falls over after 2 minutes.**

- [x] **Benchmark:** Tested NeMo inference on 30s, 60s, 90s, 120s, 180s, 300s, 520s audio
  - RTF ratio (30s vs 520s): 1.11x — **scaling is ~linear**
  - 520s audio: 11.5s total inference (RTF 0.022x), 45x faster than real-time
  - VRAM grows with length: 9 GB (30s) → 15.6 GB (520s) — VRAM is the constraint, not time
  - Sortformer works beyond its configured `session_len_sec: 90`
- [x] **Decision: Growing buffer with VRAM-aware flush**
  - Re-process full audio each chunk (best diarization consistency)
  - If VRAM approaches 15 GB, flush and restart the buffer
  - For typical 10-15 min GP consultation: at most 1-2 flushes
- [x] Documented in `docs/nemo-api-notes.md` (section 5)
- [x] Benchmark script: `scripts/nemo_benchmark_buffer.py`

### 1.7 Audio Format Spike — COMPLETE

- [x] Browser `MediaRecorder` outputs WebM/Opus by default (compressed, small bandwidth)
- [x] NeMo requires 16kHz mono WAV (PCM) — server-side conversion required
- [x] **Decision: WebM/Opus from browser + ffmpeg conversion on server**
  - ffmpeg is already a dependency (added to `setup-initial.sh`)
  - Conversion adds <50ms per chunk — negligible vs NeMo inference time
  - Lowest bandwidth option (compressed audio over WebSocket)
  - PyAV rejected: adds dependency for minimal gain
  - AudioWorklet PCM rejected: 10x bandwidth increase not justified
- [x] Documented in `docs/nemo-api-notes.md` (section 7)

### 1.8 Silence and Single-Speaker Handling Spike — COMPLETE

- [x] **Silence (15s):** Clean — no segments, no ASR hallucination. Safe to pass through pipeline.
- [x] **Single-speaker monologue (45s, looped):** Sortformer hallucinated a second speaker (2 detected instead of 1). Turn-taking patterns in the source 15s leaked through looping.
  - **Implication:** Role inference agent cannot trust speaker count alone — needs conversational context
- [x] **Speech → silence → speech (15s gap):** Correctly handled. Segments placed before/after gap with 1 minor boundary leak.
- [x] **Baseline two-speaker OSCE (4.5 min):** 88 segments, 2 speakers correctly identified, 783 words transcribed
- [x] Documented in `docs/nemo-api-notes.md` (section 6)
- [x] Test script: `scripts/nemo_edge_cases_test.py`

---

## Exit Criteria

- [x] Project cloned and scaffolded with clean file structure
- [x] NeMo container runs on RTX 5080 in WSL Docker
- [x] **NeMo API surface documented** — actual classes, methods, output format, not assumed (see `docs/nemo-api-notes.md`)
- [x] Test script processes a WAV file and produces diarization + ASR output
- [x] Diarization shows `speaker_0` segments with timestamps; ASR produces correct text
- [x] VRAM usage confirmed under 14GB (actual: 11.3 GB peak)
- [x] Audio buffer strategy decided and benchmarked (growing buffer, linear scaling confirmed)
- [x] Audio format conversion approach decided (WebM/Opus → ffmpeg → WAV)
- [x] Single-speaker and silence behaviour documented (silence clean, monologue hallucinates 2nd speaker)

---

## Gotchas & Fallbacks

| Issue | Fallback |
|---|---|
| CUDA version mismatch with RTX 5080 (Blackwell) | Switch to AWS g5.2xlarge spot (~$0.36/hr) immediately — don't burn the weekend on drivers |
| VRAM exceeds 16GB | Use Parakeet CTC 0.6B (smaller) or process speakers sequentially |
| NeMo container won't build | Use pre-built `nvcr.io/nvidia/nemo:24.12` directly with volume mounts |
| WSL GPU passthrough issues | Run Docker Desktop with WSL2 backend + GPU support enabled |
| Model download slow/fails | Pre-download models to a volume mount, skip build-time download |
| NeMo API is completely different from plan's assumptions | That's why this spike exists. Adjust Milestone 2 code sketches based on actual findings. |
| Audio buffer benchmarks show quadratic cost | Use sliding window with reconciliation. Accept label discontinuities for PoC. |

---

## Key Decisions

### Model Loading Strategy
Models must be loaded **once at process startup** and shared across sessions — not per-WebSocket connection. Sortformer + 2x Parakeet are multi-GB GPU models. Validate this works during this milestone by confirming a single Python process can hold all models in VRAM simultaneously.

### Timeline Honesty
This milestone is budgeted at 5-7 hours (up from the original 4-5) because the NeMo API discovery spike is real engineering work, not just "run the tutorial." If the GPU works and NeMo loads cleanly, this may take only 4 hours. If there are CUDA issues or the API is significantly different from expectations, it could take the full weekend. **That's fine — this is the riskiest milestone and it's better to over-budget here than to discover problems in Milestone 2.**
