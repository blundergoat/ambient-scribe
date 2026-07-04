---
category: audio
hallucination-risk: high
last_reviewed: 2026-07-04
---

# Audio Pipeline Footguns

## Footgun: Audio format is a browser/env/Python contract
**Status:** active | **Created:** 2026-03-21 | **Evidence:** ACTUAL_MEASURED

- **Files:** `public/js/scribe-streaming.js` (search: "class PcmStreamer")
- **Files:** `public/js/scribe-recording.js` (search: "new WebSocket(`${CONFIG.wsUrl}/ws/transcribe/")
- **Files:** `.env.example` (search: "NEMO_STREAM_INPUT_FORMAT=pcm")
- **Files:** `docker-compose.yml` (search: "NEMO_STREAM_INPUT_FORMAT=${NEMO_STREAM_INPUT_FORMAT:-pcm}")
- **Files:** `strands_agents/nemo_session.py` (search: "def _validate_audio_format")
- **What breaks:** The browser always streams 16 kHz PCM through `PcmStreamer`, while the server trusts `NEMO_STREAM_INPUT_FORMAT`. If env/config drifts to `webm`, live sessions fail on the first chunk. Historically NeMo received garbage audio and produced nonsensical transcriptions with no errors in logs.
- **Evidence:** `PcmStreamer` down-samples and emits 16-bit PCM bytes, Docker and `.env.example` expose `NEMO_STREAM_INPUT_FORMAT`, and `_validate_audio_format()` rejects mismatched magic bytes after the session starts.
