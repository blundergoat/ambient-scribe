#!/usr/bin/env python3
"""
NeMo Edge Cases Test — Milestone 1, Task 1.8

Tests NeMo behaviour with:
  1. Single-speaker monologue (30+ seconds of one person)
  2. Silence (10+ seconds of no speech)
  3. Mixed: speech → silence → speech

Documents output so we know what the role inference agent will receive
in real consultations (e.g., doctor monologue during examination).

Usage (inside NeMo container):
  python /app/scripts/nemo_edge_cases_test.py /app/audio/osce-chest-pain-short.wav
"""

import os
import sys

import numpy as np
import soundfile as sf
import torch


def create_silence(duration_sec: float, sr: int, out_path: str) -> str:
    """Create a silent WAV file."""
    samples = int(duration_sec * sr)
    silence = np.zeros(samples, dtype=np.float32)
    sf.write(out_path, silence, sr)
    print(f"  Created silence: {out_path} ({duration_sec}s)")
    return out_path


def create_monologue(source_path: str, duration_sec: float, out_path: str) -> str:
    """Create a single-speaker clip by taking one chunk and looping."""
    data, sr = sf.read(source_path)
    target_samples = int(duration_sec * sr)

    # Take the first 15s (likely single speaker at start) and loop
    chunk_samples = min(int(15 * sr), len(data))
    chunk = data[:chunk_samples]
    repeats = (target_samples // len(chunk)) + 1
    mono = np.tile(chunk, repeats)[:target_samples]
    sf.write(out_path, mono, sr)
    print(f"  Created monologue: {out_path} ({duration_sec}s from first 15s)")
    return out_path


def create_speech_silence_speech(
    source_path: str, silence_sec: float, out_path: str
) -> str:
    """Create audio: 15s speech + N seconds silence + 15s speech."""
    data, sr = sf.read(source_path)
    chunk_samples = min(int(15 * sr), len(data))
    first_chunk = data[:chunk_samples]
    # Take from the end for the second chunk (different content)
    second_chunk = data[-chunk_samples:]
    silence = np.zeros(int(silence_sec * sr), dtype=np.float32)
    combined = np.concatenate([first_chunk, silence, second_chunk])
    sf.write(out_path, combined, sr)
    total_dur = len(combined) / sr
    print(
        f"  Created speech-silence-speech: {out_path} ({total_dur:.1f}s, "
        f"silence gap={silence_sec}s)"
    )
    return out_path


def run_pipeline(diar_model, asr_model, audio_path: str, label: str):
    """Run diarization + ASR and print results."""
    print(f"\n{'=' * 60}")
    print(f"TEST: {label}")
    print(f"{'=' * 60}")

    info = sf.info(audio_path)
    print(f"  Duration: {info.duration:.1f}s, SR: {info.samplerate}Hz")

    # Diarization
    print("\n  --- Diarization ---")
    with torch.inference_mode():
        segments, tensors = diar_model.diarize(
            audio=audio_path,
            batch_size=1,
            include_tensor_outputs=True,
            verbose=False,
        )

    if segments and segments[0]:
        print(f"  Segments ({len(segments[0])}):")
        for seg in segments[0]:
            print(f"    {seg}")
    else:
        print("  No segments detected!")

    # Speaker activity analysis
    if tensors:
        probs = tensors[0]  # [B, T, 4]
        print(f"\n  Speaker activity (tensor shape: {probs.shape}):")
        threshold = 0.5
        for spk_idx in range(probs.shape[2]):
            activity = probs[0, :, spk_idx]
            max_val = activity.max().item()
            mean_val = activity.mean().item()
            active = (activity > threshold).sum().item()
            total = activity.shape[0]
            if active > 0 or max_val > 0.1:
                print(
                    f"    Speaker {spk_idx}: max={max_val:.3f}, mean={mean_val:.3f}, "
                    f"active={active}/{total} frames"
                )
    else:
        print("  No tensor output")

    # ASR
    print("\n  --- ASR ---")
    with torch.inference_mode():
        hyps = asr_model.transcribe([audio_path], return_hypotheses=True, verbose=False)

    text = hyps[0].text if hasattr(hyps[0], "text") else str(hyps[0])
    words = text.split()
    print(f"  Words: {len(words)}")
    if len(text) > 500:
        print(f"  Text (first 500 chars): {text[:500]}...")
    else:
        print(f"  Text: {text}")

    return segments, tensors, text


def main():
    if len(sys.argv) < 2:
        print("Usage: python nemo_edge_cases_test.py <audio_file>")
        sys.exit(1)

    source_path = sys.argv[1]
    if not os.path.exists(source_path):
        print(f"ERROR: File not found: {source_path}")
        sys.exit(1)

    tmp_dir = "/tmp/nemo_edge"
    os.makedirs(tmp_dir, exist_ok=True)

    # Create test files
    print("Creating test audio files...")
    silence_path = create_silence(15.0, 16000, f"{tmp_dir}/silence_15s.wav")
    mono_path = create_monologue(source_path, 45.0, f"{tmp_dir}/monologue_45s.wav")
    gap_path = create_speech_silence_speech(
        source_path, 15.0, f"{tmp_dir}/speech_gap_speech.wav"
    )

    # Load models
    print("\nLoading models...")
    from nemo.collections.asr.models import SortformerEncLabelModel
    from nemo.collections.asr.models.multitalker_asr_models import (
        EncDecMultiTalkerRNNTBPEModel,
    )

    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")

    diar_model = (
        SortformerEncLabelModel.from_pretrained(
            "nvidia/diar_streaming_sortformer_4spk-v2.1"
        )
        .eval()
        .to(device)
    )

    asr_model = (
        EncDecMultiTalkerRNNTBPEModel.from_pretrained(
            "nvidia/multitalker-parakeet-streaming-0.6b-v1"
        )
        .eval()
        .to(device)
    )
    asr_model.decoding.decoding.use_cuda_graph_decoder = False
    asr_model.decoding.decoding.decoding_computer.disable_cuda_graphs()
    print("Models loaded.\n")

    # Run tests
    results = {}

    # Test 1: Original two-speaker audio (baseline)
    results["baseline"] = run_pipeline(
        diar_model, asr_model, source_path, "Baseline — original two-speaker OSCE audio"
    )

    # Test 2: Pure silence
    results["silence"] = run_pipeline(
        diar_model, asr_model, silence_path, "Pure silence (15 seconds)"
    )

    # Test 3: Single-speaker monologue
    results["monologue"] = run_pipeline(
        diar_model,
        asr_model,
        mono_path,
        "Single-speaker monologue (45 seconds, looped from first 15s)",
    )

    # Test 4: Speech → silence → speech
    results["gap"] = run_pipeline(
        diar_model, asr_model, gap_path, "Speech (15s) → silence (15s) → speech (15s)"
    )

    # Summary
    print(f"\n{'=' * 60}")
    print("SUMMARY")
    print(f"{'=' * 60}")

    for name, (segs, tensors, text) in results.items():
        num_segs = len(segs[0]) if segs and segs[0] else 0
        num_words = len(text.split()) if text else 0

        if tensors:
            probs = tensors[0]
            active_spks = sum(
                1
                for i in range(probs.shape[2])
                if (probs[0, :, i] > 0.5).sum().item() > 0
            )
        else:
            active_spks = 0

        print(f"\n  {name}:")
        print(
            f"    Segments: {num_segs}, Active speakers: {active_spks}, Words: {num_words}"
        )

    print(f"\n{'=' * 60}")
    print("KEY FINDINGS FOR ROLE INFERENCE")
    print(f"{'=' * 60}")

    # Analyse silence behaviour
    sil_words = len(results["silence"][2].split()) if results["silence"][2] else 0
    print(
        f"\n  Silence: {'hallucinated' if sil_words > 0 else 'clean (no hallucination)'}"
    )
    if sil_words > 0:
        print(f"    WARNING: ASR produced {sil_words} words from silence!")
        print(f"    Text: {results['silence'][2][:200]}")

    # Analyse monologue behaviour
    mono_tensors = results["monologue"][1]
    if mono_tensors:
        probs = mono_tensors[0]
        active_count = sum(
            1 for i in range(probs.shape[2]) if (probs[0, :, i] > 0.5).sum().item() > 0
        )
        print(f"\n  Monologue: {active_count} speaker(s) detected")
        if active_count > 1:
            print(
                "    WARNING: Sortformer hallucinated extra speaker(s) from a monologue!"
            )
        else:
            print("    Good: single speaker correctly identified")

    # Analyse gap behaviour
    gap_segs = results["gap"][0]
    if gap_segs and gap_segs[0]:
        # Check if silence gap is respected
        seg_times = []
        for seg in gap_segs[0]:
            parts = seg.split()
            seg_times.append((float(parts[0]), float(parts[1]), parts[2]))
        print("\n  Speech-gap-speech segments:")
        for start, end, spk in seg_times:
            print(f"    [{start:.1f}s - {end:.1f}s] {spk}")

        # Check for segments during the silence gap (15s-30s)
        gap_segments = [s for s in seg_times if s[0] >= 14.0 and s[1] <= 31.0]
        if gap_segments:
            print(
                f"    WARNING: {len(gap_segments)} segment(s) detected during silence gap!"
            )
        else:
            print("    Good: silence gap is clean")

    print("\nDONE")


if __name__ == "__main__":
    main()
