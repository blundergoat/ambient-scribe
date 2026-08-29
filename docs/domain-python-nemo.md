# Domain: Python / NeMo

Rules and patterns for the Python layer. This service owns the GPU, runs NeMo inference, manages WebSocket connections, and publishes to Mercure. For full NeMo API details, see `docs/nemo-api-notes.md`.

## GPU Rules (Non-Negotiable)

1. **NeMo owns the GPU exclusively.** No other GPU workload may run in the same container or on the same card.
2. **Role inference uses Bedrock (cloud) or Ollama (CPU).** Never a local GPU model. See `.goat-flow/learning-loop/footguns/runtime.md`.
3. **All NeMo inference runs in ThreadPoolExecutor.** GPU-bound work is synchronous - without `run_in_executor`, the async event loop freezes. See `api/server.py` (search: "nemo_executor = ThreadPoolExecutor") for the pool and its `run_in_executor(nemo_executor, ...)` call sites for usage.
4. **CUDA graph workaround is required.** After loading Parakeet, disable CUDA graphs. See `.goat-flow/learning-loop/footguns/runtime.md`.

## NeMo Models

| Model | Class | VRAM | Purpose |
|---|---|---|---|
| Sortformer | `SortformerEncLabelModel` | ~1.2 GB | Speaker diarization (who spoke when) |
| Parakeet | `EncDecMultiTalkerRNNTBPEModel` | ~4.5 GB | Multitalker ASR (what was said) |

Both loaded once at startup as a singleton (`NemoPipeline` in `nemo_pipeline.py`), shared across WebSocket sessions. Peak VRAM at load: 11.3 GB (69% of 16 GB).

## Audio Processing

1. **Browser sends raw PCM** chunks over WebSocket via `PcmStreamer` in `public/js/scribe-streaming.js`
2. **Server expects `input_format="pcm"` by default** and appends the bytes directly to `AudioBuffer`
3. **Growing buffer strategy:** re-process full buffered audio on each chunk via `TranscriptionSession.process_chunk()`
4. **AudioBuffer** (`nemo_session.py`) has a 15-minute safety cap to prevent unbounded memory growth
5. **Alternate WebM path still exists** in `TranscriptionSession._decode_webm_chunk()`, but it is not the current default and requires `NEMO_STREAM_INPUT_FORMAT=webm`

Any change to browser capture format, sample rate, or `NEMO_STREAM_INPUT_FORMAT` must update both sides of the contract together.

## Endpoint Contracts

### WebSocket

| Path | Protocol | Direction | Format |
|---|---|---|---|
| `/ws/transcribe/{session_id}` | WebSocket | Browser → Server | Binary ArrayBuffer (16kHz PCM by default; WebM only when explicitly configured) |

No server-to-client WebSocket messages - segments are delivered via Mercure SSE.

### HTTP

| Method | Path | Purpose |
|---|---|---|
| POST | `/transcribe/file` | Batch multipart upload; returns `{segments: [...], duration_ms: int}` |
| GET, POST | `/session/{id}/history` | Stored live transcript rows |
| GET, POST | `/session/{id}/roles` | Current mapping, e.g. `{mapping: {spk_0: "DOCTOR", ...}}` |
| POST | `/session/{id}/roles/override` | Persist a clinician's speaker-level role correction |
| POST | `/session/{id}/correction` | Run the post-stop second-pass ASR over retained audio |
| GET | `/session/{id}/corrected-transcript` | Corrected rows for QA scoring; same `segments` shape |
| POST | `/session/{id}/summary` | Generate the SOAP note |
| GET | `/health` | `{status: "ok"}`, or 503 with `{status: "degraded", ...}` when model loading failed |
| GET | `/agent/model-health` | Pre-flight check the browser uses before starting a visit |

Symfony proxies these from the browser origin under `/scribe/...` and `/session/...`; see
`docs/domain-php-symfony.md`.

### Pydantic Models

Defined in `api/server.py`. Key models:
- `Segment`: `{speaker: str, start_time: float, end_time: float, words: str}`
- `TranscribeFileResponse`: `{segments: list[Segment], duration_ms: int}` - the `/transcribe/file` response model

## Mercure Publishing

Published from `api/server.py` via `publish_to_mercure()` (search: "async def publish_to_mercure"):

| Topic | Event | When |
|---|---|---|
| `scribe/session/{id}/raw` | `segment` | After each NeMo inference with results |
| `scribe/session/{id}/raw` | `finalized` | On WebSocket close |
| `scribe/session/{id}/roles` | `role_update` | After Strands agent completes role inference |
| `scribe/session/{id}/summary` | `summary` | After the summary endpoint completes |

Requires `MERCURE_JWT` env var (pre-signed JWT). See `.goat-flow/learning-loop/footguns/runtime.md` for Mercure publish-failure debugging.

## Session Management

- `TranscriptionSession` (`nemo_session.py`) - per-WebSocket: audio buffer, accumulated transcript, chunk count
- `SessionStore` (`session.py`) - in-memory transcript history across sessions
- Sessions keyed by UUID (generated in PHP, passed through WebSocket URL)

## Testing

```bash
cd strands_agents && .venv/bin/pip install -r ../tests/python/requirements-dev.txt
.venv/bin/pytest ../tests/python/                  # Run all Python tests
.venv/bin/pytest ../tests/python/ -v               # Verbose output
.venv/bin/pytest ../tests/python/test_api.py       # Single test file
```

Test files: `tests/python/test_api.py`, `test_nemo_pipeline.py`, `test_nemo_session.py`, `test_role_inference.py`

Fixtures in `tests/python/conftest.py` - mock NeMo models and provide test audio files.

## Transcription Quality Evals

Use `scripts/eval-fixtures.sh --all` when a transcription, diarization, or role-label
change needs user-visible evidence. It streams the PriMock57 WAV fixtures through the same
WebSocket path as the browser, saves `history.json` plus `quality.json`, runs
`scripts/transcript-quality.py`, and appends `var/quality/trend.jsonl`.

The scorer reports word recall, 4-gram duplication, speaker-attribution accuracy overall
and outside overlap spans, residual phantom speaker IDs, and role-flip counts. It also
splits the diagnosis three ways: per-speaker purity and the free-role speaker oracle show
how badly diarization mixed the voices, while `best dyadic mapping accuracy` constrains
the oracle to one DOCTOR and one PATIENT - the ceiling any real role mapping can reach.
`role mapping headroom` (best dyadic minus visible attribution) is the honest recoverable
amount; the unconstrained `role mapping gap` exceeds it whenever one voice dominates both
speaker IDs, so use headroom when deciding whether role mapping or diarization is at fault.

Flip counters come from two places with different timing: `quality.json` is snapshotted at
WebSocket disconnect while the role worker may still be draining its tail batch, so the
`role-timeline.jsonl` built from server logs is the complete record. The eval runner passes
the quality record to `scripts/role-timeline.py`, which appends a
`role_timeline.quality_check` row comparing both counters and warns on mismatch.

A manual single-session run uses the saved history and quality record:

```bash
python3 scripts/transcript-quality.py --quality-json var/quality/runs/<run>/<fixture>/quality.json \
  var/quality/runs/<run>/<fixture>/history.json <cutoff_seconds> \
  tests/fixtures/audio/<fixture>.doctor.TextGrid \
  tests/fixtures/audio/<fixture>.patient.TextGrid
```

### Tracing one wrong Doctor/Patient row

Use this when someone reports a specific mislabeled transcript card, e.g. "the
doctor's question at 01:00 shows as Patient". Each step narrows which layer
produced the wrong label. `scripts/eval-fixtures.sh` writes every artifact
below automatically; the manual commands are for a browser session you drove
yourself. The agent container must run with `LOG_FORMAT=json` for steps 4-5.

1. **Timecode -> history row.** The card's timestamp is the row's `start` in
   seconds (01:00 -> ~60s). Fetch the stored history promptly (it is in-memory
   unless SQLite storage is enabled) and find rows near that time:

   ```bash
   # Session ID: dev panel State tab, or the WebSocket line in the agent log.
   curl -s "http://localhost:48082/scribe/<session-id>/history" -o /tmp/history.json
   python3 -c "import json;[print(s['start'],s['end'],s['speaker_id'],s.get('role')) \
     for s in json.load(open('/tmp/history.json'))['segments'] if 55<=s['start']<=85]"
   ```

2. **History row -> expected role.** Score the history against the fixture's
   TextGrids with the row artifact enabled; `expected_role` is the reference
   truth for each row, and `confidently_wrong` marks the rows that misled the
   clinician. `mapping_correctable` says whether the best valid global mapping
   would have fixed the row - if it is `false`, no speaker-level relabel can
   fix it and the row needs row-level attribution or uncertainty:

   ```bash
   python3 scripts/transcript-quality.py \
     --window-artifact /tmp/window-continuity.jsonl \
     --row-diagnostics-json /tmp/row-diagnostics.json \
     /tmp/history.json <cutoff_seconds> \
     tests/fixtures/audio/<fixture>.doctor.TextGrid \
     tests/fixtures/audio/<fixture>.patient.TextGrid
   python3 -c "import json;[print(r) for r in \
     json.load(open('/tmp/row-diagnostics.json'))['rows'] if 55<=r['start']<=85]"
   ```

3. **Row -> role decision.** The role timeline shows which mapping decision
   (accepted update, accepted/suppressed flip, fallback) was live when the row
   rendered, so you can see whether the label came from the agent's better or
   worse valid mapping:

   ```bash
   docker compose logs nemo-agent --since <window> \
     | python3 scripts/role-timeline.py <session-id> | head
   ```

4. **Row -> emission window.** The window artifact says which emission window
   published the row (`window_index` in the row diagnostics joins against it),
   what raw speaker IDs NeMo produced, how they were remapped to the visible
   IDs, and on what overlap-vote evidence. A row with `crosses_window_seam:
   true` or a window whose `mapping_reasons` show a swap/merge is speaker
   identity drift, not a role-agent mistake:

   ```bash
   docker compose logs nemo-agent --since <window> \
     | python3 scripts/window-continuity.py <session-id> > /tmp/window-continuity.jsonl
   ```

5. **Health check before trusting any of it.** Errors invalidate the run:

   ```bash
   docker compose logs nemo-agent --since <window> \
     | rg -n "websocket\.error|Traceback|CUDA error|AcceleratorError|max_tokens truncation"
   ```

`NEMO_SPEAKER_CAP` defaults to `2`, so normal doctor/patient visits merge stray
window-local speaker IDs back into stable visible identities. Set it to `0` only when
testing or demonstrating a true multi-party consultation.

The cap is a containment fallback for the transcript UI: it prevents `speaker_2+`
phantoms from reaching role state, but it does not prove that every merged row belongs
to the correct person. Use the non-overlap attribution metric above before accepting
any diarization or role-stability mechanism.

### Separated-channel ceiling caveat

`scripts/eval-channel-ceiling.py` exists only for targeted channel-ceiling diagnosis. It downloads
PriMock57 doctor/patient source channels, normalizes them to browser PCM, streams named
fixtures through the live WebSocket path, combines role-fixed histories, and scores them
with `scripts/transcript-quality.py`.

Do not run it as a routine full-corpus gate. During that work, `/transcribe/file` exceeded the
16 GB GPU budget on full-length separated channels, while WebSocket streaming of every
channel destabilized the shared NeMo singleton with Sortformer `KeyError` and ASR
`unfreeze()` errors. Use named fixtures only, grep `nemo-agent` logs after each run, and
restart `nemo-agent` before trusting later quality numbers if any NeMo internal error
appears. The current separated-channel result is not an accepted overlap ceiling.

## Feature Checklist

After implementing any Python feature, verify:

1. Pydantic models in `api/server.py` match the endpoint contract
2. Mercure topics follow the `scribe/session/{id}/{type}` pattern
3. GPU-bound work uses `run_in_executor(nemo_executor, ...)`
4. pytest tests cover the new code path
5. No GPU models added outside NemoPipeline
6. `composer preflight` passes (includes Python syntax check)
