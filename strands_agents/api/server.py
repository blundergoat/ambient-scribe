"""
FastAPI routes for the Ambient Medical Scribe.

This module keeps the browser-facing API surface: file upload, live WebSocket
recording, session history, role overrides, summaries, replay, and health.
Workflow helpers own the longer queue and streaming loops so these routes stay
focused on what the clinician sees in the transcript and summary UI.
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
from api.agent_observability import (
    agent_metric_fields as _agent_metric_fields,
    configure_strands_telemetry as _configure_strands_telemetry,
)
from api.mercure_publisher import did_publish_mercure_event
from api.replay_session import (
    ReplayServices,
    replay_segments,
    replay_tasks,
    start_replay_upload,
)
from api.role_heuristics import heuristic_role_inference as _heuristic_role_inference
from api.streaming_session import StreamingServices, transcribe_stream_session
from nemo_pipeline import NemoPipeline
from session import SessionStore
from session_lifecycle import SessionLifecycle
from storage import StorageBackend
from tools.assign_roles import (
    get_or_create_state,
)
from logging_config import configure_logging
from api.summary_generation import run_summary_generation as _run_summary_generation
from clinical_hints import generate_clinical_hints

configure_logging()
logger = logging.getLogger(__name__)

# =============================================================================
# CORRELATION ID — request-scoped tracing
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
CLINICAL_HINTS_ENABLED = os.environ.get(
    "CLINICAL_HINTS_ENABLED", "1"
).strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}


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
# LIFESPAN — Load NeMo models once at startup
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

    Returns:
        Storage backend used by transcript history, role labeling, and summaries.
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


def _replay_services() -> ReplayServices:
    """Bundle current server callbacks for uploaded demo replay.

    Returns:
        Services using the current NeMo pipeline, publisher, and role queue so
        replay behaves like the live transcript the browser already understands.
    """
    return ReplayServices(
        pipeline=app.state.nemo_pipeline,
        executor=nemo_executor,
        sessions=sessions,
        get_running_loop=asyncio.get_running_loop,
        publish_to_mercure=publish_to_mercure,
        enqueue_role_inference=enqueue_role_inference,
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

    Batch mode entry point for testing and demo replay.

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

    # Uploaded demo audio is saved briefly so NeMo can read it like a normal file.
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


@app.post("/session/{session_id}/roles/override")
async def roles_override(session_id: str, request: Request) -> dict:
    """Apply a manual speaker role override from the frontend.

    Updates the role mapping state and publishes the change to Mercure
    so all connected clients see the correction immediately.

    Args:
        session_id: UUID for the transcript the user corrected.
        request: JSON body with speaker_id and role selected in the transcript UI.

    Returns:
        Updated role mapping shown by the browser.

    Raises:
        HTTPException: When speaker or role is empty, so the UI can show a validation error.
    """
    _validate_session_id(session_id)
    body = await request.json()
    speaker_id = str(body.get("speaker_id", ""))
    role = str(body.get("role", "")).upper()

    # Empty role selections cannot update the visible transcript labels.
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


@app.post("/session/{session_id}/summary")
async def generate_summary(session_id: str) -> dict:
    """Generate a structured session summary.

    Triggered by the frontend when the user ends a session. Runs the summary
    agent against the full role-attributed transcript and publishes the result
    to Mercure.

    Args:
        session_id: UUID for the finished recording the user wants summarized.

    Returns:
        SOAP-style summary sections for the browser summary panel.

    Raises:
        HTTPException: When no transcript exists or summary generation fails.
    """
    _validate_session_id(session_id)

    stored_segments = sessions.get_segments(session_id)
    # No transcript means the user ended a session before usable text was captured.
    if not stored_segments:
        raise HTTPException(status_code=404, detail="No transcript found for session")

    transcript = sessions.get_transcript_text(session_id, max_chars=8000)

    loop = asyncio.get_running_loop()
    started_at = time.time()
    summary = await loop.run_in_executor(
        None,
        _run_summary_generation,
        session_id,
        transcript,
    )
    duration_ms = int((time.time() - started_at) * 1000)

    # A missing summary lets the browser show a retryable generation failure.
    if summary is None:
        logger.warning(
            "summary.generation_failed",
            extra={
                "session_id": session_id,
                "duration_ms": duration_ms,
            },
        )
        raise HTTPException(status_code=502, detail="Summary generation failed")

    summary_metric_fields = summary.pop("_agent_metrics", {})
    clinical_hints: list[dict[str, str]] = []

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

    # Clinical hints are assistive only and appear beside the generated summary.
    if CLINICAL_HINTS_ENABLED:
        clinical_hints = generate_clinical_hints(transcript)
        # No hints means the clinician's sidebar stays hidden for this session.
        if clinical_hints:
            _mercure_event_ids[session_id] += 1
            hints_delivered = await publish_to_mercure(
                f"scribe/session/{session_id}/hints",
                {
                    "type": "clinical_hints",
                    "session_id": session_id,
                    "hints": clinical_hints,
                },
                event_id=_mercure_event_ids[session_id],
            )
            # A failed Mercure publish still leaves hints in the HTTP summary response.
            if not hints_delivered:
                logger.warning(
                    "clinical_hints.publish_failed",
                    extra={"session_id": session_id},
                )

    logger.info(
        "summary.completed",
        extra={
            "session_id": session_id,
            "sections": len(summary.get("sections", [])),
            "duration_ms": duration_ms,
            **summary_metric_fields,
        },
    )

    return {"session_id": session_id, **summary, "clinical_hints": clinical_hints}


_replay_tasks = replay_tasks


@app.post("/session/{session_id}/replay")
async def replay_file(
    session_id: str,
    file: UploadFile,
    speed: float = Query(1.0, ge=0.25, le=10.0),
) -> dict:
    """Replay a WAV file through the pipeline with real-time pacing.

    Processes the WAV through NeMo in batch, then replays segments to Mercure
    with delays matching actual timestamps (adjusted by speed factor).

    Args:
        session_id: UUID for the browser replay session.
        file: WAV file the user selected in the demo replay control.
        speed: Replay speed multiplier; low values slow the visible transcript.

    Returns:
        Replay metadata; zero segments means no transcript will appear.
    """
    _validate_session_id(session_id)
    return await start_replay_upload(session_id, file, speed, _replay_services())


async def _replay_segments(
    session_id: str,
    segments: list[dict[str, Any]],
    speed: float,
) -> None:
    """Replay segments through the current server seams used by tests and UI."""
    await replay_segments(session_id, segments, speed, _replay_services())


@app.api_route("/session/{session_id}/roles", methods=["GET", "POST"])
async def roles_snapshot(session_id: str) -> dict:
    """Return the current role mapping for a session.

    Quick lookup — no inference, just the last known state.
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


def _run_role_inference(
    session_id: str,
    segments: list[dict[str, Any]],
    transcript: str,
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
        from agents import MEDICAL_ROLE_INSTRUCTION, create_role_inference_agent

        agent = create_role_inference_agent()
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
        agent_result = agent(f"{MEDICAL_ROLE_INSTRUCTION}\n\n{json.dumps(payload)}")
        metric_fields = _agent_metric_fields(agent_result, "role-inference")

        # Check if the assign_roles tool was invoked (it persists state directly)
        if len(state.mapping_history) > history_len_before:
            return {
                "mapping": state.current_mapping,
                "confidence": state.running_confidence,
                "reasoning": "",
                "_tool_invoked": True,
                "path": "tool",
                **metric_fields,
            }

        # Fallback: parse the agent's free-text JSON response
        response_text = str(agent_result)
        try:
            parsed = json.loads(response_text)
        except json.JSONDecodeError:
            match = re.search(r"\{.*\}", response_text, re.DOTALL)
            if match:
                parsed = json.loads(match.group())
            else:
                logger.warning(
                    "role_inference.no_json_found",
                    extra={
                        "session_id": session_id,
                        **metric_fields,
                    },
                )
                return None
        parsed["path"] = "freetext"
        parsed.update(metric_fields)
        return parsed
    except Exception as e:
        logger.warning(
            "role_inference.agent_failed_falling_back_to_heuristic",
            extra={
                "session_id": session_id,
                "error_type": type(e).__name__,
                "error": str(e)[:200],
            },
        )

    # --- Tier 2: Heuristic fallback ---
    try:
        heuristic_result = _heuristic_role_inference(segments, transcript)
        if heuristic_result is not None:
            heuristic_result["path"] = "heuristic"
            logger.info(
                "role_inference.heuristic_used",
                extra={
                    "session_id": session_id,
                    "roles": len(heuristic_result.get("mapping", {})),
                },
            )
            return heuristic_result
    except Exception as e:
        logger.error(
            "role_inference.heuristic_failed",
            extra={
                "session_id": session_id,
                "error_type": type(e).__name__,
                "error": str(e)[:200],
            },
        )

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
