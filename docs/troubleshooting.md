# Troubleshooting

## NeMo Container Version Compatibility

### RTX 5080 (Blackwell, sm_120) + Streaming Sortformer v2.1

The RTX 5080 is a Blackwell GPU (compute capability sm_120). Two compatibility constraints determine the minimum NeMo container version:

1. **Streaming Sortformer v2.1** requires NeMo >= 2.5.0 (the `spkcache_len` parameter was added in that release). NeMo 24.12 ships v2.1.0, which is too old.
2. **Blackwell sm_120** requires PyTorch compiled with sm_120 support. NeMo 24.12 ships PyTorch with max sm_90.

**Result:** `nvcr.io/nvidia/nemo:24.12` fails on both counts.

**Solution:** Use the pinned project image base, `nvcr.io/nvidia/nemo:26.02`, with `nemo_toolkit[asr]==2.7.3`. Earlier 25.09 testing proved the minimum Blackwell direction, but the maintained Dockerfile now targets the released multitalker line instead of a moving NeMo main checkout.

**Symptoms with 24.12:**
- `SortformerModules.__init__() got an unexpected keyword argument 'spkcache_len'`
- `NVIDIA GeForce RTX 5080 Laptop GPU with CUDA capability sm_120 is not compatible with the current PyTorch installation`
- `WARNING: Detected NVIDIA GeForce RTX 5080 Laptop GPU GPU, which is not yet supported in this version of the container`

### CUDA Version Mismatch (General)

Host CUDA 13.1 is newer than the container's CUDA toolkit. Newer host drivers are backwards compatible, so this is usually fine. If NeMo has issues, check here first.

**Symptoms:**
- NeMo model loading fails with CUDA errors
- `RuntimeError: CUDA error: no kernel image is available for execution on the device`
- GPU detected but inference crashes

**Fix options:**
1. Use the pinned NeMo container in `docker/nemo/Dockerfile` (`nvcr.io/nvidia/nemo:26.02`)
2. Fall back to AWS g5.2xlarge spot instance (~$0.36/hr) if Blackwell issues persist

### References

- [NeMo NGC Container Tags](https://catalog.ngc.nvidia.com/orgs/nvidia/containers/nemo/tags)
- [GitHub Issue #14472 - spkcache_len error](https://github.com/NVIDIA-NeMo/NeMo/issues/14472)
- [nvidia/diar_streaming_sortformer_4spk-v2.1 on Hugging Face](https://huggingface.co/nvidia/diar_streaming_sortformer_4spk-v2.1)
