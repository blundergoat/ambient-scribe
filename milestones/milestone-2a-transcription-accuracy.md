# Milestone 2.5 — Transcription Accuracy

**Timeline:** ~6-8 hours
**Status:** Not Started
**Dependencies:** Milestone 2 complete (live pipeline verified)

---

## Objective

Make transcription accurate enough to trust: correct speaker-word attribution, sustainable performance for 15+ minute sessions, and measurable quality with benchmarks. This is the single highest-impact milestone — everything downstream (role inference, summaries, clinical value) depends on accurate transcription.

---

## Tasks

### 2A.1 Word-Level Timestamp Alignment (replaces proportional distribution)

> **Why:** The current `_parse_nemo_output` distributes ASR words across diarisation segments proportionally by duration. This assigns words to the wrong speaker when speech density varies (fast talker vs slow talker). Word-level timestamps enable correct assignment.

- [ ] Enable word-level timestamps from Parakeet via `timestamps` config on `asr_model.transcribe()`
- [ ] Replace proportional distribution (`nemo_pipeline.py:274-302`) with timestamp-based alignment: assign each word to the diarisation segment whose time range contains the word's timestamp
- [ ] Handle overlapping speech: words during overlap assigned to the speaker with greater diarisation probability at that timestamp
- [ ] Handle words outside any diarisation segment: assign to nearest speaker by time proximity
- [ ] Evaluate `SpeakerTaggedASR` composite pipeline as an alternative (documented in `nemo-api-notes.md:166-199`)
- [ ] Update `_parse_nemo_output` tests with real NeMo output fixtures (not hand-crafted strings)

### 2A.2 Sliding Window with Periodic Reconciliation

> **Why:** The growing buffer re-processes the entire session on every 5-second chunk. At ~4 minutes, NeMo inference takes longer than the chunk interval and the pipeline falls permanently behind. VRAM also grows linearly — OOM at ~8 minutes on 16GB.

- [ ] **Fast path (every chunk):** Process only the last 30-60 seconds of audio. Mark segments as `is_interim=true`.
- [ ] **Slow path (every 60 seconds):** Reprocess the last 3-5 minutes for diarisation consistency. Mark segments as `is_interim=false`. These supersede interim segments by timestamp overlap.
- [ ] **Finalize (on disconnect):** Full-buffer reprocess (existing behaviour). All segments become final.
- [ ] Define window sizes as configurable env vars (`NEMO_FAST_WINDOW_SECONDS`, `NEMO_RECONCILE_WINDOW_SECONDS`)
- [ ] Update `AudioBuffer` to support efficient windowed access (not just `current_window()` returning everything)
- [ ] Use `collections.deque` in `AudioBuffer` for O(1) popleft (currently `list.pop(0)` is O(n))

### 2A.3 VRAM-Aware Buffer Management

- [ ] Check `torch.cuda.memory_allocated()` before inference
- [ ] If VRAM > 14 GB (configurable), flush to a shorter window before processing
- [ ] Log VRAM flush events with session context
- [ ] Verify: 15-minute session stays within VRAM budget without OOM

### 2A.4 Speaker Identity Across Window Boundaries

> **Why:** When the buffer window slides forward, Sortformer may assign different speaker labels to the same voice. `spk_0` in window A might become `spk_1` in window B.

- [ ] Extract speaker embeddings using NeMo's TitaNet (192-dim voice print) during the first 30 seconds
- [ ] Store embeddings as session-level speaker anchors
- [ ] After each window inference, compare new speaker embeddings against anchors using cosine similarity
- [ ] Remap speaker labels to maintain identity consistency across windows
- [ ] Fall back to label continuity heuristic if TitaNet is unavailable

### 2A.5 VAD Gating

> **Why:** During silence (doctor writing notes, patient thinking), NeMo inference wastes GPU cycles producing empty results. VAD (Voice Activity Detection) skips inference when no speech is detected.

- [ ] Add `silero-vad` (CPU-only, ~10ms per chunk) as a pre-filter before NeMo
- [ ] Skip NeMo inference when VAD detects no speech in the chunk
- [ ] Send a `{"type": "silence", "duration": N}` event to the browser so the UI knows the mic is working
- [ ] Log VAD skip events for observability

### 2A.6 Speaker Hallucination Filter

- [ ] After diarisation, calculate each speaker's frame activity percentage
- [ ] Suppress speakers with < 5% total frame activity (phantom speakers from Sortformer)
- [ ] Log suppressed speakers for debugging

### 2A.7 Quality Benchmarking

- [ ] Record 3-5 self-recorded multi-speaker conversations (2-3 minutes each, varied scenarios)
- [ ] Create ground truth transcripts with speaker attribution and timestamps
- [ ] Add `scripts/benchmark_accuracy.py` — computes WER (Word Error Rate) and DER (Diarisation Error Rate) against ground truth
- [ ] Establish baselines: WER before/after timestamp alignment, DER before/after speaker embedding
- [ ] Add to `composer preflight` as an optional quality gate

---

## Exit Criteria

- [ ] Words assigned to correct speakers via timestamp alignment (not proportional duration)
- [ ] 15-minute session completes without falling behind real-time or OOMing
- [ ] Speaker identity maintained across window boundaries via embeddings
- [ ] Silence does not trigger GPU inference (VAD gating)
- [ ] WER and DER measured against ground truth fixtures
- [ ] `is_interim` / `is_final` semantics implemented and visible in the browser

---

## Architecture: Sliding Window

```
Chunk 1-6:   [=====] Fast path (last 30s)
Chunk 7-12:  [=====] Fast path (last 30s)
Chunk 12:    [================] Reconciliation (last 180s, replaces interim segments)
Chunk 13-18: [=====] Fast path
Chunk 24:    [================] Reconciliation
...
Disconnect:  [================================] Full finalize (complete session audio)
```

Interim segments (grey/italic in UI) are replaced by reconciliation segments (solid text). The user sees immediate feedback that firms up as context grows.
