---
category: audio
hallucination-risk: high
last_reviewed: 2026-07-25
---

# Audio Pipeline Footguns

## Footgun: Stereo browser fixtures overwrite the mono eval WAVs at the same path
**Status:** active | **Created:** 2026-07-22 | **Evidence:** ACTUAL_MEASURED
**Incident count:** 5 | **Latest occurrence:** 2026-07-25

- **Files:** `tests/fixtures/audio/README.md` (search: "Generate stereo fixtures")
- **Files:** `tests/fixtures/audio/development-corpus-0.5.0.json` (search: "58283edfe790ba2d")
- **Files:** `scripts/eval-fixtures.sh` (search: "16 kHz mono")
- **What breaks:** The documented stereo-fixture flow copies each stereo WAV over the top-level `tests/fixtures/audio/<name>.wav` so the browser Demo Audio picker plays it (the browser averages channels client-side). But the SAME top-level path is what `scripts/eval-fixtures.sh` and every replay harness stream server-side, and that lane requires 16 kHz MONO signed 16-bit PCM. After a stereo refresh, all replay campaigns fail fast with "fixture must be 16 kHz mono signed 16-bit PCM for the browser PCM path" - and any hash-comparison campaign would be invalid even if it ran, because the input bytes no longer match the frozen corpus manifest.
- **Evidence:** The 2026-07-22 flag-off comparison trio's first leg failed exactly this way; on-disk c02 WAV was 35,788,878 bytes / 2 channels (stereo copy dated 2026-07-21) vs the corpus-frozen mono 17,894,478 bytes sha256 `58283edf...`. Regenerating with `scripts/generate-demo-consultation-audio.py --force --include-primock57 --case ...` reproduced the frozen mono hashes byte-for-byte and left `generated-manifest.json` at its pinned sha `b1eea405...`. The 2026-07-24 d2c09 confidence-fallback probe then invoked `development-corpus.py` while the operator fixtures were intentionally restored to stereo; it correctly exited 2 on `size_drift` even though the probe's separately frozen mono chunk was exact. The corrected preflight preserved that rejection as non-applicable to the no-replay probe and independently required 10/10 stereo backups plus the isolated chunk hash. The reproducible anchors are the byte sizes and hash above plus `scripts/development-corpus.py` (search: `size_drift`); the probe receipts were local-only.
- **2026-07-25 recurrence:** The recovery-only replacement replay again ran `development-corpus.py` before `prepare-mono-fixtures.sh`; the validator exited 2 on the restored stereo c02 size before any Docker/GPU action. The failed preflight was preserved, then the documented stereo-backup check → mono preparation → manifest validation order passed. Run that order every time: restore mono fixtures with `scripts/generate-demo-consultation-audio.py --force --include-primock57 --case ...` before `scripts/development-corpus.py` validates them, never the reverse. (A local-only `prepare-mono-fixtures.sh` was used historically; it was never committed, so the generator above is the reproducible path.)
- **2026-07-25 closure recurrence:** The final closure audit re-ran the frozen-baseline `development-corpus.py --json` gate after the corpus-on evaluation had correctly restored the browser fixtures to stereo. The validator reported c02 `size_drift`: the active 35,788,878-byte stereo file was byte-identical to `tests/fixtures/audio/stereo/`, while the frozen manifest correctly expected the 17,894,478-byte mono evaluation file. All ten active WAVs matched their stereo copies. No fixture was mutated; the historical gate was recorded as non-reproducible in restored operator state rather than forcing an evaluation-only conversion during documentation closure.
- **Prevention:** After a stereo restore, prepare the mono eval fixtures before running an identity gate that invokes `development-corpus.py`; the gate otherwise correctly stops on `size_drift` before a replay. Then verify the target WAV's sha256 against `development-corpus-0.5.0.json` (or at minimum check channels==1 via `soundfile.info`). To restore the eval lane after a stereo refresh, back the stereo copies into `tests/fixtures/audio/stereo/` and rerun the mono generator for the affected cases; the mono output is deterministic against the frozen hashes. For documentation-only closure after restoration, do not convert fixtures merely to reproduce a historical gate: verify the active set matches the stereo copies and record the gate as a historical receipt gap.

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

## Footgun: AudioBuffer trims its head silently; whole-visit consumers corrupt output

**Status:** active | **Created:** 2026-07-07 | **Evidence:** OBSERVED

- **Files:** `strands_agents/nemo_session.py` (search: "def full_audio")
- **Files:** `strands_agents/nemo_session.py` (search: "def trimmed_seconds")
- **Files:** `strands_agents/api/server.py` (search: "retention window")
- **Files:** `strands_agents/post_visit_correction.py` (search: "confident_anchor_count < max")
- **What breaks:** `AudioBuffer` drops audio from the front once a visit exceeds `NEMO_BUFFER_MAX_DURATION` (default 900s), and `full_audio()` returns only the retained tail with no signal that anything is missing. Any consumer that pairs that audio with whole-visit state silently corrupts clinical output. The post-visit correction endpoint did exactly this (found via PR #3 bot review): it aligned tail-only second-pass ASR against the full-visit `live_segments` scaffold, and once tail anchors fell below the confidence gate, the proportional fallback spread tail words across every row of the visit - early history dropped or misattributed, then preferred by the summary. A sibling trap: the windowed emission shift used the REQUESTED window start while `audio_from()` clamps to the retained head, timestamping post-trim speech too early.
- **Prevention:** Any new consumer of buffered session audio (export, finalize, re-transcription, diagnostics) MUST check `buffer.trimmed_seconds` first and either scope its other inputs to the retained window or refuse with an explicit fallback. Current guards: the correction endpoint returns `_correction_unavailable_response` when trimmed (regression: `tests/python/test_post_visit_correction.py`, search: "falls_back_when_buffer_trimmed"), and the window shift clamps via `effective_window_start_seconds` (regression: `tests/python/test_nemo_session.py`, search: "uses_retained_start_after_trim").
