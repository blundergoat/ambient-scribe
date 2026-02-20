#!/usr/bin/env python3
"""
Test combined diarization + multi-speaker ASR pipeline.

Approach:
1. Run Sortformer diarization to get frame-level speaker activity [B, T, num_speakers]
2. For each speaker, set speaker targets on the ASR model
3. Run ASR transcription per speaker
4. Merge results into a speaker-attributed transcript
"""
import os
import time
import urllib.request

import torch

audio_path = "/tmp/nemo_sample.wav"
if not os.path.exists(audio_path):
    urllib.request.urlretrieve(
        "https://dldata-public.s3.us-east-2.amazonaws.com/2086-149220-0033.wav",
        audio_path,
    )

device = torch.device("cuda")

# --- Load models ---
print("Loading models...")
from nemo.collections.asr.models import SortformerEncLabelModel
from nemo.collections.asr.models.multitalker_asr_models import EncDecMultiTalkerRNNTBPEModel

diar = SortformerEncLabelModel.from_pretrained(
    "nvidia/diar_streaming_sortformer_4spk-v2.1"
).eval().to(device)

asr = EncDecMultiTalkerRNNTBPEModel.from_pretrained(
    "nvidia/multitalker-parakeet-streaming-0.6b-v1"
).eval().to(device)
asr.decoding.decoding.use_cuda_graph_decoder = False
asr.decoding.decoding.decoding_computer.disable_cuda_graphs()

print("Models loaded.")

# --- Step 1: Diarization ---
print("\n=== Step 1: Diarization ===")
with torch.inference_mode():
    segments, tensors = diar.diarize(
        audio=audio_path,
        batch_size=1,
        include_tensor_outputs=True,
    )

print(f"Segments: {segments[0]}")
speaker_probs = tensors[0]  # shape: [1, T, 4]
print(f"Speaker probs shape: {speaker_probs.shape}")
print(f"Speaker probs dtype: {speaker_probs.dtype}")
print(f"Speaker probs device: {speaker_probs.device}")

# Move to CUDA if needed
if speaker_probs.device.type == 'cpu':
    speaker_probs = speaker_probs.to(device)

# Determine which speakers are active (have any activity > threshold)
threshold = 0.5
num_speakers = speaker_probs.shape[2]
active_speakers = []
for spk_idx in range(num_speakers):
    spk_activity = speaker_probs[0, :, spk_idx]
    max_activity = spk_activity.max().item()
    mean_activity = spk_activity.mean().item()
    active_frames = (spk_activity > threshold).sum().item()
    total_frames = spk_activity.shape[0]
    print(f"  Speaker {spk_idx}: max={max_activity:.3f}, mean={mean_activity:.3f}, "
          f"active_frames={active_frames}/{total_frames}")
    if active_frames > 0:
        active_speakers.append(spk_idx)

print(f"Active speakers: {active_speakers}")

# --- Step 2: Per-speaker ASR ---
print("\n=== Step 2: Per-speaker ASR ===")
results = {}

for spk_idx in active_speakers:
    print(f"\n  --- Transcribing speaker_{spk_idx} ---")
    # Extract speaker mask: [B, T] for this speaker
    spk_mask = speaker_probs[:, :, spk_idx]  # [1, T]
    # Background mask: sum of all other speakers
    bg_mask = speaker_probs.sum(dim=2) - spk_mask  # [1, T]
    bg_mask = bg_mask.clamp(0, 1)

    print(f"  spk_mask shape: {spk_mask.shape}, mean: {spk_mask.mean():.3f}")
    print(f"  bg_mask shape: {bg_mask.shape}, mean: {bg_mask.mean():.3f}")

    # Set speaker targets on the model
    asr.set_speaker_targets(spk_mask, bg_mask)

    # Run transcription
    with torch.inference_mode():
        hyps = asr.transcribe([audio_path], return_hypotheses=True)

    text = hyps[0].text if hasattr(hyps[0], 'text') else str(hyps[0])
    print(f"  Text: {text}")
    results[f"speaker_{spk_idx}"] = text

    # Clear targets after use
    asr.clear_speaker_targets()

# --- Step 3: Also run single-speaker mode for comparison ---
print("\n=== Comparison: Single-speaker mode (no mask) ===")
with torch.inference_mode():
    single_hyps = asr.transcribe([audio_path], return_hypotheses=True)
print(f"  Text: {single_hyps[0].text}")

# --- Summary ---
print("\n=== Combined Output ===")
for spk, text in results.items():
    print(f"  {spk}: {text}")

print("\n=== Diarization Segments ===")
for seg in segments[0]:
    parts = seg.split()
    start, end, speaker = float(parts[0]), float(parts[1]), parts[2]
    print(f"  [{start:.1f}s - {end:.1f}s] {speaker}")

print("\nDONE")
