#!/usr/bin/env python3
"""Explore the NeMo diarization + ASR API surface in detail."""
import inspect
import os
import time
import urllib.request

import torch

# Download sample audio
audio_path = "/tmp/nemo_sample.wav"
if not os.path.exists(audio_path):
    urllib.request.urlretrieve(
        "https://dldata-public.s3.us-east-2.amazonaws.com/2086-149220-0033.wav",
        audio_path,
    )

device = torch.device("cuda" if torch.cuda.is_available() else "cpu")

# --- Load diarizer ---
from nemo.collections.asr.models import SortformerEncLabelModel

diar = SortformerEncLabelModel.from_pretrained(
    "nvidia/diar_streaming_sortformer_4spk-v2.1"
).eval().to(device)

print("=== diar.diarize signature ===")
sig = inspect.signature(diar.diarize)
for name, param in sig.parameters.items():
    default = param.default if param.default != inspect.Parameter.empty else "(required)"
    print(f"  {name}: {default}")

print("\n=== Diarization output ===")
with torch.inference_mode():
    output = diar.diarize(audio=audio_path, batch_size=1)

print(f"type(output) = {type(output)}")
print(f"len(output) = {len(output)}")
print(f"type(output[0]) = {type(output[0])}")
print(f"len(output[0]) = {len(output[0])}")
for seg in output[0]:
    print(f"  Segment: {repr(seg)}")

# --- Load ASR ---
from nemo.collections.asr.models.multitalker_asr_models import EncDecMultiTalkerRNNTBPEModel

asr = EncDecMultiTalkerRNNTBPEModel.from_pretrained(
    "nvidia/multitalker-parakeet-streaming-0.6b-v1"
).eval().to(device)

# Disable CUDA graphs
asr.decoding.decoding.use_cuda_graph_decoder = False
asr.decoding.decoding.decoding_computer.disable_cuda_graphs()

print("\n=== asr.transcribe signature ===")
sig = inspect.signature(asr.transcribe)
for name, param in sig.parameters.items():
    default = param.default if param.default != inspect.Parameter.empty else "(required)"
    print(f"  {name}: {default}")

# --- Test ASR with speaker mask ---
print("\n=== ASR without speaker mask (single speaker mode) ===")
with torch.inference_mode():
    result = asr.transcribe([audio_path])
print(f"type(result) = {type(result)}")
print(f"result[0].text = {result[0].text}")

# Explore the Hypothesis object
hyp = result[0]
print(f"\n=== Hypothesis object attributes ===")
for attr in dir(hyp):
    if not attr.startswith("_"):
        val = getattr(hyp, attr)
        if not callable(val):
            val_str = str(val)
            if len(val_str) > 200:
                val_str = val_str[:200] + "..."
            print(f"  {attr} = {val_str}")

# --- Check how multitalker ASR uses speaker masks ---
print("\n=== Exploring multitalker_asr_mixins ===")

# Look at the transcribe method override
from nemo.collections.asr.parts.mixins import multitalker_asr_mixins
src_file = inspect.getfile(multitalker_asr_mixins)
print(f"Source: {src_file}")

# Check _transcribe_forward method (the multitalker override)
if hasattr(asr, '_transcribe_forward'):
    sig = inspect.signature(asr._transcribe_forward)
    print(f"_transcribe_forward signature: {sig}")

# Check the config for multitalker transcription
if hasattr(asr, '_cfg'):
    cfg = asr._cfg
    if hasattr(cfg, 'num_speakers'):
        print(f"num_speakers config: {cfg.num_speakers}")

# --- Check SpeakerTaggedASR (composite pipeline) ---
print("\n=== Checking SpeakerTaggedASR availability ===")
try:
    from nemo.collections.asr.models import SpeakerTaggedASR
    print("SpeakerTaggedASR is available!")
    sig = inspect.signature(SpeakerTaggedASR.__init__)
    print(f"__init__ signature: {sig}")
except ImportError as e:
    print(f"SpeakerTaggedASR not available: {e}")

# Try alternative import paths
for path in [
    "nemo.collections.asr.models.speaker_tagged_asr",
    "nemo.collections.asr.models.multitalker_asr_models",
]:
    try:
        mod = __import__(path, fromlist=["SpeakerTaggedASR"])
        if hasattr(mod, "SpeakerTaggedASR"):
            print(f"Found SpeakerTaggedASR in {path}")
    except (ImportError, AttributeError):
        pass

# List all classes in multitalker_asr_models
import nemo.collections.asr.models.multitalker_asr_models as mtm
print(f"\nClasses in multitalker_asr_models:")
for name in dir(mtm):
    obj = getattr(mtm, name)
    if isinstance(obj, type):
        print(f"  {name}")

print("\n=== DONE ===")
