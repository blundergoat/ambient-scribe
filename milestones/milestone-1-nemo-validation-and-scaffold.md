# Milestone 1 — NeMo Validation + Project Scaffold

**Timeline:** Weekend 1 (~5-7 hours across 2 sessions)
**Status:** Complete
**Dependencies:** RTX 5080 accessible in WSL, NVIDIA Container Toolkit installed

---

## Objective

Prove NeMo multitalker Parakeet runs on the RTX 5080 and produces speaker-attributed transcripts from a test audio file. Understand the actual NeMo API surface. Set up the project skeleton.

---

## Tasks

### 1.1 Project Scaffold — COMPLETE

- [x] Scaffold project structure (PHP Symfony + Python FastAPI + Docker)
- [x] Stub out core files: `nemo_pipeline.py`, `nemo_session.py`, `transcription_agent.py`, `assign_roles.py`, `ScribeController.php`, `index.html.twig`
- [x] Docker Compose with NeMo GPU service, PHP app, Mercure hub
- [x] `.env.example` with full configuration
- [x] Documentation: `CLAUDE.md`, `AGENTS.md`, architecture overview
- [x] PHPStan Level 10 passes, PHP-CS-Fixer clean, Python tests pass

### 1.2 Test Audio

- [ ] Record a 2-3 minute self-recorded multi-speaker conversation for test fixture
- [x] Downloaded OSCE audio for local testing (not committed — licence restriction)
- [x] `tests/fixtures/audio/` directory structure in place

### 1.3 GPU Prerequisites — COMPLETE

- [x] RTX 5080 Laptop GPU verified: 16,303 MiB VRAM, Driver 591.74, CUDA 13.1
- [x] NVIDIA Container Toolkit confirmed
- [x] `scripts/gpu-check.sh` for repeatable verification

### 1.4 NeMo API Discovery Spike — COMPLETE

- [x] Models: `nvidia/diar_streaming_sortformer_4spk-v2.1` (diarisation) + `nvidia/multitalker-parakeet-streaming-0.6b-v1` (ASR)
- [x] Diarisation: `diar_model.diarize(audio=path)` → `[['start end speaker_id', ...]]`
- [x] ASR: `asr_model.transcribe([path])` → text or Hypothesis objects
- [x] PoC approach: independent diarize + ASR, aligned proportionally (accuracy upgrade planned for M2.5)
- [x] Findings documented in `docs/nemo-api-notes.md`

### 1.5 VRAM Profiling — COMPLETE

- [x] CUDA context: 5,670 MiB
- [x] Sortformer: +1,163 MiB (total: 6,833 MiB)
- [x] Parakeet: +4,506 MiB (total: 11,339 MiB)
- [x] **Peak: 11,339 MiB (69% of 16 GB) — within budget**
- [x] CUDA graph workaround implemented (PyTorch 2.8 compat)

### 1.6 Audio Buffer Strategy Spike — COMPLETE

- [x] Benchmarked: 30s-520s audio, scaling ~linear, RTF 0.022x at 520s
- [x] VRAM grows with buffer: 9 GB (30s) → 15.6 GB (520s)
- [x] **Decision: Growing buffer for PoC** (sliding window upgrade planned for M2.5)

### 1.7 Audio Format Spike — COMPLETE

- [x] Browser sends PCM via `PcmStreamer` (Web Audio API, 16kHz mono)
- [x] NeMo requires 16kHz mono WAV — direct PCM-to-WAV conversion in pipeline
- [x] WebM/Opus fallback path exists via ffmpeg (`NEMO_STREAM_INPUT_FORMAT=webm`)
- [x] **Decision: PCM is the default path** (lowest complexity, no ffmpeg in hot path)

### 1.8 Edge Case Spikes — COMPLETE

- [x] Silence: clean — no hallucinated segments
- [x] Single speaker: Sortformer can hallucinate a 2nd speaker from turn-taking patterns
- [x] Speech-gap-speech: correctly handled with minor boundary leak
- [x] Two-speaker OSCE: 88 segments, 2 speakers correctly identified

---

## Exit Criteria

All met. See `docs/nemo-api-notes.md` for full API surface documentation.
