"""
Replay workflow for uploaded consultation audio.

The demo UI uploads a WAV file, receives transcript segments, then reveals those
segments from the browser audio clock. This keeps what the tester hears aligned
with what appears on screen while `server.py` keeps the FastAPI route and tests
keep their existing task-state seam.
"""

from __future__ import annotations

import asyncio
import logging
import tempfile
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fastapi import UploadFile
from storage import StorageBackend

logger = logging.getLogger(__name__)

PublishToMercure = Callable[[str, dict[str, Any], int | None], Awaitable[bool]]
EnqueueRoleInference = Callable[[str, list[dict[str, Any]]], Awaitable[None]]
GetRunningLoop = Callable[[], Any]

replay_tasks: dict[str, asyncio.Task[None]] = {}


@dataclass(slots=True)
class ReplayServices:
    """
    Runtime dependencies for one uploaded replay.

    `server.py` builds this at request time so tests and demos use the current
    NeMo pipeline, transcript store, role queue, and event counters. Browser
    audio owns replay timing while these services keep labels and summaries usable.

    Attributes:
        pipeline: Loaded NeMo pipeline used to transcribe the uploaded WAV.
        executor: Shared NeMo executor so replay transcription does not block.
        sessions: Transcript storage read by history and summary views.
        get_running_loop: Loop getter preserved as a route-level test seam.
        publish_to_mercure: Publisher retained for legacy replay helpers and tests.
        enqueue_role_inference: Role queue callback for replayed text.
        mercure_event_ids: Per-session counters for browser reconnects.
    """

    pipeline: Any
    executor: Any
    sessions: StorageBackend
    get_running_loop: GetRunningLoop
    publish_to_mercure: PublishToMercure
    enqueue_role_inference: EnqueueRoleInference
    mercure_event_ids: dict[str, int]


async def start_replay_upload(
    session_id: str,
    file: UploadFile,
    speed: float,
    services: ReplayServices,
) -> dict:
    """Transcribe an uploaded WAV and return browser-paced replay data.

    Args:
        session_id: Replay session UUID used by the browser.
        file: WAV file selected in the demo replay control.
        speed: Legacy replay multiplier; browser-clock replay keeps it for compatibility.
        services: Runtime callbacks and stores used by the replay task.

    Returns:
        Replay metadata and segments; zero segments means no replay text will appear.
    """
    segments = await _transcribe_replay_upload(file, services)
    # Empty or failed transcription leaves the replay panel with no transcript.
    if not segments:
        return {
            "session_id": session_id,
            "segments": 0,
            "duration_seconds": 0,
            "transcript_segments": [],
            "pacing": "browser_audio_clock",
        }

    max_end = max(float(segment.get("end", 0)) for segment in segments)
    _did_cancel_existing_replay(session_id)
    services.sessions.replace_segments(session_id, segments)
    await services.enqueue_role_inference(session_id, segments)

    logger.info(
        "replay.prepared segments=%s duration_seconds=%.1f pacing=browser_audio_clock",
        len(segments),
        round(max_end, 1),
        extra={
            "session_id": session_id,
            "segments": len(segments),
            "duration_seconds": round(max_end, 1),
            "requested_speed": speed,
            "pacing": "browser_audio_clock",
            "mercure_raw_pacing": False,
        },
    )

    return {
        "session_id": session_id,
        "segments": len(segments),
        "transcript_segments": segments,
        "duration_seconds": round(max_end, 1),
        "speed": speed,
        "pacing": "browser_audio_clock",
    }


async def replay_segments(
    session_id: str,
    segments: list[dict[str, Any]],
    speed: float,
    services: ReplayServices,
) -> None:
    """Publish stored NeMo segments as if the user were recording live.

    Args:
        session_id: Replay session UUID visible in browser URLs and topics.
        segments: Transcript segments to pace; empty only publishes finalization.
        speed: Replay multiplier; zero is rejected by the route before this runs.
        services: Runtime stores and callbacks used to publish replay events.
    """
    try:
        clock_start = time.time()

        # Each replayed segment becomes visible in order, matching the demo timeline.
        for segment_index, segment in enumerate(segments):
            segment_start = float(segment.get("start", 0))
            target_wall_time = clock_start + (segment_start / speed)
            delay_seconds = target_wall_time - time.time()
            # Future timestamps wait so the browser sees a realistic transcript cadence.
            if delay_seconds > 0:
                await asyncio.sleep(delay_seconds)

            services.sessions.append_segment(session_id, segment)
            event_id = _next_replay_event_id(session_id, services)
            await services.publish_to_mercure(
                f"scribe/session/{session_id}/raw",
                {"type": "segment", **segment},
                event_id=event_id,
            )

            # Every third segment gives the role queue enough fresh text to relabel.
            if (segment_index + 1) % 3 == 0 or segment_index == len(segments) - 1:
                recent_segments = segments[
                    max(0, segment_index - 2) : segment_index + 1
                ]
                await services.enqueue_role_inference(session_id, recent_segments)

        event_id = _next_replay_event_id(session_id, services)
        await services.publish_to_mercure(
            f"scribe/session/{session_id}/raw",
            {"type": "finalized", "session_id": session_id},
            event_id=event_id,
        )

        logger.info(
            "replay.completed segments=%s",
            len(segments),
            extra={
                "session_id": session_id,
                "segments": len(segments),
            },
        )
    except asyncio.CancelledError:
        logger.info("replay.cancelled", extra={"session_id": session_id})
    except Exception:
        logger.exception("replay.failed", extra={"session_id": session_id})
    finally:
        current_task = asyncio.current_task()
        # Only the task currently registered for this session may clear replay state.
        if replay_tasks.get(session_id) is current_task:
            replay_tasks.pop(session_id, None)


async def _transcribe_replay_upload(
    file: UploadFile, services: ReplayServices
) -> list[dict[str, Any]]:
    """Save the user's replay WAV briefly and run NeMo off the event loop."""
    replay_file_handle = tempfile.NamedTemporaryFile(
        suffix=".wav", prefix="replay_", delete=False
    )
    replay_audio_path = Path(replay_file_handle.name)
    replay_file_handle.close()
    try:
        content = await file.read()
        replay_audio_path.write_bytes(content)

        loop = services.get_running_loop()
        result = await loop.run_in_executor(
            services.executor,
            services.pipeline.transcribe_file,
            str(replay_audio_path),
        )
        return [segment.dict() for segment in result.segments]
    finally:
        replay_audio_path.unlink(missing_ok=True)


def did_cancel_replay(session_id: str) -> bool:
    """Stop the active browser-paced replay for one visible session.

    Args:
        session_id: Replay session UUID; empty means no browser task can match.

    Returns:
        True when a running replay was cancelled; false means there was nothing to stop.
    """
    return _did_cancel_existing_replay(session_id)


def _did_cancel_existing_replay(session_id: str) -> bool:
    """Cancel any existing replay so the browser sees the latest uploaded file."""
    existing_task = replay_tasks.pop(session_id, None)
    # Starting a new replay replaces the transcript cadence for that session.
    if existing_task and not existing_task.done():
        existing_task.cancel()
        return True

    return False


def _next_replay_event_id(session_id: str, services: ReplayServices) -> int:
    """Advance the Mercure event ID used by replay subscribers."""
    services.mercure_event_ids.setdefault(session_id, 0)
    services.mercure_event_ids[session_id] += 1
    return services.mercure_event_ids[session_id]
