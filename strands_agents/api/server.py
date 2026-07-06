"""
FastAPI routes for the Ambient Medical Scribe.

This module keeps the browser-facing API surface: file upload, live WebSocket
recording, session history, role overrides, summaries, and health.
Workflow helpers own the longer queue and streaming loops so these routes stay
focused on what the clinician sees in the transcript and summary UI.
"""

from __future__ import annotations

import asyncio
from functools import partial
import logging
import os
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
from fastapi import FastAPI, Form, HTTPException, Query, Request, UploadFile, WebSocket
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from starlette.middleware.base import BaseHTTPMiddleware
from api.role_inference_queue import (
    RoleInferenceServices,
    cancel_orphaned_role_inference,
    close_role_inference as close_queued_role_inference,
    enqueue_role_inference as enqueue_queued_role_inference,
    prune_orphaned_role_states,
    role_inference_queues,
    role_inference_workers,
)
from api.role_agent_runtime import run_role_inference as _run_role_inference
from api.role_heuristics import compute_row_role_exceptions
from api.agent_observability import configure_strands_telemetry as _configure_strands_telemetry
from api.mercure_publisher import did_publish_mercure_event
from api.streaming_session import StreamingServices, transcribe_stream_session
from api.summary_request import (
    BrowserVisibleSegment,
    SummaryRequest,
    build_summary_context,
    browser_visible_segments_from_summary,
    publish_summary_outputs,
)
from nemo_pipeline import NemoPipeline
from post_visit_correction import (
    DEFAULT_POST_VISIT_ASR_MODEL,
    PostVisitCorrectionError,
    run_post_visit_correction,
)
from session import SessionStore
from session_lifecycle import SessionLifecycle
from storage import StorageBackend
from tools.assign_roles import get_or_create_state, peek_state
from logging_config import configure_logging
from api.summary_generation import run_summary_generation as _run_summary_generation

configure_logging()
logger = logging.getLogger(__name__)

# =============================================================================
# CORRELATION ID - request-scoped tracing
# =============================================================================

correlation_id_var: ContextVar[str] = ContextVar("correlation_id", default="-")


class CorrelationIdFilter(logging.Filter):
    """
    Adds the current request/session correlation ID to log records.

    Use this when a browser action spans HTTP, WebSocket, Mercure, and role
    worker logs. It lets support trace one clinician recording without exposing
    transcript text.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        """Attach the current correlation ID so user-session logs group together.

        Args:
            record: Log record emitted during a browser action.

        Returns:
            True so Python logging keeps the record.
        """
        record.correlation_id = correlation_id_var.get("-")
        return True


class CorrelationIdMiddleware(BaseHTTPMiddleware):
    """
    Keeps HTTP request logs tied to one browser action.

    The browser or proxy may provide `X-Correlation-ID`; otherwise the API
    creates one. WebSocket sessions set their own ID in the streaming workflow.
    """

    async def dispatch(self, request: Request, call_next):
        """Wrap one HTTP request and return the same correlation ID to the browser.

        Args:
            request: Browser or service HTTP request.
            call_next: Next ASGI handler that produces the response.
        """
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

NEMO_STREAM_INPUT_FORMAT = os.environ.get("NEMO_STREAM_INPUT_FORMAT", "pcm")
NEMO_BUFFER_MAX_DURATION = float(os.environ.get("NEMO_BUFFER_MAX_DURATION", "900"))
SESSION_STORAGE = os.environ.get("SESSION_STORAGE", "memory")
SESSION_RECONNECT_GRACE_SECONDS = float(
    os.environ.get("SESSION_RECONNECT_GRACE_SECONDS", "30")
)
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
        raise HTTPException(
            status_code=400, detail="Invalid session_id: must be a valid UUID"
        )
    return session_id


def log_vram() -> None:
    """Log current GPU VRAM usage via nvidia-smi."""
    try:
        result = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=memory.used,memory.total",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            logger.info("gpu.vram_usage", extra={"vram": result.stdout.strip()})
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass  # nvidia-smi not available (no GPU or mock mode)


# Thread pool for GPU-bound NeMo inference.
# Prevents blocking the async event loop, keeping /health and other
# WebSocket connections responsive during inference.
NEMO_MAX_WORKERS = int(os.environ.get("NEMO_MAX_WORKERS", "2"))
nemo_executor = ThreadPoolExecutor(
    max_workers=NEMO_MAX_WORKERS, thread_name_prefix="nemo"
)

_inference_queues = role_inference_queues
_inference_workers = role_inference_workers
_mercure_event_ids: dict[str, int] = {}


# =============================================================================
# LIFESPAN - Load NeMo models once at startup
# =============================================================================


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Load NeMo models into GPU memory at process startup.

    This runs once when the FastAPI server starts. It may take 30-60 seconds
    depending on model size and disk speed.

    Args:
        app: FastAPI application whose state is shared by browser requests.

    Returns:
        Async lifespan context; `None` means startup/shutdown only configure shared services.
    """
    _configure_strands_telemetry()
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
    and assign_roles._session_states.
    """
    while True:
        await asyncio.sleep(300)  # 5 minutes
        try:
            active_ids = {sid for sid in lifecycle._active}
            # Sessions with a pending grace-period destroy are still "alive"
            pending_ids = {sid for sid in lifecycle._pending_destroys}
            live_ids = active_ids | pending_ids
            # Inactive role workers would publish labels to sessions the user cannot resume.
            orphaned_queues = cancel_orphaned_role_inference(live_ids)
            # Clean Mercure event IDs
            orphaned_event_ids = [
                sid for sid in _mercure_event_ids if sid not in live_ids
            ]
            for sid in orphaned_event_ids:
                _mercure_event_ids.pop(sid, None)
            # Role mappings outside live sessions could relabel a future visit incorrectly.
            orphaned_roles = prune_orphaned_role_states(live_ids)
            if orphaned_queues or orphaned_roles or orphaned_event_ids:
                logger.info(
                    "periodic_cleanup.completed",
                    extra={
                        "orphaned_queues": orphaned_queues,
                        "orphaned_event_ids": len(orphaned_event_ids),
                        "orphaned_roles": orphaned_roles,
                        "active_sessions": len(active_ids),
                        "pending_destroys": len(pending_ids),
                    },
                )
        except Exception as cleanup_error:
            logger.exception(
                "periodic_cleanup.failed %s: %s",
                type(cleanup_error).__name__,
                str(cleanup_error)[:200],
                extra={
                    "error_type": type(cleanup_error).__name__,
                    "error": str(cleanup_error)[:200],
                },
            )


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

    Returns:
        Storage backend used by transcript history, role labeling, and summaries.
    """
    if SESSION_STORAGE == "sqlite":
        from storage import SqliteBackend

        return SqliteBackend()
    return SessionStore()


# Transcript storage - in-memory (default) or SQLite (SESSION_STORAGE=sqlite)
sessions: StorageBackend = create_storage_backend()

# Coordinated session lifecycle (replaces bare active_sessions dict)
lifecycle = SessionLifecycle()


class TranscribeFileResponse(BaseModel):
    """Response for the /transcribe/file endpoint."""

    session_id: str
    segments: list[dict] = Field(default_factory=list)
    duration_seconds: float = 0.0


class CorrectionRequest(BaseModel):
    """
    Browser request for a post-stop transcript correction.

    Use after the backend publishes `finalized` and before summary generation.
    The browser sends the rows it can see so server-side correction can keep the
    same reviewed timing and role scaffold. Empty segments mean the endpoint
    uses stored transcript rows only.
    """

    segments: list[BrowserVisibleSegment] = Field(default_factory=list)
    force: bool = False


async def publish_to_mercure(
    topic: str,
    data: dict[str, Any],
    event_id: int | None = None,
) -> bool:
    """Publish one transcript, role, or summary event to the browser.

    Args:
        topic: Mercure topic for the visible browser session.
        data: Event payload; empty still sends a browser-visible event shell.
        event_id: Resume ID for browser reconnects; null means no replay ID is attached.

    Returns:
        True when Mercure accepted the event; false means the UI did not receive this update.
    """
    return await did_publish_mercure_event(
        topic,
        data,
        app.state.http_client,
        event_id,
    )


def _role_inference_services() -> RoleInferenceServices:
    """Bundle current server callbacks for queued role labels.

    Returns:
        Services using the current publish and inference functions; tests may
        patch these so queued role updates still exercise the visible seam.
    """
    return RoleInferenceServices(
        sessions=sessions,
        lifecycle=lifecycle,
        publish_to_mercure=publish_to_mercure,
        run_role_inference=_run_role_inference,
        mercure_event_ids=_mercure_event_ids,
    )


async def enqueue_role_inference(
    session_id: str,
    segments: list[dict[str, Any]],
) -> None:
    """Queue transcript text for the role labels shown after raw segments.

    Args:
        session_id: Browser recording session that owns the transcript.
        segments: New NeMo segments; empty means the browser has no new text to relabel.
    """
    await enqueue_queued_role_inference(
        session_id, segments, _role_inference_services()
    )


async def close_role_inference(session_id: str) -> None:
    """Finish queued role labeling after the user stops or reconnects.

    Args:
        session_id: Browser recording session whose role worker should close.
    """
    await close_queued_role_inference(session_id)


def _streaming_services() -> StreamingServices:
    """Bundle current server callbacks for one live browser recording.

    Returns:
        Services using the current executor, publisher, role queue, and app
        state so tests can patch the same route-level seams users exercise.
    """
    return StreamingServices(
        pipeline=app.state.nemo_pipeline,
        input_format=app.state.nemo_input_format,
        max_buffer_duration=NEMO_BUFFER_MAX_DURATION,
        reconnect_grace_seconds=SESSION_RECONNECT_GRACE_SECONDS,
        executor=nemo_executor,
        sessions=sessions,
        lifecycle=lifecycle,
        get_running_loop=asyncio.get_running_loop,
        set_correlation_id=correlation_id_var.set,
        publish_to_mercure=publish_to_mercure,
        enqueue_role_inference=enqueue_role_inference,
        close_role_inference=close_role_inference,
        log_vram=log_vram,
        mercure_event_ids=_mercure_event_ids,
    )


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

    Batch mode entry point for tests and offline tooling; the browser demo
    streams WAV PCM through the live WebSocket route instead.

    Args:
        file: WAV file upload (16kHz mono PCM expected)
        session_id_query: Optional session ID for grouping, accepted via query or form
        session_id_form: Optional form session ID; missing means a new session ID is generated.

    Returns:
        TranscribeFileResponse with speaker-attributed segments.
    """
    resolved_session_id = session_id_query or session_id_form or str(uuid.uuid4())

    # Validate user-supplied session_id (skip auto-generated UUIDs)
    if session_id_query or session_id_form:
        _validate_session_id(resolved_session_id)

    # Uploaded audio is saved briefly so NeMo can read it like a normal file.
    scratch_file_handle = tempfile.NamedTemporaryFile(
        suffix=".wav", prefix="scribe_", delete=False
    )
    scratch_audio_path = Path(scratch_file_handle.name)
    scratch_file_handle.close()
    try:
        content = await file.read()
        scratch_audio_path.write_bytes(content)

        # Run NeMo inference in thread pool
        loop = asyncio.get_running_loop()
        started_at = time.time()
        result = await loop.run_in_executor(
            nemo_executor,
            app.state.nemo_pipeline.transcribe_file,
            str(scratch_audio_path),
        )
        duration = time.time() - started_at

        segments = [s.dict() for s in result.segments]

        logger.info(
            "transcribe_file.completed",
            extra={
                "session_id": resolved_session_id,
                "segments": len(segments),
                "duration_seconds": round(duration, 2),
            },
        )

        return TranscribeFileResponse(
            session_id=resolved_session_id,
            segments=segments,
            duration_seconds=round(duration, 2),
        )
    finally:
        scratch_audio_path.unlink(missing_ok=True)


@app.websocket("/ws/transcribe/{session_id}")
async def transcribe_stream(websocket: WebSocket, session_id: str) -> None:
    """Run live audio transcription for the browser recorder.

    The route validates the session ID, then delegates the user's audio stream
    to the workflow module that publishes raw segments, role work, and final
    events.

    Args:
        websocket: Browser socket sending PCM audio chunks.
        session_id: UUID recording session; invalid values return HTTP 400.
    """
    _validate_session_id(session_id)
    await transcribe_stream_session(websocket, session_id, _streaming_services())


@app.post("/session/{session_id}/correction")
async def correct_session_transcript(
    session_id: str,
    correction_request: CorrectionRequest | None = None,
) -> dict:
    """Create a corrected transcript artifact before summary generation.

    The browser calls this after Stop/finalized and before Summarise. It keeps
    the live preview transcript intact, writes corrected rows into the separate
    corrected storage lane, and returns a non-fatal fallback payload when the
    correction pass is unavailable.

    Args:
        session_id: UUID for the stopped recording whose audio is still retained.
        correction_request: Optional browser-visible rows and force flag; null uses storage.

    Returns:
        Correction status and metadata; `unavailable` means the browser should summarize live rows.
    """
    _validate_session_id(session_id)
    force_requested = bool(correction_request.force) if correction_request else False

    existing_corrected_segments = sessions.get_corrected_segments(session_id)
    # Existing corrected rows can be reused for retry clicks on the summary button.
    if existing_corrected_segments and not force_requested:
        return {
            "session_id": session_id,
            "status": "ready",
            "source": "corrected_segments",
            "segments": len(existing_corrected_segments),
            "model": existing_corrected_segments[0].get("source_model", ""),
            "reused": True,
        }

    browser_visible_segments = browser_visible_segments_from_summary(correction_request)
    # Browser rows carry the roles the clinician currently sees before correction runs.
    if browser_visible_segments:
        merge_result = sessions.merge_browser_segments(session_id, browser_visible_segments)
        logger.info(
            "correction.segments_merged session_id=%s matched=%s unknown=%s restored=%s",
            session_id,
            merge_result.get("matched", 0),
            merge_result.get("unknown", 0),
            merge_result.get("restored", False),
            extra={"session_id": session_id, **merge_result},
        )

    live_segments = sessions.get_segments(session_id)
    active_session = lifecycle.get(session_id)
    # After reconnect grace expires, the audio buffer is gone and correction cannot run.
    if active_session is None:
        return _correction_unavailable_response(
            session_id,
            "Session audio is no longer available for correction.",
            live_segments,
        )

    retained_audio = active_session.buffer.full_audio()
    # Empty audio means the user stopped before the browser sent usable samples.
    if retained_audio == b"":
        return _correction_unavailable_response(
            session_id,
            "No retained audio is available for correction.",
            live_segments,
        )

    loop = asyncio.get_running_loop()
    started_at = time.time()
    try:
        correction_result = await loop.run_in_executor(
            nemo_executor,
            partial(
                run_post_visit_correction,
                pcm_audio=retained_audio,
                live_segments=live_segments,
                model_name=DEFAULT_POST_VISIT_ASR_MODEL,
            ),
        )
    except PostVisitCorrectionError as correction_error:
        duration_ms = int((time.time() - started_at) * 1000)
        logger.warning(
            "correction.unavailable session_id=%s duration_ms=%s detail=%s",
            session_id,
            duration_ms,
            str(correction_error),
            extra={"session_id": session_id, "duration_ms": duration_ms},
        )
        return _correction_unavailable_response(
            session_id,
            str(correction_error),
            live_segments,
        )

    sessions.replace_corrected_segments(session_id, correction_result.segments)
    duration_ms = int((time.time() - started_at) * 1000)
    logger.info(
        "correction.completed session_id=%s segments=%s words=%s duration_ms=%s",
        session_id,
        len(correction_result.segments),
        correction_result.word_count,
        duration_ms,
        extra={
            "session_id": session_id,
            "segments": len(correction_result.segments),
            "words": correction_result.word_count,
            "duration_ms": duration_ms,
            "model": correction_result.model_name,
        },
    )

    return {
        "session_id": session_id,
        "status": "ready",
        "source": correction_result.source,
        "segments": len(correction_result.segments),
        "word_count": correction_result.word_count,
        "model": correction_result.model_name,
        "reused": False,
    }


def _correction_unavailable_response(
    session_id: str,
    detail: str,
    live_segments: list[dict[str, Any]],
) -> dict:
    """Build a non-fatal correction response for live-preview fallback.

    Args:
        session_id: Browser session UUID the user tried to correct.
        detail: Plain-English reason shown to developers/support; empty gives no context.
        live_segments: Stored live rows; empty means summary may still return 404.

    Returns:
        JSON payload telling the browser to continue with the live preview.
    """
    return {
        "session_id": session_id,
        "status": "unavailable",
        "source": "live_segments",
        "segments": len(live_segments),
        "detail": detail,
    }


@app.api_route("/session/{session_id}/history", methods=["GET", "POST"])
async def session_history(session_id: str) -> dict:
    """View the transcript history for a session.

    Returns the accumulated transcript segments.
    Accepts both GET and POST (PHP StrandsClient uses postJson).

    Args:
        session_id: UUID for the browser recording; invalid values return HTTP 400.

    Returns:
        Transcript payload; empty segments means the user has no saved transcript yet.
    """
    request_started_at = time.time()
    _validate_session_id(session_id)
    session = lifecycle.get(session_id)
    stored_segments = sessions.get_segments(session_id)

    # Stored segments are what the browser can restore after a page refresh.
    if stored_segments:
        response_payload = {
            "session_id": session_id,
            "segments": stored_segments,
            "duration_seconds": round(
                session.buffer.duration_seconds
                if session
                else _history_duration(stored_segments),
                1,
            ),
            "chunk_count": session.chunk_count if session else 0,
        }
        logger.info(
            "session_history.completed",
            extra={
                "session_id": session_id,
                "correlation_id": correlation_id_var.get("-"),
                "duration_ms": int((time.time() - request_started_at) * 1000),
                "segments": len(stored_segments),
                "source": "stored",
            },
        )
        return response_payload

    # A live session may still have buffered transcript text not yet stored.
    if session:
        live_segments = [s.dict() for s in session.accumulated_transcript]
        response_payload = {
            "session_id": session_id,
            "segments": live_segments,
            "duration_seconds": round(session.buffer.duration_seconds, 1),
            "chunk_count": session.chunk_count,
        }
        logger.info(
            "session_history.completed",
            extra={
                "session_id": session_id,
                "correlation_id": correlation_id_var.get("-"),
                "duration_ms": int((time.time() - request_started_at) * 1000),
                "segments": len(live_segments),
                "source": "live",
            },
        )
        return response_payload

    logger.info(
        "session_history.completed",
        extra={
            "session_id": session_id,
            "correlation_id": correlation_id_var.get("-"),
            "duration_ms": int((time.time() - request_started_at) * 1000),
            "segments": 0,
            "source": "missing",
        },
    )
    return {
        "session_id": session_id,
        "segments": [],
        "message": "Session not found or ended",
    }


@app.get("/session/{session_id}/corrected-transcript")
async def corrected_session_transcript(session_id: str) -> dict:
    """View the corrected transcript artifact for local QA scoring.

    Use after the user stops a visit and runs correction, so developers can
    score the exact rows the summary used without changing the live preview
    history or browser restore path.

    Args:
        session_id: UUID for the stopped recording; invalid values return HTTP 400.

    Returns:
        Corrected transcript payload; empty segments means no correction exists yet.
    """
    request_started_at = time.time()
    _validate_session_id(session_id)
    corrected_segments = sessions.get_corrected_segments(session_id)
    source = "corrected_segments"
    response_payload: dict[str, Any] = {
        "session_id": session_id,
        "source": source,
        "segments": corrected_segments,
        "duration_seconds": round(_history_duration(corrected_segments), 1),
    }

    # No corrected artifact exists yet, so QA sees an empty scoreable shape.
    if corrected_segments == []:
        source = "missing"
        response_payload["source"] = source
        response_payload["message"] = "Corrected transcript not found"

    logger.info(
        "corrected_transcript.completed",
        extra={
            "session_id": session_id,
            "correlation_id": correlation_id_var.get("-"),
            "duration_ms": int((time.time() - request_started_at) * 1000),
            "segments": len(corrected_segments),
            "source": source,
        },
    )
    return response_payload


@app.post("/session/{session_id}/roles/override")
async def roles_override(session_id: str, request: Request) -> dict:
    """Apply a manual role correction from the frontend.

    Two scopes share this route. A `speaker_id` body relabels every row of
    that speaker and becomes a confirmed override the agent must respect. A
    `segment_id` body corrects exactly one transcript row; it is stored in the
    row-scoped correction store and never touches the speaker mapping, so a
    later agent update cannot undo it. Both publish to Mercure so all
    connected clients see the correction immediately.

    Args:
        session_id: UUID for the transcript the user corrected.
        request: JSON body with `role` plus exactly one of `speaker_id` or
            `segment_id` selected in the transcript UI.

    Returns:
        Updated role mapping (speaker scope) or the corrected row (row scope).

    Raises:
        HTTPException: When the scope/role is missing or ambiguous, or when a
            row correction targets a session with no stored transcript.
    """
    _validate_session_id(session_id)
    body = await request.json()
    speaker_id = str(body.get("speaker_id", ""))
    segment_id = str(body.get("segment_id", ""))
    role = str(body.get("role", "")).upper()

    # Empty role selections cannot update the visible transcript labels.
    if not role or bool(speaker_id) == bool(segment_id):
        raise HTTPException(
            status_code=400,
            detail="role plus exactly one of speaker_id or segment_id required",
        )

    # Row scope: correct one visible transcript row without relabeling the speaker.
    if segment_id:
        return await _apply_row_role_override(session_id, segment_id, role)

    # Update the role state
    state = get_or_create_state(session_id)
    state.current_mapping[speaker_id] = role

    # Store as confirmed override so the agent respects it
    state.confirmed_overrides[speaker_id] = role

    # Apply to stored segments
    sessions.apply_role_mapping(session_id, state.current_mapping)
    # Re-judge rows against the corrected mapping so automatic row exceptions
    # stay consistent with the labels the clinician now sees.
    row_exceptions = compute_row_role_exceptions(
        sessions.get_segments(session_id), state.current_mapping
    )
    sessions.set_auto_row_roles(session_id, row_exceptions)

    # Publish the override to Mercure so other clients see it
    _mercure_event_ids.setdefault(session_id, 0)
    _mercure_event_ids[session_id] += 1
    await publish_to_mercure(
        f"scribe/session/{session_id}/roles",
        {
            "type": "role_update",
            "mapping": state.current_mapping,
            "row_exceptions": row_exceptions,
            "confidence": state.running_confidence,
            "flip_detected": False,
            "manual_override": True,
            "session_id": session_id,
        },
        event_id=_mercure_event_ids[session_id],
    )

    logger.info(
        "roles_override.applied",
        extra={
            "session_id": session_id,
            "speaker_id": speaker_id,
            "role": role,
            "mapping": state.current_mapping,
        },
    )

    return {"status": "ok", "mapping": state.current_mapping}


async def _apply_row_role_override(session_id: str, segment_id: str, role: str) -> dict:
    """Persist and broadcast a single-row role correction.

    The clinician clicked one transcript row whose label was wrong (e.g. a
    doctor question shown as Patient). The correction is stored row-scoped so
    speaker-level mappings and finalize rebuilds cannot undo it, then published
    on the roles topic so other tabs show the same corrected row.

    Args:
        session_id: Recording UUID the clinician is correcting.
        segment_id: Stable row ID from the clicked transcript row.
        role: Corrected role label for exactly that row.

    Returns:
        Confirmation payload with the corrected row for the browser.

    Raises:
        HTTPException: When no stored transcript exists for the session, so the
            UI can tell the user the correction could not be saved.
    """
    applied = sessions.set_row_role(session_id, segment_id, role)

    # A missing session means there is no stored row this correction could stick to.
    if not applied:
        raise HTTPException(
            status_code=404,
            detail="No stored transcript for this session; row correction not saved",
        )

    # Peek only: after grace teardown there is no live role state, and creating
    # one here would broadcast a fabricated empty mapping with zero confidence.
    state = peek_state(session_id)
    _mercure_event_ids.setdefault(session_id, 0)
    _mercure_event_ids[session_id] += 1
    # `row_overrides` is the additive row-scoped signal other tabs apply.
    role_event: dict = {
        "type": "role_update",
        "row_overrides": {segment_id: role},
        "flip_detected": False,
        "manual_override": True,
        "session_id": session_id,
    }
    # A still-live visit shares its unchanged speaker mapping so existing
    # consumers keep working; a finished visit sends only the row signal.
    if state is not None:
        role_event["mapping"] = state.current_mapping
        role_event["confidence"] = state.running_confidence
    await publish_to_mercure(
        f"scribe/session/{session_id}/roles",
        role_event,
        event_id=_mercure_event_ids[session_id],
    )

    logger.info(
        "roles_override.row_applied",
        extra={
            "session_id": session_id,
            "segment_id": segment_id,
            "role": role,
        },
    )

    return {"status": "ok", "segment_id": segment_id, "role": role}


@app.post("/session/{session_id}/summary")
async def generate_summary(
    session_id: str,
    summary_request: SummaryRequest | None = None,
) -> dict:
    """Generate a structured session summary.

    Triggered by the frontend when the user ends a session. Runs the summary
    agent against role-attributed transcript text and publishes the result to
    Mercure. The browser may pass only its visible transcript rows.

    Args:
        session_id: UUID for the finished recording the user wants summarized.
        summary_request: Optional browser-visible transcript; null uses stored session text.

    Returns:
        SOAP-style summary sections for the browser summary panel.

    Raises:
        HTTPException: When no transcript exists or summary generation fails.
    """
    _validate_session_id(session_id)

    summary_context = build_summary_context(session_id, summary_request, sessions)

    # No transcript means the user ended a session before usable text was captured.
    if not summary_context.stored_segments:
        raise HTTPException(status_code=404, detail="No transcript found for session")

    logger.info(
        "summary.requested source=%s segments=%s transcript_chars=%s",
        summary_context.source,
        len(summary_context.stored_segments),
        len(summary_context.transcript),
        extra={
            "session_id": session_id,
            "source": summary_context.source,
            "segments": len(summary_context.stored_segments),
            "transcript_chars": len(summary_context.transcript),
        },
    )

    loop = asyncio.get_running_loop()
    started_at = time.time()
    summary = await loop.run_in_executor(
        None,
        _run_summary_generation,
        session_id,
        summary_context.transcript,
        summary_context.citation_segments,
    )
    duration_ms = int((time.time() - started_at) * 1000)

    # A missing summary lets the browser show a retryable generation failure.
    if summary is None:
        logger.warning(
            "summary.generation_failed session_id=%s source=%s duration_ms=%s",
            session_id,
            summary_context.source,
            duration_ms,
            extra={
                "session_id": session_id,
                "duration_ms": duration_ms,
                "source": summary_context.source,
            },
        )
        raise HTTPException(status_code=502, detail="Summary generation failed")

    summary_metric_fields = summary.pop("_agent_metrics", {})
    await publish_summary_outputs(
        session_id,
        summary,
        mercure_event_ids=_mercure_event_ids,
        publish_to_mercure=publish_to_mercure,
        logger=logger,
    )

    logger.info(
        "summary.completed source=%s sections=%s duration_ms=%s",
        summary_context.source,
        len(summary.get("sections", [])),
        duration_ms,
        extra={
            "session_id": session_id,
            "source": summary_context.source,
            "sections": len(summary.get("sections", [])),
            "duration_ms": duration_ms,
            **summary_metric_fields,
        },
    )

    return {"session_id": session_id, **summary}


@app.api_route("/session/{session_id}/roles", methods=["GET", "POST"])
async def roles_snapshot(session_id: str) -> dict:
    """Return the current role mapping for a session.

    Quick lookup - no inference, just the last known state.
    Accepts both GET and POST (PHP StrandsClient uses postJson).

    Args:
        session_id: UUID for the transcript whose visible role labels are requested.

    Returns:
        Current role mapping and confidence; empty mapping means labels are still raw.
    """
    request_started_at = time.time()
    _validate_session_id(session_id)
    state = get_or_create_state(session_id)
    response_payload = {
        "session_id": session_id,
        "mapping": state.current_mapping,
        "confidence": state.running_confidence,
    }
    logger.info(
        "roles_snapshot.completed",
        extra={
            "session_id": session_id,
            "correlation_id": correlation_id_var.get("-"),
            "duration_ms": int((time.time() - request_started_at) * 1000),
            "roles": len(state.current_mapping),
            "confidence": state.running_confidence,
        },
    )
    return response_payload


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


def _summary_model_reachable() -> tuple[bool, str]:
    """Best-effort reachability check for the off-GPU role/summary model.

    Used by the browser pre-flight so a consultation is not started when roles
    and the summary would fail. Ollama is verified by listing tags; other
    providers (Bedrock) cannot be cheaply probed here and are assumed configured.

    Returns:
        (available, detail) - detail is a short reason shown to the clinician.
    """
    provider = os.environ.get("ROLE_AGENT_MODEL_PROVIDER", "bedrock")
    if provider != "ollama":
        return True, provider
    host = os.environ.get("OLLAMA_HOST", "http://ollama:11434").rstrip("/")
    model = os.environ.get("ROLE_AGENT_OLLAMA_MODEL", "qwen3.5:9b")
    try:
        response = httpx.get(f"{host}/api/tags", timeout=4.0)
        response.raise_for_status()
        names = [entry.get("name", "") for entry in response.json().get("models", [])]
        if any(model.split(":")[0] in name for name in names):
            return True, f"ollama:{model}"
        return False, f"model '{model}' is not pulled"
    except Exception as exc:
        return False, f"ollama unreachable ({type(exc).__name__})"


@app.get("/agent/model-health")
async def agent_model_health() -> dict:
    """Report whether the off-GPU role/summary model can be reached.

    The browser calls this before starting a consultation so it does not
    transcribe when DOCTOR/PATIENT roles and the summary would fail.
    """
    loop = asyncio.get_running_loop()
    available, detail = await loop.run_in_executor(None, _summary_model_reachable)
    return {"available": available, "detail": detail}
