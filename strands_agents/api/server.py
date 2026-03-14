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
import sys
import time
import uuid
from collections.abc import AsyncGenerator
from concurrent.futures import ThreadPoolExecutor
from contextvars import ContextVar
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI, Request, UploadFile, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field
from starlette.middleware.base import BaseHTTPMiddleware
from sse_starlette.sse import EventSourceResponse

from nemo_pipeline import NemoPipeline
from nemo_session import TranscriptionSession
from session import SessionStore
from tools.assign_roles import get_or_create_state, cleanup_session

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
MERCURE_JWT_SECRET = os.environ.get("MERCURE_JWT_SECRET", "")
NEMO_STREAM_INPUT_FORMAT = os.environ.get("NEMO_STREAM_INPUT_FORMAT", "pcm")
_mercure_jwt_cache: str | None = None

# Thread pool for GPU-bound NeMo inference.
# Prevents blocking the async event loop, keeping /health and other
# WebSocket connections responsive during inference.
nemo_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="nemo")

# Active transcription sessions (WebSocket → TranscriptionSession)
active_sessions: dict[str, TranscriptionSession] = {}


def _should_load_models() -> bool:
    """Avoid GPU model loading under pytest unless explicitly requested."""
    if os.environ.get("NEMO_SKIP_MODEL_LOAD", "").lower() in {"1", "true", "yes"}:
        return False

    return "pytest" not in sys.modules


def _resolve_mercure_jwt() -> str:
    """Return an explicit publisher JWT or mint one from the shared secret."""
    global _mercure_jwt_cache

    if MERCURE_JWT != "":
        return MERCURE_JWT

    if _mercure_jwt_cache is not None:
        return _mercure_jwt_cache

    if MERCURE_JWT_SECRET == "":
        _mercure_jwt_cache = ""
        return _mercure_jwt_cache

    try:
        import jwt
    except ImportError:
        logger.warning("mercure.publish.skipped", extra={"reason": "pyjwt_not_installed"})
        _mercure_jwt_cache = ""
        return _mercure_jwt_cache

    token = jwt.encode(
        {"mercure": {"publish": ["*"]}},
        MERCURE_JWT_SECRET,
        algorithm="HS256",
    )
    if isinstance(token, bytes):
        token = token.decode("utf-8")

    _mercure_jwt_cache = token
    return token


# =============================================================================
# LIFESPAN — Load NeMo models once at startup
# =============================================================================

@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Load NeMo models into GPU memory at process startup.

    This runs once when the FastAPI server starts. It may take 30-60 seconds
    depending on model size and disk speed.
    """
    load_models = _should_load_models()
    logger.info("server.startup.loading_nemo_models", extra={
        "load_models": load_models,
    })
    app.state.nemo_pipeline = NemoPipeline(
        load_models=load_models,
        strict_startup=load_models,
    )
    app.state.nemo_input_format = NEMO_STREAM_INPUT_FORMAT
    logger.info("server.startup.ready", extra={
        "models_loaded": app.state.nemo_pipeline.is_loaded,
        "input_format": app.state.nemo_input_format,
    })
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

async def publish_to_mercure(topic: str, data: dict[str, Any]) -> None:
    """Publish a JSON event to a Mercure topic.

    Args:
        topic: The Mercure topic URI (e.g., "scribe/session/{id}/raw")
        data: The event data to JSON-encode and publish
    """
    token = _resolve_mercure_jwt()
    if token == "":
        logger.warning("mercure.publish.skipped", extra={"reason": "no JWT configured"})
        return

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
    except Exception as e:
        logger.error("mercure.publish.failed", extra={
            "topic": topic,
            "error": str(e),
        })


# =============================================================================
# ENDPOINTS
# =============================================================================

@app.post("/transcribe/file", response_model=TranscribeFileResponse)
async def transcribe_file(file: UploadFile, session_id: str = "") -> TranscribeFileResponse:
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

    session = TranscriptionSession(
        session_id,
        pipeline=app.state.nemo_pipeline,
        input_format=app.state.nemo_input_format,
    )
    active_sessions[session_id] = session

    logger.info("websocket.connected", extra={"session_id": session_id})

    loop = asyncio.get_event_loop()
    chunk_count = 0

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
            for segment in segments:
                segment_payload = segment.dict()
                sessions.append_segment(session_id, segment_payload)
                await publish_to_mercure(
                    f"scribe/session/{session_id}/raw",
                    {
                        "type": "segment",
                        **segment_payload,
                    },
                )

            # Log periodically (every 10th chunk) to avoid log spam
            if chunk_count % 10 == 0:
                logger.info("websocket.chunk_stats", extra={
                    "session_id": session_id,
                    "chunk_count": chunk_count,
                    "buffer_seconds": round(session.buffer.duration_seconds, 1),
                    "last_inference_ms": duration_ms,
                })

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

        # Publish finalized event
        await publish_to_mercure(
            f"scribe/session/{session_id}/raw",
            {"type": "finalized", "session_id": session_id},
        )
    except Exception as e:
        logger.exception("websocket.error", extra={
            "session_id": session_id,
            "error": str(e),
        })
        await publish_to_mercure(
            f"scribe/session/{session_id}/raw",
            {"type": "error", "message": str(e)},
        )
    finally:
        active_sessions.pop(session_id, None)
        cleanup_session(session_id)


@app.get("/session/{session_id}/history")
async def session_history(session_id: str) -> dict:
    """View the transcript history for a session.

    Returns the accumulated transcript segments.
    """
    session = active_sessions.get(session_id)
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
        session_segments = [s.dict() for s in session.accumulated_transcript]
        return {
            "session_id": session_id,
            "segments": session_segments,
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
    state = get_or_create_state(session_id)
    transcript = sessions.get_transcript_text(session_id)

    async def event_generator():
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
                transcript,
            )

            if result:
                flip = state.update(result["mapping"], result["confidence"])
                yield {
                    "event": "role_update",
                    "data": json.dumps({
                        "type": "role_update",
                        "mapping": result["mapping"],
                        "confidence": state.running_confidence,
                        "flip_detected": flip,
                        "reasoning": result.get("reasoning", ""),
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

    return EventSourceResponse(event_generator())


@app.get("/session/{session_id}/roles")
async def roles_snapshot(session_id: str) -> dict:
    """Return the current role mapping for a session.

    Quick lookup — no inference, just the last known state.
    """
    state = get_or_create_state(session_id)
    return {
        "session_id": session_id,
        "mapping": state.current_mapping,
        "confidence": state.running_confidence,
    }


def _run_role_inference(session_id: str, transcript: str) -> dict | None:
    """Run the Strands role inference agent synchronously.

    Called via run_in_executor() to avoid blocking the event loop.
    Returns the agent's role mapping result or None on failure.
    """
    try:
        from agents import create_role_inference_agent

        agent = create_role_inference_agent()
        result = agent(
            f"Assign DOCTOR/PATIENT roles for this transcript:\n\n{transcript}"
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


def _history_duration(segments: list[dict[str, Any]]) -> float:
    """Estimate transcript duration from the last stored segment."""
    if segments == []:
        return 0.0

    last_segment = segments[-1]
    end = last_segment.get("end", 0.0)
    return float(end) if isinstance(end, (int, float)) else 0.0


@app.get("/health")
async def health() -> dict:
    """Health check endpoint for Docker healthcheck.

    Returns 200 if the server is running. Does NOT check if NeMo
    models are loaded (that would make healthcheck slow).
    """
    return {"status": "ok", "service": "ambient-scribe-agent"}
