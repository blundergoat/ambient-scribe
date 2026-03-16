"""
FastAPI server for the Ambient Medical Scribe.

=============================================================================
WHAT THIS FILE DOES
=============================================================================

This is the HTTP + WebSocket API that handles real-time transcription.

Endpoints:
  POST /transcribe/file             — Upload a WAV file, get transcript (batch mode)
  WS   /ws/transcribe/{session_id}  — Live audio streaming via WebSocket
  GET  /session/{id}/history         — View session transcript history
  POST /session/{id}/roles/override  — Manual speaker role override
  GET  /health                       — Docker healthcheck

=============================================================================
ARCHITECTURE
=============================================================================

  Browser ──(WebSocket binary audio)──► /ws/transcribe/{session_id}
                                             │
                                             ▼
                                        NemoPipeline (GPU, in thread pool)
                                             │
                                             ▼
                                        Publish to Mercure (SSE)
                                             │
                                             ▼
                                        Browser EventSource receives segments

  NeMo pipeline is loaded once at startup and shared across all sessions.
  GPU-bound inference runs in a ThreadPoolExecutor to avoid blocking the
  async event loop (which would freeze all other connections + /health).

=============================================================================
MERCURE TOPICS
=============================================================================

  scribe/session/{id}/raw    — Raw spk_0/spk_1 segments (immediate)
  scribe/session/{id}/roles  — DOCTOR/PATIENT role updates (async, higher latency)

=============================================================================
SSE EVENT CONTRACT (published to Mercure)
=============================================================================

  { "type": "segment",     "speaker_id": "spk_0", "text": "...", "start": 0.0, "end": 1.5, "is_interim": true }
  { "type": "role_update", "mapping": {"spk_0": "DOCTOR"}, "confidence": 0.85, "flip_detected": false, "manual_override": false }
  { "type": "finalized",   "session_id": "..." }
  { "type": "error",       "message": "..." }
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import subprocess
import tempfile
import time
import uuid
from collections.abc import AsyncGenerator
from concurrent.futures import ThreadPoolExecutor
from contextvars import ContextVar
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI, Form, HTTPException, Query, Request, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from starlette.middleware.base import BaseHTTPMiddleware
from nemo_pipeline import NemoPipeline
from nemo_session import TranscriptionSession
from session import SessionStore
from session_lifecycle import SessionLifecycle
from storage import StorageBackend
from tools.assign_roles import (
    apply_role_mapping_result,
    cleanup_session as cleanup_role_state,
    get_or_create_state,
)

logger = logging.getLogger(__name__)

# =============================================================================
# CORRELATION ID — request-scoped tracing
# =============================================================================

correlation_id_var: ContextVar[str] = ContextVar("correlation_id", default="-")


class CorrelationIdFilter(logging.Filter):
    """Inject correlation_id into every log record automatically."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.correlation_id = correlation_id_var.get("-")
        return True


class CorrelationIdMiddleware(BaseHTTPMiddleware):
    """Extract or generate X-Correlation-ID for every HTTP request."""

    async def dispatch(self, request: Request, call_next):
        cid = request.headers.get("x-correlation-id") or str(uuid.uuid4())
        token = correlation_id_var.set(cid)
        try:
            response = await call_next(request)
            response.headers["X-Correlation-ID"] = cid
            return response
        finally:
            correlation_id_var.reset(token)

# =============================================================================
# CONFIGURATION
# =============================================================================

MERCURE_HUB_URL = os.environ.get("MERCURE_HUB_URL", "http://mercure:3701/.well-known/mercure")
MERCURE_JWT_SECRET = os.environ.get("MERCURE_JWT_SECRET", "")
NEMO_STREAM_INPUT_FORMAT = os.environ.get("NEMO_STREAM_INPUT_FORMAT", "pcm")
NEMO_BUFFER_MAX_DURATION = float(os.environ.get("NEMO_BUFFER_MAX_DURATION", "900"))
SESSION_STORAGE = os.environ.get("SESSION_STORAGE", "memory")
SESSION_RECONNECT_GRACE_SECONDS = float(os.environ.get("SESSION_RECONNECT_GRACE_SECONDS", "30"))
_mercure_jwt_cache: str | None = None


def _resolve_mercure_jwt() -> str:
    """Return the Mercure publisher JWT.

    Prefers MERCURE_JWT (a pre-signed token) over MERCURE_JWT_SECRET.
    Caches the result so we don't re-read env vars on every publish.
    """
    global _mercure_jwt_cache
    if _mercure_jwt_cache is not None:
        return _mercure_jwt_cache

    jwt_env = os.environ.get("MERCURE_JWT", "")
    if jwt_env:
        _mercure_jwt_cache = jwt_env
        return _mercure_jwt_cache

    secret = MERCURE_JWT_SECRET
    if not secret:
        _mercure_jwt_cache = ""
        return _mercure_jwt_cache

    try:
        import jwt as pyjwt
        token = pyjwt.encode(
            {"mercure": {"publish": ["*"]}},
            secret,
            algorithm="HS256",
        )
        _mercure_jwt_cache = token if isinstance(token, str) else token.decode("utf-8")
    except Exception:
        _mercure_jwt_cache = ""

    return _mercure_jwt_cache


def _history_duration(segments: list[dict]) -> float:
    """Estimate session duration from stored segment timestamps."""
    if not segments:
        return 0.0
    ends = [float(seg.get("end", 0)) for seg in segments if seg.get("end") is not None]
    return max(ends) if ends else 0.0


def _validate_session_id(session_id: str) -> str:
    """Validate that session_id is a well-formed UUID.

    Raises HTTPException(400) if the value is not a valid UUID.
    Returns the session_id unchanged on success.
    """
    try:
        uuid.UUID(session_id)
    except (ValueError, AttributeError):
        raise HTTPException(status_code=400, detail="Invalid session_id: must be a valid UUID")
    return session_id


def log_vram() -> None:
    """Log current GPU VRAM usage via nvidia-smi."""
    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.used,memory.total", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0:
            logger.info("gpu.vram_usage", extra={"vram": result.stdout.strip()})
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass  # nvidia-smi not available (no GPU or mock mode)


# Thread pool for GPU-bound NeMo inference.
# Prevents blocking the async event loop, keeping /health and other
# WebSocket connections responsive during inference.
NEMO_MAX_WORKERS = int(os.environ.get("NEMO_MAX_WORKERS", "2"))
nemo_executor = ThreadPoolExecutor(max_workers=NEMO_MAX_WORKERS, thread_name_prefix="nemo")

ROLE_INFERENCE_IDLE_TIMEOUT_SECONDS = 60.0
_inference_queues: dict[str, asyncio.Queue[list[dict[str, Any]] | None]] = {}
_inference_workers: dict[str, asyncio.Task[None]] = {}
_session_modes: dict[str, str] = {}
_mercure_event_ids: dict[str, int] = {}

VALID_MODES = {"medical", "meeting", "interview", "tv", "lecture", "general"}


# =============================================================================
# LIFESPAN — Load NeMo models once at startup
# =============================================================================

@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Load NeMo models into GPU memory at process startup.

    This runs once when the FastAPI server starts. It may take 30-60 seconds
    depending on model size and disk speed.
    """
    logger.info("server.startup.loading_nemo_models")
    app.state.nemo_pipeline = NemoPipeline()
    app.state.nemo_input_format = NEMO_STREAM_INPUT_FORMAT
    app.state.http_client = httpx.AsyncClient(timeout=5.0)
    logger.info("server.startup.nemo_models_loaded")

    # Periodic cleanup of orphaned session state (every 5 minutes)
    cleanup_task = asyncio.create_task(_periodic_cleanup())

    yield

    cleanup_task.cancel()
    await app.state.http_client.aclose()
    nemo_executor.shutdown(wait=False)


async def _periodic_cleanup() -> None:
    """Remove orphaned entries from module-level dicts every 5 minutes.

    Handles the case where a WebSocket handler crashes before reaching
    its finally block, leaving entries in _inference_queues, _inference_workers,
    _session_modes, and assign_roles._session_states.
    """
    while True:
        await asyncio.sleep(300)  # 5 minutes
        try:
            active_ids = {sid for sid in lifecycle._active}
            # Sessions with a pending grace-period destroy are still "alive"
            pending_ids = {sid for sid in lifecycle._pending_destroys}
            live_ids = active_ids | pending_ids
            # Clean inference queues/workers for inactive sessions
            orphaned_queues = [sid for sid in _inference_queues if sid not in live_ids]
            for sid in orphaned_queues:
                _inference_queues.pop(sid, None)
                worker = _inference_workers.pop(sid, None)
                if worker and not worker.done():
                    worker.cancel()
            # Clean session modes
            orphaned_modes = [sid for sid in _session_modes if sid not in live_ids]
            for sid in orphaned_modes:
                _session_modes.pop(sid, None)
            # Clean Mercure event IDs
            orphaned_event_ids = [sid for sid in _mercure_event_ids if sid not in live_ids]
            for sid in orphaned_event_ids:
                _mercure_event_ids.pop(sid, None)
            # Clean role states
            from tools.assign_roles import _session_states, _states_lock
            with _states_lock:
                orphaned_roles = [sid for sid in _session_states if sid not in live_ids]
                for sid in orphaned_roles:
                    _session_states.pop(sid, None)
            if orphaned_queues or orphaned_modes or orphaned_roles or orphaned_event_ids:
                logger.info("periodic_cleanup.completed", extra={
                    "orphaned_queues": len(orphaned_queues),
                    "orphaned_modes": len(orphaned_modes),
                    "orphaned_event_ids": len(orphaned_event_ids),
                    "orphaned_roles": len(orphaned_roles),
                    "active_sessions": len(active_ids),
                    "pending_destroys": len(pending_ids),
                })
        except Exception:
            logger.exception("periodic_cleanup.failed")


# =============================================================================
# APPLICATION
# =============================================================================

app = FastAPI(title="Ambient Scribe Agent", version="0.1.0", lifespan=lifespan)
app.add_middleware(CorrelationIdMiddleware)

# Install correlation_id filter on root logger so ALL log records include it.
logging.getLogger().addFilter(CorrelationIdFilter())

def create_storage_backend() -> StorageBackend:
    """Create the storage backend based on SESSION_STORAGE env var.

    Returns an in-memory SessionStore (default) or a persistent SqliteBackend.
    """
    if SESSION_STORAGE == "sqlite":
        from storage import SqliteBackend
        return SqliteBackend()
    return SessionStore()


# Transcript storage — in-memory (default) or SQLite (SESSION_STORAGE=sqlite)
sessions: StorageBackend = create_storage_backend()

# Coordinated session lifecycle (replaces bare active_sessions dict)
lifecycle = SessionLifecycle()


# =============================================================================
# PYDANTIC MODELS
# =============================================================================

class TranscribeFileResponse(BaseModel):
    """Response for the /transcribe/file endpoint."""
    session_id: str
    segments: list[dict] = Field(default_factory=list)
    duration_seconds: float = 0.0


# =============================================================================
# MERCURE PUBLISHING
# =============================================================================

MERCURE_PUBLISH_MAX_RETRIES = 3
MERCURE_PUBLISH_BACKOFF_SECONDS = 2.0


async def publish_to_mercure(
    topic: str,
    data: dict[str, Any],
    event_id: int | None = None,
) -> bool:
    """Publish a JSON event to a Mercure topic with retry.

    Retries up to MERCURE_PUBLISH_MAX_RETRIES times with exponential backoff
    before returning False.

    Args:
        topic: The Mercure topic URI (e.g., "scribe/session/{id}/raw")
        data: The event data to JSON-encode and publish
        event_id: Optional monotonic event ID for Mercure Last-Event-ID support.
                  When set, Mercure stores the ID so that reconnecting subscribers
                  can resume from this point via the Last-Event-ID query parameter.

    Returns:
        True if published successfully, False otherwise.
    """
    token = _resolve_mercure_jwt()
    if token == "":
        logger.error("mercure.publish.skipped", extra={"reason": "no JWT configured"})
        return False

    client = app.state.http_client
    payload: dict[str, str] = {
        "topic": topic,
        "data": json.dumps(data),
    }
    if event_id is not None:
        payload["id"] = str(event_id)

    last_error: Exception | None = None
    for attempt in range(MERCURE_PUBLISH_MAX_RETRIES):
        try:
            response = await client.post(
                MERCURE_HUB_URL,
                data=payload,
                headers={"Authorization": f"Bearer {token}"},
            )
            response.raise_for_status()
            return True
        except Exception as e:
            last_error = e
            if attempt < MERCURE_PUBLISH_MAX_RETRIES - 1:
                backoff = MERCURE_PUBLISH_BACKOFF_SECONDS * (2 ** attempt)
                logger.warning("mercure.publish.retrying", extra={
                    "topic": topic,
                    "attempt": attempt + 1,
                    "backoff_seconds": backoff,
                    "error": str(e),
                })
                await asyncio.sleep(backoff)

    logger.error("mercure.publish.failed", extra={
        "topic": topic,
        "attempts": MERCURE_PUBLISH_MAX_RETRIES,
        "error": str(last_error),
    })
    return False


def _session_has_multiple_speakers(session_id: str) -> bool:
    """Return True once at least two speakers appear in the stored transcript."""
    speaker_ids = {
        str(segment.get("speaker_id", "")).strip()
        for segment in sessions.get_segments(session_id)
        if str(segment.get("speaker_id", "")).strip() != ""
    }

    return len(speaker_ids) >= 2


async def enqueue_role_inference(
    session_id: str,
    segments: list[dict[str, Any]],
    mode: str | None = None,
) -> None:
    """Queue raw transcript segments for sequential per-session role inference."""
    if segments == []:
        return

    if mode is not None:
        _session_modes[session_id] = mode

    queue = _inference_queues.get(session_id)
    if queue is None:
        queue = asyncio.Queue(maxsize=50)
        _inference_queues[session_id] = queue

    try:
        queue.put_nowait([dict(segment) for segment in segments])
    except asyncio.QueueFull:
        logger.warning("role_inference.queue_full", extra={"session_id": session_id})

    worker = _inference_workers.get(session_id)
    if worker is None or worker.done():
        _inference_workers[session_id] = asyncio.create_task(
            _role_inference_worker(session_id),
            name=f"role-inference-{session_id}",
        )


async def close_role_inference(session_id: str) -> None:
    """Signal the session's inference worker to exit once queued work is done."""
    queue = _inference_queues.get(session_id)
    if queue is None:
        cleanup_role_state(session_id)
        return

    await queue.put(None)


async def _role_inference_worker(session_id: str) -> None:
    """Process role inference requests sequentially for one session."""
    queue = _inference_queues[session_id]

    try:
        while True:
            try:
                batch = await asyncio.wait_for(
                    queue.get(),
                    timeout=ROLE_INFERENCE_IDLE_TIMEOUT_SECONDS,
                )
            except asyncio.TimeoutError:
                logger.info("role_inference.worker_idle", extra={
                    "session_id": session_id,
                })
                break

            close_requested = batch is None
            merged_segments: list[dict[str, Any]] = [] if batch is None else list(batch)

            while True:
                try:
                    queued_batch = queue.get_nowait()
                except asyncio.QueueEmpty:
                    break

                if queued_batch is None:
                    close_requested = True
                    continue

                merged_segments.extend(queued_batch)

            if merged_segments != [] and _session_has_multiple_speakers(session_id):
                transcript = sessions.get_transcript_text(session_id)
                mode = _session_modes.get(session_id, "medical")
                loop = asyncio.get_running_loop()
                started_at = time.time()
                result = await loop.run_in_executor(
                    None,
                    _run_role_inference,
                    session_id,
                    merged_segments,
                    transcript,
                    mode,
                )
                duration_ms = int((time.time() - started_at) * 1000)

                if result:
                    tool_invoked = result.get("_tool_invoked", False)

                    if tool_invoked:
                        # Tool already called apply_role_mapping_result
                        state = get_or_create_state(session_id)
                        mapping = state.current_mapping
                        confidence = state.running_confidence
                        flip_detected = state.last_flip_detected
                        attributed_segments = [
                            {**seg, "role": mapping.get(str(seg.get("speaker_id", "")), "UNKNOWN")}
                            for seg in merged_segments
                        ]
                        reasoning = str(result.get("reasoning", ""))
                    else:
                        role_update = apply_role_mapping_result(
                            session_id=session_id,
                            segments=merged_segments,
                            mapping=result.get("mapping", {}),
                            confidence=float(result.get("confidence", 0.0)),
                            reasoning=str(result.get("reasoning", "")),
                        )
                        mapping = role_update.mapping
                        confidence = role_update.confidence
                        flip_detected = role_update.flip_detected
                        attributed_segments = role_update.attributed_segments
                        reasoning = role_update.reasoning

                    sessions.apply_role_mapping(session_id, mapping)

                    _mercure_event_ids.setdefault(session_id, 0)
                    _mercure_event_ids[session_id] += 1
                    await publish_to_mercure(
                        f"scribe/session/{session_id}/roles",
                        {
                            "type": "role_update",
                            "mapping": mapping,
                            "attributed_segments": attributed_segments,
                            "confidence": confidence,
                            "flip_detected": flip_detected,
                            "reasoning": reasoning,
                            "session_id": session_id,
                        },
                        event_id=_mercure_event_ids[session_id],
                    )
                    logger.info("role_inference.completed", extra={
                        "session_id": session_id,
                        "segments": len(merged_segments),
                        "confidence": confidence,
                        "flip_detected": flip_detected,
                        "tool_invoked": tool_invoked,
                        "duration_ms": duration_ms,
                    })
                else:
                    logger.warning("role_inference.empty_result", extra={
                        "session_id": session_id,
                        "segments": len(merged_segments),
                        "duration_ms": duration_ms,
                    })
            elif merged_segments != []:
                logger.info("role_inference.skipped", extra={
                    "session_id": session_id,
                    "reason": "insufficient_speaker_variety",
                    "segments": len(merged_segments),
                })

            if close_requested and queue.empty():
                break
    finally:
        _inference_workers.pop(session_id, None)
        _inference_queues.pop(session_id, None)

        if not lifecycle.is_active(session_id):
            cleanup_role_state(session_id)


# =============================================================================
# ENDPOINTS
# =============================================================================

@app.post("/transcribe/file", response_model=TranscribeFileResponse)
async def transcribe_file(
    file: UploadFile,
    session_id_query: str | None = Query(None, alias="session_id"),
    session_id_form: str | None = Form(None, alias="session_id"),
) -> TranscribeFileResponse:
    """Upload a WAV file and get a complete transcript.

    Batch mode entry point for testing and demo replay.

    Args:
        file: WAV file upload (16kHz mono PCM expected)
        session_id_query: Optional session ID for grouping, accepted via query or form

    Returns:
        TranscribeFileResponse with speaker-attributed segments.
    """
    resolved_session_id = session_id_query or session_id_form or str(uuid.uuid4())

    # Validate user-supplied session_id (skip auto-generated UUIDs)
    if session_id_query or session_id_form:
        _validate_session_id(resolved_session_id)

    # Save uploaded file to a secure temp path
    temp_fd = tempfile.NamedTemporaryFile(suffix=".wav", prefix="scribe_", delete=False)
    temp_path = Path(temp_fd.name)
    temp_fd.close()
    try:
        content = await file.read()
        temp_path.write_bytes(content)

        # Run NeMo inference in thread pool
        loop = asyncio.get_running_loop()
        started_at = time.time()
        result = await loop.run_in_executor(
            nemo_executor,
            app.state.nemo_pipeline.transcribe_file,
            str(temp_path),
        )
        duration = time.time() - started_at

        segments = [s.dict() for s in result.segments]

        logger.info("transcribe_file.completed", extra={
            "session_id": resolved_session_id,
            "segments": len(segments),
            "duration_seconds": round(duration, 2),
        })

        return TranscribeFileResponse(
            session_id=resolved_session_id,
            segments=segments,
            duration_seconds=round(duration, 2),
        )
    finally:
        temp_path.unlink(missing_ok=True)


@app.websocket("/ws/transcribe/{session_id}")
async def transcribe_stream(websocket: WebSocket, session_id: str) -> None:
    """Live audio transcription via WebSocket.

    Flow:
      1. Browser sends binary audio chunks (5-second intervals)
      2. NeMo processes each chunk in a thread pool (GPU-bound)
      3. Segments published to Mercure for browser SSE pickup
      4. On disconnect, run a final transcription pass

    Args:
        websocket: The WebSocket connection
        session_id: Unique session identifier
    """
    _validate_session_id(session_id)
    await websocket.accept()

    # Read mode from query parameter (e.g. ?mode=meeting), default to medical
    raw_mode = websocket.query_params.get("mode", "medical")
    mode = raw_mode if raw_mode in VALID_MODES else "medical"
    _session_modes[session_id] = mode

    # WebSocket connections bypass HTTP middleware, so set correlation_id from
    # the upgrade headers or fall back to the session_id itself.
    cid = websocket.headers.get("x-correlation-id", session_id)
    correlation_id_var.set(cid)

    # Reconnection support: if the session is still alive (within grace period),
    # resume it instead of creating a new one. register() cancels pending destroys.
    existing_session = lifecycle.get(session_id)
    if existing_session is not None:
        session = existing_session
        logger.info("websocket.resumed", extra={
            "session_id": session_id,
            "buffer_seconds": round(session.buffer.duration_seconds, 1),
            "chunk_count": session.chunk_count,
        })
    else:
        session = TranscriptionSession(
            session_id,
            pipeline=app.state.nemo_pipeline,
            input_format=app.state.nemo_input_format,
            max_buffer_duration=NEMO_BUFFER_MAX_DURATION,
        )
    await lifecycle.register(session_id, session)

    logger.info("websocket.connected", extra={"session_id": session_id, "mode": mode})

    loop = asyncio.get_running_loop()
    chunk_count = 0
    mercure_warned = False

    try:
        while True:
            audio_chunk = await websocket.receive_bytes()
            chunk_count += 1

            # Run GPU-bound NeMo inference in thread pool.
            # Without this, all other WebSocket connections and /health
            # freeze during inference.
            started_at = time.time()
            segments = await loop.run_in_executor(
                nemo_executor,
                session.process_chunk,
                audio_chunk,
            )
            duration_ms = int((time.time() - started_at) * 1000)

            # Publish raw segments immediately (hot path, low latency)
            segment_payloads: list[dict[str, Any]] = []
            for segment in segments:
                segment_payload = segment.dict()
                segment_payloads.append(segment_payload)
                sessions.append_segment(session_id, segment_payload)
                _mercure_event_ids.setdefault(session_id, 0)
                _mercure_event_ids[session_id] += 1
                published = await publish_to_mercure(
                    f"scribe/session/{session_id}/raw",
                    {
                        "type": "segment",
                        **segment.dict(),
                    },
                    event_id=_mercure_event_ids[session_id],
                )
                if not published and not mercure_warned:
                    mercure_warned = True
                    try:
                        await websocket.send_json({
                            "type": "system_error",
                            "message": "Real-time streaming unavailable",
                        })
                    except Exception:
                        pass

            if segment_payloads != []:
                await enqueue_role_inference(session_id, segment_payloads, mode=mode)

            # Track total chunk-to-publish latency (inference + Mercure)
            total_ms = int((time.time() - started_at) * 1000)
            logger.info("websocket.chunk_e2e", extra={
                "session_id": session_id,
                "chunk_count": chunk_count,
                "inference_ms": duration_ms,
                "total_ms": total_ms,
                "segments": len(segments),
            })

            # Log VRAM periodically (every 10th chunk) to avoid log spam
            if chunk_count % 10 == 0:
                log_vram()

    except WebSocketDisconnect:
        logger.info("websocket.disconnected", extra={
            "session_id": session_id,
            "total_chunks": chunk_count,
        })

        # Final transcription pass on disconnect
        final_segments = await loop.run_in_executor(nemo_executor, session.finalize)
        sessions.replace_segments(
            session_id,
            [segment.dict() for segment in final_segments],
        )
        current_state = get_or_create_state(session_id)
        if current_state.current_mapping:
            sessions.apply_role_mapping(session_id, current_state.current_mapping)

        # Publish finalized event with event_id
        _mercure_event_ids.setdefault(session_id, 0)
        _mercure_event_ids[session_id] += 1
        await publish_to_mercure(
            f"scribe/session/{session_id}/raw",
            {"type": "finalized", "session_id": session_id},
            event_id=_mercure_event_ids[session_id],
        )
    except Exception as e:
        logger.error("websocket.error", extra={
            "session_id": session_id,
            "error": str(e),
        })
        _mercure_event_ids.setdefault(session_id, 0)
        _mercure_event_ids[session_id] += 1
        await publish_to_mercure(
            f"scribe/session/{session_id}/raw",
            {"type": "error", "message": "Transcription error occurred"},
            event_id=_mercure_event_ids[session_id],
        )
    finally:
        # Grace period: keep the session alive for reconnection instead of
        # destroying immediately. If no reconnect arrives within the window,
        # schedule_destroy's timer fires and cleans up normally.
        await lifecycle.schedule_destroy(
            session_id, close_role_inference,
            grace_seconds=SESSION_RECONNECT_GRACE_SECONDS,
        )
        # Note: _session_modes and _mercure_event_ids are cleaned up when
        # the grace period expires and destroy() actually runs. We keep them
        # alive so a reconnecting session can continue seamlessly.


@app.api_route("/session/{session_id}/history", methods=["GET", "POST"])
async def session_history(session_id: str) -> dict:
    """View the transcript history for a session.

    Returns the accumulated transcript segments.
    Accepts both GET and POST (PHP StrandsClient uses postJson).
    """
    _validate_session_id(session_id)
    session = lifecycle.get(session_id)
    stored_segments = sessions.get_segments(session_id)

    if stored_segments:
        return {
            "session_id": session_id,
            "segments": stored_segments,
            "duration_seconds": round(
                session.buffer.duration_seconds if session else _history_duration(stored_segments),
                1,
            ),
            "chunk_count": session.chunk_count if session else 0,
        }

    if session:
        return {
            "session_id": session_id,
            "segments": [s.dict() for s in session.accumulated_transcript],
            "duration_seconds": round(session.buffer.duration_seconds, 1),
            "chunk_count": session.chunk_count,
        }
    return {"session_id": session_id, "segments": [], "message": "Session not found or ended"}


@app.post("/session/{session_id}/roles/override")
async def roles_override(session_id: str, request: Request) -> dict:
    """Apply a manual speaker role override from the frontend.

    Updates the role mapping state and publishes the change to Mercure
    so all connected clients see the correction immediately.
    """
    _validate_session_id(session_id)
    body = await request.json()
    speaker_id = str(body.get("speaker_id", ""))
    role = str(body.get("role", "")).upper()

    if not speaker_id or not role:
        raise HTTPException(status_code=400, detail="speaker_id and role required")

    # Update the role state
    state = get_or_create_state(session_id)
    state.current_mapping[speaker_id] = role

    # Store as confirmed override so the agent respects it
    state.confirmed_overrides[speaker_id] = role

    # Apply to stored segments
    sessions.apply_role_mapping(session_id, state.current_mapping)

    # Publish the override to Mercure so other clients see it
    _mercure_event_ids.setdefault(session_id, 0)
    _mercure_event_ids[session_id] += 1
    await publish_to_mercure(
        f"scribe/session/{session_id}/roles",
        {
            "type": "role_update",
            "mapping": state.current_mapping,
            "confidence": state.running_confidence,
            "flip_detected": False,
            "manual_override": True,
            "session_id": session_id,
        },
        event_id=_mercure_event_ids[session_id],
    )

    logger.info("roles_override.applied", extra={
        "session_id": session_id,
        "speaker_id": speaker_id,
        "role": role,
        "mapping": state.current_mapping,
    })

    return {"status": "ok", "mapping": state.current_mapping}


@app.post("/session/{session_id}/summary")
async def generate_summary(session_id: str) -> dict:
    """Generate a structured session summary.

    Triggered by the frontend when the user ends a session. Runs the summary
    agent against the full role-attributed transcript and publishes the result
    to Mercure.
    """
    _validate_session_id(session_id)

    stored_segments = sessions.get_segments(session_id)
    if not stored_segments:
        raise HTTPException(status_code=404, detail="No transcript found for session")

    transcript = sessions.get_transcript_text(session_id, max_chars=8000)
    mode = _session_modes.get(session_id, "medical")

    loop = asyncio.get_running_loop()
    started_at = time.time()
    summary = await loop.run_in_executor(
        None,
        _run_summary_generation,
        session_id,
        transcript,
        mode,
    )
    duration_ms = int((time.time() - started_at) * 1000)

    if summary is None:
        logger.warning("summary.generation_failed", extra={
            "session_id": session_id,
            "duration_ms": duration_ms,
        })
        raise HTTPException(status_code=502, detail="Summary generation failed")

    # Publish to Mercure
    _mercure_event_ids.setdefault(session_id, 0)
    _mercure_event_ids[session_id] += 1
    await publish_to_mercure(
        f"scribe/session/{session_id}/summary",
        {
            "type": "summary",
            "session_id": session_id,
            **summary,
        },
        event_id=_mercure_event_ids[session_id],
    )

    logger.info("summary.completed", extra={
        "session_id": session_id,
        "mode": mode,
        "sections": len(summary.get("sections", [])),
        "duration_ms": duration_ms,
    })

    return {"session_id": session_id, **summary}


def _run_summary_generation(
    session_id: str,
    transcript: str,
    mode: str = "medical",
) -> dict | None:
    """Run the summary agent synchronously.

    Called via run_in_executor() to avoid blocking the event loop.
    """
    try:
        from agents import create_summary_agent

        agent = create_summary_agent(mode=mode)
        result = agent(
            f"Generate a {mode} summary for this session transcript:\n\n{transcript}"
        )

        response_text = str(result)
        try:
            return json.loads(response_text)
        except json.JSONDecodeError:
            match = re.search(r'\{.*\}', response_text, re.DOTALL)
            if match:
                return json.loads(match.group())
            logger.warning("summary.no_json_found", extra={
                "session_id": session_id,
                "response_preview": response_text[:200],
            })
            return None
    except Exception as e:
        logger.error("summary.agent_failed", extra={
            "session_id": session_id,
            "error_type": type(e).__name__,
            "error": str(e)[:200],
        })
        return None


_replay_tasks: dict[str, asyncio.Task[None]] = {}


@app.post("/session/{session_id}/replay")
async def replay_file(
    session_id: str,
    file: UploadFile,
    speed: float = Query(1.0, ge=0.25, le=10.0),
    mode: str = Query("medical"),
) -> dict:
    """Replay a WAV file through the pipeline with real-time pacing.

    Processes the WAV through NeMo in batch, then replays segments to Mercure
    with delays matching actual timestamps (adjusted by speed factor).
    """
    _validate_session_id(session_id)

    if mode not in VALID_MODES:
        mode = "medical"
    _session_modes[session_id] = mode

    # Save and process through NeMo
    temp_fd = tempfile.NamedTemporaryFile(suffix=".wav", prefix="replay_", delete=False)
    temp_path = Path(temp_fd.name)
    temp_fd.close()
    try:
        content = await file.read()
        temp_path.write_bytes(content)

        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(
            nemo_executor,
            app.state.nemo_pipeline.transcribe_file,
            str(temp_path),
        )
        segments = [s.dict() for s in result.segments]
    finally:
        temp_path.unlink(missing_ok=True)

    if not segments:
        return {"session_id": session_id, "segments": 0, "duration_seconds": 0}

    # Calculate total audio duration from segment timestamps
    max_end = max(float(s.get("end", 0)) for s in segments)

    # Cancel any existing replay for this session
    existing = _replay_tasks.pop(session_id, None)
    if existing and not existing.done():
        existing.cancel()

    # Spawn paced replay task
    _replay_tasks[session_id] = asyncio.create_task(
        _replay_segments(session_id, segments, speed, mode),
        name=f"replay-{session_id}",
    )

    logger.info("replay.started", extra={
        "session_id": session_id,
        "segments": len(segments),
        "duration_seconds": round(max_end, 1),
        "speed": speed,
        "mode": mode,
    })

    return {
        "session_id": session_id,
        "segments": len(segments),
        "duration_seconds": round(max_end, 1),
        "speed": speed,
    }


async def _replay_segments(
    session_id: str,
    segments: list[dict[str, Any]],
    speed: float,
    mode: str,
) -> None:
    """Replay segments to Mercure with real-time pacing."""
    try:
        clock_start = time.time()

        for i, segment in enumerate(segments):
            seg_start = float(segment.get("start", 0))
            # Wait until the real-time moment for this segment
            target_wall_time = clock_start + (seg_start / speed)
            delay = target_wall_time - time.time()
            if delay > 0:
                await asyncio.sleep(delay)

            # Store and publish
            sessions.append_segment(session_id, segment)
            _mercure_event_ids.setdefault(session_id, 0)
            _mercure_event_ids[session_id] += 1
            await publish_to_mercure(
                f"scribe/session/{session_id}/raw",
                {"type": "segment", **segment},
                event_id=_mercure_event_ids[session_id],
            )

            # Enqueue role inference periodically (every 3 segments)
            if (i + 1) % 3 == 0 or i == len(segments) - 1:
                recent = segments[max(0, i - 2):i + 1]
                await enqueue_role_inference(session_id, recent, mode=mode)

        # Publish finalized
        _mercure_event_ids[session_id] += 1
        await publish_to_mercure(
            f"scribe/session/{session_id}/raw",
            {"type": "finalized", "session_id": session_id},
            event_id=_mercure_event_ids[session_id],
        )

        logger.info("replay.completed", extra={
            "session_id": session_id,
            "segments": len(segments),
        })
    except asyncio.CancelledError:
        logger.info("replay.cancelled", extra={"session_id": session_id})
    except Exception:
        logger.exception("replay.failed", extra={"session_id": session_id})
    finally:
        _replay_tasks.pop(session_id, None)


@app.api_route("/session/{session_id}/roles", methods=["GET", "POST"])
async def roles_snapshot(session_id: str) -> dict:
    """Return the current role mapping for a session.

    Quick lookup — no inference, just the last known state.
    Accepts both GET and POST (PHP StrandsClient uses postJson).
    """
    _validate_session_id(session_id)
    state = get_or_create_state(session_id)
    return {
        "session_id": session_id,
        "mapping": state.current_mapping,
        "confidence": state.running_confidence,
    }


def _heuristic_role_inference(
    segments: list[dict[str, Any]],
    transcript: str,
    mode: str = "medical",
) -> dict | None:
    """Keyword-based role assignment fallback when the LLM agent is unavailable.

    Returns a result dict with mapping, confidence (always 0.4), and reasoning,
    or None if the transcript is empty and no segments are provided.
    """
    if not segments and not transcript.strip():
        return None

    # Build per-speaker text from segments
    speaker_texts: dict[str, str] = {}
    speaker_order: list[str] = []
    for seg in segments:
        spk = str(seg.get("speaker_id", ""))
        text = str(seg.get("text", ""))
        if spk:
            speaker_texts.setdefault(spk, "")
            speaker_texts[spk] += " " + text
            if spk not in speaker_order:
                speaker_order.append(spk)

    mapping: dict[str, str] = {}

    if mode == "medical":
        doctor_keywords = {"prescribe", "diagnosis", "symptoms", "mg", "dosage", "treatment plan", "medication"}
        patient_keywords = {"i feel", "my pain", "hurts", "i've been feeling", "it hurts", "i have a"}

        for spk, text in speaker_texts.items():
            text_lower = text.lower()
            doc_score = sum(1 for kw in doctor_keywords if kw in text_lower)
            pat_score = sum(1 for kw in patient_keywords if kw in text_lower)
            if doc_score > pat_score:
                mapping[spk] = "DOCTOR"
            elif pat_score > doc_score:
                mapping[spk] = "PATIENT"

        # Fill unmapped speakers
        assigned_roles = set(mapping.values())
        for spk in speaker_order:
            if spk not in mapping:
                if "DOCTOR" not in assigned_roles:
                    mapping[spk] = "DOCTOR"
                    assigned_roles.add("DOCTOR")
                elif "PATIENT" not in assigned_roles:
                    mapping[spk] = "PATIENT"
                    assigned_roles.add("PATIENT")
                else:
                    mapping[spk] = "PATIENT"

    elif mode == "meeting":
        organiser_keywords = {"agenda", "action items", "let's move on", "let's review", "next item", "meeting"}

        for spk, text in speaker_texts.items():
            text_lower = text.lower()
            org_score = sum(1 for kw in organiser_keywords if kw in text_lower)
            if org_score > 0:
                mapping[spk] = "ORGANISER"

        assigned_roles = set(mapping.values())
        for spk in speaker_order:
            if spk not in mapping:
                if "ORGANISER" not in assigned_roles:
                    mapping[spk] = "ORGANISER"
                    assigned_roles.add("ORGANISER")
                else:
                    mapping[spk] = "PARTICIPANT"

    elif mode == "interview":
        interviewer_keywords = {"tell me about", "experience with", "your background", "walk me through", "why did you"}

        for spk, text in speaker_texts.items():
            text_lower = text.lower()
            int_score = sum(1 for kw in interviewer_keywords if kw in text_lower)
            if int_score > 0:
                mapping[spk] = "INTERVIEWER"

        assigned_roles = set(mapping.values())
        for spk in speaker_order:
            if spk not in mapping:
                if "INTERVIEWER" not in assigned_roles:
                    mapping[spk] = "INTERVIEWER"
                    assigned_roles.add("INTERVIEWER")
                else:
                    mapping[spk] = "CANDIDATE"

    else:
        # general / tv / lecture / unknown — assign SPEAKER_A, SPEAKER_B by order
        for idx, spk in enumerate(speaker_order):
            label = chr(ord("A") + idx) if idx < 26 else str(idx)
            mapping[spk] = f"SPEAKER_{label}"

    if not mapping:
        return None

    return {
        "mapping": mapping,
        "confidence": 0.4,
        "reasoning": f"Heuristic keyword-based assignment for mode={mode}",
    }


def _run_role_inference(
    session_id: str,
    segments: list[dict[str, Any]],
    transcript: str,
    mode: str = "medical",
) -> dict | None:
    """Run the Strands role inference agent synchronously.

    Called via run_in_executor() to avoid blocking the event loop.

    Uses a 3-tier fallback strategy:
      1. Configured LLM agent (Ollama or Bedrock)
      2. Heuristic keyword-based classifier
      3. None (graceful degradation)

    Returns the role mapping result or None on failure.
    """
    # --- Tier 1: LLM agent ---
    try:
        from agents import create_role_inference_agent, get_role_instruction

        agent = create_role_inference_agent(mode=mode)
        state = get_or_create_state(session_id)
        history_len_before = len(state.mapping_history)
        payload = {
            "session_id": session_id,
            "current_mapping": state.current_mapping,
            "mapping_history": state.mapping_history[-5:],
            "confirmed_overrides": state.confirmed_overrides,
            "new_segments": segments,
            "transcript_so_far": transcript,
        }
        instruction = get_role_instruction(mode)
        result = agent(
            f"{instruction}\n\n"
            f"{json.dumps(payload)}"
        )

        # Check if the assign_roles tool was invoked (it persists state directly)
        if len(state.mapping_history) > history_len_before:
            return {
                "mapping": state.current_mapping,
                "confidence": state.running_confidence,
                "reasoning": "",
                "_tool_invoked": True,
            }

        # Fallback: parse the agent's free-text JSON response
        response_text = str(result)
        try:
            parsed = json.loads(response_text)
        except json.JSONDecodeError:
            match = re.search(r'\{.*\}', response_text, re.DOTALL)
            if match:
                parsed = json.loads(match.group())
            else:
                logger.warning("role_inference.no_json_found", extra={
                    "session_id": session_id,
                    "response_preview": response_text[:200],
                })
                return None
        return parsed
    except Exception as e:
        logger.warning("role_inference.agent_failed_falling_back_to_heuristic", extra={
            "session_id": session_id,
            "error_type": type(e).__name__,
            "error": str(e)[:200],
            "mode": mode,
        })

    # --- Tier 2: Heuristic fallback ---
    try:
        heuristic_result = _heuristic_role_inference(segments, transcript, mode)
        if heuristic_result is not None:
            logger.info("role_inference.heuristic_used", extra={
                "session_id": session_id,
                "mode": mode,
                "mapping": heuristic_result.get("mapping", {}),
            })
            return heuristic_result
    except Exception as e:
        logger.error("role_inference.heuristic_failed", extra={
            "session_id": session_id,
            "error_type": type(e).__name__,
            "error": str(e)[:200],
        })

    # --- Tier 3: None (graceful degradation) ---
    return None


@app.get("/health")
async def health():
    """Health check endpoint for Docker healthcheck.

    Returns 200 if models are loaded or intentionally skipped.
    Returns 503 if model loading failed (degraded state).
    """
    pipeline = app.state.nemo_pipeline
    load_error = pipeline.load_error

    if load_error is not None:
        return JSONResponse(
            status_code=503,
            content={
                "status": "degraded",
                "service": "ambient-scribe-agent",
                "models_loaded": False,
                "load_error": load_error,
            },
        )

    return {
        "status": "ok",
        "service": "ambient-scribe-agent",
        "models_loaded": pipeline.is_loaded,
    }
