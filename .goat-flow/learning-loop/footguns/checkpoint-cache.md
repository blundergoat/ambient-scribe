---
category: checkpoint-cache
last_reviewed: 2026-07-18
---

# Checkpoint Cache Footguns

These traps affect post-visit ASR checkpoints stored inside NeMo containers.
Use this file before recreating the GPU service or promising local-only model availability.
The clinician impact is silent fallback from the corrected transcript to the live preview.

## Footgun: A healthy image rollback does not restore a container-writable checkpoint cache

**Status:** active | **Created:** 2026-07-18 | **Evidence:** OBSERVED
**hallucination-risk:** high

**Symptoms:** The recreated NeMo service is healthy and live Sortformer/Multitalker models load, but the next
stopped visit cannot resolve its pinned post-visit TDT checkpoint and falls back to the live transcript.

**Why it happens:** `strands_agents/post_visit_correction.py` (`_verified_checkpoint_path`) requires the
exact checkpoint through `hf_hub_download(..., local_files_only=True)`. The `nemo-agent` service in
`docker-compose.yml` (search: `volumes:` below `nemo-agent:`) persists `/data` and bind-mounts `/app`, but does
not persist `/root/.cache/huggingface`. A checkpoint downloaded into the container writable layer disappears
when `docker compose up --force-recreate` replaces that container. Retagging the prior image restores image
layers, not the deleted writable cache.

**Evidence:** A unified-runtime compatibility preflight failed with
`Pinned post-visit ASR checkpoint is unavailable from local cache` before model load/decode, and
reproduced the same local-only failure after an exact old-image rollback. The preflight receipt
was a local-only artifact; the durable anchor is the checkpoint-load path in
`strands_agents/post_visit_correction.py` (search: `_load_post_visit_asr_model`).

**Prevention:** Before any NeMo recreate, prove every required local-only checkpoint is in an image layer or
an explicitly persistent mounted cache. Treat image identity, live-model health, and post-visit checkpoint
availability as three separate gates. If writable-cache loss is found, stop before decoding and obtain exact
approval for a pinned cache restoration; never enable floating fallback or infer availability from health.
