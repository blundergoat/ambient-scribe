# TODO: Improvement Recommendations

Deep audit of the Ambient Scribe codebase — 82/100. Every finding below includes
exact file:line evidence and a clear rationale.

**Severity key:** CRITICAL | HIGH | MEDIUM | LOW

---

## Table of Contents

1. [Security: Authentication & Authorization](#1-security-authentication--authorization)
2. [Security: Rate Limiting](#2-security-rate-limiting)
3. [Security: Input Validation](#3-security-input-validation)
4. [Performance: O(n²) Buffer Reprocessing](#4-performance-on²-buffer-reprocessing)
5. [Performance: Sequential Mercure Publishing](#5-performance-sequential-mercure-publishing)
6. [Architecture: Split server.py (1,335 lines)](#6-architecture-split-serverpy-1335-lines)
7. [Architecture: DOCTOR/PATIENT Key Reuse Across Modes](#7-architecture-doctorpatient-key-reuse-across-modes)
8. [Testing: Missing Coverage](#8-testing-missing-coverage)
9. [CI/CD: Tests Not Running in Pipeline](#9-cicd-tests-not-running-in-pipeline)
10. [Frontend: Accessibility & Code Quality](#10-frontend-accessibility--code-quality)
11. [Docker & Deployment](#11-docker--deployment)
12. [Error Handling Gaps](#12-error-handling-gaps)
13. [Dead Code Cleanup](#13-dead-code-cleanup)
14. [Data Privacy & Compliance](#14-data-privacy--compliance)

---

## 1. Security: Authentication & Authorization

**Severity:** CRITICAL
**Effort:** Large
**Milestone target:** M5 (Cloud Deployment)

### Problem

Every endpoint — HTTP and WebSocket — is publicly accessible without any
authentication. Any client that knows (or guesses) a session UUID can read,
modify, or replay any session's transcript.

### Evidence

**Python endpoints (no `Depends(...)` or token checks):**

| Endpoint | File | Line |
|---|---|---|
| `POST /transcribe/file` | `strands_agents/api/server.py` | 576 |
| `WS /ws/transcribe/{session_id}` | `strands_agents/api/server.py` | 634 |
| `GET\|POST /session/{id}/history` | `strands_agents/api/server.py` | 794 |
| `POST /session/{id}/roles/override` | `strands_agents/api/server.py` | 826 |
| `POST /session/{id}/summary` | `strands_agents/api/server.py` | 877 |
| `POST /session/{id}/replay` | `strands_agents/api/server.py` | 976 |
| `GET\|POST /session/{id}/roles` | `strands_agents/api/server.py` | 1098 |
| `GET /health` | `strands_agents/api/server.py` | 1310 (intentionally public) |

**PHP endpoints (no firewall, no voter, no token):**

| Endpoint | File | Line |
|---|---|---|
| `GET /scribe` | `src/Controller/ScribeController.php` | 51 |
| `GET /scribe/{id}/history` | `src/Controller/ScribeController.php` | 100 |
| `GET /scribe/{id}/roles` | `src/Controller/ScribeController.php` | 132 |
| `GET /` | `src/Controller/ScribeController.php` | 151 |

**Mercure subscription:**

`docker-compose.yml:149` sets `anonymous` mode, allowing any browser to
subscribe to any topic without a JWT:

```yaml
MERCURE_EXTRA_DIRECTIVES: |
  anonymous
  cors_origins ${MERCURE_CORS_ORIGINS:-http://localhost:48082}
```

### Recommendation

1. **Short-term (before demo):** Add a shared secret/API key for the Python
   agent endpoints. Create a FastAPI dependency that checks `Authorization:
   Bearer <token>` on all non-health endpoints.

2. **Medium-term (M5):** Implement session-scoped JWTs. The PHP app issues a
   JWT when rendering `/scribe` (containing the `session_id` claim). The browser
   passes this JWT to both the WebSocket upgrade and Mercure subscription. Python
   validates the JWT and ensures the session_id in the URL matches the claim.

3. **Mercure:** Remove `anonymous` directive. Issue subscriber JWTs scoped to
   `scribe/session/{id}/*` topics — the PHP app can embed these in the Twig
   template alongside the session config.

4. **WebSocket:** Validate the JWT during the WebSocket handshake (before
   `websocket.accept()`). Reject connections with invalid/expired tokens.

### Impact

Without this, anyone on the same network (or the internet, once deployed) can:
- Listen to any active transcription session
- Override speaker roles on any session
- Trigger summary generation for any session
- Replay audio files through the GPU pipeline without authorization

---

## 2. Security: Rate Limiting

**Severity:** CRITICAL
**Effort:** Medium
**Milestone target:** M3.5 / M5

### Problem

Zero rate limiting exists anywhere in the codebase. A single malicious client
can exhaust GPU resources, fill session storage, or overwhelm Mercure.

### Evidence

- No FastAPI rate limiting middleware in `strands_agents/api/server.py` (lines 1-130)
- No Symfony rate limiter in `config/packages/` (only `framework.yaml`, `twig.yaml`, `mercure.yaml`, `strands.yaml` exist)
- `MAX_SESSIONS=100` (`strands_agents/session.py:38`) caps total sessions but has no per-IP limit
- `nemo_executor = ThreadPoolExecutor(max_workers=2)` (`server.py:202`) means 2 concurrent GPU jobs max, but unlimited queueing
- WebSocket handler (`server.py:634`) accepts unlimited connections
- `/transcribe/file` (`server.py:576`) accepts unlimited uploads with no file size check
- Replay tasks (`server.py:973`) accumulate in `_replay_tasks` dict without limit

### Recommendation

1. **FastAPI middleware:** Add `slowapi` or a custom middleware:
   - WebSocket connections: max 5 per IP per minute
   - `/transcribe/file`: max 10 requests per IP per minute, max 50MB file size
   - `/session/{id}/replay`: max 3 concurrent replays total
   - `/session/{id}/roles/override`: max 30 requests per session per minute

2. **GPU queue depth:** Add a max queue depth to `nemo_executor`. When the queue
   is full, return `HTTP 503 Service Unavailable` or send a WebSocket frame
   telling the client to back off. Currently requests queue indefinitely.

3. **WebSocket connection limit:** Cap concurrent WebSocket connections per IP
   (e.g., 3) and globally (e.g., 20). Reject with `4008 Too Many Connections`.

4. **File upload validation:**
   ```python
   MAX_UPLOAD_SIZE = 100 * 1024 * 1024  # 100MB
   content = await file.read(MAX_UPLOAD_SIZE + 1)
   if len(content) > MAX_UPLOAD_SIZE:
       raise HTTPException(413, "File too large")
   ```

---

## 3. Security: Input Validation

**Severity:** HIGH
**Effort:** Small
**Milestone target:** M3.5

### Problem

Several endpoints accept raw JSON or query parameters without Pydantic
validation. While `_validate_session_id()` checks UUID format, other inputs
are loosely validated.

### Evidence

**`POST /session/{id}/roles/override`** — `server.py:826-838`:

```python
body = await request.json()  # Raw JSON, no schema
speaker_id = str(body.get("speaker_id", ""))
role = str(body.get("role", "")).upper()

if not speaker_id or not role:
    raise HTTPException(status_code=400, detail="speaker_id and role required")
```

- No validation that `speaker_id` matches a known speaker (e.g., `spk_0`)
- No validation that `role` is a valid role for the current mode
- Accepts arbitrary strings as roles (could inject `<script>` tags if rendered)

**`POST /transcribe/file`** — `server.py:576-631`:

- No file size validation
- No file type validation (accepts any upload, not just WAV)
- `UploadFile` content is written directly to temp file without sanitization

**`WS /ws/transcribe/{session_id}`** — `server.py:652-653`:

```python
raw_mode = websocket.query_params.get("mode", "medical")
mode = raw_mode if raw_mode in VALID_MODES else "medical"
```

- Silently falls back to `"medical"` for invalid modes instead of rejecting
- No logging of invalid mode attempts

**`POST /session/{id}/replay`** — `server.py:976-981`:

- `speed` parameter has bounds (`ge=0.25, le=10.0`) via FastAPI Query — good
- `mode` parameter has no enum validation — falls back silently to `"medical"`
  at line 990

### Recommendation

Create Pydantic request models:

```python
from enum import Enum

class ScribeMode(str, Enum):
    medical = "medical"
    meeting = "meeting"
    interview = "interview"
    tv = "tv"
    lecture = "lecture"
    general = "general"

class RoleOverrideRequest(BaseModel):
    speaker_id: str = Field(..., pattern=r"^spk_\d+$")
    role: str = Field(..., min_length=1, max_length=50)

class ReplayRequest(BaseModel):
    speed: float = Field(1.0, ge=0.25, le=10.0)
    mode: ScribeMode = ScribeMode.medical
```

For file uploads, add size and type validation:
```python
if file.content_type not in ("audio/wav", "audio/x-wav", "audio/wave"):
    raise HTTPException(415, "Only WAV files accepted")
```

---

## 4. Performance: O(n²) Buffer Reprocessing

**Severity:** HIGH
**Effort:** Large
**Milestone target:** M2.5

### Problem

Each 5-second audio chunk triggers NeMo inference on the ENTIRE accumulated
session audio. With N chunks, the Nth chunk processes N×5 seconds of audio.
Total GPU work = 5 + 10 + 15 + ... + 5N = O(n²).

### Evidence

**Buffer accumulation** — `strands_agents/nemo_session.py:193-195`:

```python
self.buffer.append(pcm_audio)
result = self.pipeline.transcribe_buffer(self.buffer.current_window())
```

**`current_window()` returns ALL audio** — `nemo_session.py:82-92`:

```python
def current_window(self) -> bytes:
    """For a growing buffer strategy, this returns all accumulated audio."""
    return b"".join(self._chunks)
```

**`transcribe_buffer` writes full audio to WAV** — `nemo_pipeline.py:231-241`:

```python
pcm_array = np.frombuffer(audio_buffer, dtype=np.int16).astype(np.float32) / 32768.0
with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
    tmp_path = f.name
    soundfile.write(tmp_path, pcm_array, 16000)
return self.transcribe_file(tmp_path)
```

### Real-world impact

| Session length | Chunks | GPU time per chunk (RTF 0.022x) | Total GPU time |
|---|---|---|---|
| 1 min | 12 | 0.07s–1.3s | ~8s |
| 4 min | 48 | 0.07s–5.3s | ~127s |
| 10 min | 120 | 0.07s–13.2s | ~792s (13 min of GPU work) |
| 30 min | 360 | 0.07s–39.6s | ~7,128s (2 hours of GPU work) |

At ~4 minutes, inference time per chunk exceeds the 5-second chunk interval,
and the pipeline falls behind permanently.

### Recommendation

Implement a **sliding window with periodic reconciliation** (documented in
`milestones/milestone-2a-transcription-accuracy.md`):

1. **Sliding window:** Process only the last 30-60 seconds of audio per chunk
   (configurable). Emit segments for the current window only.

2. **Periodic full reconciliation:** Every N chunks (e.g., every 10th), run a
   full-audio pass to reconcile speaker identity across window boundaries.

3. **VRAM-aware buffer cap:** Monitor GPU memory via `nvidia-smi` (already
   called in `log_vram()` at `server.py:185-195`). If VRAM exceeds 80%, trim
   the buffer from the front.

4. **Differential segment emission:** Track which segments are "finalized" vs
   "interim" (the `is_interim` field exists but is never set — see item 13).
   Only emit new/changed segments, not the entire transcript.

This would change complexity from O(n²) to O(n) with O(1) per-chunk cost.

---

## 5. Performance: Sequential Mercure Publishing

**Severity:** MEDIUM
**Effort:** Small
**Milestone target:** M3.5

### Problem

Segments are published to Mercure one-at-a-time in a sequential loop, each
awaiting the HTTP response before publishing the next.

### Evidence

`strands_agents/api/server.py:704-716`:

```python
for segment in segments:
    segment_payload = segment.dict()
    segment_payloads.append(segment_payload)
    sessions.append_segment(session_id, segment_payload)
    _mercure_event_ids.setdefault(session_id, 0)
    _mercure_event_ids[session_id] += 1
    published = await publish_to_mercure(  # Awaits each one
        f"scribe/session/{session_id}/raw",
        {"type": "segment", **segment.dict()},
        event_id=_mercure_event_ids[session_id],
    )
```

With `MERCURE_PUBLISH_MAX_RETRIES = 3` and `MERCURE_PUBLISH_BACKOFF_SECONDS = 2.0`,
a single Mercure failure can block segment publishing for up to 14 seconds
(2 + 4 + 8 backoff), delaying ALL subsequent segments.

### Recommendation

1. **Batch publishing:** Combine multiple segments into a single Mercure POST:
   ```python
   payload = {"type": "segments_batch", "segments": segment_payloads}
   await publish_to_mercure(topic, payload, event_id=next_id)
   ```

2. **Fire-and-forget with queue:** Publish Mercure events via an async queue
   with a dedicated worker, rather than awaiting inline in the WebSocket handler.
   This decouples segment processing from Mercure availability.

3. **Concurrent publish:** If batching isn't possible (event_id ordering), use
   `asyncio.gather()` for parallel HTTP posts with ordered IDs.

---

## 6. Architecture: Split server.py (1,335 lines)

**Severity:** MEDIUM
**Effort:** Medium
**Milestone target:** M4 / M5

### Problem

`strands_agents/api/server.py` is a 1,335-line monolith containing endpoint
definitions, Mercure publishing, role inference worker, replay logic, summary
generation, heuristic inference, and utility functions.

### Evidence

| Logical group | Lines | Functions |
|---|---|---|
| Config & middleware | 1-210 | `CorrelationIdMiddleware`, `_resolve_mercure_jwt`, `_validate_session_id`, `log_vram` |
| Lifespan & cleanup | 218-295 | `lifespan()`, `_periodic_cleanup()` |
| Mercure publishing | 328-395 | `publish_to_mercure()` |
| Role inference worker | 398-569 | `enqueue_role_inference()`, `close_role_inference()`, `_role_inference_worker()` |
| HTTP endpoints | 576-874 | `transcribe_file()`, `transcribe_stream()`, `session_history()`, `roles_override()` |
| Summary generation | 877-970 | `generate_summary()`, `_run_summary_generation()` |
| Replay service | 973-1093 | `replay_file()`, `_replay_segments()` |
| Roles endpoint | 1098-1110 | `session_roles()` |
| Role inference logic | 1114-1308 | `_heuristic_role_inference()`, `_run_role_inference()` |
| Health check | 1310-1335 | `health()` |

### Recommendation

Extract into focused modules under `strands_agents/api/`:

```
strands_agents/api/
├── server.py              # FastAPI app, lifespan, middleware (~200 lines)
├── endpoints/
│   ├── transcribe.py      # /transcribe/file, /ws/transcribe/{id}
│   ├── session.py         # /session/{id}/history, /roles, /roles/override
│   ├── summary.py         # /session/{id}/summary
│   ├── replay.py          # /session/{id}/replay
│   └── health.py          # /health
├── mercure.py             # publish_to_mercure(), JWT resolution
├── role_inference.py      # Worker, queue, heuristic inference
└── dependencies.py        # Shared state (sessions, lifecycle, executor)
```

Use FastAPI `APIRouter` for each endpoint group. Shared state lives in
`dependencies.py` and is injected via `Depends()`.

---

## 7. Architecture: DOCTOR/PATIENT Key Reuse Across Modes

**Severity:** HIGH
**Effort:** Medium
**Milestone target:** M4

### Problem

The frontend uses `DOCTOR` and `PATIENT` as internal dictionary keys for ALL 6
modes. In meeting mode, the code maps `DOCTOR → 'Organiser'` and
`PATIENT → 'Participant'`. This creates a semantic mismatch: the backend
produces mode-specific roles (ORGANISER, INTERVIEWER, HOST, etc.) but the
frontend's color/avatar/label mapping only recognizes DOCTOR and PATIENT.

### Evidence

**Frontend MODES object** — `public/js/scribe.js:11-17`:

```javascript
const MODES = {
    medical:   { roleLabels: { DOCTOR: 'Doctor', PATIENT: 'Patient' }, ... },
    meeting:   { roleLabels: { DOCTOR: 'Organiser', PATIENT: 'Participant' }, ... },
    interview: { roleLabels: { DOCTOR: 'Interviewer', PATIENT: 'Candidate' }, ... },
    tv:        { roleLabels: { DOCTOR: 'Host', PATIENT: 'Guest' }, ... },
    lecture:   { roleLabels: { DOCTOR: 'Lecturer', PATIENT: 'Student' }, ... },
    general:   { roleLabels: { DOCTOR: 'Speaker A', PATIENT: 'Speaker B' }, ... },
};
```

**Color assignment hardcoded** — `scribe.js:934-937`:

```javascript
case 'DOCTOR': return 'var(--color-speaker-a)';
case 'PATIENT': return 'var(--color-speaker-b)';
```

**Backend produces mode-specific roles** — `transcription_agent.py:95-170`:

Medical mode prompt says "determine which speaker is the DOCTOR and which is
the PATIENT", but meeting mode says "determine which speaker is the ORGANISER
and which are PARTICIPANT speakers". The LLM will output `ORGANISER`, not
`DOCTOR`.

**The mapping mismatch:** When the backend returns `{"spk_0": "ORGANISER"}` in
meeting mode, the frontend tries `MODES.meeting.roleLabels["ORGANISER"]` which
is `undefined`. It falls back to the generic formatter, but colors and avatars
default to UNKNOWN styling.

### Recommendation

Use neutral internal keys:

```javascript
const MODES = {
    medical:   { roles: { PRIMARY: 'Doctor', SECONDARY: 'Patient' }, ... },
    meeting:   { roles: { PRIMARY: 'Organiser', SECONDARY: 'Participant' }, ... },
};
```

Map backend role strings to `PRIMARY`/`SECONDARY` via a per-mode lookup:

```javascript
const ROLE_MAP = {
    medical:   { DOCTOR: 'PRIMARY', PATIENT: 'SECONDARY', NURSE: 'TERTIARY' },
    meeting:   { ORGANISER: 'PRIMARY', PARTICIPANT: 'SECONDARY' },
    interview: { INTERVIEWER: 'PRIMARY', CANDIDATE: 'SECONDARY' },
    ...
};
```

This also makes it trivial to add new roles (NURSE, FAMILY_MEMBER, COMMENTATOR)
without touching the color/layout system.

---

## 8. Testing: Missing Coverage

**Severity:** HIGH
**Effort:** Medium
**Milestone target:** M3.5 / M4

### Problem

Several critical modules have no tests, and key integration paths are untested.

### Evidence

**Modules with ZERO test coverage:**

| Module | Lines | Why it matters |
|---|---|---|
| `strands_agents/session.py` | 207 | In-memory session store with TTL, LRU eviction, max_segments cap — all untested |
| `strands_agents/session_lifecycle.py` | 165 | Asyncio lock coordination, grace period, pending destroy cancellation — all untested |

**Endpoints with no integration tests:**

| Endpoint | Test file | Status |
|---|---|---|
| `WS /ws/transcribe/{id}` | None | WebSocket handler completely untested (TestClient doesn't support WS easily) |
| `POST /session/{id}/replay` | `test_replay.py` (16 lines) | Skeletal — only checks file processing, not pacing/speed/segment ordering |
| `POST /session/{id}/summary` | `test_summary.py` | Tests agent creation only, not the endpoint or Mercure publishing |
| `POST /session/{id}/roles/override` | None | Manual override endpoint untested |

**PHP test coverage:**

Only 13 test functions for the PHP layer (`tests/Unit/Controller/ScribeControllerTest.php`
and `tests/Unit/Service/RoleInferenceServiceTest.php`). While the PHP layer is
thin (198 lines), the `ScribeController.index()` method has scenario-loading
logic and template variable assembly that could regress.

### Recommendation

**Priority 1 — Session store tests (`test_session_store.py`):**

```python
def test_ttl_eviction_removes_expired_sessions(): ...
def test_lru_eviction_removes_oldest_when_at_capacity(): ...
def test_max_segments_cap_prevents_unbounded_growth(): ...
def test_get_transcript_text_truncates_long_transcripts(): ...
def test_apply_role_mapping_updates_stored_segments(): ...
def test_cleanup_removes_session_data(): ...
```

**Priority 2 — Session lifecycle tests (`test_session_lifecycle.py`):**

```python
async def test_register_cancels_pending_destroy(): ...
async def test_destroy_acquires_lock_and_cleans_up(): ...
async def test_schedule_destroy_fires_after_grace_period(): ...
async def test_concurrent_register_and_destroy_are_safe(): ...
async def test_lock_timeout_triggers_best_effort_cleanup(): ...
```

**Priority 3 — WebSocket integration tests:**

Use `httpx.AsyncClient` with `ASGITransport` or a real WebSocket test client
to test the full WebSocket lifecycle: connect → send chunks → receive segments
→ disconnect → grace period → reconnect.

**Priority 4 — Contract tests between PHP and Python:**

Validate that the PHP `StrandsClient` calls match what the Python API expects:
- `/session/{id}/history` — response shape matches what ScribeController parses
- `/session/{id}/roles` — response shape matches what RoleInferenceService parses

---

## 9. CI/CD: Tests Not Running in Pipeline

**Severity:** HIGH
**Effort:** Small
**Milestone target:** M4

### Problem

The GitHub Actions deployment workflow (`deploy-prod.yml`) builds and deploys
without running any tests. A broken test suite would deploy to production.

### Evidence

`.github/workflows/deploy-prod.yml` (227 lines):

- Lines 49-61: Validates AWS env vars
- Lines 73-88: Builds Docker image
- Lines 90-122: Registers + updates ECS task
- Lines 124-187: Verifies deployment + health check
- **No `pytest` step. No `composer test` step. No `composer preflight` step.**

`.github/workflows/context-validation.yml` (68 lines):

- Only validates CLAUDE.md structure — no code quality checks

### Recommendation

Add a `test` job that runs before `deploy`:

```yaml
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      # PHP tests
      - uses: shivammathur/setup-php@v2
        with: { php-version: '8.3' }
      - run: composer install --no-interaction
      - run: composer preflight

      # Python tests
      - uses: actions/setup-python@v5
        with: { python-version: '3.11' }
      - run: pip install -r strands_agents/requirements.txt -r tests/python/requirements-dev.txt
      - run: NEMO_MODEL_PROVIDER=mock pytest tests/python/ -v

  deploy:
    needs: test  # Only deploy if tests pass
    ...
```

Also add a PR check workflow that runs on every pull request (not just pushes
to main).

---

## 10. Frontend: Accessibility & Code Quality

**Severity:** MEDIUM
**Effort:** Medium
**Milestone target:** M4

### Problem

The frontend has accessibility gaps and code quality issues that would affect
usability and maintainability.

### Evidence

**Accessibility issues:**

1. **No ARIA live region for streaming segments** —
   `templates/scribe/index.html.twig:739` has `aria-live="polite"` on the
   transcript container, but individual segments added dynamically don't have
   `role="listitem"` or announce themselves to screen readers.

2. **Avatar badges lack aria-labels** — `scribe.js:919-930`: Segment avatars
   show "Dr" or "Pt" visually but have no `aria-label` attribute:
   ```javascript
   avatar.className = 'segment__avatar';
   avatar.textContent = getAvatarLabel(backendRole);
   // Missing: avatar.setAttribute('aria-label', getRoleLabel(backendRole));
   ```

3. **Mode dropdown has no keyboard navigation** — `scribe.js:62-70`:
   `toggleModeDropdown()` only handles click events. No arrow key navigation,
   no Escape to close, no focus management.

4. **Recording state not announced** — When recording starts/stops, the
   `#srAnnounce` element (`index.html.twig:767`) exists but isn't used
   consistently to announce state changes.

**Code quality issues:**

5. **Global state pollution** — `scribe.js` has 15+ module-level mutable
   variables:
   ```javascript
   let segmentIndex = {};          // line ~400
   let roleMapping = {};           // line ~410
   let previousRoleMapping = {};   // line ~411
   let confidence = 0;             // line ~412
   let manualOverrides = {};       // line ~413
   let segmentsBySpeaker = {};     // line ~414
   let ws = null;                  // line ~500
   let mediaStream = null;         // line ~501
   let audioContext = null;        // line ~502
   ```
   No encapsulation. Any function can mutate any state.

6. **O(n) relabeling on every role update** — `scribe.js` `relabelSegments()`
   iterates ALL DOM `.segment` elements on every role update or mode switch.
   With 500+ segments, this causes visible jank.

7. **Hardcoded color switch** — `scribe.js:934-937`: Only `DOCTOR` and
   `PATIENT` have defined colors. All other roles (NURSE, FAMILY_MEMBER,
   COMMENTATOR, ORGANISER, etc.) fall through to UNKNOWN styling:
   ```javascript
   case 'DOCTOR': return 'var(--color-speaker-a)';
   case 'PATIENT': return 'var(--color-speaker-b)';
   default: return 'var(--color-unknown)';
   ```

8. **No error distinction in EventSource** — `scribe.js` ~line 178:
   `onerror` handler reconnects on all errors with no distinction between
   network errors, invalid session, or auth failures. Results in infinite
   retry loops.

### Recommendation

1. **Accessibility:** Add `role="log"` to transcript, `role="listitem"` to
   segments, `aria-label` to avatars, keyboard navigation to dropdowns, and
   consistent use of `#srAnnounce` for state changes.

2. **Encapsulation:** Wrap state in a `ScribeApp` class or module pattern:
   ```javascript
   const ScribeApp = (() => {
       const state = { segmentIndex: {}, roleMapping: {}, ... };
       return { start, stop, getState, ... };
   })();
   ```

3. **Dynamic color mapping:** Replace the hardcoded switch with a lookup from
   the MODES config, using `PRIMARY`/`SECONDARY`/`TERTIARY` slots (ties into
   item 7).

4. **Efficient relabeling:** Use a `MutationObserver` or maintain a
   `Map<speakerId, Set<Element>>` to update only affected segments instead of
   scanning all DOM nodes.

---

## 11. Docker & Deployment

**Severity:** MEDIUM
**Effort:** Medium
**Milestone target:** M5

### Evidence & issues

**1. PHP uses development-only web server** — `Dockerfile`:

The app container uses `php:8.3-cli` with Symfony's built-in development
server. This is single-threaded and not suitable for production.

**Recommendation:** Switch to `php:8.3-fpm` + Nginx (or Caddy) for production,
or use FrankenPHP.

**2. NeMo Dockerfile pins to `@main` branch** — `docker/nemo/Dockerfile:54-57`:

```dockerfile
RUN pip install --no-cache-dir \
    git+https://github.com/NVIDIA/NeMo.git@main#egg=nemo_toolkit[asr]
```

The `@main` branch can break at any time. Pin to a specific commit hash or
release tag.

**Recommendation:**
```dockerfile
RUN pip install --no-cache-dir \
    git+https://github.com/NVIDIA/NeMo.git@v2.5.3#egg=nemo_toolkit[asr]
```

**3. No health check for PHP app** — `docker-compose.yml`:

The `nemo-agent` service (line 92) and `mercure` service have health checks,
but the `app` service (line 107) has none.

**Recommendation:**
```yaml
app:
  healthcheck:
    test: ["CMD", "curl", "-f", "http://localhost:8080/"]
    interval: 15s
    timeout: 5s
    retries: 3
```

**4. Ollama requires manual model pull** — `docker-compose.yml:158-166`:

The `ollama` service starts but doesn't automatically pull the required model.
Users must manually run `docker compose exec ollama ollama pull qwen2.5:14b`.

**Recommendation:** Add an init container or startup script:
```yaml
ollama-init:
  image: ollama/ollama
  profiles: ["local"]
  depends_on: [ollama]
  entrypoint: ["sh", "-c", "sleep 5 && ollama pull qwen2.5:14b"]
```

**5. `--reload` in production command** — `docker-compose.yml:84`:

```yaml
command: ["uvicorn", "api.server:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]
```

`--reload` watches for file changes and restarts the server. This is
development-only behavior. In production, it adds filesystem polling overhead
and can trigger unexpected restarts.

**Recommendation:** Remove `--reload` for production. Use an environment
variable:
```yaml
command: ["uvicorn", "api.server:app", "--host", "0.0.0.0", "--port", "8000",
          "${UVICORN_RELOAD:+--reload}"]
```

**6. Session storage defaults to memory** — `docker-compose.yml:65`:

```yaml
SESSION_STORAGE=${SESSION_STORAGE:-memory}
```

Container restarts lose all session data. The SQLite backend exists and is
tested but is not the default.

**Recommendation:** Default to `sqlite` now that `SqliteBackend` is implemented
and tested. The `session_data` volume is already mounted.

---

## 12. Error Handling Gaps

**Severity:** MEDIUM
**Effort:** Small
**Milestone target:** M3.5

### Problem

Broad `except Exception` blocks mask specific failures and make debugging
harder. Some errors are silently swallowed.

### Evidence

**1. JWT encoding silently fails** — `server.py:158`:

```python
try:
    import jwt as pyjwt
    token = pyjwt.encode(...)
    _mercure_jwt_cache = token ...
except Exception:
    _mercure_jwt_cache = ""  # Silently returns empty string
```

If `pyjwt` is not installed or the secret is invalid, all Mercure publishing
silently stops. No log, no error, no alert.

**Fix:** Log the exception. Raise at startup if JWT is required.

**2. Periodic cleanup catches all exceptions** — `server.py:284`:

```python
except Exception:
    logger.exception("periodic_cleanup.failed")
```

This is correct (cleanup should not crash the server), but should distinguish
between `asyncio.CancelledError` (expected on shutdown) and real errors.

**3. ffmpeg decode returns empty bytes on failure** — `nemo_session.py:310-314`:

```python
except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
    logger.error("transcription_session.webm_decode.failed", ...)
    return b""
```

An empty buffer means the chunk is silently dropped. The caller gets zero
segments with no indication of a decode failure. Should propagate a signal
to the WebSocket handler so the client can be notified.

**4. Summary JSON parsing falls back to regex** — `server.py:955-958`:

```python
except json.JSONDecodeError:
    match = re.search(r'\{.*\}', response_text, re.DOTALL)
    if match:
        return json.loads(match.group())
```

The `re.DOTALL` regex `\{.*\}` is greedy and can match nested braces
incorrectly. If the LLM returns `"Here is the summary: {...} and also {...}"`,
the regex matches from the first `{` to the last `}`, potentially including
garbage between objects.

**Fix:** Use a more robust JSON extractor, or require strict JSON output via
the agent's system prompt.

---

## 13. Dead Code Cleanup

**Severity:** LOW
**Effort:** Small
**Milestone target:** Next commit

### Evidence

**1. `_convert_webm_to_wav` is never called** — `nemo_session.py:321-368`:

This 47-line static method converts WebM bytes to a WAV file via ffmpeg. It is
never referenced anywhere in the codebase. The actual WebM decode path goes
through `_decode_webm_chunk()` (line 286) which converts WebM to raw PCM, not
WAV.

**Action:** Delete lines 321-368.

**2. `is_interim` is defined but never set to True** — `nemo_pipeline.py:57`:

```python
is_interim: bool = False  # True if this segment may be revised
```

The field exists in `Segment`, is serialized in `dict()`, stored in SQLite
(`storage.py:95`), and documented in the SSE contract (`server.py:47`). But no
code path ever sets it to `True`. All segments are implicitly "final".

**Action:** Either implement interim segment support (M2.5) or remove the field
and its references to reduce confusion.

**3. `strands_agents/transcription_agent.py` (root level):**

There is a file `strands_agents/transcription_agent.py` at the root AND
`strands_agents/agents/transcription_agent.py` in the agents subdirectory.
Verify if the root-level one is a legacy wrapper that should be deleted.

---

## 14. Data Privacy & Compliance

**Severity:** HIGH (for production)
**Effort:** Large
**Milestone target:** M5 / M6

### Problem

The system transcribes sensitive conversations (medical consultations,
interviews) with no privacy controls, encryption, or audit trail.

### Gaps

1. **No encryption at rest:** Transcripts stored in memory or SQLite with no
   encryption. SQLite file on the Docker volume is plaintext.

2. **No data retention policy:** Sessions are kept until TTL expires (2 hours)
   or manually cleaned up. No automated purge for old data. No user-facing
   "delete my session" endpoint.

3. **No audit trail:** No logging of who accessed which session, when, or what
   data was retrieved. The correlation ID middleware tracks requests but doesn't
   log access patterns.

4. **No consent mechanism:** The system starts recording immediately when the
   user clicks "Start Recording". No consent prompt, no disclosure that AI
   transcription is active.

5. **No data minimization:** The full audio buffer is retained for the duration
   of the session (up to 15 minutes at 32KB/s = ~28MB). No option to process
   and discard audio after transcription.

6. **Transcript download has no watermarking:** The "Download" button exports
   the full transcript as plaintext with no indication of origin, timestamp, or
   session provenance.

### Recommendation

Before any production deployment with real patient/employee data:

1. Add a session consent flow (GDPR Article 6/7)
2. Implement encryption at rest for SQLite backend (SQLCipher)
3. Add an audit log for session access
4. Implement a `/session/{id}/delete` endpoint for data subject requests
5. Add a data retention policy with automated purge
6. Include a "This session is being transcribed by AI" disclosure in the UI

---

## Summary Priority Matrix

| # | Item | Severity | Effort | Milestone |
|---|---|---|---|---|
| 1 | Authentication & Authorization | CRITICAL | Large | M5 |
| 2 | Rate Limiting | CRITICAL | Medium | M3.5/M5 |
| 4 | O(n²) Buffer Reprocessing | HIGH | Large | M2.5 |
| 7 | DOCTOR/PATIENT Key Reuse | HIGH | Medium | M4 |
| 8 | Missing Test Coverage | HIGH | Medium | M3.5 |
| 9 | CI/CD Tests Not Running | HIGH | Small | M4 |
| 14 | Data Privacy & Compliance | HIGH | Large | M5/M6 |
| 3 | Input Validation | HIGH | Small | M3.5 |
| 6 | Split server.py | MEDIUM | Medium | M4/M5 |
| 5 | Sequential Mercure Publishing | MEDIUM | Small | M3.5 |
| 10 | Frontend Accessibility | MEDIUM | Medium | M4 |
| 11 | Docker & Deployment | MEDIUM | Medium | M5 |
| 12 | Error Handling Gaps | MEDIUM | Small | M3.5 |
| 13 | Dead Code Cleanup | LOW | Small | Next commit |
