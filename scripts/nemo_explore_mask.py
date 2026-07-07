#!/usr/bin/env python3
"""Explore how diarization masks feed into multitalker ASR."""

# ruff: noqa: E402
import os
import urllib.request

import torch

audio_path = "/tmp/nemo_sample.wav"
if not os.path.exists(audio_path):
    urllib.request.urlretrieve(
        "https://dldata-public.s3.us-east-2.amazonaws.com/2086-149220-0033.wav",
        audio_path,
    )

device = torch.device("cuda")

# Load diarizer
from nemo.collections.asr.models import SortformerEncLabelModel

diar = (
    SortformerEncLabelModel.from_pretrained(
        "nvidia/diar_streaming_sortformer_4spk-v2.1"
    )
    .eval()
    .to(device)
)

# Load ASR
from nemo.collections.asr.models.multitalker_asr_models import (
    EncDecMultiTalkerRNNTBPEModel,
)

asr = (
    EncDecMultiTalkerRNNTBPEModel.from_pretrained(
        "nvidia/multitalker-parakeet-streaming-0.6b-v1"
    )
    .eval()
    .to(device)
)
asr.decoding.decoding.use_cuda_graph_decoder = False
asr.decoding.decoding.decoding_computer.disable_cuda_graphs()

# Read the multitalker mixin source to understand mask handling
src_file = "/usr/local/lib/python3.12/dist-packages/nemo/collections/asr/parts/mixins/multitalker_asr_mixins.py"
print(f"=== Reading {src_file} ===")
with open(src_file) as f:
    content = f.read()

# Find the _transcribe_forward method
import re

# Print the _transcribe_forward and _transcribe_input_processing methods
for method_name in [
    "_transcribe_forward",
    "_transcribe_input_processing",
    "_transcribe_on_begin",
]:
    pattern = rf"(    def {method_name}\(.*?\n)((?:        .*\n)*)"
    match = re.search(pattern, content)
    if match:
        full = match.group(0)
        # Print first 50 lines
        lines = full.split("\n")[:50]
        print(f"\n--- {method_name} (first 50 lines) ---")
        for line in lines:
            print(line)
    else:
        print(f"\n{method_name}: not found")

# Also check the diarize method for include_tensor_outputs
print("\n\n=== Testing diarize with include_tensor_outputs=True ===")
with torch.inference_mode():
    tensor_output = diar.diarize(
        audio=audio_path, batch_size=1, include_tensor_outputs=True
    )

print(f"type(tensor_output) = {type(tensor_output)}")
if isinstance(tensor_output, tuple):
    print(f"len(tensor_output) = {len(tensor_output)}")
    for i, item in enumerate(tensor_output):
        print(f"  [{i}] type={type(item)}")
        if isinstance(item, list):
            print(f"      len={len(item)}")
            if len(item) > 0:
                print(f"      [0] type={type(item[0])}")
                if isinstance(item[0], torch.Tensor):
                    print(f"      [0] shape={item[0].shape}")
                elif isinstance(item[0], list):
                    print(
                        f"      [0] len={len(item[0])}, first={item[0][:3] if item[0] else 'empty'}"
                    )
                else:
                    s = str(item[0])
                    print(f"      [0] = {s[:200]}")
        elif isinstance(item, torch.Tensor):
            print(f"      shape={item.shape}")
        elif isinstance(item, dict):
            print(f"      keys={list(item.keys())}")
            for k, v in item.items():
                if isinstance(v, torch.Tensor):
                    print(f"        {k}: shape={v.shape}, dtype={v.dtype}")
                elif (
                    isinstance(v, list)
                    and len(v) > 0
                    and isinstance(v[0], torch.Tensor)
                ):
                    print(
                        f"        {k}: list of {len(v)} tensors, [0].shape={v[0].shape}"
                    )
                else:
                    s = str(v)
                    print(f"        {k}: {s[:200]}")
elif isinstance(tensor_output, list):
    print(f"len(tensor_output) = {len(tensor_output)}")
    for i, item in enumerate(tensor_output):
        print(f"  [{i}] type={type(item)}")
        if isinstance(item, torch.Tensor):
            print(f"      shape={item.shape}, dtype={item.dtype}")
        elif isinstance(item, list):
            print(f"      len={len(item)}")
else:
    print(f"Unexpected: {str(tensor_output)[:500]}")

print("\n=== DONE ===")
