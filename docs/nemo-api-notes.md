# NeMo API Discovery Notes

**Status:** Superseded runtime target; API notes retained for implementation context.
**Date:** 2026-02-19
**Hardware:** RTX 5080 Laptop GPU (16GB VRAM, Blackwell sm_120)
**Container:** Runtime now targets `nvcr.io/nvidia/nemo:26.02` + `nemo_toolkit[asr]==2.7.3`.

---

## Environment

| Component | Version |
|---|---|
| NeMo Framework | 2.7.3 pinned runtime target |
| PyTorch | 2.8.0a0+5228986c39.nv25.06 |
| CUDA (container) | 13.x-class NVIDIA NeMo release |
| CUDA (host driver) | 13.1, Driver 591.74 |
| GPU | NVIDIA GeForce RTX 5080 Laptop GPU (sm_120, 16303 MiB) |

---

## 1. Actual API Surface

### Models are loaded independently

The Sortformer diarizer and multitalker Parakeet ASR are **separate models** loaded independently:

```python
from nemo.collections.asr.models import SortformerEncLabelModel
from nemo.collections.asr.models.multitalker_asr_models import EncDecMultiTalkerRNNTBPEModel

diar_model = SortformerEncLabelModel.from_pretrained(
    "nvidia/diar_streaming_sortformer_4spk-v2.1"
).eval().to(device)

asr_model = EncDecMultiTalkerRNNTBPEModel.from_pretrained(
    "nvidia/multitalker-parakeet-streaming-0.6b-v1"
).eval().to(device)
```

### Diarization API

```python
# Text output only
segments = diar_model.diarize(audio="path.wav", batch_size=1)
# Returns: [['0.560 3.120 speaker_0', '3.280 4.560 speaker_0', ...]]

# Text + tensor output (frame-level speaker probabilities)
segments, tensors = diar_model.diarize(
    audio="path.wav",
    batch_size=1,
    include_tensor_outputs=True,
)
# tensors[0] shape: [batch, time_frames, num_speakers] = [1, 93, 4]
# Values are probabilities (0.0 to 1.0) per speaker per frame
```

**Full `diarize()` signature:**

| Parameter | Default | Description |
|---|---|---|
| `audio` | (required) | Path to WAV file or list of paths |
| `sample_rate` | None | Sample rate (autodetected from file) |
| `batch_size` | 1 | Batch size for inference |
| `include_tensor_outputs` | False | Return frame-level speaker probabilities |
| `postprocessing_yaml` | None | Custom postprocessing config |
| `num_workers` | 0 | Dataloader workers |
| `verbose` | True | Show progress bars |
| `override_config` | None | Config overrides |

### ASR API

```python
# Simple text output
texts = asr_model.transcribe(["path.wav"])
# Returns: ['transcribed text...']

# Hypothesis output (includes word list, scores)
hyps = asr_model.transcribe(["path.wav"], return_hypotheses=True)
# Returns: [Hypothesis(text='...', words=['word1', ...], score=-891.69)]
```

**Full `transcribe()` signature:**

| Parameter | Default | Description |
|---|---|---|
| `audio` | (required) | List of audio paths |
| `use_lhotse` | True | Use Lhotse dataloader |
| `batch_size` | 4 | Batch size |
| `return_hypotheses` | False | Return Hypothesis objects instead of strings |
| `num_workers` | 0 | Dataloader workers |
| `verbose` | True | Show progress bars |
| `timestamps` | None | Timestamp config |
| `override_config` | None | Config overrides |

### CUDA Graph Workaround (Required)

PyTorch 2.8.0a0 pre-release changed the `cu_call()` return values (5 instead of 6), breaking CUDA graph capture in the RNNT decoder. **Must disable CUDA graphs after loading the ASR model:**

```python
asr_model.decoding.decoding.use_cuda_graph_decoder = False
asr_model.decoding.decoding.decoding_computer.disable_cuda_graphs()
```

Without this, ASR inference crashes with:
```
ValueError: not enough values to unpack (expected 6, got 5)
```

---

## 2. Output Format Examples

### Diarization Output (text)

```python
[['0.560 3.120 speaker_0', '3.280 4.560 speaker_0', '5.040 7.200 speaker_0']]
```

Format: `"{start_sec} {end_sec} {speaker_id}"` — space-separated string per segment.

### Diarization Output (tensor)

```python
# Shape: [1, 93, 4] = [batch, time_frames, 4_speakers]
# For the sample audio (~7s, single speaker):
#   Speaker 0: max=0.994, mean=0.797, active_frames=75/93
#   Speaker 1: max=0.015, mean=0.008, active_frames=0/93
#   Speaker 2: max=0.001, mean=0.000, active_frames=0/93
#   Speaker 3: max=0.000, mean=0.000, active_frames=0/93
```

Frame resolution: ~0.08s per frame (93 frames for ~7.2s audio = 80ms window stride).

### ASR Output (Hypothesis)

```python
Hypothesis(
    text="Well, I don't wish to see it any more, observed Phoebe, turning away her eyes it is certainly very like the old portrait.",
    words=['Well,', 'I', "don't", 'wish', 'to', 'see', 'it', 'any', 'more,', 'observed', 'Phoebe,', 'turning', 'away', 'her', 'eyes', 'it', 'is', 'certainly', 'very', 'like', 'the', 'old', 'portrait.'],
    score=-891.6928,
    timestamp=[],       # Empty by default, need timestamps config
    alignments=None,
    token_confidence=None,
    word_confidence=None,
)
```

---

## 3. Multitalker Speaker Kernel Architecture

The `EncDecMultiTalkerRNNTBPEModel` uses a **speaker kernel injection** mechanism:

1. Speaker activity masks (from diarization) are set via `set_speaker_targets(spk_mask, bg_mask)`
2. Speaker kernels (feed-forward networks) are hooked into encoder layer 0
3. During forward pass, the speaker kernel multiplies encoder hidden states by the speaker mask, producing speaker-conditioned features
4. The decoder then produces per-speaker transcriptions

**Important limitation when calling `transcribe()` directly:**
- `set_speaker_targets()` is overwritten by `_transcribe_forward()`, which unpacks targets from the dataloader batch
- The `transcribe()` API doesn't accept external speaker masks

### SpeakerTaggedASR Composite Pipeline (Recommended)

NeMo provides a `SpeakerTaggedASR` orchestrator class that handles the full streaming pipeline:

```python
from nemo.collections.asr.parts.utils.multispk_transcribe_utils import SpeakerTaggedASR
from nemo.collections.asr.parts.utils.streaming_utils import CacheAwareStreamingAudioBuffer

# Set up streaming buffer
streaming_buffer = CacheAwareStreamingAudioBuffer(
    model=asr_model,
    online_normalization=cfg.online_normalization,
    pad_and_drop_preencoded=cfg.pad_and_drop_preencoded,
)
streaming_buffer.append_audio_file(audio_filepath=path, stream_id=-1)

# Initialize composite pipeline
multispk_streamer = SpeakerTaggedASR(cfg, asr_model, diar_model)

# Process chunks
for step_num, (chunk_audio, chunk_lengths) in enumerate(streaming_buffer):
    with torch.inference_mode():
        multispk_streamer.perform_parallel_streaming_stt_spk(
            step_num=step_num,
            chunk_audio=chunk_audio,
            chunk_lengths=chunk_lengths,
            is_buffer_empty=streaming_buffer.is_buffer_empty(),
            drop_extra_pre_encoded=drop_extra_pre_encoded,
        )

# Get results in SegLST format
multispk_streamer.generate_seglst_dicts_from_parallel_streaming(
    samples=[{'audio_filepath': path}]
)
results = multispk_streamer.instance_manager.seglst_dict_list
```

There's also a ready-made CLI script:
```bash
python ${NEMO_ROOT}/examples/asr/asr_cache_aware_streaming/speech_to_text_multitalker_streaming_infer.py \
    asr_model="nvidia/multitalker-parakeet-streaming-0.6b-v1" \
    diar_model="nvidia/diar_streaming_sortformer_4spk-v2.1" \
    att_context_size="[70,13]" \
    audio_file="/path/to/audio.wav" \
    output_path="/path/to/output.json"
```

### SegLST Output Format

The composite pipeline outputs SegLST (Segment-wise Long-form Speech Transcription):

```json
[
  {"session_id": "audio_001", "speaker": "speaker_0", "start_time": 0.0, "end_time": 2.45, "words": "Hello how are you"},
  {"session_id": "audio_001", "speaker": "speaker_1", "start_time": 1.20, "end_time": 3.80, "words": "I'm doing great"}
]
```

### Recommended Approach for Milestone 2

**Option A (Preferred):** Use `SpeakerTaggedASR` composite pipeline — handles streaming, overlapping speech, speaker-kernel injection natively. Requires `MultitalkerTranscriptionConfig` from NeMo examples directory.

**Option B (Simpler fallback):** Independent diarize + ASR, align by timestamps. Good enough for GP consultations with minimal overlap:
```python
segments, tensors = diar_model.diarize(audio=path, include_tensor_outputs=True)
hyps = asr_model.transcribe([path], return_hypotheses=True)
# Align words to segments by timestamps
```

### VRAM Note for Multi-Speaker

The multitalker Parakeet spawns one ASR instance per detected speaker. With 2 speakers (GP consultation), VRAM usage may increase. The 5 GB headroom should accommodate this, but needs verification during Milestone 2.

---

## 4. VRAM Measurements

| Stage | VRAM Used | VRAM Total | Notes |
|---|---|---|---|
| Before loading | 5,670 MiB | 16,303 MiB | CUDA context overhead |
| After Sortformer | 6,833 MiB | 16,303 MiB | +1,163 MiB |
| After both models | 11,339 MiB | 16,303 MiB | +4,506 MiB for Parakeet |
| During diarization | 8,827 MiB | 16,303 MiB | Drops after inference |
| During ASR | 8,893 MiB | 16,303 MiB | Drops after inference |

**Summary:**
- Sortformer: ~1.2 GB
- Parakeet: ~4.5 GB
- **Peak total: 11.3 GB (69% of 16GB)**
- **Headroom: ~5 GB** — sufficient for audio buffers and Python overhead
- No need for Parakeet CTC fallback or sequential processing

---

## 5. Processing Time

| Operation | Time | Audio Duration | RTF |
|---|---|---|---|
| Model load (both) | 21.0s | — | One-time startup |
| Sortformer load | 2.8s | — | |
| Parakeet load | 9.0s | — | |
| Diarization | 0.5s | ~7.2s | 0.07x |
| ASR transcription | 0.3s | ~7.2s | 0.04x |
| **Total inference** | **0.8s** | **~7.2s** | **0.11x** |

Real-time factor (RTF) of 0.11x means we can process audio ~9x faster than real-time.

### Buffer Strategy Benchmark (Task 1.6)

Tested diarization + ASR at increasing audio lengths using OSCE chest pain audio (8.5 min source, looped to reach longer durations):

| Duration | Diar (s) | ASR (s) | Total (s) | RTF | VRAM (MiB) |
|----------|----------|---------|-----------|------|------------|
| 30s | 0.16 | 0.44 | 0.60 | 0.0200 | 9,067 |
| 60s | 0.32 | 0.83 | 1.15 | 0.0191 | 9,347 |
| 90s | 0.30 | 0.89 | 1.19 | 0.0132 | 9,585 |
| 120s | 0.44 | 1.53 | 1.98 | 0.0165 | 9,805 |
| 180s | 0.58 | 2.27 | 2.84 | 0.0158 | 10,405 |
| 300s | 1.15 | 3.52 | 4.67 | 0.0156 | 12,845 |
| 520s | 1.66 | 9.88 | 11.54 | 0.0222 | 15,568 |

**Key findings:**
- RTF ratio (first vs last): **1.11x — scaling is ~linear**
- **Growing buffer is viable** for consultations up to ~8-9 minutes
- At 520s (full 8.5 min clip), total inference is 11.5s with RTF 0.022x — still 45x faster than real-time
- VRAM grows with audio length: 9 GB at 30s → 15.6 GB at 520s (near the 16 GB limit)
- **VRAM is the constraint, not processing time** — for 15+ minute consultations, a hybrid approach (growing buffer with periodic flush) will be needed
- Sortformer works correctly beyond its configured `session_len_sec: 90` — segments found at all durations tested

**Decision: Growing buffer with VRAM-aware flush.** Re-process full audio each chunk. If VRAM approaches 15 GB, flush and restart the buffer. For a typical 10-15 minute GP consultation, this means at most 1-2 flushes.

---

## 6. Edge Case Behaviour (Task 1.8)

### Pure Silence (15 seconds)
- **Diarization:** No segments detected (clean)
- **ASR:** Zero words output (no hallucination)
- **Implication:** Safe to send silence through the pipeline — no spurious transcripts

### Single-Speaker Monologue (45 seconds, looped)
- **Diarization:** 2 speakers detected — **Sortformer hallucinated a second speaker**
- Speaker 0: 394/563 active frames, Speaker 1: 137/563 active frames
- The OSCE audio has two speakers (doctor intro + patient responses) in the first 15s — even when looped as a "monologue", Sortformer picks up turn-taking patterns
- **Implication:** The role inference agent should not rely solely on speaker count — it needs conversational context to validate attributions

### Speech → Silence (15s) → Speech
- **Diarization:** Correctly places segments before and after the gap. 1 minor segment leaked into the gap boundary
- **ASR:** Correctly transcribes both speech portions, nothing during silence
- **Implication:** Silence gaps are handled well — the pipeline can tolerate pauses during examination

### Baseline Two-Speaker OSCE (4.5 minutes)
- **Diarization:** 88 segments, 2 active speakers correctly identified
- Speaker 0: 2307/3371 frames (doctor — talks more), Speaker 1: 1024/3371 frames (patient)
- **ASR:** 783 words, good transcription quality
- **Implication:** The pipeline produces usable output for real consultation audio

---

## 7. Audio Format Notes (Task 1.7)

### Browser to Server
- Current browser path uses `PcmStreamer` in `public/js/scribe.js` to emit 16 kHz 16-bit PCM
- NeMo consumes PCM via `AudioBuffer` when `NEMO_STREAM_INPUT_FORMAT=pcm`
- WebM/Opus decoding remains available only when the environment contract is explicitly changed to `webm`

### Recommended Approach: ffmpeg subprocess
- ffmpeg is already a dependency (installed in setup-initial.sh)
- Single `subprocess.run()` call per chunk: `ffmpeg -i input.webm -ar 16000 -ac 1 output.wav`
- Alternative: `PyAV` (in-process, no subprocess overhead) — viable but adds a dependency
- Alternative: `AudioWorklet` in browser sending raw PCM Float32 — eliminates server conversion but increases bandwidth ~10x

### Current Decision: Browser PCM + direct server buffering
- Avoids per-chunk ffmpeg subprocess work in the live path
- Keeps the browser/server contract explicit through `NEMO_STREAM_INPUT_FORMAT=pcm`
- Uses more bandwidth than WebM/Opus, but removes a silent format-conversion failure mode from live sessions

---

## 8. Model Details

| Model | Class | HuggingFace ID |
|---|---|---|
| Sortformer diarizer | `nemo.collections.asr.models.sortformer_diar_models.SortformerEncLabelModel` | `nvidia/diar_streaming_sortformer_4spk-v2.1` |
| Multitalker Parakeet | `nemo.collections.asr.models.multitalker_asr_models.EncDecMultiTalkerRNNTBPEModel` | `nvidia/multitalker-parakeet-streaming-0.6b-v1` |

### Key Public Methods

**Diarizer:**
- `diarize(audio, batch_size, include_tensor_outputs, ...)` — main inference
- `forward(...)` — training/raw forward pass
- `predict_step(...)` — PyTorch Lightning predict

**ASR:**
- `transcribe(audio, return_hypotheses, ...)` — main inference
- `change_decoding_strategy(decoding_cfg)` — change greedy/beam config
- `set_speaker_targets(spk_targets, bg_spk_targets)` — set speaker masks for kernel injection
- `clear_speaker_targets()` — reset masks
- `forward(...)` — training/raw forward pass

---

## 9. Gotchas and Quirks

### Container Version Matrix

| Issue | NeMo 24.12-era container | Older 2025 stable container | Current 26.02 + 2.7.3 target |
|---|---|---|---|
| RTX 5080 (sm_120) CUDA | Fails | Works | Works |
| Streaming Sortformer v2.1 | Fails (`spkcache_len`) | Works | Works |
| Multitalker ASR module | Missing | Missing | Expected in released line; verify during GPU build |
| CUDA graph decoder | N/A | N/A | Verify during GPU build before changing workaround code |

### Sortformer Configuration

The Sortformer model reports `num_spks: 4` and `session_len_sec: 90`. Despite the 90s config, **diarization works correctly on audio up to 520s** (tested in Task 1.6 benchmark). The streaming mode internally handles longer sessions.

### Single-Speaker Fallback

When no speaker mask is provided to the ASR model, it triggers "single speaker mode" with an all-ones mask. This produces correct transcription but without speaker attribution. The warning is:
```
Mask is None, triggering single speaker mode and assigning all ones with shape: torch.Size([1, 94])
```

### Frame Alignment

Diarization tensor output has 93 frames for ~7.2s audio (80ms stride), while ASR uses 94 frames. The `solve_length_mismatch()` method handles this by padding/truncating, so exact alignment isn't required.

### Model Download

Models are multi-GB and cached at `/root/.cache/huggingface/hub/`. Download at Docker build time (not runtime) via:
```python
from huggingface_hub import hf_hub_download
hf_hub_download('nvidia/diar_streaming_sortformer_4spk-v2.1', filename='...')
hf_hub_download('nvidia/multitalker-parakeet-streaming-0.6b-v1', filename='...')
```

### NeMo Warning Spam

Loading models produces extensive warnings about training/validation/test data loaders not being configured. These are harmless for inference-only use and can be suppressed by setting NeMo logging level.
