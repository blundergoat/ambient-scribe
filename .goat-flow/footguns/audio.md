---
category: audio
hallucination-risk: high
---

# Audio Pipeline Footguns

## Footgun: Audio format is a browser/env/Python contract

- **Files:** `public/js/scribe.js:193-255`
- **Files:** `public/js/scribe.js:367-383`
- **Files:** `.env.example:28-33`
- **Files:** `docker-compose.yml:57-63`
- **Files:** `strands_agents/nemo_session.py:250-268`
- **What breaks:** The browser always streams 16 kHz PCM through `PcmStreamer`, while the server trusts `NEMO_STREAM_INPUT_FORMAT`. If env/config drifts to `webm`, live sessions fail on the first chunk. Historically NeMo received garbage audio and produced nonsensical transcriptions with no errors in logs.
- **Evidence:** `PcmStreamer` down-samples and emits 16-bit PCM bytes, Docker and `.env.example` expose `NEMO_STREAM_INPUT_FORMAT`, and `_validate_audio_format()` rejects mismatched magic bytes after the session starts.
