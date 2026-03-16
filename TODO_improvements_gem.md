# TODO: Architectural & Engineering Improvements — Ambient Scribe

This document outlines high-priority technical improvements to move **Ambient Scribe** from a high-quality prototype to a production-ready, scalable medical transcription system.

---

## 1. Audio Processing & GPU Efficiency

### [P0] Hybrid Sliding Window Buffer Strategy
*   **Current Issue:** `nemo_session.py` uses a "growing buffer" strategy where every 5s chunk triggers a full re-transcription of the entire session ($O(N^2)$ complexity).
*   **Implementation:**
    *   Modify `AudioBuffer` to support a `sliding_window(seconds=30, overlap=5)` method.
    *   Perform "Interim" transcription on the sliding window for immediate UI feedback.
    *   Perform a "Global" alignment pass every 60 seconds or at the end of the session to resolve long-term diarization consistency.
*   **Files:** `strands_agents/nemo_session.py`, `strands_agents/nemo_pipeline.py`.

### [P1] Dynamic Batching Worker
*   **Current Issue:** `NEMO_MAX_WORKERS` processes sessions in parallel threads, but doesn't batch them at the CUDA level. Three concurrent sessions result in three separate GPU kernel launches.
*   **Implementation:**
    *   Replace the `ThreadPoolExecutor` with a dedicated `BatchingWorker` thread.
    *   The worker should wait ~200ms to collect chunks from multiple active WebSockets.
    *   Concatenate audio into a single batch for `nemo_pipeline.transcribe_batch()`.
*   **Files:** `strands_agents/api/server.py`, `strands_agents/nemo_pipeline.py`.

### [P2] Per-Word Timestamp Alignment
*   **Current Issue:** Words are distributed across diarization segments "proportionally" based on segment duration. This is a heuristic and can lead to word-to-speaker misalignment if one speaker talks faster than the other.
*   **Implementation:**
    *   Update `NemoPipeline._parse_nemo_output` to extract word-level timestamps from the Parakeet model output.
    *   Perform a temporal join between word timestamps and Sortformer speaker segments.
*   **Files:** `strands_agents/nemo_pipeline.py`.

---

## 2. Agent Intelligence & Role Inference

### [P1] Incremental Speaker Profiling
*   **Current Issue:** Every role inference call sends the *entire* transcript to the LLM. This increases latency and token costs linearly as the session progresses.
*   **Implementation:**
    *   Introduce a `SpeakerProfile` in `RoleMappingState` (e.g., "spk_0: Uses clinical terms, asks about pain, opened the session").
    *   Update the Strands agent to maintain these profiles.
    *   Only send the *new* segments + existing profiles to the LLM for the next decision.
*   **Files:** `strands_agents/tools/assign_roles.py`, `strands_agents/agents/transcription_agent.py`.

### [P2] Multi-Turn Role Refinement
*   **Current Issue:** If the agent is "unsure" (low confidence), it currently just waits for more data.
*   **Implementation:**
    *   Add a `clarify_roles` tool that allows the agent to ask the UI to highlight a specific segment for the user to "Verify Speaker" manually.
*   **Files:** `strands_agents/agents/transcription_agent.py`, `public/js/scribe.js`.

---

## 3. Frontend & Resilience

### [P1] AudioWorklet Migration
*   **Current Issue:** `scribe.js` uses the deprecated `ScriptProcessorNode`, which runs on the main thread and can cause audio dropouts if the browser is busy rendering.
*   **Implementation:**
    *   Create `public/js/audio-processor.js` containing an `AudioWorkletProcessor`.
    *   Refactor `PcmStreamer` to use `AudioWorkletNode`.
*   **Files:** `public/js/scribe.js`, `templates/scribe/index.html.twig`.

### [P1] Write-Ahead Audio Logging (WAL)
*   **Current Issue:** Raw audio is buffered in Python RAM. If the service restarts, the session is unrecoverable even if the transcript segments were saved.
*   **Implementation:**
    *   Stream incoming WebSocket chunks directly to a temporary file or a `blob` storage (S3/Minio).
    *   Allow the `/session/{id}/history` endpoint to trigger a "Reprocess Full Session" pass using the stored raw audio.
*   **Files:** `strands_agents/api/server.py`, `strands_agents/storage.py`.

---

## 4. Observability & DevSecOps

### [P2] OpenTelemetry Integration
*   **Current Issue:** Tracing spans multiple languages (PHP -> Python) but relies on a manual `X-Correlation-ID`.
*   **Implementation:**
    *   Add `opentelemetry-instrumentation-fastapi` and `open-telemetry/opentelemetry-bundle` (PHP).
    *   Export traces to a collector (Jaeger/Honeycomb) to visualize the "Audio Chunk -> Mercure Publish" latency.

### [P3] Automated Evaluation Pipeline (RAGAS style)
*   **Current Issue:** Testing role inference accuracy is currently manual/anecdotal.
*   **Implementation:**
    *   Create a set of "Gold Standard" transcripts in `tests/fixtures/scribe/scenarios.json`.
    *   Implement a script to run the agent against these fixtures and calculate F1-scores for role attribution.
