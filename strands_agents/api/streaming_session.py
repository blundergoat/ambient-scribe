"""
Live WebSocket transcription workflow for a browser recording session.

This module receives audio chunks, runs NeMo off the event loop, publishes raw
segments to Mercure, and asks the role queue to relabel the transcript. The
FastAPI route remains in `server.py`; this file owns the user's live
recording loop so the route module stays small and easier to verify.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from fastapi import WebSocket, WebSocketDisconnect
from nemo_session import TranscriptionSession
from session_lifecycle import SessionLifecycle
from storage import StorageBackend
from tools.assign_roles import get_or_create_state

logger = logging.getLogger(__name__)

PublishToMercure = Callable[[str, dict[str, Any], int | None], Awaitable[bool]]
EnqueueRoleInference = Callable[[str, list[dict[str, Any]]], Awaitable[None]]
CloseRoleInference = Callable[[str], Awaitable[None]]
GetRunningLoop = Callable[[], Any]
SetCorrelationId = Callable[[str], object]
LogVram = Callable[[], None]


@dataclass(slots=True)
class StreamingServices:
    """
    Runtime dependencies for one live browser recording.

    `server.py` builds this at call time so tests and routes can patch the same
    visible seams: NeMo executor, Mercure publisher, role queue, and reconnect
    grace. The workflow uses these services to keep transcript, role, and
    reconnect state consistent for the user.

    Attributes:
        pipeline: Loaded NeMo pipeline used for the user's audio.
        input_format: Browser audio format expected by the session buffer.
        max_buffer_duration: Safety cap for one recording buffer.
        reconnect_grace_seconds: Time a stopped socket can resume its session.
        executor: Shared NeMo executor; GPU work must not run on the event loop.
        sessions: Transcript storage read by history and summary views.
        lifecycle: Session registry used for reconnect and cleanup.
        get_running_loop: Loop getter, passed from `server.py` for test seams.
        set_correlation_id: Request/session log correlation setter.
        publish_to_mercure: Publisher for raw/final/error events.
        enqueue_role_inference: Role queue callback for newly visible text.
        close_role_inference: Role queue close callback for cleanup.
        log_vram: Lightweight GPU visibility hook.
        mercure_event_ids: Per-session counters for browser reconnects.
    """

    pipeline: Any
    input_format: str
    max_buffer_duration: float
    reconnect_grace_seconds: float
    executor: Any
    sessions: StorageBackend
    lifecycle: SessionLifecycle
    get_running_loop: GetRunningLoop
    set_correlation_id: SetCorrelationId
    publish_to_mercure: PublishToMercure
    enqueue_role_inference: EnqueueRoleInference
    close_role_inference: CloseRoleInference
    log_vram: LogVram
    mercure_event_ids: dict[str, int]


@dataclass(slots=True)
class StreamState:
    """
    Mutable state for the current WebSocket recording loop.

    It tracks what the user has streamed so far and whether the browser has
    already been warned about degraded Mercure delivery. Keeping it explicit
    makes the live loop small and avoids hidden cross-chunk state.

    Attributes:
        chunk_count: Number of audio chunks processed for this connection.
        mercure_warning_sent: True after the browser saw one streaming warning.
    """

    chunk_count: int = 0
    mercure_warning_sent: bool = False


async def transcribe_stream_session(
    websocket: WebSocket,
    session_id: str,
    services: StreamingServices,
) -> None:
    """Run the live audio -> raw transcript -> role queue workflow.

    Args:
        websocket: Accepted browser socket sending PCM chunks.
        session_id: Browser recording session ID; already UUID-validated.
        services: Runtime callbacks and stores for this recording.
    """
    await websocket.accept()
    _set_stream_correlation_id(websocket, session_id, services)
    session = await _resume_or_create_session(session_id, services)
    logger.info("websocket.connected", extra={"session_id": session_id})

    loop = services.get_running_loop()
    state = StreamState()

    try:
        while True:
            await _process_next_audio_chunk(
                websocket, session_id, session, services, loop, state
            )
    except WebSocketDisconnect:
        await _finalize_after_disconnect(session_id, session, services, loop, state)
    except Exception as error:
        await _publish_transcription_error(session_id, services, error)
    finally:
        await _schedule_stream_cleanup(session_id, services)


def _set_stream_correlation_id(
    websocket: WebSocket,
    session_id: str,
    services: StreamingServices,
) -> None:
    """Attach a correlation ID so logs follow the user's recording session."""
    correlation_id = websocket.headers.get("x-correlation-id", session_id)
    services.set_correlation_id(correlation_id)


async def _resume_or_create_session(
    session_id: str,
    services: StreamingServices,
) -> TranscriptionSession:
    """Resume a reconnecting recording or create a new NeMo session."""
    existing_session = services.lifecycle.get(session_id)
    # A reconnect during the grace window should preserve the user's transcript buffer.
    if existing_session is not None:
        logger.info(
            "websocket.resumed",
            extra={
                "session_id": session_id,
                "buffer_seconds": round(existing_session.buffer.duration_seconds, 1),
                "chunk_count": existing_session.chunk_count,
            },
        )
        session = existing_session
    else:
        session = TranscriptionSession(
            session_id,
            pipeline=services.pipeline,
            input_format=services.input_format,
            max_buffer_duration=services.max_buffer_duration,
        )

    await services.lifecycle.register(session_id, session)
    return session


async def _process_next_audio_chunk(
    websocket: WebSocket,
    session_id: str,
    session: TranscriptionSession,
    services: StreamingServices,
    loop: Any,
    state: StreamState,
) -> None:
    """Process one browser audio chunk and publish resulting transcript text."""
    audio_chunk = await websocket.receive_bytes()
    state.chunk_count += 1
    started_at = time.time()
    segments = await loop.run_in_executor(
        services.executor,
        session.process_chunk,
        audio_chunk,
    )
    duration_ms = int((time.time() - started_at) * 1000)

    segment_payloads = await _publish_raw_segments(
        websocket, session_id, segments, services, state
    )
    # Newly visible raw text gives the role queue something to label for the user.
    if segment_payloads != []:
        await services.enqueue_role_inference(session_id, segment_payloads)

    total_ms = int((time.time() - started_at) * 1000)
    logger.info(
        "websocket.chunk_e2e",
        extra={
            "session_id": session_id,
            "chunk_count": state.chunk_count,
            "inference_ms": duration_ms,
            "total_ms": total_ms,
            "segments": len(segments),
        },
    )

    # Periodic VRAM logs help explain recording stalls without spamming every chunk.
    if state.chunk_count % 10 == 0:
        services.log_vram()


async def _publish_raw_segments(
    websocket: WebSocket,
    session_id: str,
    segments: list[Any],
    services: StreamingServices,
    state: StreamState,
) -> list[dict[str, Any]]:
    """Store and publish raw NeMo segments for immediate browser display."""
    segment_payloads: list[dict[str, Any]] = []
    # Each segment becomes visible in the transcript before slower role labels arrive.
    for segment in segments:
        segment_payload = segment.dict()
        segment_payloads.append(segment_payload)
        services.sessions.append_segment(session_id, segment_payload)
        event_id = _next_stream_event_id(session_id, services)
        published = await services.publish_to_mercure(
            f"scribe/session/{session_id}/raw",
            {
                "type": "segment",
                **segment_payload,
            },
            event_id=event_id,
        )
        # The browser only needs one warning that real-time delivery is degraded.
        if not published and not state.mercure_warning_sent:
            state.mercure_warning_sent = True
            await _warn_live_streaming_failure(websocket, session_id)

    return segment_payloads


async def _warn_live_streaming_failure(websocket: WebSocket, session_id: str) -> None:
    """Send one browser-visible warning when Mercure cannot stream updates."""
    try:
        await websocket.send_json(
            {
                "type": "system_error",
                "message": "Real-time streaming unavailable",
            }
        )
    except Exception as send_error:
        logger.warning(
            "websocket.system_error_notice_failed",
            extra={
                "session_id": session_id,
                "error_type": type(send_error).__name__,
                "error": str(send_error)[:200],
            },
        )


async def _finalize_after_disconnect(
    session_id: str,
    session: TranscriptionSession,
    services: StreamingServices,
    loop: Any,
    state: StreamState,
) -> None:
    """Finalize transcript text when the user stops or the socket disconnects."""
    logger.info(
        "websocket.disconnected",
        extra={
            "session_id": session_id,
            "total_chunks": state.chunk_count,
        },
    )
    final_segments = await loop.run_in_executor(services.executor, session.finalize)
    services.sessions.replace_segments(
        session_id,
        [segment.dict() for segment in final_segments],
    )
    current_state = get_or_create_state(session_id)
    # Confirmed role mappings keep final transcript labels aligned with the live view.
    if current_state.current_mapping:
        services.sessions.apply_role_mapping(session_id, current_state.current_mapping)

    event_id = _next_stream_event_id(session_id, services)
    await services.publish_to_mercure(
        f"scribe/session/{session_id}/raw",
        {"type": "finalized", "session_id": session_id},
        event_id=event_id,
    )


async def _publish_transcription_error(
    session_id: str,
    services: StreamingServices,
    error: Exception,
) -> None:
    """Publish a generic transcript error without exposing clinical text."""
    logger.error(
        "websocket.error",
        extra={
            "session_id": session_id,
            "error": str(error),
        },
    )
    event_id = _next_stream_event_id(session_id, services)
    await services.publish_to_mercure(
        f"scribe/session/{session_id}/raw",
        {"type": "error", "message": "Transcription error occurred"},
        event_id=event_id,
    )


async def _schedule_stream_cleanup(
    session_id: str, services: StreamingServices
) -> None:
    """Keep the session briefly resumable before role and audio state are cleaned."""
    await services.lifecycle.schedule_destroy(
        session_id,
        services.close_role_inference,
        grace_seconds=services.reconnect_grace_seconds,
    )


def _next_stream_event_id(session_id: str, services: StreamingServices) -> int:
    """Advance the raw-transcript Mercure event ID for reconnecting browsers."""
    services.mercure_event_ids.setdefault(session_id, 0)
    services.mercure_event_ids[session_id] += 1
    return services.mercure_event_ids[session_id]
