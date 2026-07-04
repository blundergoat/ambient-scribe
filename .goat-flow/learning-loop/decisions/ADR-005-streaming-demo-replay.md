# ADR-005: Demo Replay Streams Through the Live Pipeline

**Date:** 2026-07-05
**Status:** Accepted

## Context

Demo replay originally uploaded the whole WAV to a batch endpoint (`POST /session/{id}/replay`), transcribed it in one NeMo `transcribe_file()` pass, and returned every segment upfront for the browser to reveal from the audio clock. That design accumulated three structural problems with observed evidence: PriMock WAVs exceeded PHP's upload ceiling, full-file NeMo forward passes hit `torch.OutOfMemoryError` on long fixtures, and an early stop required a `/replay/stop` reconciliation endpoint to make server history match what the user actually heard. It also meant the demo exercised a different code path than the product's core live-streaming flow.

## Decision

- The browser decodes the selected WAV locally (`OfflineAudioContext` at 16 kHz) and streams Int16 PCM chunks over the same `/ws/transcribe/{session_id}` WebSocket the microphone uses (`WavPcmStreamer` in `public/js/scribe-streaming.js`).
- Chunk pacing follows the audible `#replayAudio` clock, so NeMo only receives audio the user has heard; early stop flushes exactly the heard span and closes the socket, making server history correct by construction.
- Replay completion waits for the backend `finalized` Mercure event (with a 15s timeout fallback) before revealing post-visit actions.
- The batch replay surface is deleted end to end: FastAPI `/session/{id}/replay` + `/session/{id}/replay/stop`, `strands_agents/api/replay_session.py`, and the Symfony proxy routes. `POST /transcribe/file` remains as a test-only batch entry point.

## Consequences

- **Easier:** One transcription ingest path; the demo now exercises the real product flow (WebSocket ingest, streaming NeMo, Mercure fan-out, role queue) end to end. No PHP upload limits, no full-file GPU pass, no stop-reconciliation contract. Transcript text appears within seconds of pressing play instead of after a long "Processing WAV..." batch wait.
- **Harder:** Replay transcript rows arrive with real streaming latency (a few seconds behind the audio) rather than at exact batch timestamps, and the final utterance can finish transcribing during the drain phase after audio ends. Replay behavior is now coupled to WebSocket availability — a dead agent fails replay at socket-open instead of at upload.
