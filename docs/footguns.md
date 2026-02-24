# Footguns

Cross-domain pitfalls verified against the codebase. Each has Symptoms, Why, and Prevention.

---

## FG-1: CUDA Graph Workaround (PyTorch 2.8)

**Symptoms:** ASR inference crashes with `ValueError: not enough values to unpack (expected 6, got 5)`.

**Why:** PyTorch 2.8.0a0 (shipped in NeMo 25.09 container) changed the `cu_call()` return signature from 6 to 5 values. The RNNT decoder's CUDA graph capture path still expects 6. This only affects the multitalker Parakeet model — Sortformer is unaffected.

**Prevention:** After loading the ASR model, disable CUDA graphs (`nemo_pipeline.py:129-131`):

```python
self._asr_model.decoding.decoding.use_cuda_graph_decoder = False
self._asr_model.decoding.decoding.decoding_computer.disable_cuda_graphs()
```

If upgrading NeMo container version, verify this workaround is still needed (test `asr_model.transcribe()` without the disable calls). See `docs/nemo-api-notes.md` §9 for container version matrix.

---

## FG-2: GPU Exclusivity Violation

**Symptoms:** CUDA out-of-memory errors during inference. NeMo models fail to load. Sporadic GPU segfaults.

**Why:** Sortformer (~1.2 GB) + Parakeet (~4.5 GB) consume 11.3 GB at load, leaving only ~5 GB headroom on a 16 GB card. VRAM grows with audio length (9 GB at 30s → 15.6 GB at 520s). Adding any other GPU workload (Ollama, another model) will OOM.

**Prevention:**
- Role inference agent MUST use Bedrock (cloud) or Ollama (CPU-only), never a local GPU model
- `docker-compose.yml:44-56` reserves the GPU exclusively for the `nemo-agent` service
- `ROLE_AGENT_MODEL_PROVIDER` must be `bedrock` or `ollama`, never `local`
- Monitor VRAM with `nvidia-smi` — if approaching 15 GB, the audio buffer flush strategy triggers

---

## FG-3: set_speaker_targets() Overwrite

**Symptoms:** Manually set speaker masks have no effect on ASR output. Transcription always runs in single-speaker mode with an all-ones mask.

**Why:** The `EncDecMultiTalkerRNNTBPEModel.transcribe()` method internally calls `_transcribe_forward()`, which unpacks speaker targets from the dataloader batch, overwriting any masks set via `set_speaker_targets()`. The public API doesn't accept external speaker masks.

**Prevention:** Do NOT try to inject speaker masks via `set_speaker_targets()` + `transcribe()`. Instead, use the `SpeakerTaggedASR` composite pipeline from `nemo.collections.asr.parts.utils.multispk_transcribe_utils`, which handles mask injection internally via the streaming buffer. See `docs/nemo-api-notes.md` §3 for the recommended approach.

---

## FG-4: Session ID Coupling (PHP → Browser → Python → Mercure)

**Symptoms:** Segments appear in NeMo logs but never reach the browser. Mercure SSE subscription receives no events. Role updates are published but UI doesn't update.

**Why:** The session ID flows through four layers and must match exactly:

1. **PHP generates it:** `ScribeController.php:54` — `Uuid::v4()->toRfc4122()`
2. **Twig passes it to JS:** `index.html.twig:146` — `sessionId: '{{ session_id }}'`
3. **JS uses it in WebSocket URL:** `index.html.twig:297` — `/ws/transcribe/${CONFIG.sessionId}`
4. **Python publishes to Mercure with it:** `server.py:317` — `scribe/session/{session_id}/raw`
5. **Browser subscribes to Mercure with it:** `index.html.twig:368-369` — same topic strings

If any layer uses a different ID (or URL-encodes it differently), events publish to one topic but the browser subscribes to another.

**Prevention:**
- Session IDs are UUIDs — no URL encoding issues, no special characters
- PHP sets both `mercure_topic_raw` and `mercure_topic_roles` server-side (`ScribeController.php:62-63`) to ensure topic strings match
- When debugging "events not arriving," compare the exact topic string in Python publish logs vs browser EventSource subscription URL

---

## FG-5: ffmpeg Conversion Silent Failure

**Symptoms:** NeMo processes audio but returns empty or garbled transcription. No error in logs. Audio buffer grows but segments are empty.

**Why:** `TranscriptionSession._convert_webm_to_wav()` (`nemo_session.py:233-280`) calls ffmpeg as a subprocess. If ffmpeg is not installed in the container, or the WebM container header is corrupt (partial first chunk), ffmpeg fails. The method returns `None` on failure, and the calling code skips the chunk — silently.

**Prevention:**
- `docker/nemo/Dockerfile` must install ffmpeg in the NeMo container
- The WebM accumulator (`nemo_session.py:142` — `_webm_accumulator: bytearray`) accumulates ALL chunks before converting, not individual chunks. This ensures the WebM container header (from the first chunk) is always present
- ffmpeg stderr is logged at DEBUG level (`nemo_session.py` ffmpeg.completed log) — set `LOG_LEVEL=DEBUG` when debugging audio issues
- Add a health check that verifies `ffmpeg -version` succeeds in the container

---

## FG-6: Mercure JWT Secret Mismatch

**Symptoms:** Mercure returns 401 Unauthorized on publish. Events never reach the browser. Python logs show `mercure.publish.failed`.

**Why:** Three components must share the same JWT signing secret:
1. **Mercure hub** — `MERCURE_PUBLISHER_JWT_KEY` and `MERCURE_SUBSCRIBER_JWT_KEY` (`docker-compose.yml:126-127`)
2. **Python publisher** — `MERCURE_JWT` env var, used in `Authorization: Bearer` header (`server.py:204`)
3. **PHP** — `MERCURE_JWT_SECRET` for signing subscriber JWTs (`docker-compose.yml:105`)

The Python publisher JWT must be pre-signed with the same secret the Mercure hub expects. The JWT itself is a different value from the secret — it's a compact JWS token signed with the secret.

**Prevention:**
- In `docker-compose.yml`, `MERCURE_JWT_SECRET` feeds both the Mercure hub's key config and PHP's signing
- The Python service receives a pre-generated JWT (`MERCURE_PUBLISHER_JWT`) — this must be generated from the same secret
- When changing `MERCURE_JWT_SECRET` in `.env`, regenerate `MERCURE_PUBLISHER_JWT` too
- Secret must be ≥32 characters (HS256 requirement)

---

## FG-7: WebSocket-to-Mercure Topic Synchronization

**Symptoms:** Browser receives stale or duplicate segments. Segments arrive out of order. Role updates reference segments the browser hasn't seen yet.

**Why:** The WebSocket connection and Mercure SSE subscription are independent channels with no ordering guarantee. The browser connects the WebSocket and subscribes to Mercure separately. If Mercure SSE reconnects (network blip), it may miss events. If the Python server publishes to Mercure before the browser has subscribed, those events are lost.

**Prevention:**
- The Twig template subscribes to Mercure BEFORE opening the WebSocket (`index.html.twig` — `subscribeToMercure()` is called during initialization, WebSocket opens on user action)
- `StreamOrchestrator` (`index.html.twig:181-190`) handles SSE reconnection with exponential backoff
- Each segment carries `start_time` and `end_time` — the UI can deduplicate and sort by timestamp regardless of arrival order
- The `finalized` event type signals end-of-session so the browser knows when to stop expecting events
