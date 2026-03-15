# Milestone 3 — Role Attribution + Agent Intelligence

**Timeline:** Weekend 3 (~5-6 hours)
**Status:** In Progress (queue + Mercure role publishing implemented; agent wiring, local-first, and UX pending)
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
- [ ] **Make system prompt mode-aware** — pass selected mode from browser → WebSocket → agent. Each mode gets a tailored prompt with appropriate role names and reasoning signals
- [ ] **Wire `assign_roles` as a real Strands `@tool`** — currently `tools=[]`, tool is called as helper functions after agent returns. Wiring it gives the agent access to mapping history during reasoning
- [ ] **Fix agent JSON parsing** — `json.loads(str(result))` fails if agent includes preamble. Add regex JSON extraction fallback
- [ ] **Cap `mapping_history` to last 5 entries** in agent prompt (currently grows unboundedly)
- [ ] **Increase transcript context** — currently capped at 2000 chars. Add "first 500 chars" (conversation opening) plus "last 3000 chars" (recent context)

### 3.2 Local-First Role Inference

- [ ] **Default to Ollama** in `.env.example` and `docker-compose.yml`
- [ ] **Test and document a specific Ollama model** — benchmark latency (CPU inference on 64GB system) and quality (does the model correctly identify roles?)
- [ ] **3-tier fallback:** Ollama → Bedrock (if configured) → heuristic rule-based classifier (keyword matching as last resort)
- [ ] Heuristic classifier: question-asking patterns → interviewer/doctor/host, symptom descriptions → patient, etc.
- [x] Ollama support exists in `transcription_agent.py` via `_create_role_agent_model()`

### 3.3 Async Role Inference with Sequential Guarantees

- [x] Two Mercure topics: `scribe/session/{id}/raw` (immediate) + `scribe/session/{id}/roles` (async)
- [x] Per-session inference queue with `asyncio.Queue` — guarantees sequential agent calls per session
- [x] Queue drains and merges batched segments before processing
- [x] Role updates published to Mercure with mapping, confidence, flip_detected, reasoning
- [x] Worker cleanup on idle timeout (60s)
- [ ] **Add max queue depth / backpressure** — prevent unbounded growth if agent is slower than NeMo
- [ ] **Fix `asyncio.get_event_loop()` → `asyncio.get_running_loop()`** everywhere (deprecated in Python 3.10+)

### 3.4 Retire Legacy PHP Role SSE Path

> **Why:** Two competing role inference paths (Mercure queue + PHP `/roles/stream` SSE proxy) produce divergent mappings and duplicate LLM calls. This is documented in `docs/footguns.md` FG-2.

- [ ] **Retire `POST /session/{id}/roles/stream`** as a live inference path — keep `GET /session/{id}/roles` as a snapshot-only endpoint
- [ ] Remove `RoleInferenceService::streamRoleInference()` or gate it behind a debug flag
- [ ] Update `ScribeController::rolesStream()` to return current cached state instead of re-running inference
- [ ] Update `docs/footguns.md` FG-2 to mark as resolved

### 3.5 Progressive Confidence UX

> **This is the demo's centrepiece.** The moment speakers transform from grey question marks to colour-coded Doctor/Patient labels is the "wow" moment.

- [x] Confidence badge: green (>=80%), amber (>=50%), grey (<50%)
- [x] `relabelSegments()` retroactively updates all DOM segments on role update
- [x] Inspector segment log updates retroactively
- [ ] **Fix confidence calculation** — use EWMA or last-5-readings window (currently simple lifetime average that's permanently skewed by early low-confidence readings)
- [ ] **Cold start animation** — segments fade from grey to colour-coded when confidence threshold crosses 0.8
- [ ] **Flip notification** — brief toast: "Speaker labels corrected" when `flip_detected=true`
- [ ] **Single speaker / silence** — maintain existing mapping, skip inference during silence (integrates with M2.5 VAD gating)

### 3.6 Manual Speaker Override

> **Why:** In a real session, the user knows who is speaking. A single click to correct a wrong assignment is faster than waiting for the agent to figure it out. This also improves agent accuracy for the rest of the session.

- [ ] Click a speaker label in the transcript to cycle through available roles for the current mode
- [ ] Override sent to backend as ground truth — locks the mapping for that speaker
- [ ] Agent receives overrides as `confirmed_mapping` in its context — stops re-guessing confirmed speakers
- [ ] Visual indicator on manually-confirmed segments

### 3.7 Mode Passthrough

- [ ] Browser sends selected mode to backend (via WebSocket connect message or session config endpoint)
- [ ] Agent system prompt dynamically includes mode-appropriate role names and reasoning signals
- [ ] Backend publishes role labels matching the mode (not hardcoded DOCTOR/PATIENT)
- [x] Frontend mode selector with 6 modes, role labels, and avatars already exists

### 3.8 Tests

- [x] `test_role_inference.py` — RoleMappingState, flip detection, session store
- [x] `test_inference_queue.py` — sequential processing, role publication, single-speaker skip
- [x] `test_concurrent_sessions.py` — multi-session isolation
- [x] `ScribeControllerTest.php` — 12 test methods covering index, history, roles, error handling
- [x] `RoleInferenceServiceTest.php` — streaming, cancellation, error handling, mapping lookup
- [ ] Test `assign_roles` tool with known segments → verify mapping structure
- [ ] Test flip detection: send mapping A then reversed → verify `flip_detected=True`
- [ ] Test 3+ speakers: verify the third speaker gets an appropriate role
- [ ] Test mode-specific prompts: Medical vs Meeting vs General produce different role labels

---

## Exit Criteria

- [ ] Roles correctly assigned in a live local session (Ollama, no AWS)
- [ ] Single role delivery path (Mercure queue only, legacy SSE retired)
- [ ] Mode-aware: selected mode determines role labels in agent reasoning and UI
- [ ] Confidence threshold reachable (EWMA, not lifetime average)
- [ ] Manual override works: click → lock → agent respects
- [ ] 3+ speakers handled: additional speakers get appropriate labels per mode
- [ ] Progressive confidence UX: grey → amber → green with animated relabel

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
