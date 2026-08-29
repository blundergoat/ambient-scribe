---
category: checkpoint-cache
last_reviewed: 2026-08-29
---

# Checkpoint Cache Footguns

These traps affect post-visit ASR checkpoints stored inside NeMo containers.
Use this file before recreating the GPU service or promising local-only model availability.
The clinician impact is silent fallback from the corrected transcript to the live preview.

## Footgun: Pinning NeMo down to a released version silently disabled post-visit correction

**Status:** active | **Created:** 2026-08-29 | **Evidence:** OBSERVED
**hallucination-risk:** high

**Symptoms:** live transcription is entirely healthy - core `/health` returns 200 with
`models_loaded` true and Sortformer/Multitalker keep decoding - while every stopped visit falls back
to the live transcript. The correction event names a model-load failure that reads exactly like a
missing or corrupt checkpoint: `correction.unavailable ... reason_category=model_load_failed
attempts=0 chunk_count_planned=0`, raised in a few seconds with no chunk ever planned.

**Why it happens:** the pinned correction checkpoint and the installed NeMo are two independent
versions, and only one of them is pinned where anyone looks. `docker/nemo/Dockerfile` (search:
`nemo_toolkit[asr]==`) installs a released NeMo. The pinned `parakeet-unified-en-0.6b` checkpoint's
own `model_config.yaml` asks `nemo.collections.asr.modules.ConformerEncoder` for
`att_chunk_context_size`, which that released encoder does not accept, so Hydra raises
`TypeError: ConformerEncoder.__init__() got an unexpected keyword argument 'att_chunk_context_size'`
and the whole correction pass is reported as a model-load failure. The exact checkpoint verifier
passes immediately before this: revision, resolved file, byte size, and SHA-256 all match. Checkpoint
integrity and checkpoint loadability are separate facts, and the failure category conflates them.

**Evidence:** commit `89a4ace` (2026-07-04) replaced an install from NeMo `main` (2.8.0rc0+, whose
removed comment recorded why main was needed) with a released pin. Correction kept working for weeks
afterwards because the running image still carried the older build; the regression only became
reachable when the image was next rebuilt, roughly seven weeks later. A restored, exactly verified
checkpoint then still produced a live-fallback note on a full replay of the 544.32 s day1
consultation03 fixture. Live transcription was never affected, because the multitalker line the
Dockerfile comment describes is supported by the released version.

**Prevention:** treat the NeMo version as part of the checkpoint contract. A checkpoint pin that
records repository, revision, size, and hash still says nothing about which runtime can instantiate
it, so record the supported runtime alongside the pin and fail loudly when they disagree. When a
dependency pin changes, remember the running image is the last thing to find out: a version edit and
the rebuild that activates it can be separated by weeks, so a regression will not appear in the
change that caused it. `reason_category=model_load_failed` covers both "the bytes are wrong" and
"this runtime cannot read correct bytes"; read the underlying exception before concluding a cache
problem, and never infer post-visit capability from live-model health.

## Footgun: A healthy image rollback does not restore a container-writable checkpoint cache

**Status:** active | **Created:** 2026-07-18 | **Evidence:** OBSERVED
**hallucination-risk:** high

**Symptoms:** The recreated NeMo service is healthy and live Sortformer/Multitalker models load, but the next
stopped visit cannot resolve its pinned post-visit TDT checkpoint and falls back to the live transcript.

**Why it happens:** `strands_agents/post_visit_correction.py` (`_verified_checkpoint_path`) requires the
exact checkpoint through `hf_hub_download(..., local_files_only=True)`, reading only
`POST_VISIT_MODEL_CACHE_DIR` (search: `POST_VISIT_MODEL_CACHE_DIR`), which defaults to
`/data/post_visit_model_cache`. That path is on the named `session_data` volume mounted at `/data`
(`docker-compose.yml`, search: `session_data:/data`), so it survives an ordinary container recreate.
It does not survive the volume itself being deleted or replaced, and the same volume also holds
`sessions.db`, so a single volume removal takes stored visits and correction capability together.
Retagging the prior image restores image layers, never volume contents. The actor and moment of the
observed loss are unknown; treat "the cache is gone" as a fact to re-establish, not as a known event.

**Correction, 2026-08-29:** an earlier version of this entry blamed `/root/.cache/huggingface` and the
container writable layer. That was wrong. The correction module has never read that path, and a plain
`--force-recreate` does not lose the cache. The durable rule below is unchanged; only the mechanism is.

**Evidence:** A unified-runtime compatibility preflight failed with
`Pinned post-visit ASR checkpoint is unavailable from local cache` before model load/decode, and
reproduced the same local-only failure after an exact old-image rollback. The preflight receipt
was a local-only artifact; the durable anchor is the checkpoint-load path in
`strands_agents/post_visit_correction.py` (search: `_load_post_visit_asr_model`).

**Local mitigation shipped 2026-08-29:** `scripts/start-dev.sh` restores the pinned checkpoint through
`strands_agents/post_visit_correction.py` (search: `ensure_pinned_checkpoint_available`) before its Ready
banner, and that helper always finishes on the exact verifier. This closes the local recovery path only;
deployment packaging remains an open decision elsewhere.

**Prevention:** Before any NeMo recreate, prove every required local-only checkpoint is in an image layer or
an explicitly persistent mounted cache. Treat image identity, live-model health, and post-visit checkpoint
availability as three separate gates. If writable-cache loss is found, stop before decoding and obtain exact
approval for a pinned cache restoration; never enable floating fallback or infer availability from health.
