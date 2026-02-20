# Milestone 1 — NeMo Validation + Project Scaffold

**Timeline:** Weekend 1 (~5-7 hours across 2 sessions)
**Status:** In Progress (Tasks 1.1, 1.3, 1.4, 1.5 complete)
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

### 1.6 Audio Buffer Strategy Spike

> **This decision determines whether the pipeline can sustain a 15-minute consultation or falls over after 2 minutes.**

- [ ] **Growing buffer (re-process everything):**
  - Pro: Best diarization consistency (Sortformer sees full context)
  - Con: Quadratic processing cost — 5 minutes of audio takes 4x longer than 2.5 minutes
  - Viable only if NeMo processing time scales sub-linearly with input length
- [ ] **Sliding window (e.g. last 30-60 seconds):**
  - Pro: Constant processing cost per chunk
  - Con: Speaker labels may be inconsistent at window boundaries
  - Requires reconciliation logic to map labels across windows
- [ ] **Growing buffer with checkpointing:**
  - Re-process full audio every N chunks (e.g. every 5th chunk)
  - Use cached results for intermediate chunks
  - Balance between consistency and cost
- [ ] **Benchmark:** Time NeMo inference on 30s, 60s, 120s, 300s audio files. Plot the curve. This determines the strategy.
- [ ] Document decision in `docs/nemo-api-notes.md`

### 1.7 Audio Format Spike

- [ ] Test browser `MediaRecorder` output formats (WebM/Opus vs PCM)
- [ ] Evaluate server-side conversion options:
  - `PyAV` (in-process, no subprocess overhead) — preferred
  - `ffmpeg` persistent subprocess with pipe I/O (not per-chunk spawn)
  - `AudioWorklet` in browser sending raw PCM Float32 (eliminates server conversion)
- [ ] Document decision in a brief ADR or code comment

### 1.8 Silence and Single-Speaker Handling Spike

- [ ] Test NeMo output when only one person speaks for 30+ seconds
  - Does Sortformer output only one speaker? Or does it hallucinate a second?
  - What does the ASR output look like for a monologue?
- [ ] Test NeMo output during silence (no speech for 10+ seconds)
- [ ] Document behaviour — this affects role inference (no turn-taking signal means the agent can't infer roles from conversational dynamics)

---

## Exit Criteria

- [x] Project cloned and scaffolded with clean file structure
- [x] NeMo container runs on RTX 5080 in WSL Docker
- [x] **NeMo API surface documented** — actual classes, methods, output format, not assumed (see `docs/nemo-api-notes.md`)
- [x] Test script processes a WAV file and produces diarization + ASR output
- [x] Diarization shows `speaker_0` segments with timestamps; ASR produces correct text
- [x] VRAM usage confirmed under 14GB (actual: 11.3 GB peak)
- [ ] Audio buffer strategy decided and benchmarked
- [ ] Audio format conversion approach decided
- [ ] Single-speaker and silence behaviour documented

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
