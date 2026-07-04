"""
Queued role inference for live consultation transcripts.

The browser first sees raw NeMo speaker labels, then this module batches those
segments and publishes DOCTOR/PATIENT role updates after enough speaker variety
exists. Keeping the queue outside `server.py` makes the live recording route
smaller while preserving the user's transcribe -> relabel -> summary flow.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from session_lifecycle import SessionLifecycle
from storage import StorageBackend
from tools.assign_roles import (
    apply_role_mapping_result,
    cleanup_session as cleanup_role_state,
    get_or_create_state,
)

logger = logging.getLogger(__name__)

ROLE_INFERENCE_IDLE_TIMEOUT_SECONDS = 60.0

PublishToMercure = Callable[[str, dict[str, Any], int | None], Awaitable[bool]]
RunRoleInference = Callable[[str, list[dict[str, Any]], str], dict[str, Any] | None]

role_inference_queues: dict[str, asyncio.Queue[list[dict[str, Any]] | None]] = {}
role_inference_workers: dict[str, asyncio.Task[None]] = {}


@dataclass(slots=True)
class RoleInferenceServices:
    """
    Runtime services needed by one role inference worker.

    These are passed in from `server.py` so tests and route code can still patch
    the same user-facing publish and inference seams. A worker uses them to read
    transcript history, apply labels, publish Mercure updates, and respect live
    session cleanup.

    Attributes:
        sessions: Transcript storage the browser history and summary views read.
        lifecycle: Live-session registry used to keep reconnecting users intact.
        publish_to_mercure: Publisher that sends role updates to the browser.
        run_role_inference: CPU/cloud role classifier run off the event loop.
        mercure_event_ids: Per-session event counters for browser reconnects.
    """

    sessions: StorageBackend
    lifecycle: SessionLifecycle
    publish_to_mercure: PublishToMercure
    run_role_inference: RunRoleInference
    mercure_event_ids: dict[str, int]


@dataclass(slots=True)
class RoleUpdatePayload:
    """
    Role update ready to publish to the user's transcript.

    The worker builds this after either the Strands tool updates shared state or
    the agent returns free-text JSON. The payload is then applied to stored
    segments and published so the browser can relabel the consultation.

    Attributes:
        mapping: Speaker-to-role labels shown in the transcript.
        confidence: Agent confidence shown in role state and dev tooling.
        flip_detected: True when diarization labels appear to have swapped.
        attributed_segments: New segments annotated with user-visible roles.
        reasoning: Short agent explanation for review/debug surfaces.
        tool_invoked: True when the Strands tool persisted state directly.
    """

    mapping: dict[str, str]
    confidence: float
    flip_detected: bool
    attributed_segments: list[dict[str, Any]]
    reasoning: str
    tool_invoked: bool


@dataclass(slots=True)
class RoleBatchRead:
    """
    Result from waiting on the role queue.

    A timeout means the user has stopped sending transcript text for long
    enough that the background worker can exit. A `None` segment batch means
    the user stopped recording and queued role work should finish cleanly.

    Attributes:
        segments: Transcript batch to label, `None` when close was requested.
        is_idle_timeout: True when no new text arrived before the worker timeout.
    """

    segments: list[dict[str, Any]] | None
    is_idle_timeout: bool


async def enqueue_role_inference(
    session_id: str,
    segments: list[dict[str, Any]],
    services: RoleInferenceServices,
) -> None:
    """Queue new transcript segments for the role labels the user sees.

    Args:
        session_id: Current browser recording session.
        segments: New raw NeMo segments; empty means no role update is needed.
        services: Runtime callbacks and stores for this session's worker.
    """
    # No new transcript text means there is nothing useful to relabel in the UI.
    if segments == []:
        return

    queue = role_inference_queues.get(session_id)
    # First role batch for this session starts the per-session queue.
    if queue is None:
        queue = asyncio.Queue(maxsize=50)
        role_inference_queues[session_id] = queue

    try:
        queue.put_nowait([dict(segment) for segment in segments])
    except asyncio.QueueFull:
        logger.warning("role_inference.queue_full", extra={"session_id": session_id})

    worker = role_inference_workers.get(session_id)
    # A missing or completed worker means the next role update needs a new task.
    if worker is None or worker.done():
        role_inference_workers[session_id] = asyncio.create_task(
            _role_inference_worker(session_id, services),
            name=f"role-inference-{session_id}",
        )


async def close_role_inference(session_id: str) -> None:
    """Ask the role worker to finish after queued transcript text is processed.

    Args:
        session_id: Current browser recording session.
    """
    queue = role_inference_queues.get(session_id)
    # If the user stops before any role queue exists, clear stale role state now.
    if queue is None:
        cleanup_role_state(session_id)
        return

    await queue.put(None)


def cancel_orphaned_role_inference(live_session_ids: set[str]) -> int:
    """Cancel role workers for sessions the user can no longer reconnect to.

    Args:
        live_session_ids: Active or grace-period sessions; empty removes all queues.

    Returns:
        Number of orphaned role queues removed from the background worker map.
    """
    orphaned_session_ids = [
        session_id
        for session_id in role_inference_queues
        if session_id not in live_session_ids
    ]
    # Each orphaned queue belongs to a recording that has aged out of the UI.
    for session_id in orphaned_session_ids:
        role_inference_queues.pop(session_id, None)
        worker = role_inference_workers.pop(session_id, None)
        # A live worker without a session would publish labels to a closed visit.
        if worker and not worker.done():
            worker.cancel()

    return len(orphaned_session_ids)


def prune_orphaned_role_states(live_session_ids: set[str]) -> int:
    """Remove role mappings for sessions no browser can resume.

    Args:
        live_session_ids: Active or grace-period sessions; empty clears all roles.

    Returns:
        Number of role-state records removed from the shared role tool state.
    """
    from tools.assign_roles import _session_states, _states_lock

    with _states_lock:
        orphaned_session_ids = [
            session_id
            for session_id in _session_states
            if session_id not in live_session_ids
        ]
        # Role state outside the live set would relabel a future visit incorrectly.
        for session_id in orphaned_session_ids:
            _session_states.pop(session_id, None)

    return len(orphaned_session_ids)


async def _role_inference_worker(
    session_id: str, services: RoleInferenceServices
) -> None:
    """Drain one session's role queue and publish visible label updates."""
    queue = role_inference_queues[session_id]

    try:
        while True:
            next_batch = await _receive_next_role_batch(queue, session_id)
            # Idle workers stop so finished visits do not keep background tasks alive.
            if next_batch.is_idle_timeout:
                break

            close_requested = next_batch.segments is None
            merged_segments: list[dict[str, Any]] = (
                [] if next_batch.segments is None else list(next_batch.segments)
            )
            close_requested = _did_drain_role_queue_request_close(
                queue, merged_segments, close_requested
            )

            # Role inference needs at least two speakers before the UI can relabel confidently.
            if merged_segments != [] and _has_multiple_speakers(
                session_id, services.sessions
            ):
                await _infer_and_publish_role_update(
                    session_id, merged_segments, services
                )
            # A single-speaker start keeps raw labels visible until another speaker appears.
            elif merged_segments != []:
                logger.info(
                    "role_inference.skipped",
                    extra={
                        "session_id": session_id,
                        "reason": "insufficient_speaker_variety",
                        "segments": len(merged_segments),
                    },
                )

            # The stop signal exits only after all already-queued segments are handled.
            if close_requested and queue.empty():
                break
    finally:
        role_inference_workers.pop(session_id, None)
        role_inference_queues.pop(session_id, None)

        # If the browser cannot reconnect, remove role state with the worker.
        if not services.lifecycle.is_active(session_id):
            cleanup_role_state(session_id)


async def _receive_next_role_batch(
    queue: asyncio.Queue[list[dict[str, Any]] | None],
    session_id: str,
) -> RoleBatchRead:
    """Wait for the next role batch or report an idle worker timeout."""
    try:
        return RoleBatchRead(
            segments=await asyncio.wait_for(
                queue.get(), timeout=ROLE_INFERENCE_IDLE_TIMEOUT_SECONDS
            ),
            is_idle_timeout=False,
        )
    except asyncio.TimeoutError:
        logger.info("role_inference.worker_idle", extra={"session_id": session_id})
        return RoleBatchRead(segments=None, is_idle_timeout=True)


def _did_drain_role_queue_request_close(
    queue: asyncio.Queue[list[dict[str, Any]] | None],
    merged_segments: list[dict[str, Any]],
    close_requested: bool,
) -> bool:
    """Merge already queued role batches so the browser gets one coherent relabel."""
    while True:
        try:
            queued_batch = queue.get_nowait()
        except asyncio.QueueEmpty:
            break

        # The close sentinel means the user stopped recording after this queued text.
        if queued_batch is None:
            close_requested = True
            continue

        merged_segments.extend(queued_batch)

    return close_requested


async def _infer_and_publish_role_update(
    session_id: str,
    merged_segments: list[dict[str, Any]],
    services: RoleInferenceServices,
) -> None:
    """Run role inference off-loop and publish the result to the transcript."""
    transcript = services.sessions.get_transcript_text(session_id)
    loop = asyncio.get_running_loop()
    started_at = time.time()
    result = await loop.run_in_executor(
        None,
        services.run_role_inference,
        session_id,
        merged_segments,
        transcript,
    )
    duration_ms = int((time.time() - started_at) * 1000)

    # A model that is unreachable should warn the browser once, even if the
    # heuristic still produced degraded labels or produced nothing at all.
    if result and result.get("provider_unreachable"):
        await _publish_provider_unavailable_once(session_id, services)

    # A role result lets the UI replace raw speaker IDs with clinical labels.
    # An empty-mapping "none" result is only a connectivity signal, so it is not applied.
    if result and result.get("path") != "none":
        role_update = _build_role_update_payload(session_id, merged_segments, result)
        services.sessions.apply_role_mapping(session_id, role_update.mapping)
        await _publish_role_update(session_id, role_update, services)
        path = str(
            result.get("path", "tool" if role_update.tool_invoked else "freetext")
        )
        logger.info(
            "role_inference.completed",
            extra={
                "session_id": session_id,
                "segments": len(merged_segments),
                "confidence": role_update.confidence,
                "flip_detected": role_update.flip_detected,
                "tool_invoked": role_update.tool_invoked,
                "path": path,
                "fallback": path in {"heuristic", "none"},
                "tokens_in": int(result.get("tokens_in", 0)),
                "tokens_out": int(result.get("tokens_out", 0)),
                "tokens_total": int(result.get("tokens_total", 0)),
                "model_latency_ms": int(result.get("model_latency_ms", 0)),
                "cycles": int(result.get("cycles", 0)),
                "agent": str(result.get("agent", "role-inference")),
                "tool_success_rate": result.get("tool_success_rate"),
                "duration_ms": duration_ms,
            },
        )
        return

    logger.warning(
        "role_inference.empty_result",
        extra={
            "session_id": session_id,
            "segments": len(merged_segments),
            "path": "none",
            "fallback": True,
            "duration_ms": duration_ms,
        },
    )


def _build_role_update_payload(
    session_id: str,
    merged_segments: list[dict[str, Any]],
    result: dict[str, Any],
) -> RoleUpdatePayload:
    """Convert agent output into the role update the browser consumes."""
    tool_invoked = result.get("_tool_invoked", False)
    # Tool invocation means shared state already contains the user's latest labels.
    if tool_invoked:
        state = get_or_create_state(session_id)
        mapping = state.current_mapping
        return RoleUpdatePayload(
            mapping=mapping,
            confidence=state.running_confidence,
            flip_detected=state.last_flip_detected,
            attributed_segments=[
                {
                    **segment,
                    "role": mapping.get(str(segment.get("speaker_id", "")), "UNKNOWN"),
                }
                for segment in merged_segments
            ],
            reasoning=str(result.get("reasoning", "")),
            tool_invoked=True,
        )

    role_update = apply_role_mapping_result(
        session_id=session_id,
        segments=merged_segments,
        mapping=result.get("mapping", {}),
        confidence=float(result.get("confidence", 0.0)),
        reasoning=str(result.get("reasoning", "")),
    )
    return RoleUpdatePayload(
        mapping=role_update.mapping,
        confidence=role_update.confidence,
        flip_detected=role_update.flip_detected,
        attributed_segments=role_update.attributed_segments,
        reasoning=role_update.reasoning,
        tool_invoked=False,
    )


async def _publish_role_update(
    session_id: str,
    role_update: RoleUpdatePayload,
    services: RoleInferenceServices,
) -> None:
    """Publish a role update so the browser can relabel visible transcript text."""
    event_id = _next_role_event_id(session_id, services)
    await services.publish_to_mercure(
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
        event_id=event_id,
    )


_provider_unavailable_warned: set[str] = set()


async def _publish_provider_unavailable_once(
    session_id: str,
    services: RoleInferenceServices,
) -> None:
    """Warn the browser once per session that the role/summary model is unreachable.

    Publishes a `system_error` on the roles topic the browser already subscribes to,
    so the warning banner appears during the consultation, not only at summary time.

    Args:
        session_id: Browser session UUID; empty would target the wrong topic.
        services: Runtime services providing the Mercure publisher and event counter.
    """
    # One warning per session keeps the banner from flapping on every failed inference.
    if session_id in _provider_unavailable_warned:
        return
    _provider_unavailable_warned.add(session_id)

    event_id = _next_role_event_id(session_id, services)
    await services.publish_to_mercure(
        f"scribe/session/{session_id}/roles",
        {
            "type": "system_error",
            "message": (
                "AI model unavailable - run  ./scripts/check-ai-model.sh  to start it "
                "(or set ROLE_AGENT_MODEL_PROVIDER=bedrock)."
            ),
            "session_id": session_id,
        },
        event_id=event_id,
    )


def _next_role_event_id(session_id: str, services: RoleInferenceServices) -> int:
    """Advance the Mercure event ID used when the browser reconnects."""
    services.mercure_event_ids.setdefault(session_id, 0)
    services.mercure_event_ids[session_id] += 1
    return services.mercure_event_ids[session_id]


def _has_multiple_speakers(session_id: str, sessions: StorageBackend) -> bool:
    """Return whether the stored transcript can support visible role labels."""
    speaker_ids: set[str] = set()
    # Each stored segment is one line the user can see in the transcript.
    for segment in sessions.get_segments(session_id):
        speaker_id = str(segment.get("speaker_id", "")).strip()
        # Blank speaker labels cannot produce useful DOCTOR/PATIENT relabeling.
        if speaker_id != "":
            speaker_ids.add(speaker_id)

    return len(speaker_ids) >= 2
