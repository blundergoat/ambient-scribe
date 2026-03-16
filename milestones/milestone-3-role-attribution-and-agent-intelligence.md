# Milestone 3 — Role Attribution + Agent Intelligence

**Timeline:** Weekend 3 (~5-6 hours)
**Status:** In Progress (tool wired, Ollama default fixed, tests added; live session verification pending)
**Dependencies:** Milestone 2 complete, Milestone 2.5 recommended (accurate transcription makes role inference more reliable)

---

## Objective

Replace raw `spk_0`/`spk_1` labels with context-appropriate roles using Strands agent reasoning. The system supports 6 modes — role inference adapts to the selected mode. Local-first: Ollama is the default, Bedrock is opt-in for cloud deployments.

### Modes and Role Labels

| Mode | Speaker A | Speaker B | Additional |
|---|---|---|---|
| Medical | Doctor | Patient | Nurse, Family Member |
| Meeting | Organiser | Participant | — |
| Interview | Interviewer | Candidate | — |
| TV/Media | Host | Guest | Commentator |
| Lecture | Lecturer | Student | — |
| General | Speaker A | Speaker B | Speaker C, D... |

---

## Tasks

### 3.1 Strands Role Inference Agent

- [x] `create_role_inference_agent()` factory with model provider selection (Bedrock/Ollama)
- [x] System prompt for DOCTOR/PATIENT reasoning (medical mode)
- [x] **Make system prompt mode-aware** — 6 mode-specific prompts in `ROLE_PROMPTS` dict, `create_role_inference_agent(mode=...)` with `@lru_cache(maxsize=6)`
- [x] **Wire `assign_roles` as a real Strands `@tool`** — `@tool`-decorated function in `tools/assign_roles.py`, passed to `Agent(tools=[assign_roles])`. Dual-path worker detects tool invocation vs free-text fallback
- [x] **Fix agent JSON parsing** — `json.loads(str(result))` fails if agent includes preamble. Add regex JSON extraction fallback
- [x] **Cap `mapping_history` to last 5 entries** in agent prompt (currently grows unboundedly)
- [x] **Increase transcript context** — currently capped at 2000 chars. Add "first 500 chars" (conversation opening) plus "last 3000 chars" (recent context)

### 3.2 Local-First Role Inference

- [x] **Default to Ollama** in `.env.example` and `docker-compose.yml`
- [x] **Test and document a specific Ollama model** — default aligned to `qwen2.5:14b` (supports tool calling, already pulled). Documented in `docs/footguns.md` FG-10
- [x] **3-tier fallback:** LLM agent → heuristic keyword classifier → None (graceful degradation)
- [x] Heuristic classifier: mode-specific keyword matching (medical/meeting/interview/general)
- [x] Ollama support exists in `transcription_agent.py` via `_create_role_agent_model()`

### 3.3 Async Role Inference with Sequential Guarantees

- [x] Two Mercure topics: `scribe/session/{id}/raw` (immediate) + `scribe/session/{id}/roles` (async)
- [x] Per-session inference queue with `asyncio.Queue` — guarantees sequential agent calls per session
- [x] Queue drains and merges batched segments before processing
- [x] Role updates published to Mercure with mapping, confidence, flip_detected, reasoning
- [x] Worker cleanup on idle timeout (60s)
- [x] **Add max queue depth / backpressure** — prevent unbounded growth if agent is slower than NeMo (maxsize=50)
- [x] **Fix `asyncio.get_event_loop()` → `asyncio.get_running_loop()`** everywhere (deprecated in Python 3.10+)

### 3.4 Retire Legacy PHP Role SSE Path

> **Why:** Two competing role inference paths (Mercure queue + PHP `/roles/stream` SSE proxy) produce divergent mappings and duplicate LLM calls. This is documented in `docs/footguns.md` FG-2.

- [x] **Retire `POST /session/{id}/roles/stream`** as a live inference path (endpoint deleted)
- [x] Remove `RoleInferenceService::streamRoleInference()` — deleted `RoleInferenceResult` class and `streamRoleInference` method
- [x] Update `ScribeController::rolesStream()` to return current cached state instead of re-running inference
- [x] Update `docs/footguns.md` FG-2 to mark as resolved

### 3.5 Progressive Confidence UX

> **This is the demo's centrepiece.** The moment speakers transform from grey question marks to colour-coded Doctor/Patient labels is the "wow" moment.

- [x] Confidence badge: green (>=80%), amber (>=50%), grey (<50%)
- [x] `relabelSegments()` retroactively updates all DOM segments on role update
- [x] Inspector segment log updates retroactively
- [x] **Fix confidence calculation** — use last-5-readings window (replaces lifetime average that was permanently skewed by early low-confidence readings)
- [x] **Cold start animation** — segments fade from grey to colour-coded with 0.6s ease-out animation on relabel
- [x] **Flip notification** — toast "Speaker labels corrected" via `showToast()` when `flip_detected=true`
- [x] **Single speaker / silence** — `_session_has_multiple_speakers` skips inference for single-speaker sessions; empty segments not enqueued

### 3.6 Manual Speaker Override

> **Why:** In a real session, the user knows who is speaking. A single click to correct a wrong assignment is faster than waiting for the agent to figure it out. This also improves agent accuracy for the rest of the session.

- [x] Click a speaker label in the transcript to cycle through available roles for the current mode
- [x] Override sent to backend as ground truth — locks the mapping for that speaker (`POST /session/{id}/roles/override`)
- [x] Agent receives overrides as `confirmed_overrides` in its context — stops re-guessing confirmed speakers
- [x] Visual indicator on manually-confirmed segments (checkmark icon next to overridden labels)

### 3.7 Mode Passthrough

- [x] Browser sends selected mode to backend via WebSocket query parameter (`?mode=meeting`)
- [x] Agent system prompt dynamically includes mode-appropriate role names and reasoning signals
- [x] Backend publishes role labels matching the mode (agent receives mode-specific prompt + role instruction)
- [x] Frontend mode selector with 6 modes, role labels, and avatars already exists

### 3.8 Tests

- [x] `test_role_inference.py` — RoleMappingState, flip detection, session store
- [x] `test_inference_queue.py` — sequential processing, role publication, single-speaker skip
- [x] `test_concurrent_sessions.py` — multi-session isolation
- [x] `ScribeControllerTest.php` — 12 test methods covering index, history, roles, error handling
- [x] `RoleInferenceServiceTest.php` — streaming, cancellation, error handling, mapping lookup
- [x] Test `assign_roles` tool with known segments → verify mapping structure (`TestAssignRolesTool`: 4 tests covering structure, persistence, field preservation, flip detection)
- [x] Test flip detection: send mapping A then reversed → verify `flip_detected=True` (`test_flip_detection_on_role_swap`, `test_no_flip_on_first_mapping`, `test_no_flip_on_same_mapping`)
- [x] Test 3+ speakers: verify the third speaker gets an appropriate role (`test_three_speakers_mapping`)
- [x] Test mode-specific prompts: verify ROLE_PROMPTS dict has correct role names per mode

---

## Exit Criteria

- [x] Roles correctly assigned in a live local session (Ollama, no AWS) — verified qwen2.5:14b tool invocation for medical + meeting modes
- [x] Single role delivery path (Mercure queue only, legacy SSE retired)
- [x] Mode-aware: selected mode determines role labels in agent reasoning and UI
- [x] Confidence threshold reachable (last-5 window, not lifetime average)
- [x] Manual override works: click → lock → agent respects
- [ ] 3+ speakers handled: additional speakers get appropriate labels per mode
- [x] Progressive confidence UX: grey → amber → green with animated relabel

---

## Latency Budget

| Step | Expected | Notes |
|---|---|---|
| Audio chunk (browser → WebSocket) | ~50ms | Network negligible on localhost |
| NeMo processing (diarize + transcribe) | 1-3s | GPU inference in thread pool |
| Mercure publish (raw segments) | ~50ms | HTTP POST to local hub |
| **Raw segment visible in browser** | **~1-3s** | User-perceived latency |
| Role inference (Ollama CPU) | 3-10s | CPU-bound, depends on model size |
| Role inference (Bedrock) | 1-3s | Network-bound |
| Mercure publish (role update) | ~50ms | |
| **Role label update in browser** | **~4-13s** | Acceptable — it's a refinement, not primary display |
