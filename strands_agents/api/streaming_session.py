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
from dataclasses import dataclass, field
from typing import Any

from api import source_integrity
from api.role_heuristics import compute_row_role_exceptions
from api.role_inference_queue import current_role_revision, wait_for_role_settlement
from fastapi import WebSocket, WebSocketDisconnect
from nemo_session import TranscriptionSession
from nemo_streaming_engine import streaming_engine_enabled
from session_quality import (
    build_session_quality_record,
    persist_session_quality_record,
)
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
        post_visit_audio_retention_seconds: Time a finalized visit's audio stays
            available for on-demand correction after the socket closes.
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
    post_visit_audio_retention_seconds: float
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
        chunk_inference_ms: Per-chunk NeMo processing time shown in quality records.
        chunk_total_ms: Per-chunk end-to-end server time for transcript publication.
        error_count: Terminal or recoverable stream errors for this visible session.
    """

    chunk_count: int = 0
    mercure_warning_sent: bool = False
    chunk_inference_ms: list[int] = field(default_factory=list)
    chunk_total_ms: list[int] = field(default_factory=list)
    error_count: int = 0


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
        await _finalize_after_disconnect(
            websocket, session_id, session, services, loop, state
        )
    except Exception as error:
        await _publish_transcription_error(session_id, services, error, state)
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
        # Resuming means the visit keeps recording: any earlier "terminal"
        # identity is no longer true and must not authorize a note source.
        source_integrity.discard_terminal_watermark(session_id)
        # The corrected artifact derives from that discarded identity. Example:
        # the user pressed Stop, read the note, then pressed Start to continue
        # the visit - kept, the old artifact would win summary source selection
        # after the next Stop and block the note as stale lineage forever.
        services.sessions.replace_corrected_segments(session_id, [])
    else:
        # M22: the process-level engine flag selects windowed (default) or
        # session-long streaming identity at session construction only.
        engine = None
        if streaming_engine_enabled():
            engine = services.pipeline.create_streaming_engine(session_id)
        session = TranscriptionSession(
            session_id,
            pipeline=services.pipeline,
            input_format=services.input_format,
            max_buffer_duration=services.max_buffer_duration,
            streaming_engine=engine,
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
    state.chunk_inference_ms.append(duration_ms)

    segment_payloads = await _publish_raw_segments(
        websocket, session_id, segments, services, state
    )
    # Newly visible raw text gives the role queue something to label for the user.
    if segment_payloads != []:
        await services.enqueue_role_inference(session_id, segment_payloads)

    total_ms = int((time.time() - started_at) * 1000)
    state.chunk_total_ms.append(total_ms)
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
            "websocket.system_error_notice_failed session_id=%s %s: %s",
            session_id,
            type(send_error).__name__,
            str(send_error)[:200],
            exc_info=send_error,
            extra={
                "session_id": session_id,
                "error_type": type(send_error).__name__,
                "error": str(send_error)[:200],
            },
        )


async def _finalize_after_disconnect(
    websocket: WebSocket,
    session_id: str,
    session: TranscriptionSession,
    services: StreamingServices,
    loop: Any,
    state: StreamState,
) -> None:
    """Publish the held-back tail and store the full transcript on session end."""
    logger.info(
        "websocket.disconnected",
        extra={
            "session_id": session_id,
            "total_chunks": state.chunk_count,
        },
    )
    tail_segments = await loop.run_in_executor(services.executor, session.finalize)
    # The held-back tail publishes now so the browser sees the final utterances.
    tail_payloads = await _publish_raw_segments(
        websocket, session_id, tail_segments, services, state
    )
    if tail_payloads != []:
        await services.enqueue_role_inference(session_id, tail_payloads)

    # The clinician's note source must not keep moving after Stop: wait for
    # the queued role tail to drain (bounded), then close the role revision so
    # any later role result is rejected stale instead of relabeling the draft.
    role_settlement = await wait_for_role_settlement(session_id)

    services.sessions.replace_segments(
        session_id,
        [segment.dict() for segment in session.accumulated_transcript],
    )
    current_state = get_or_create_state(session_id)
    # Confirmed role mappings keep final transcript labels aligned with the live view.
    if current_state.current_mapping:
        services.sessions.apply_role_mapping(session_id, current_state.current_mapping)
        # Re-judge rows after the final mapping so automatic row exceptions
        # survive the history rebuild exactly as the clinician last saw them.
        services.sessions.set_auto_row_roles(
            session_id,
            compute_row_role_exceptions(
                services.sessions.get_segments(session_id),
                current_state.current_mapping,
            ),
        )

    await _emit_session_quality_record(
        session_id, session, services, state, current_state
    )

    # Freeze the terminal source identity the note is allowed to use. This is
    # the only moment "the whole visit" is true: final rows are committed and
    # role settlement has closed. Correction/summary requests bind to it.
    watermark = source_integrity.record_terminal_watermark(
        session_id,
        services.sessions.get_segments(session_id),
        # Buffer counters default to zero so a minimal test session (or a
        # future buffer variant) still finalizes instead of crashing the stop.
        audio_seconds=float(getattr(session.buffer, "duration_seconds", 0.0) or 0.0),
        trimmed_seconds=float(getattr(session.buffer, "trimmed_seconds", 0.0) or 0.0),
        role_revision=current_role_revision(session_id),
        role_settlement=role_settlement,
    )

    event_id = _next_stream_event_id(session_id, services)
    await services.publish_to_mercure(
        f"scribe/session/{session_id}/raw",
        {
            "type": "finalized",
            "session_id": session_id,
            # Opaque attestation only - hashes stay server-side. The browser
            # uses this to know a terminal note source now exists.
            "attestation_id": watermark.attestation_id,
            "terminal_row_count": watermark.terminal_live_row_count,
            "role_settlement": watermark.terminal_role_settlement,
        },
        event_id=event_id,
    )


async def _publish_transcription_error(
    session_id: str,
    services: StreamingServices,
    error: Exception,
    state: StreamState | None = None,
) -> None:
    """Publish a generic transcript error without exposing clinical text."""
    # Some focused tests call this helper without a live StreamState.
    if state is not None:
        state.error_count += 1

    # The error type/text goes into the message because plain log formats drop
    # `extra` fields, and exc_info records the traceback for diagnosis.
    logger.error(
        "websocket.error session_id=%s %s: %s",
        session_id,
        type(error).__name__,
        str(error)[:300],
        exc_info=error,
        extra={
            "session_id": session_id,
            "error_type": type(error).__name__,
            "error": str(error),
        },
    )
    event_id = _next_stream_event_id(session_id, services)
    await services.publish_to_mercure(
        f"scribe/session/{session_id}/raw",
        {"type": "error", "message": "Transcription error occurred"},
        event_id=event_id,
    )


async def _emit_session_quality_record(
    session_id: str,
    session: TranscriptionSession,
    services: StreamingServices,
    state: StreamState,
    current_state: Any,
) -> None:
    """Log, persist, and publish one final quality record for the recording."""
    quality_record = build_session_quality_record(
        session_id=session_id,
        audio_session=session,
        stream_state=state,
        role_state=current_state,
    )
    # Tail role inferences keep landing after this record closes; the snapshot
    # lets the role worker report the post-finalize delta as `quality_tail`.
    current_state.quality_flip_snapshot = {
        "role_flips_accepted": quality_record["role_flips_accepted"],
        "role_flips_suppressed": quality_record["role_flips_suppressed"],
    }
    quality_record_path = None

    try:
        quality_record_path = persist_session_quality_record(quality_record)
    except Exception as persist_error:
        logger.error(
            "session.quality_persist_failed session_id=%s %s: %s",
            session_id,
            type(persist_error).__name__,
            str(persist_error)[:300],
            exc_info=persist_error,
            extra={
                "session_id": session_id,
                "error_type": type(persist_error).__name__,
                "error": str(persist_error)[:300],
            },
        )

    logger.info(
        "session.quality session_id=%s chunks=%s emitted_segments=%s "
        "held_segments=%s errors=%s confidence=%.3f",
        session_id,
        quality_record["chunks"],
        quality_record["emitted_segments"],
        quality_record["held_segments"],
        quality_record["error_count"],
        quality_record["final_confidence"],
        extra={
            **quality_record,
            "session_id": session_id,
            "quality_record_path": str(quality_record_path)
            if quality_record_path is not None
            else None,
        },
    )

    event_id = _next_stream_event_id(session_id, services)
    await services.publish_to_mercure(
        f"scribe/session/{session_id}/raw",
        {"type": "quality", "quality": quality_record},
        event_id=event_id,
    )


async def _schedule_stream_cleanup(
    session_id: str, services: StreamingServices
) -> None:
    """Keep the session resumable, or correctable after finalize, before cleanup."""
    await services.lifecycle.schedule_destroy(
        session_id,
        services.close_role_inference,
        grace_seconds=_stream_cleanup_grace_seconds(session_id, services),
    )


def _stream_cleanup_grace_seconds(
    session_id: str, services: StreamingServices
) -> float:
    """Choose how long a closed session's audio stays before cleanup.

    A finalized visit keeps its audio for the post-visit retention window so the
    clinician can read the transcript before pressing Generate summary without
    losing the corrected pass (the pre-M11 auto-summary fired within the short
    reconnect grace, which hid this). A visit that ended without a terminal
    watermark keeps the short reconnect grace: that timer exists for socket
    resumption, not for post-visit reading time.

    Args:
        session_id: Closed stream's session UUID.
        services: Stream service container; both grace values live here.

    Returns:
        Seconds before the session and its audio are destroyed.
    """
    if source_integrity.get_terminal_watermark(session_id) is not None:
        return services.post_visit_audio_retention_seconds

    return services.reconnect_grace_seconds


def _next_stream_event_id(session_id: str, services: StreamingServices) -> int:
    """Advance the raw-transcript Mercure event ID for reconnecting browsers."""
    services.mercure_event_ids.setdefault(session_id, 0)
    services.mercure_event_ids[session_id] += 1
    return services.mercure_event_ids[session_id]
