# Milestone 3.5 — Transcript Trust + Persistence

**Timeline:** ~4-5 hours
**Status:** Not Started
**Dependencies:** Milestone 3 complete (role attribution working)

---

## Objective

The transcript is the product. It must be trustworthy: no duplicates, deterministic reconnection, server-authoritative state, and local persistence that survives page reloads and container restarts. Privacy-aware from the start.

---

## Tasks

### 3A.1 Transcript Event Protocol v1

> **Why:** The current model is append-only with heuristic deduplication. There's no way to correct, replace, or supersede a segment. The browser and server can end up with different transcripts after finalization.

- [ ] Define segment schema:
  ```json
  {
    "segment_id": "uuid",
    "revision": 1,
    "speaker_id": "spk_0",
    "role": "DOCTOR",
    "text": "What brings you in today?",
    "start": 0.0,
    "end": 3.2,
    "is_interim": false,
    "supersedes": null,
    "source": "nemo_reconciliation"
  }
  ```
- [ ] Server assigns `segment_id` (not browser) — server is the transcript authority
- [ ] Interim segments from fast-path can be superseded by reconciliation segments
- [ ] Browser merges by `segment_id`: new revision replaces old, `supersedes` removes replaced segments
- [ ] Finalized event includes the authoritative segment list — browser reconciles against it

### 3A.2 Persistent httpx Client

- [ ] Create shared `httpx.AsyncClient` in FastAPI `lifespan()`, store on `app.state`
- [ ] Use for all Mercure publishes (replaces per-publish client creation)
- [ ] Close in lifespan teardown
- [ ] Batch multiple segments into a single Mercure publish per chunk

### 3A.3 Session Persistence (SQLite, local-first)

> **Why:** In-memory `SessionStore` loses all data on container restart. For a tool used in real sessions, this is unacceptable. SQLite is the simplest local persistence and needs no external service.

- [ ] Add a `StorageBackend` interface: `save_segments()`, `get_segments()`, `save_role_mapping()`, `cleanup()`
- [ ] Implement `SqliteBackend` (default for local dev)
- [ ] Implement `MemoryBackend` (for tests, current behaviour)
- [ ] Later: `DynamoDbBackend` (for cloud deployment, M5)
- [ ] Session recovery on page reload: `GET /session/{id}/history` returns persisted state
- [ ] Configure via `SESSION_STORAGE=sqlite|memory|dynamodb`

### 3A.4 WebSocket Reconnection with Session Preservation

- [ ] On disconnect, keep `TranscriptionSession` alive for 30 seconds (grace period)
- [ ] If same `session_id` reconnects within grace, resume existing session (audio buffer + transcript state preserved)
- [ ] If grace period expires, finalize and clean up normally
- [ ] Configure grace period via `SESSION_RECONNECT_GRACE_SECONDS`

### 3A.5 Mercure Last-Event-ID

- [ ] Set `id` field on Mercure publishes (monotonic per session)
- [ ] Browser passes `lastEventId` on SSE reconnect — resumes from where it dropped
- [ ] Eliminates silent segment loss during brief network interruptions

### 3A.6 Input Validation + Security

- [ ] Validate `session_id` is UUID format on all endpoints (prevents path traversal in temp file paths)
- [ ] Sanitise error messages in Mercure publishes (don't leak internal details to browser)
- [ ] Rate limit WebSocket connections per IP (configurable)

### 3A.7 Privacy Baseline

- [ ] Temp WAV files: use `tempfile.NamedTemporaryFile(delete=True)` everywhere, verify cleanup in `finally` blocks
- [ ] Audio buffer: zero-fill and release memory on session finalize
- [ ] Logging: strip transcript content from log lines (log metadata only — session_id, segment count, timing)
- [ ] Local-only mode audit: verify no data leaves the machine when using Ollama (no Bedrock HTTP calls)
- [ ] Document data flow with privacy annotations

### 3A.8 Periodic Cleanup

- [ ] Add background task (every 5 minutes): clean up orphaned entries in `_locks`, `_inference_queues`, `_inference_workers`, `_session_states` for sessions not in `lifecycle._active`
- [ ] `SessionStore` bulk eviction on access (not just lazy per-read eviction)

---

## Exit Criteria

- [ ] Transcripts survive page reload (fetched from SQLite via history endpoint)
- [ ] Transcripts survive container restart (SQLite on a volume)
- [ ] Reconnection within grace period resumes without data loss
- [ ] Mercure SSE reconnect resumes from last event (no silent gaps)
- [ ] `session_id` validated as UUID on all endpoints
- [ ] No transcript content in application logs
- [ ] No temp files persist after session cleanup
