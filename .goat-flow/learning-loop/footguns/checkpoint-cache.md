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

## Footgun: The readiness memo sat behind the work it was meant to skip

**Status:** active | **Created:** 2026-08-29 | **Evidence:** ACTUAL_MEASURED
**Decision changed:** before trusting a memo cache to bound a request's cost, read what runs *before* the
lookup; a cache placed after the expensive call bounds nothing.
**Trigger phase:** ACT

**Symptoms:** the pre-visit gate refuses to start a consultation with "agent unreachable" while the agent is
healthy and answers a direct request. Clicking Start again a moment later works. Nothing is logged as an
error, because from the agent's side every request succeeded.

**Why it happens:** `strands_agents/post_visit_correction.py` (search: `def correction_readiness`) called
the full verifier before reading `_CHECKPOINT_LOAD_PROBE_CACHE`, and that verifier hashes the entire
checkpoint. The memo therefore skipped only the model restore, never the digest, so every Start click paid a
SHA-256 over 2,474,055,680 bytes. `src/Controller/ScribeController.php` (search:
`scribe_model_health_proxy`) proxies that check with an 8-second idle timeout, and Symfony's transport
exception is mapped to `available: false` — so a slow-but-correct answer is indistinguishable from a dead
agent. Measured in `ambient-scribe-nemo-agent-1`: 1.40 s warm and 9.46 s on the first call in a process,
against an 8 s ceiling. `docker-compose.yml` runs uvicorn with `--reload` over a bind-mounted source tree,
so an ordinary source edit empties the memo and restores the cold cost.

**Prevention:** split identification from verification. Resolving the file
(search: `_resolved_checkpoint_path`) is a stat and a path lookup; proving it is a hash and a model build.
Key the memo on the cheap identity and let the expensive proof run once behind a non-blocking single flight
(search: `_CHECKPOINT_PROBE_LOCK`), so a second caller is told the check is still running instead of
queueing past the point the browser stops listening. After the split the warm gate measured 0.0003 s
in-process and 0.009 s end-to-end through the proxy. The first call in a fresh process still costs the full
proof, so a request-path gate is the wrong place to pay it: `strands_agents/api/server.py`
(search: `_prove_correction_readiness`) now pays it from a background task in `lifespan`, which brought
the first call after a restart from 9.3 s to 0.15 s.

**Also watch:** a verdict produced by a catch-all handler must not be memoised on a key as stable as a
checkpoint's identity. `_probe_checkpoint_loadability` now remembers only facts about the checkpoint and the
agent build; a `MemoryError` or `OSError` is a fact about the machine, and remembering it kept refusing
consultations long after an operator freed the space.

## Footgun: A plain refetch cannot repair a corrupt Hugging Face cache entry

**Status:** active | **Created:** 2026-08-29 | **Evidence:** ACTUAL_MEASURED
**Decision changed:** a download called to *repair* a failed integrity check must force the fetch; without
it the repair path is a no-op that reports success while changing nothing.
**Trigger phase:** ACT

**Symptoms:** startup refuses to proceed with "could not be restored - check network and disk space" on a
machine with working network and free disk, and it refuses identically on every retry.

**Why it happens:** `strands_agents/post_visit_correction.py` (search: `ensure_pinned_checkpoint_available`)
reaches its download specifically because the verifier rejected the cached file, but
`hf_hub_download` short-circuits when the pin's revision is a full commit hash: it returns the existing
pointer with no network request and no inspection of the file's contents. The corrupt bytes are handed
straight back to the verifier, which rejects them again. Reproduced this session against the real artifact:
flipping one byte left `ensure_pinned_checkpoint_available(allow_download=True)` blocked on two consecutive
calls at 2.4 s and 2.1 s — too fast to have transferred anything — while a forced fetch took 30.9 s and
repaired it.

**Prevention:** ask the cache whether an entry already exists (search: `try_to_load_from_cache`) and force
the refetch only in that case. Forcing unconditionally is the wrong fix: it destroys the partial-file resume
that a first 2.5 GB download depends on. Note that the error message points at network and disk, which is
exactly where an operator will not find the cause.

## Footgun: The PHP proxy undoes the agent's message sanitisation

**Status:** active | **Created:** 2026-08-29 | **Evidence:** OBSERVED
**Decision changed:** when one side of the PHP <-> Python boundary promises a sanitised message, check
what the other side appends to it before trusting that promise end to end.
**Trigger phase:** READ

**Symptoms:** the clinician's page shows an internal service name and port in a banner, on the exact path
that was designed to say nothing about the server's internals.

**Why it happens:** `strands_agents/post_visit_correction.py` (search: `def correction_readiness`)
guarantees a detail that "never contains a filesystem path or a raw exception message", and every error
it raises uses a fixed literal. But `src/Controller/ScribeController.php` caught the transport failure
and appended `$agentUnreachable->getMessage()` to the browser-facing `detail`, and Symfony embeds the
full target URL in that message. `public/js/scribe-output.js` (search: `ensureAiModelAvailable`) renders
the field verbatim into a page-level banner. The guarantee held on the side that wrote it down and was
lost one hop later, on the failure path most likely to fire.

**Prevention:** a sanitisation promise is a property of the whole path, not of the function that states
it. The controller now logs the transport message through its injected logger and returns a fixed
detail; a test pins that the model-health failure names neither the host nor the port
(search: `testModelHealthFailureNamesNoInternalAddress`). Grep the proxy layer for `getMessage()` in any
`catch` whose value reaches a response body before assuming a Python-side guarantee survives.
