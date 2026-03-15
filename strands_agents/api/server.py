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
  { "type": "role_update", "mapping": {"spk_0": "DOCTOR"}, "confidence": 0.85, "flip_detected": false }
  { "type": "finalized",   "session_id": "..." }
  { "type": "error",       "message": "..." }
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import subprocess
import time
import uuid
from collections.abc import AsyncGenerator
from concurrent.futures import ThreadPoolExecutor
from contextvars import ContextVar
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import httpx
<<<<<<< Updated upstream
from fastapi import FastAPI, Form, Request, UploadFile, WebSocket, WebSocketDisconnect
=======
from fastapi import FastAPI, Request, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
>>>>>>> Stashed changes
from pydantic import BaseModel, Field
from starlette.middleware.base import BaseHTTPMiddleware
from sse_starlette.sse import EventSourceResponse

from nemo_pipeline import NemoPipeline
from nemo_session import TranscriptionSession
from session import SessionStore
from session_lifecycle import SessionLifecycle
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
MERCURE_JWT = os.environ.get("MERCURE_JWT", "")
<<<<<<< Updated upstream


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

=======
MERCURE_JWT_SECRET = os.environ.get("MERCURE_JWT_SECRET", "")
NEMO_STREAM_INPUT_FORMAT = os.environ.get("NEMO_STREAM_INPUT_FORMAT", "pcm")
NEMO_BUFFER_MAX_DURATION = float(os.environ.get("NEMO_BUFFER_MAX_DURATION", "900"))
_mercure_jwt_cache: str | None = None
>>>>>>> Stashed changes

# Thread pool for GPU-bound NeMo inference.
# Prevents blocking the async event loop, keeping /health and other
# WebSocket connections responsive during inference.
NEMO_MAX_WORKERS = int(os.environ.get("NEMO_MAX_WORKERS", "2"))
nemo_executor = ThreadPoolExecutor(max_workers=NEMO_MAX_WORKERS, thread_name_prefix="nemo")

ROLE_INFERENCE_IDLE_TIMEOUT_SECONDS = 60.0
_inference_queues: dict[str, asyncio.Queue[list[dict[str, Any]] | None]] = {}
_inference_workers: dict[str, asyncio.Task[None]] = {}


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
    logger.info("server.startup.nemo_models_loaded")
    yield
    nemo_executor.shutdown(wait=False)


# =============================================================================
# APPLICATION
# =============================================================================

app = FastAPI(title="Ambient Scribe Agent", version="0.1.0", lifespan=lifespan)
app.add_middleware(CorrelationIdMiddleware)

# Install correlation_id filter on root logger so ALL log records include it.
logging.getLogger().addFilter(CorrelationIdFilter())

# In-memory session store for transcript history
sessions = SessionStore()

# Coordinated session lifecycle (replaces bare active_sessions dict)
lifecycle = SessionLifecycle(sessions)


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


async def publish_to_mercure(topic: str, data: dict[str, Any]) -> bool:
    """Publish a JSON event to a Mercure topic with retry.

    Retries up to MERCURE_PUBLISH_MAX_RETRIES times with exponential backoff
    before returning False.

    Args:
        topic: The Mercure topic URI (e.g., "scribe/session/{id}/raw")
        data: The event data to JSON-encode and publish

    Returns:
        True if published successfully, False otherwise.
    """
<<<<<<< Updated upstream
    if not MERCURE_JWT:
        logger.warning("mercure.publish.skipped", extra={"reason": "no JWT configured"})
=======
    token = _resolve_mercure_jwt()
    if token == "":
        logger.error("mercure.publish.skipped", extra={"reason": "no JWT configured"})
        return False

    last_error: Exception | None = None
    for attempt in range(MERCURE_PUBLISH_MAX_RETRIES):
        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    MERCURE_HUB_URL,
                    data={
                        "topic": topic,
                        "data": json.dumps(data),
                    },
                    headers={"Authorization": f"Bearer {token}"},
                    timeout=5.0,
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
) -> None:
    """Queue raw transcript segments for sequential per-session role inference."""
    if segments == []:
>>>>>>> Stashed changes
        return

    queue = _inference_queues.get(session_id)
    if queue is None:
        queue = asyncio.Queue()
        _inference_queues[session_id] = queue

    await queue.put([dict(segment) for segment in segments])

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
<<<<<<< Updated upstream
        async with httpx.AsyncClient() as client:
            await client.post(
                MERCURE_HUB_URL,
                data={
                    "topic": topic,
                    "data": json.dumps(data),
                },
                headers={"Authorization": f"Bearer {MERCURE_JWT}"},
                timeout=5.0,
            )
    except Exception as e:
        logger.error("mercure.publish.failed", extra={
            "topic": topic,
            "error": str(e),
        })
=======
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
                loop = asyncio.get_event_loop()
                started_at = time.time()
                result = await loop.run_in_executor(
                    None,
                    _run_role_inference,
                    session_id,
                    merged_segments,
                    transcript,
                )
                duration_ms = int((time.time() - started_at) * 1000)

                if result:
                    role_update = apply_role_mapping_result(
                        session_id=session_id,
                        segments=merged_segments,
                        mapping=result.get("mapping", {}),
                        confidence=float(result.get("confidence", 0.0)),
                        reasoning=str(result.get("reasoning", "")),
                    )
                    sessions.apply_role_mapping(session_id, role_update.mapping)

                    await publish_to_mercure(
                        f"scribe/session/{session_id}/roles",
                        {
                            "type": "role_update",
                            "mapping": role_update.mapping,
                            "attributed_segments": role_update.attributed_segments,
                            "confidence": role_update.confidence,
                            "flip_detected": role_update.flip_detected,
                            "reasoning": role_update.reasoning,
                            "session_id": session_id,
                        },
                    )
                    logger.info("role_inference.completed", extra={
                        "session_id": session_id,
                        "segments": len(merged_segments),
                        "confidence": role_update.confidence,
                        "flip_detected": role_update.flip_detected,
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
>>>>>>> Stashed changes


# =============================================================================
# ENDPOINTS
# =============================================================================

@app.post("/transcribe/file", response_model=TranscribeFileResponse)
async def transcribe_file(file: UploadFile, session_id: str = Form("")) -> TranscribeFileResponse:
    """Upload a WAV file and get a complete transcript.

    Batch mode entry point for testing and demo replay.

    Args:
        file: WAV file upload (16kHz mono PCM expected)
        session_id: Optional session ID for grouping

    Returns:
        TranscribeFileResponse with speaker-attributed segments.
    """
    if not session_id:
        session_id = str(uuid.uuid4())

    # Save uploaded file temporarily
    temp_path = Path(f"/tmp/scribe_{session_id}.wav")
    try:
        content = await file.read()
        temp_path.write_bytes(content)

        # Run NeMo inference in thread pool
        loop = asyncio.get_event_loop()
        started_at = time.time()
        result = await loop.run_in_executor(
            nemo_executor,
            app.state.nemo_pipeline.transcribe_file,
            str(temp_path),
        )
        duration = time.time() - started_at

        segments = [s.dict() for s in result.segments]

        logger.info("transcribe_file.completed", extra={
            "session_id": session_id,
            "segments": len(segments),
            "duration_seconds": round(duration, 2),
        })

        return TranscribeFileResponse(
            session_id=session_id,
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
    await websocket.accept()

    # WebSocket connections bypass HTTP middleware, so set correlation_id from
    # the upgrade headers or fall back to the session_id itself.
    cid = websocket.headers.get("x-correlation-id", session_id)
    correlation_id_var.set(cid)

<<<<<<< Updated upstream
    session = TranscriptionSession(session_id, pipeline=app.state.nemo_pipeline)
    active_sessions[session_id] = session
=======
    session = TranscriptionSession(
        session_id,
        pipeline=app.state.nemo_pipeline,
        input_format=app.state.nemo_input_format,
        max_buffer_duration=NEMO_BUFFER_MAX_DURATION,
    )
    await lifecycle.register(session_id, session)
>>>>>>> Stashed changes

    logger.info("websocket.connected", extra={"session_id": session_id})

    loop = asyncio.get_event_loop()
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
<<<<<<< Updated upstream
                await publish_to_mercure(
=======
                segment_payload = segment.dict()
                segment_payloads.append(segment_payload)
                sessions.append_segment(session_id, segment_payload)
                published = await publish_to_mercure(
>>>>>>> Stashed changes
                    f"scribe/session/{session_id}/raw",
                    {
                        "type": "segment",
                        **segment.dict(),
                    },
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
                await enqueue_role_inference(session_id, segment_payloads)

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
<<<<<<< Updated upstream
        await loop.run_in_executor(nemo_executor, session.finalize)
=======
        final_segments = await loop.run_in_executor(nemo_executor, session.finalize)
        sessions.replace_segments(
            session_id,
            [segment.dict() for segment in final_segments],
        )
        current_state = get_or_create_state(session_id)
        if current_state.current_mapping:
            sessions.apply_role_mapping(session_id, current_state.current_mapping)
>>>>>>> Stashed changes

        # Publish finalized event
        await publish_to_mercure(
            f"scribe/session/{session_id}/raw",
            {"type": "finalized", "session_id": session_id},
        )
    except Exception as e:
        logger.error("websocket.error", extra={
            "session_id": session_id,
            "error": str(e),
        })
        await publish_to_mercure(
            f"scribe/session/{session_id}/raw",
            {"type": "error", "message": str(e)},
        )
    finally:
        await lifecycle.destroy(session_id, close_role_inference)


@app.api_route("/session/{session_id}/history", methods=["GET", "POST"])
async def session_history(session_id: str) -> dict:
    """View the transcript history for a session.

    Returns the accumulated transcript segments.
    Accepts both GET and POST (PHP StrandsClient uses postJson).
    """
<<<<<<< Updated upstream
    session = active_sessions.get(session_id)
=======
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

>>>>>>> Stashed changes
    if session:
        return {
            "session_id": session_id,
            "segments": [s.dict() for s in session.accumulated_transcript],
            "duration_seconds": round(session.buffer.duration_seconds, 1),
            "chunk_count": session.chunk_count,
        }
    return {"session_id": session_id, "segments": [], "message": "Session not found or ended"}


@app.post("/session/{session_id}/roles/stream")
async def roles_stream(session_id: str) -> EventSourceResponse:
    """Stream progressive role inference results via SSE.

    Called by PHP's RoleInferenceService via streamSse(). Each SSE event
    is a JSON role_update with the current mapping and confidence.

    The Strands agent runs asynchronously — this endpoint yields events
    as the agent's confidence evolves. Uses the session's accumulated
    transcript as context for role reasoning.
    """
    lifecycle.sse_consumer_start(session_id)
    state = get_or_create_state(session_id)
    transcript = sessions.get_transcript_text(session_id)

    async def event_generator():
        try:
            # Yield current state immediately (cold start or resume)
            yield {
                "event": "role_update",
                "data": json.dumps({
                    "type": "role_update",
                    "mapping": state.current_mapping,
                    "confidence": state.running_confidence,
                    "flip_detected": False,
                    "session_id": session_id,
                }),
            }

            # If no transcript yet, signal that we're waiting
            if not transcript:
                yield {
                    "event": "role_update",
                    "data": json.dumps({
                        "type": "role_update",
                        "mapping": {},
                        "confidence": 0.0,
                        "flip_detected": False,
                        "message": "No transcript data yet",
                        "session_id": session_id,
                    }),
                }
                return

            # Run role inference via the Strands agent in executor
            # (Bedrock call is I/O-bound but the Strands SDK is synchronous)
            try:
                loop = asyncio.get_event_loop()
                result = await loop.run_in_executor(
                    None,
                    _run_role_inference,
                    session_id,
                    sessions.get_segments(session_id),
                    transcript,
                )

                if result:
                    role_update = apply_role_mapping_result(
                        session_id=session_id,
                        segments=sessions.get_segments(session_id),
                        mapping=result.get("mapping", {}),
                        confidence=float(result.get("confidence", 0.0)),
                        reasoning=str(result.get("reasoning", "")),
                    )
                    sessions.apply_role_mapping(session_id, role_update.mapping)
                    yield {
                        "event": "role_update",
                        "data": json.dumps({
                            "type": "role_update",
                            "mapping": role_update.mapping,
                            "confidence": role_update.confidence,
                            "flip_detected": role_update.flip_detected,
                            "reasoning": role_update.reasoning,
                            "session_id": session_id,
                        }),
                    }
            except Exception as e:
                logger.error("roles_stream.inference_failed", extra={
                    "session_id": session_id,
                    "error": str(e),
                })
                yield {
                    "event": "role_update",
                    "data": json.dumps({
                        "type": "error",
                        "message": str(e),
                        "session_id": session_id,
                    }),
                }
        finally:
            lifecycle.sse_consumer_end(session_id)

    return EventSourceResponse(event_generator())


@app.api_route("/session/{session_id}/roles", methods=["GET", "POST"])
async def roles_snapshot(session_id: str) -> dict:
    """Return the current role mapping for a session.

    Quick lookup — no inference, just the last known state.
    Accepts both GET and POST (PHP StrandsClient uses postJson).
    """
    state = get_or_create_state(session_id)
    return {
        "session_id": session_id,
        "mapping": state.current_mapping,
        "confidence": state.running_confidence,
    }


def _run_role_inference(
    session_id: str,
    segments: list[dict[str, Any]],
    transcript: str,
) -> dict | None:
    """Run the Strands role inference agent synchronously.

    Called via run_in_executor() to avoid blocking the event loop.
    Returns the agent's role mapping result or None on failure.
    """
    try:
        from agents import create_role_inference_agent

        agent = create_role_inference_agent()
        state = get_or_create_state(session_id)
        payload = {
            "session_id": session_id,
            "current_mapping": state.current_mapping,
            "mapping_history": state.mapping_history,
            "new_segments": segments,
            "transcript_so_far": transcript,
        }
        result = agent(
            "Assign DOCTOR/PATIENT roles for this consultation transcript.\n\n"
            f"{json.dumps(payload)}"
        )

        # Parse the agent's JSON response
        response_text = str(result)
        parsed = json.loads(response_text)
        return parsed
    except Exception as e:
        logger.error("role_inference.agent_failed", extra={
            "session_id": session_id,
            "error": str(e),
        })
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
