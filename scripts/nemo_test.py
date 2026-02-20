#!/usr/bin/env python3
"""
NeMo API Discovery Test Script — Milestone 1, Task 1.4

Run this inside the NeMo Docker container to:
  1. Load the streaming Sortformer diarizer + multitalker Parakeet ASR
  2. Test standalone diarization and ASR on a test WAV file
  3. Print raw output (exactly as NeMo returns it)

This script becomes the ground truth for the NemoPipeline wrapper.

Usage:
  docker run --rm --gpus all --ipc=host --ulimit memlock=-1 --ulimit stack=67108864 \
    -v $(pwd)/scripts:/app/scripts \
    -v $(pwd)/tests/fixtures/audio:/app/audio \
    ambient-scribe-nemo python /app/scripts/nemo_test.py [/app/audio/test.wav]

If no test audio is provided, the script downloads NeMo's sample file.
"""

import json
import os
import subprocess
import sys
import time


def download_sample_audio(dest_path: str) -> str:
    """Download NeMo's sample audio file for basic validation."""
    url = "https://dldata-public.s3.us-east-2.amazonaws.com/2086-149220-0033.wav"
    print(f"  Downloading sample audio from {url}")
    import urllib.request
    urllib.request.urlretrieve(url, dest_path)
    print(f"  Saved to {dest_path}")
    return dest_path


def log_vram(label: str):
    """Print current GPU VRAM usage."""
    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.used,memory.total,utilization.gpu", "--format=csv,noheader"],
            capture_output=True, text=True,
        )
        print(f"  VRAM ({label}): {result.stdout.strip()}")
    except FileNotFoundError:
        print(f"  VRAM ({label}): nvidia-smi not found")


def main():
    audio_path = sys.argv[1] if len(sys.argv) > 1 else None

    if audio_path is None:
        audio_path = "/tmp/nemo_sample.wav"
        if not os.path.exists(audio_path):
            download_sample_audio(audio_path)
        print(f"Using sample audio: {audio_path}")
    else:
        print(f"Audio file: {audio_path}")

    if not os.path.exists(audio_path):
        print(f"ERROR: File not found: {audio_path}")
        sys.exit(1)

    print("=" * 70)
    log_vram("before loading")

    # =========================================================================
    # STEP 1: Load models
    # =========================================================================
    print("\n[1/3] Loading NeMo models...")
    load_start = time.time()

    import torch
    print(f"  PyTorch version: {torch.__version__}")
    print(f"  CUDA available: {torch.cuda.is_available()}")
    if torch.cuda.is_available():
        print(f"  CUDA device: {torch.cuda.get_device_name(0)}")
        print(f"  CUDA capability: {torch.cuda.get_device_capability(0)}")

    import nemo
    print(f"  NeMo version: {nemo.__version__}")

    from nemo.collections.asr.models import SortformerEncLabelModel

    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    print(f"  Device: {device}")

    print("  Loading streaming Sortformer diarizer...")
    diar_start = time.time()
    diar_model = SortformerEncLabelModel.from_pretrained(
        "nvidia/diar_streaming_sortformer_4spk-v2.1"
    ).eval().to(device)
    print(f"  Sortformer loaded in {time.time() - diar_start:.1f}s")
    log_vram("after Sortformer")

    print("  Loading multitalker Parakeet ASR...")
    asr_start = time.time()
    from nemo.collections.asr.models.multitalker_asr_models import EncDecMultiTalkerRNNTBPEModel
    asr_model = EncDecMultiTalkerRNNTBPEModel.from_pretrained(
        "nvidia/multitalker-parakeet-streaming-0.6b-v1"
    ).eval().to(device)
    # Disable CUDA graphs in decoding — incompatible with PyTorch 2.8.0a0 pre-release
    # (cu_call returns 5 values instead of expected 6)
    if hasattr(asr_model, 'decoding') and hasattr(asr_model.decoding, 'decoding'):
        dd = asr_model.decoding.decoding
        dd.use_cuda_graph_decoder = False
        if hasattr(dd, 'decoding_computer') and hasattr(dd.decoding_computer, 'disable_cuda_graphs'):
            dd.decoding_computer.disable_cuda_graphs()
            print("  Disabled CUDA graphs on decoding_computer (PyTorch 2.8 compat)")
    print(f"  Parakeet loaded in {time.time() - asr_start:.1f}s")

    load_time = time.time() - load_start
    print(f"  Both models loaded in {load_time:.1f}s")
    log_vram("after both models")

    # =========================================================================
    # STEP 2: Test models
    # =========================================================================
    print("\n[2/3] Testing models...")

    # --- Standalone diarization ---
    print("\n  --- Standalone diarization (Sortformer) ---")
    try:
        diar_infer_start = time.time()
        with torch.inference_mode():
            diar_output = diar_model.diarize(
                audio=audio_path,
                batch_size=1,
            )
        diar_infer_time = time.time() - diar_infer_start
        print(f"  Diarization time: {diar_infer_time:.1f}s")
        print(f"  Output type: {type(diar_output)}")
        if isinstance(diar_output, list):
            print(f"  Output length: {len(diar_output)}")
            for i, item in enumerate(diar_output[:3]):
                print(f"  Output[{i}] type: {type(item)}")
                print(f"  Output[{i}]: {item}")
        else:
            print(f"  Raw output: {diar_output}")
        log_vram("after diarization")
    except Exception as e:
        print(f"  Diarization failed: {e}")
        import traceback
        traceback.print_exc()

    # --- Standalone ASR ---
    print("\n  --- Standalone ASR (multitalker Parakeet) ---")
    try:
        asr_infer_start = time.time()
        with torch.inference_mode():
            asr_output = asr_model.transcribe([audio_path])
        asr_infer_time = time.time() - asr_infer_start
        print(f"  ASR time: {asr_infer_time:.1f}s")
        print(f"  Output type: {type(asr_output)}")
        if hasattr(asr_output, '__len__'):
            print(f"  Output length: {len(asr_output)}")
        # Try to get text output
        if isinstance(asr_output, list):
            for i, item in enumerate(asr_output[:3]):
                print(f"  Output[{i}] type: {type(item)}")
                if hasattr(item, 'text'):
                    print(f"  Output[{i}].text: {item.text}")
                elif isinstance(item, str):
                    print(f"  Output[{i}]: {item}")
                else:
                    print(f"  Output[{i}]: {item}")
        elif hasattr(asr_output, 'text'):
            print(f"  Text: {asr_output.text}")
        else:
            output_str = str(asr_output)
            print(f"  Raw output: {output_str[:2000]}")
        log_vram("after ASR")
    except Exception as e:
        print(f"  ASR failed: {e}")
        import traceback
        traceback.print_exc()

    # =========================================================================
    # STEP 3: Summary
    # =========================================================================
    print("\n[3/3] Summary")
    print("=" * 70)

    print(f"\n--- Timing ---")
    print(f"  Model load:       {load_time:.1f}s")
    log_vram("final")

    # Print model class info for API documentation
    print(f"\n--- Model Classes ---")
    print(f"  Diarizer: {type(diar_model).__module__}.{type(diar_model).__name__}")
    print(f"  ASR:      {type(asr_model).__module__}.{type(asr_model).__name__}")

    # Check what methods are available
    print(f"\n--- Diarizer API (public methods) ---")
    diar_methods = [m for m in dir(diar_model) if not m.startswith('_') and callable(getattr(diar_model, m, None))]
    for m in sorted(diar_methods):
        if m in ('diarize', 'forward', 'transcribe', 'predict_step', 'infer_diarize'):
            print(f"  {m}")

    print(f"\n--- ASR API (public methods) ---")
    asr_methods = [m for m in dir(asr_model) if not m.startswith('_') and callable(getattr(asr_model, m, None))]
    for m in sorted(asr_methods):
        if m in ('transcribe', 'forward', 'predict_step', 'change_decoding_strategy'):
            print(f"  {m}")

    print("\n" + "=" * 70)
    print("DONE. Document findings in docs/nemo-api-notes.md")


if __name__ == "__main__":
    main()
