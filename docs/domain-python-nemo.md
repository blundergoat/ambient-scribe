# Domain: Python / NeMo

Rules and patterns for the Python layer. This service owns the GPU, runs NeMo inference, manages WebSocket connections, and publishes to Mercure. For full NeMo API details, see `docs/nemo-api-notes.md`.

## GPU Rules (Non-Negotiable)

1. **NeMo owns the GPU exclusively.** No other GPU workload may run in the same container or on the same card.
2. **Role inference uses Bedrock (cloud) or Ollama (CPU).** Never a local GPU model. See `docs/footguns.md` FG-2.
3. **All NeMo inference runs in ThreadPoolExecutor.** GPU-bound work is synchronous — without `run_in_executor`, the async event loop freezes. See `api/server.py:131-134` for the executor and `:243-250`, `:307-311` for usage.
4. **CUDA graph workaround is required.** After loading Parakeet, disable CUDA graphs. See `docs/footguns.md` FG-1.

## NeMo Models

| Model | Class | VRAM | Purpose |
|---|---|---|---|
| Sortformer | `SortformerEncLabelModel` | ~1.2 GB | Speaker diarization (who spoke when) |
| Parakeet | `EncDecMultiTalkerRNNTBPEModel` | ~4.5 GB | Multitalker ASR (what was said) |

Both loaded once at startup as a singleton (`NemoPipeline` in `nemo_pipeline.py`), shared across WebSocket sessions. Peak VRAM at load: 11.3 GB (69% of 16 GB).

## Audio Processing

1. **Browser sends WebM/Opus** chunks over WebSocket (~1s intervals)
2. **Server accumulates** WebM bytes in `TranscriptionSession._webm_accumulator` (needs full container header)
3. **ffmpeg converts** accumulated WebM → 16kHz mono WAV via subprocess (`nemo_session.py:233-280`)
4. **Growing buffer strategy:** re-process full audio each chunk. VRAM grows with length (9 GB at 30s → 15.6 GB at 520s). Flush when approaching 15 GB.
5. **AudioBuffer** (`nemo_session.py:56-79`) has a 15-minute safety cap to prevent unbounded memory growth

See `docs/footguns.md` FG-5 for ffmpeg silent failure patterns.

## Endpoint Contracts

### WebSocket

| Path | Protocol | Direction | Format |
|---|---|---|---|
| `/ws/transcribe/{session_id}` | WebSocket | Browser → Server | Binary ArrayBuffer (WebM/Opus) |

No server-to-client WebSocket messages — segments are delivered via Mercure SSE.

### HTTP

| Method | Path | Request | Response |
|---|---|---|---|
| POST | `/transcribe/file` | Multipart file upload | `{segments: [...], duration_ms: int}` |
| GET | `/session/{id}/history` | — | `{segments: [...]}` |
| GET | `/session/{id}/roles` | — | `{mapping: {spk_0: "DOCTOR", ...}}` |
| GET | `/health` | — | `{status: "ok"}` |

### Pydantic Models

Defined in `api/server.py`. Key models:
- `Segment`: `{speaker: str, start_time: float, end_time: float, words: str}`
- `TranscribeResponse`: `{segments: list[Segment], duration_ms: int}`

## Mercure Publishing

Published from `api/server.py` via `publish_to_mercure()` (`server.py:187-213`):

| Topic | Event | When |
|---|---|---|
| `scribe/session/{id}/raw` | `segment` | After each NeMo inference with results |
| `scribe/session/{id}/raw` | `finalized` | On WebSocket close |
| `scribe/session/{id}/roles` | `role_update` | After Strands agent completes role inference |

Requires `MERCURE_JWT` env var (pre-signed JWT). See `docs/footguns.md` FG-6 for JWT mismatch issues.

## Session Management

- `TranscriptionSession` (`nemo_session.py`) — per-WebSocket: audio buffer, accumulated transcript, chunk count
- `SessionStore` (`session.py`) — in-memory transcript history across sessions
- Sessions keyed by UUID (generated in PHP, passed through WebSocket URL)

## Testing

```bash
cd strands_agents && pip install -r ../tests/python/requirements-dev.txt
pytest ../tests/python/                  # Run all Python tests
pytest ../tests/python/ -v               # Verbose output
pytest ../tests/python/test_api.py       # Single test file
```

Test files: `tests/python/test_api.py`, `test_nemo_pipeline.py`, `test_nemo_session.py`, `test_role_inference.py`

Fixtures in `tests/python/conftest.py` — mock NeMo models and provide test audio files.

## Feature Checklist

After implementing any Python feature, verify:

1. Pydantic models in `api/server.py` match the endpoint contract
2. Mercure topics follow the `scribe/session/{id}/{type}` pattern
3. GPU-bound work uses `run_in_executor(nemo_executor, ...)`
4. pytest tests cover the new code path
5. No GPU models added outside NemoPipeline
6. `composer preflight` passes (includes Python syntax check)
