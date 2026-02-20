# Milestone 3 — Role Attribution + Agent Intelligence

**Timeline:** Weekend 3 (~4-5 hours across 2 sessions)
**Status:** Not Started
**Dependencies:** Milestone 2 complete (end-to-end audio pipeline with `spk_0`/`spk_1` working)

---

## Objective

Replace raw `spk_0`/`spk_1` with DOCTOR/PATIENT using Strands agent reasoning. Implement progressive confidence with retroactive relabeling. This is the demo's centrepiece and the strongest content for the blog.

---

## Tasks

### 3.1 Strands Role Inference Agent (Session A, ~2 hours)

- [ ] Create `strands_agents/transcription_agent.py`:
  ```python
  from strands import Agent

  transcription_agent = Agent(
      model="bedrock/anthropic.claude-3-5-sonnet",  # GPU is reserved for NeMo — agent uses Bedrock
      system_prompt="""You are a medical transcription agent.

  You receive transcript segments with speaker labels (spk_0, spk_1).
  Your job is to determine which speaker is the DOCTOR and which is the PATIENT.

  Reasoning signals:
  - Doctors ask clinical questions, use medical terminology, give instructions
  - Patients describe symptoms, ask about treatment, express concerns
  - Doctors typically speak first in a consultation (greeting, opening)
  - Medical jargon density is higher for the doctor

  Edge cases:
  - If only one speaker is present for an extended period, maintain the existing
    mapping. Do NOT reassign roles based on a monologue.
  - If diarization labels flip (a known Sortformer issue), detect the flip by
    comparing speech content against established role patterns. Report the correction.
  - During silence or minimal speech, return the existing mapping unchanged
    with the same confidence level.

  Maintain your speaker->role mapping across the session. If you become more
  confident over time, update the mapping.

  You MUST respond with valid JSON only. No explanation text outside the JSON.
  """,
      tools=[assign_roles_tool],
  )
  ```

- [ ] **Clarify the `assign_roles` tool boundary.** This tool does **programmatic state management**, not LLM reasoning (the agent's system prompt handles reasoning). The tool:
  1. Persists the speaker→role mapping across invocations
  2. Detects label flips by comparing new mapping against mapping history
  3. Returns structured output (Pydantic model, not free-text)
  4. Tracks confidence over time (running average, not per-invocation)

  ```python
  # strands_agents/tools/assign_roles.py
  from pydantic import BaseModel

  class RoleMapping(BaseModel):
      mapping: dict[str, str]           # {"spk_0": "DOCTOR", "spk_1": "PATIENT"}
      attributed_segments: list[dict]   # segments with roles applied
      confidence: float                 # 0.0-1.0, running average
      flip_detected: bool               # True if labels swapped since last call
      reasoning: str                    # Brief explanation

  @tool
  def assign_roles(
      segments: list[dict],
      current_mapping: dict | None,
      mapping_history: list[dict],
      session_transcript_so_far: str
  ) -> RoleMapping:
      """
      State management tool for role assignment. The LLM decides the roles;
      this tool persists the mapping, detects flips, and returns structured output.

      Args:
          segments: New segments with spk_0/spk_1 labels
          current_mapping: Previous mapping or None (cold start)
          mapping_history: List of all previous mappings with timestamps
          session_transcript_so_far: Accumulated context (last 2000 chars)
      """
  ```

### 3.2 Async Role Inference with Sequential Guarantees

> **Key design decision:** Role inference runs asynchronously, separate from the raw transcript stream. This keeps latency low and makes the progressive confidence UX natural.
>
> **But it must be sequenced per session.** Fire-and-forget `asyncio.create_task` per chunk creates race conditions: two concurrent agent calls reading/writing `role_mapping` simultaneously causes mapping flips and inconsistent UI relabels.

- [ ] **Two Mercure topic pattern:**
  - `scribe/session/{id}/raw` — immediate `spk_0`/`spk_1` segments from NeMo (low latency)
  - `scribe/session/{id}/roles` — role attribution updates from Strands agent (higher latency, ok)
- [ ] Raw segments published immediately after NeMo processing (no agent in the hot path)
- [ ] **Per-session inference queue** (not fire-and-forget):
  ```python
  import asyncio
  from collections import defaultdict

  # One queue per session — guarantees sequential agent calls per session
  # while allowing concurrent inference across different sessions
  _inference_queues: dict[str, asyncio.Queue] = defaultdict(asyncio.Queue)
  _inference_workers: dict[str, asyncio.Task] = {}

  async def enqueue_role_inference(session_id: str, new_segments: list[Segment]):
      """Add segments to the session's inference queue. Non-blocking."""
      await _inference_queues[session_id].put(new_segments)

      # Start worker if not already running
      if session_id not in _inference_workers:
          _inference_workers[session_id] = asyncio.create_task(
              _role_inference_worker(session_id)
          )

  async def _role_inference_worker(session_id: str):
      """Processes role inference requests sequentially for a single session."""
      queue = _inference_queues[session_id]
      while True:
          segments = await asyncio.wait_for(queue.get(), timeout=60.0)

          try:
              result = await asyncio.get_event_loop().run_in_executor(
                  None,  # default executor
                  lambda: transcription_agent.invoke(
                      message=f"Assign roles: {json.dumps(segments)}",
                      session_id=session_id,
                      context={
                          "current_mapping": sessions[session_id].role_mapping,
                          "mapping_history": sessions[session_id].mapping_history,
                          "transcript_so_far": sessions[session_id].transcript[-2000:],
                      }
                  )
              )
              sessions[session_id].role_mapping = result["mapping"]
              sessions[session_id].mapping_history.append(result["mapping"])

              await publish_to_mercure(f"scribe/session/{session_id}/roles", {
                  "mapping": result["mapping"],
                  "attributed_segments": result["attributed_segments"],
                  "confidence": result["confidence"],
                  "flip_detected": result.get("flip_detected", False),
              })
          except asyncio.TimeoutError:
              break  # No more work for 60s, clean up
          except Exception as e:
              logger.error("role_inference_failed", session_id=session_id, error=str(e))
              # Raw segments remain visible — graceful degradation
  ```
- [ ] Wire into the WebSocket handler:
  ```python
  segments = await loop.run_in_executor(nemo_executor, session.process_chunk, audio_chunk)

  # Publish raw segments immediately (hot path, low latency)
  for segment in segments:
      await publish_to_mercure(f"scribe/session/{session_id}/raw", segment.dict())

  # Enqueue role inference (sequential per session, non-blocking)
  await enqueue_role_inference(session_id, segments)
  ```

### 3.3 Progressive Confidence UX

- [ ] **Cold start (first 2-3 chunks, confidence < 0.8):**
  - Show segments as `SPEAKER 1` / `SPEAKER 2` with muted styling
  - Display indicator: "Identifying speakers..."
- [ ] **Confidence threshold crossed (confidence >= 0.8):**
  - Switch to `DOCTOR` / `PATIENT` labels
  - Retroactively relabel previous segments via a Mercure `role_update` event
  - Animate the transition (fade from grey to colour-coded)
  - Display indicator: "Roles identified (high confidence)"
- [ ] **Sortformer label flip detection:**
  - If `flip_detected` is true in the role update, browser swaps all labels for affected segments
  - Show brief notification: "Speaker labels corrected"
- [ ] **Single-speaker / silence handling:**
  - If only one speaker detected for 30+ seconds, maintain existing mapping at current confidence
  - Do not reduce confidence during monologues — absence of turn-taking is not evidence of wrong mapping
  - If extended silence, skip agent invocation entirely (nothing to infer)

### 3.4 Colour-Coded Transcript UI (Session B, ~1-2 hours)

- [ ] Role-specific styling:
  ```css
  .segment--DOCTOR {
      border-left: 3px solid #2563eb;  /* blue */
      background: #eff6ff;
  }
  .segment--PATIENT {
      border-left: 3px solid #16a34a;  /* green */
      background: #f0fdf4;
  }
  .segment--UNKNOWN {
      border-left: 3px solid #9ca3af;  /* grey */
      opacity: 0.7;
  }
  ```
- [ ] Dark mode variants for all role colours

- [ ] Confidence badge in the UI header:
  - `"Identifying speakers..."` (grey, pulsing)
  - `"Roles identified"` (green, with confidence percentage)

- [ ] Segment rendering handles both raw and role-attributed data:
  - Initially renders with `spk_0`/`spk_1` and `segment--UNKNOWN` class
  - When role update arrives, finds matching segments by timestamp and relabels
  - Assigns `data-segment-id` attributes for efficient DOM updates

### 3.5 Session Controls via Symfony

- [ ] `ScribeController.php`:
  ```php
  #[Route('/scribe', name: 'scribe_index')]
  public function index(): Response
  {
      $sessionId = Uuid::v4()->toRfc4122();
      return $this->render('scribe/index.html.twig', [
          'session_id' => $sessionId,
          'ws_url' => $this->getParameter('nemo_websocket_url'),
          'mercure_topic_raw' => "scribe/session/{$sessionId}/raw",
          'mercure_topic_roles' => "scribe/session/{$sessionId}/roles",
      ]);
  }

  #[Route('/scribe/{sessionId}/history', name: 'scribe_history')]
  public function history(string $sessionId): JsonResponse
  {
      $response = $this->strandsClient->invoke(
          message: 'Return the full session transcript',
          sessionId: $sessionId,
      );
      return $this->json($response->getContent());
  }
  ```
- [ ] Pass both Mercure topics to the Twig template
- [ ] Browser subscribes to both SSE topics simultaneously

### 3.6 Agent Error Handling

- [ ] If Strands agent call fails, raw `spk_0`/`spk_1` segments remain visible (graceful degradation)
- [ ] Log agent errors with correlation ID and timing for debugging
- [ ] If agent is consistently slow (>5s), the queue naturally batches — next dequeue gets accumulated segments
- [ ] Add timeout on agent invocation (10 second max)
- [ ] Clean up inference workers and queues on WebSocket disconnect

### 3.7 Tests for Role Inference

- [x] Create `tests/python/test_role_inference.py` (structural tests from scaffold):
  - [x] Test `RoleMappingState` initial state, update, running confidence
  - [x] Test no flip on first mapping or same mapping
  - [x] Test `get_or_create_state` and `cleanup_session` for session store
  - [x] Test `RoleMapping` dataclass creation
  - [ ] Test `assign_roles` tool with known segments → verify mapping structure
  - [ ] Test flip detection: send mapping A, then reversed mapping → verify `flip_detected=True`
  - [ ] Test progressive confidence: sequential invocations → verify confidence increases
  - [ ] Test single-speaker input: only `spk_0` segments → verify stable mapping
- [ ] Create `tests/python/test_inference_queue.py`:
  - Test sequential processing: enqueue 3 items → verify processed in order
  - Test worker cleanup: no work for 60s → verify worker terminates
  - Test concurrent sessions: two sessions → verify independent queues
- [ ] Add `tests/php/Controller/ScribeControllerTest.php`:
  - Test `GET /scribe` returns page with session ID and topic URLs
  - Test `GET /scribe/{id}/history` calls Strands client correctly

---

## Exit Criteria

- [ ] Strands agent receives raw segments and returns DOCTOR/PATIENT attributed text
- [ ] Role mapping improves over first 2-3 chunks (progressive confidence)
- [ ] UI shows colour-coded DOCTOR/PATIENT transcript with timestamps
- [ ] Raw segments appear immediately; role labels arrive asynchronously (no added latency on hot path)
- [ ] **Role inference is sequenced per session** (no race conditions on shared mapping)
- [ ] Agent handles Sortformer label flips gracefully (detects and corrects)
- [ ] **Single-speaker and silence handled without mapping degradation**
- [ ] Full demo: start recording -> see speakers identified -> watch attributed transcript build in real-time
- [ ] **Python and PHP tests pass**

---

## Latency Budget

| Step | Expected | Notes |
|---|---|---|
| Audio chunk (browser -> WebSocket) | ~50ms | Network negligible on localhost |
| NeMo processing (diarize + transcribe) | 1-3s | GPU inference in thread pool, depends on chunk size |
| Mercure publish (raw segments) | ~50ms | HTTP POST to local hub |
| **Raw segment visible in browser** | **~1-3s** | This is the user-perceived latency |
| Strands agent call (role inference) | 1-5s | LLM inference via Bedrock (not GPU — NeMo owns GPU) |
| Mercure publish (role update) | ~50ms | |
| **Role label update in browser** | **~2-8s** | Acceptable — it's a refinement, not the primary display |

---

## Key Risks

| Risk | Mitigation |
|---|---|
| Agent latency makes role updates feel disconnected | Show clear "identifying..." state so users understand it's working. Queue naturally batches. |
| Agent hallucinates roles with low context | Require confidence >= 0.8 before switching from UNKNOWN. Let uncertainty be visible. |
| Strands + Ollama too slow locally | Agent uses Bedrock, not local GPU. For fully-offline dev, use CPU-only Ollama with small model (slow but functional). |
| Sortformer label flips confuse the agent | Flip detection in tool, history tracking in mapping. System prompt includes explicit flip handling instructions. |
| Race conditions on role mapping | Per-session sequential queue, not fire-and-forget tasks. |
| Single speaker for extended period | Agent maintains existing mapping. No inference during silence. |
| WebSocket has no auth | Acknowledged. Fine for PoC. Document as known limitation. Production would need token-based auth. |
