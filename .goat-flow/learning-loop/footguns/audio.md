---
category: audio
hallucination-risk: high
last_reviewed: 2026-07-07
---

# Audio Pipeline Footguns

## Footgun: PCM byte offsets must be sample-aligned or NeMo rejects the buffer
**Status:** active | **Created:** 2026-07-05 | **Evidence:** ACTUAL_MEASURED

- **Files:** `strands_agents/nemo_session.py` (search: "window_start_byte = int(window_start_seconds * 16000) * 2")
- **Files:** `strands_agents/nemo_session.py` (search: "def audio_from")
- **What breaks:** The stream is 16 kHz 16-bit PCM, so every sample is 2 bytes. Any byte offset computed as `int(seconds * 32000)` can land on an odd byte for fractional segment times; slicing there splits a sample in half and NeMo's `np.frombuffer(dtype=int16)` raises `ValueError: buffer size must be a multiple of element size`, killing the live WebSocket loop mid-session (observed ~40-70s in, whenever the emission mark first hit an odd offset).
- **Evidence:** `websocket.error ValueError: buffer size must be a multiple of element size` reproduced on a demo replay after windowed emission landed; fixed by computing offsets in whole samples (`int(seconds * 16000) * 2`) and hardening `audio_from` to even starts/lengths, with the parity regression pinned in `tests/python/test_nemo_session.py` (search: "test_window_audio_stays_sample_aligned").
- **Prevention:** Compute PCM offsets in samples and multiply by the sample width - never in raw bytes from a float. Any new slicing of buffered audio must keep both the start offset and the slice length even.

## Footgun: Audio format is a browser/env/Python contract
**Status:** active | **Created:** 2026-03-21 | **Evidence:** ACTUAL_MEASURED

- **Files:** `public/js/scribe-streaming.js` (search: "class PcmStreamer")
- **Files:** `public/js/scribe-recording.js` (search: "new WebSocket(`${CONFIG.wsUrl}/ws/transcribe/")
- **Files:** `.env.example` (search: "NEMO_STREAM_INPUT_FORMAT=pcm")
- **Files:** `docker-compose.yml` (search: "NEMO_STREAM_INPUT_FORMAT=${NEMO_STREAM_INPUT_FORMAT:-pcm}")
- **Files:** `strands_agents/nemo_session.py` (search: "def _validate_audio_format")
- **What breaks:** The browser always streams 16 kHz PCM through `PcmStreamer`, while the server trusts `NEMO_STREAM_INPUT_FORMAT`. If env/config drifts to `webm`, live sessions fail on the first chunk. Historically NeMo received garbage audio and produced nonsensical transcriptions with no errors in logs.
- **Evidence:** `PcmStreamer` down-samples and emits 16-bit PCM bytes, Docker and `.env.example` expose `NEMO_STREAM_INPUT_FORMAT`, and `_validate_audio_format()` rejects mismatched magic bytes after the session starts.
