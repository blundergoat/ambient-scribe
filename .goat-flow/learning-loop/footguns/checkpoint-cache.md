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

**Evidence:**
`var/quality/0.5.0-m00b-unified-runtime-compatibility-20260718T072654Z/verification/m00b.5-tdt-preflight.txt`
(search: `Pinned post-visit ASR checkpoint is unavailable from local cache`) records the candidate failure
before model load/decode and the same local-only failure after exact old-image rollback.

**Prevention:** Before any NeMo recreate, prove every required local-only checkpoint is in an image layer or
an explicitly persistent mounted cache. Treat image identity, live-model health, and post-visit checkpoint
availability as three separate gates. If writable-cache loss is found, stop before decoding and obtain exact
approval for a pinned cache restoration; never enable floating fallback or infer availability from health.
