#!/usr/bin/env python3
"""
NeMo Buffer Strategy Benchmark - Milestone 1, Task 1.6

Tests diarization + ASR inference time as a function of audio length.
Determines whether growing-buffer, sliding-window, or hybrid is viable.

Key question: does NeMo inference time scale linearly or quadratically
with audio length? If quadratic, growing-buffer won't sustain a 15-min
consultation.

Also tests: what happens when audio exceeds Sortformer's session_len_sec (90s)?

Usage (inside NeMo container):
  python /app/scripts/nemo_benchmark_buffer.py /app/audio/osce-chest-pain-medium.wav

Creates truncated versions at 30s, 60s, 90s, 120s, 180s, 300s and benchmarks each.
"""

import os
import subprocess
import sys
import time

import soundfile as sf
import torch


def log_vram(label: str):
    try:
        result = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=memory.used,memory.total",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
        )
        parts = result.stdout.strip().split(", ")
        print(f"  VRAM ({label}): {parts[0]} / {parts[1]} MiB")
    except FileNotFoundError:
        pass


def create_truncated(source_path: str, target_seconds: float, out_dir: str) -> str:
    """Create a truncated WAV file at the target duration."""
    data, sr = sf.read(source_path)
    target_samples = int(target_seconds * sr)

    if len(data) >= target_samples:
        truncated = data[:target_samples]
    else:
        # Loop the audio to reach target length
        import numpy as np

        repeats = (target_samples // len(data)) + 1
        truncated = np.tile(data, repeats)[:target_samples]

    out_path = os.path.join(out_dir, f"bench_{int(target_seconds)}s.wav")
    sf.write(out_path, truncated, sr)
    actual_duration = len(truncated) / sr
    print(f"  Created {out_path} ({actual_duration:.1f}s, {sr}Hz)")
    return out_path


def benchmark_diarization(model, audio_path: str, label: str) -> dict:
    """Benchmark diarization on a single file."""
    torch.cuda.synchronize()
    start = time.time()
    with torch.inference_mode():
        segments = model.diarize(audio=audio_path, batch_size=1, verbose=False)
    torch.cuda.synchronize()
    elapsed = time.time() - start

    num_segments = len(segments[0]) if segments and segments[0] else 0
    return {"label": label, "time": elapsed, "segments": num_segments}


def benchmark_asr(model, audio_path: str, label: str) -> dict:
    """Benchmark ASR on a single file."""
    torch.cuda.synchronize()
    start = time.time()
    with torch.inference_mode():
        hyps = model.transcribe([audio_path], return_hypotheses=True, verbose=False)
    torch.cuda.synchronize()
    elapsed = time.time() - start

    text = hyps[0].text if hasattr(hyps[0], "text") else str(hyps[0])
    word_count = len(text.split())
    return {"label": label, "time": elapsed, "words": word_count}


def main():
    if len(sys.argv) < 2:
        print("Usage: python nemo_benchmark_buffer.py <audio_file>")
        print("  Audio should be at least 5 minutes for meaningful benchmarks.")
        sys.exit(1)

    source_path = sys.argv[1]
    if not os.path.exists(source_path):
        print(f"ERROR: File not found: {source_path}")
        sys.exit(1)

    # Check source audio length
    info = sf.info(source_path)
    print(f"Source: {source_path}")
    print(
        f"  Duration: {info.duration:.1f}s, Sample rate: {info.samplerate}Hz, "
        f"Channels: {info.channels}"
    )

    # Target durations to benchmark
    durations = [30, 60, 90, 120, 180, 300]
    # Filter out durations longer than 2x source (looping beyond 2x is unrealistic)
    max_duration = info.duration * 2
    durations = [d for d in durations if d <= max_duration]
    # Always include the full source duration
    if info.duration not in durations:
        durations.append(int(info.duration))
    durations.sort()

    print(f"  Will benchmark: {durations}s")

    # Create temp dir for truncated files
    tmp_dir = "/tmp/nemo_bench"
    os.makedirs(tmp_dir, exist_ok=True)

    # Create truncated files
    print("\nCreating benchmark audio files...")
    bench_files = {}
    for dur in durations:
        bench_files[dur] = create_truncated(source_path, dur, tmp_dir)

    # Load models
    print("\n" + "=" * 70)
    print("Loading models...")
    log_vram("before loading")

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

    print("Models loaded.")
    log_vram("after loading")

    # Warmup run (first inference is always slower)
    print("\nWarmup run (30s)...")
    benchmark_diarization(diar_model, bench_files[durations[0]], "warmup")
    benchmark_asr(asr_model, bench_files[durations[0]], "warmup")
    print("Warmup complete.")

    # Benchmark
    print("\n" + "=" * 70)
    print("BENCHMARK RESULTS")
    print("=" * 70)

    diar_results = []
    asr_results = []

    for dur in durations:
        path = bench_files[dur]
        label = f"{dur}s"
        print(f"\n--- {label} ---")

        dr = benchmark_diarization(diar_model, path, label)
        diar_results.append(dr)
        print(
            f"  Diar: {dr['time']:.2f}s ({dr['segments']} segments, "
            f"RTF={dr['time'] / dur:.4f})"
        )

        ar = benchmark_asr(asr_model, path, label)
        asr_results.append(ar)
        print(
            f"  ASR:  {ar['time']:.2f}s ({ar['words']} words, "
            f"RTF={ar['time'] / dur:.4f})"
        )

        total = dr["time"] + ar["time"]
        print(f"  Total: {total:.2f}s (RTF={total / dur:.4f})")
        log_vram(f"after {label}")

    # Summary table
    print("\n" + "=" * 70)
    print("SUMMARY")
    print("=" * 70)
    print(
        f"\n{'Duration':>10} | {'Diar (s)':>10} | {'ASR (s)':>10} | {'Total (s)':>10} | {'RTF':>8} | {'Scale':>8}"
    )
    print("-" * 70)

    prev_total = None
    for i, dur in enumerate(durations):
        d = diar_results[i]["time"]
        a = asr_results[i]["time"]
        t = d + a
        rtf = t / dur
        if prev_total and durations[i - 1] > 0:
            time_ratio = t / prev_total
            scale = f"{time_ratio:.2f}x"
        else:
            scale = "-"
        prev_total = t
        print(
            f"{dur:>8}s | {d:>10.2f} | {a:>10.2f} | {t:>10.2f} | {rtf:>8.4f} | {scale:>8}"
        )

    # Decision
    print("\n" + "=" * 70)
    print("ANALYSIS")
    print("=" * 70)

    if len(diar_results) >= 2:
        # Check if scaling is roughly linear
        first_rtf = (diar_results[0]["time"] + asr_results[0]["time"]) / durations[0]
        last_rtf = (diar_results[-1]["time"] + asr_results[-1]["time"]) / durations[-1]
        ratio = last_rtf / first_rtf if first_rtf > 0 else 0

        print(f"\n  First RTF ({durations[0]}s): {first_rtf:.4f}")
        print(f"  Last RTF ({durations[-1]}s):  {last_rtf:.4f}")
        print(f"  RTF ratio: {ratio:.2f}x")

        if ratio < 1.5:
            print("\n  => SCALING IS ~LINEAR. Growing buffer is viable.")
            print(
                "     Re-processing full audio each chunk is feasible for consultations."
            )
        elif ratio < 3.0:
            print("\n  => SCALING IS SUPER-LINEAR. Consider hybrid approach:")
            print(
                "     Growing buffer for short sessions, switch to sliding window after N minutes."
            )
        else:
            print("\n  => SCALING IS QUADRATIC+. Sliding window required.")
            print(
                "     Cannot re-process full audio - processing time will exceed real-time."
            )

    # Sortformer limit check
    print("\n  Sortformer session_len_sec: 90s")
    over_90 = [d for d in durations if d > 90]
    if over_90:
        diar_90_ok = all(
            diar_results[durations.index(d)]["segments"] > 0 for d in over_90
        )
        if diar_90_ok:
            print(f"  Audio >90s diarized OK: segments found at {over_90}")
        else:
            print("  WARNING: Audio >90s may have diarization issues")

    print("\nDONE")


if __name__ == "__main__":
    main()
