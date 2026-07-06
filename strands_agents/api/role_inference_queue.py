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

from api.role_heuristics import (
    compute_row_role_exceptions,
    summarize_role_establishment_cues,
)
from session_lifecycle import SessionLifecycle
from session_quality import build_quality_tail_record, persist_session_quality_record
from storage import StorageBackend
from tools.assign_roles import (
    apply_role_mapping_result,
    cleanup_session as cleanup_role_state,
    get_or_create_state,
)

logger = logging.getLogger(__name__)

ROLE_INFERENCE_IDLE_TIMEOUT_SECONDS = 60.0
ROLE_EVIDENCE_RECENT_UTTERANCES = 3
ROLE_EVIDENCE_REPRESENTATIVE_UTTERANCES = 2
ROLE_EVIDENCE_OPENING_UTTERANCES = 3
ROLE_EVIDENCE_MAX_TEXT_CHARS = 120
ROLE_EVIDENCE_MAX_SPEAKERS = 6

# Speaker-identity stability gate for the browser confidence badge. M20 Phase 0
# measured 0.87-1.13 anchor remaps per window on every misleading PriMock
# session, so any threshold well below that band behaves identically on the
# corpus; 0.2 leaves headroom for genuinely clean close-mic audio to earn a
# confident badge while mixed audio stays qualified.
STABLE_MAX_ANCHOR_REMAP_RATE = 0.2

PublishToMercure = Callable[[str, dict[str, Any], int | None], Awaitable[bool]]
RunRoleInference = Callable[
    [str, list[dict[str, Any]], dict[str, Any]], dict[str, Any] | None
]

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
        logger.warning(
            "role_inference.queue_full session_id=%s",
            session_id,
            extra={"session_id": session_id},
        )

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

        # Post-finalize role churn is invisible to the already-written quality
        # record; persist the additive tail delta before state cleanup.
        _emit_quality_tail_if_needed(session_id)

        # If the browser cannot reconnect, remove role state with the worker.
        if not services.lifecycle.is_active(session_id):
            cleanup_role_state(session_id)


def _emit_quality_tail_if_needed(session_id: str) -> None:
    """Persist a `quality_tail` record when role flips landed after finalize.

    Args:
        session_id: Session whose role worker just drained; unknown or
            never-finalized sessions produce no record.
    """
    from tools.assign_roles import _session_states, _states_lock

    with _states_lock:
        role_state = _session_states.get(session_id)

    # No role state means the session never inferred roles - nothing to report.
    if role_state is None:
        return

    tail_record = build_quality_tail_record(session_id, role_state)
    # A quiet tail is the normal case; only real churn earns an artifact row.
    if tail_record is None:
        return

    try:
        persist_session_quality_record(tail_record)
    except Exception as persist_error:
        logger.error(
            "session.quality_tail_persist_failed session_id=%s %s: %s",
            session_id,
            type(persist_error).__name__,
            str(persist_error)[:200],
            extra={"session_id": session_id},
        )
        return

    logger.info(
        "session.quality_tail session_id=%s tail_flips_accepted=%s tail_flips_suppressed=%s",
        session_id,
        tail_record["tail_role_flips_accepted"],
        tail_record["tail_role_flips_suppressed"],
        extra={**tail_record},
    )


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
    role_evidence = _build_bounded_role_evidence(
        session_id, services.sessions, merged_segments
    )
    loop = asyncio.get_running_loop()
    started_at = time.time()
    result = await loop.run_in_executor(
        None,
        services.run_role_inference,
        session_id,
        merged_segments,
        role_evidence,
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
        # After every mapping, the row-cue lane re-judges rows whose text
        # contradicts their speaker's role (e.g. a doctor question rendered
        # inside a Patient card) and relabels or un-labels just those rows.
        row_exceptions = compute_row_role_exceptions(
            services.sessions.get_segments(session_id), role_update.mapping
        )
        services.sessions.set_auto_row_roles(session_id, row_exceptions)
        await _publish_role_update(session_id, role_update, services, row_exceptions)
        path = str(
            result.get("path", "tool" if role_update.tool_invoked else "mapping")
        )
        logger.info(
            "role_inference.completed",
            extra={
                "session_id": session_id,
                "mapping": role_update.mapping,
                "segments": len(merged_segments),
                "confidence": role_update.confidence,
                "flip_detected": role_update.flip_detected,
                "tool_invoked": role_update.tool_invoked,
                "path": path,
                "fallback": path in {"heuristic", "none"},
                "row_exceptions": len(row_exceptions),
                # Tail inferences land after the quality record closed; the
                # flag lets log analysis attribute churn to the settle window.
                "post_finalize": not services.lifecycle.is_active(session_id),
                "tokens_in": int(result.get("tokens_in", 0)),
                "tokens_out": int(result.get("tokens_out", 0)),
                "tokens_total": int(result.get("tokens_total", 0)),
                "model_latency_ms": int(result.get("model_latency_ms", 0)),
                "cycles": int(result.get("cycles", 0)),
                "agent": str(result.get("agent", "role-inference")),
                "tool_success_rate": result.get("tool_success_rate"),
                "role_input_chars": int(result.get("role_input_chars", 0)),
                "duration_ms": duration_ms,
            },
        )
        return

    logger.warning(
        "role_inference.empty_result session_id=%s segments=%s duration_ms=%s",
        session_id,
        len(merged_segments),
        duration_ms,
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


def _build_role_stability(
    session_id: str,
    services: RoleInferenceServices,
) -> dict[str, Any] | None:
    """Summarize live speaker-identity stability for the confidence badge.

    Role confidence only says how sure the agent is about the global
    DOCTOR/PATIENT mapping; it says nothing about whether the underlying
    speaker IDs stayed one voice each. This reads the live audio session's
    identity counters so the browser can refuse to show a confident
    "Roles identified" badge while speaker identity is churning.

    Args:
        session_id: Browser recording UUID whose stability is being judged.
        services: Runtime services; the lifecycle holds the live audio session.

    Returns:
        Stability payload for the roles topic, or None when the audio session
        is gone (e.g. the tail batch drains after disconnect) - the browser
        then keeps the last stability it saw instead of resetting.
    """
    audio_session = services.lifecycle.get(session_id)

    # A drained/ended session has no live counters; None keeps the last badge state.
    if audio_session is None:
        return None

    quality_stats = audio_session.quality_stats
    window_count = len(quality_stats.window_seconds)
    anchor_remap_rate = (
        quality_stats.speaker_anchor_remap_count / window_count if window_count else 0.0
    )

    role_state = get_or_create_state(session_id)
    # Mapping churn is informational only: Phase 0 showed accepted flips
    # correlate with healthy sessions (they are corrections), so churn must
    # not gate the badge - identity-layer evidence does.
    mapping_changes = sum(
        1
        for previous_mapping, current_mapping in zip(
            role_state.mapping_history, role_state.mapping_history[1:]
        )
        if previous_mapping != current_mapping
    )
    pending_contrary_mapping = role_state.pending_flip_mapping is not None

    # Any of these means the visible speaker identities cannot be trusted yet,
    # so a green "Roles identified" badge would overstate row-level truth.
    is_unstable = (
        anchor_remap_rate > STABLE_MAX_ANCHOR_REMAP_RATE
        or quality_stats.phantom_speaker_merge_count > 0
        or pending_contrary_mapping
    )

    return {
        "level": "unstable" if is_unstable else "stable",
        "anchor_remap_rate": round(anchor_remap_rate, 3),
        "anchor_remaps": quality_stats.speaker_anchor_remap_count,
        "phantom_merges": quality_stats.phantom_speaker_merge_count,
        "windows": window_count,
        "mapping_changes": mapping_changes,
        "pending_contrary_mapping": pending_contrary_mapping,
    }


async def _publish_role_update(
    session_id: str,
    role_update: RoleUpdatePayload,
    services: RoleInferenceServices,
    row_exceptions: dict[str, str] | None = None,
) -> None:
    """Publish a role update so the browser can relabel visible transcript text."""
    event_id = _next_role_event_id(session_id, services)
    role_update_event: dict[str, Any] = {
        "type": "role_update",
        "mapping": role_update.mapping,
        "attributed_segments": role_update.attributed_segments,
        "confidence": role_update.confidence,
        "flip_detected": role_update.flip_detected,
        "reasoning": role_update.reasoning,
        "session_id": session_id,
    }

    # Row exceptions ship as the full current set (replace semantics) so the
    # browser can also clear markers for rows a new mapping now explains.
    # None means the caller did not recompute; the field is omitted entirely.
    if row_exceptions is not None:
        role_update_event["row_exceptions"] = row_exceptions

    role_stability = _build_role_stability(session_id, services)
    # Stability is additive so older payload consumers keep working; a missing
    # field means "no new identity evidence" and the browser keeps its last value.
    if role_stability is not None:
        role_update_event["role_stability"] = role_stability

    await services.publish_to_mercure(
        f"scribe/session/{session_id}/roles",
        role_update_event,
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


def _build_bounded_role_evidence(
    session_id: str,
    sessions: StorageBackend,
    new_segments: list[dict[str, Any]],
) -> dict[str, Any]:
    """Build capped speaker evidence for the role agent prompt.

    Args:
        session_id: Browser recording UUID whose stored rows are summarized.
        sessions: Transcript storage backing history and summaries.
        new_segments: Latest visible rows; empty means only mapping context changed.

    Returns:
        Prompt-safe role evidence; transcript text is capped per speaker.
    """
    stored_segments = sessions.get_segments(session_id)
    evidence_by_speaker: dict[str, dict[str, Any]] = {}

    # Stored rows include the latest visible text, so the model does not need a full transcript.
    for segment_index, segment in enumerate(stored_segments):
        speaker_id = str(segment.get("speaker_id", "")).strip()
        visible_text = _trim_role_evidence_text(str(segment.get("text", "")))

        # Blank speaker or text cannot help the UI choose DOCTOR/PATIENT labels.
        if speaker_id == "" or visible_text == "":
            continue

        cue_counts = summarize_role_establishment_cues(visible_text)

        speaker_evidence = evidence_by_speaker.setdefault(
            speaker_id,
            {
                "speaker_id": speaker_id,
                "first_seen_index": segment_index,
                "segment_count": 0,
                "word_count": 0,
                "opening_role_cue_counts": {
                    "doctor": 0,
                    "patient": 0,
                    "questions": 0,
                },
                "role_cue_counts": {"doctor": 0, "patient": 0, "questions": 0},
                "representative_utterances": [],
                "recent_utterances": [],
                "_opening_utterances_seen": 0,
                "_representative_candidates": [],
            },
        )
        speaker_evidence["segment_count"] += 1
        speaker_evidence["word_count"] += len(visible_text.split())
        # Cue counts summarize what the user said without expanding prompt text.
        for cue_name, cue_count in cue_counts.items():
            speaker_evidence["role_cue_counts"][cue_name] += cue_count

        # Opening cues establish the visit baseline before later seam swaps confuse identity.
        if speaker_evidence["_opening_utterances_seen"] < ROLE_EVIDENCE_OPENING_UTTERANCES:
            for cue_name, cue_count in cue_counts.items():
                speaker_evidence["opening_role_cue_counts"][cue_name] += cue_count
            speaker_evidence["_opening_utterances_seen"] += 1

        utterance = {
            "text": visible_text,
            "start": segment.get("start"),
            "end": segment.get("end"),
        }

        # Later clean questions or symptom reports should replace garbled opening cross-talk.
        speaker_evidence["_representative_candidates"].append(
            {
                **utterance,
                "_role_cue_strength": (
                    cue_counts["doctor"] + cue_counts["patient"] + cue_counts["questions"]
                ),
            }
        )

        recent_utterances = speaker_evidence["recent_utterances"]
        recent_utterances.append(utterance)
        # Keep only the latest rows so long visits do not grow the model input.
        if len(recent_utterances) > ROLE_EVIDENCE_RECENT_UTTERANCES:
            del recent_utterances[0]

    # Each speaker gets cue-rich examples, not just the first rows the user happened to say.
    for speaker_evidence in evidence_by_speaker.values():
        speaker_evidence["representative_utterances"] = (
            _select_representative_role_utterances(
                speaker_evidence.pop("_representative_candidates", [])
            )
        )
        speaker_evidence.pop("_opening_utterances_seen", None)

    speaker_evidence_rows = list(evidence_by_speaker.values())
    # Real visits can include stray diarization labels; cap prompt rows before the model sees them.
    if len(speaker_evidence_rows) > ROLE_EVIDENCE_MAX_SPEAKERS:
        speaker_evidence_rows = speaker_evidence_rows[:ROLE_EVIDENCE_MAX_SPEAKERS]

    return {
        "speaker_count": len(speaker_evidence_rows),
        "total_segments_considered": len(stored_segments),
        "new_segment_count": len(new_segments),
        "establishment_hint": _build_role_establishment_hint(speaker_evidence_rows),
        "caps": {
            "recent_utterances_per_speaker": ROLE_EVIDENCE_RECENT_UTTERANCES,
            "representative_utterances_per_speaker": (
                ROLE_EVIDENCE_REPRESENTATIVE_UTTERANCES
            ),
            "opening_utterances_per_speaker": ROLE_EVIDENCE_OPENING_UTTERANCES,
            "max_text_chars": ROLE_EVIDENCE_MAX_TEXT_CHARS,
            "max_speakers": ROLE_EVIDENCE_MAX_SPEAKERS,
        },
        "speakers": speaker_evidence_rows,
    }


def _trim_role_evidence_text(text: str) -> str:
    """Return a short utterance snippet for role evidence.

    Args:
        text: Transcript text from a visible row; blank means no evidence.

    Returns:
        Trimmed snippet capped for model input; empty means the row is skipped.
    """
    normalized_text = " ".join(text.split())

    # Empty transcript rows are timing artifacts, not role evidence for the UI.
    if normalized_text == "":
        return ""

    return normalized_text[:ROLE_EVIDENCE_MAX_TEXT_CHARS]


def _build_role_establishment_hint(
    speaker_evidence_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    """Suggest a dyadic mapping from clean opening cues.

    Args:
        speaker_evidence_rows: Bounded speaker summaries; empty means no role evidence.

    Returns:
        Hint payload for the role agent; empty means cues were weak or ambiguous.
    """
    # More or fewer than two visible speakers makes a dyadic hint unsafe.
    if len(speaker_evidence_rows) != 2:
        return {}

    scored_speakers: list[dict[str, Any]] = []
    # Each speaker's opening cue balance is one establishment vote.
    for speaker_evidence in speaker_evidence_rows:
        opening_counts = speaker_evidence.get("opening_role_cue_counts", {})
        doctor_score = int(opening_counts.get("doctor", 0))
        patient_score = int(opening_counts.get("patient", 0))
        scored_speakers.append(
            {
                "speaker_id": speaker_evidence["speaker_id"],
                "doctor_margin": doctor_score - patient_score,
                "patient_margin": patient_score - doctor_score,
            }
        )

    doctor_candidate = max(
        scored_speakers,
        key=lambda scored_speaker: scored_speaker["doctor_margin"],
    )
    patient_candidate = max(
        scored_speakers,
        key=lambda scored_speaker: scored_speaker["patient_margin"],
    )

    # Weak or same-speaker cues are reported as no hint, not a guessed label.
    if (
        doctor_candidate["doctor_margin"] <= 0
        or patient_candidate["patient_margin"] <= 0
        or doctor_candidate["speaker_id"] == patient_candidate["speaker_id"]
    ):
        return {}

    return {
        "mapping": {
            doctor_candidate["speaker_id"]: "DOCTOR",
            patient_candidate["speaker_id"]: "PATIENT",
        },
        "source": "opening_role_cue_counts",
    }


def _select_representative_role_utterances(
    candidate_utterances: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Pick bounded cue-rich rows that help establish speaker roles.

    Args:
        candidate_utterances: Visible rows for one speaker; empty means no UI text.

    Returns:
        Chronological rows selected for the role agent; empty means no evidence.
    """
    # No visible rows means the agent has no safe text to use for this speaker.
    if candidate_utterances == []:
        return []

    ranked_utterances = sorted(
        candidate_utterances,
        key=lambda utterance: (
            int(utterance.get("_role_cue_strength", 0)),
            _role_evidence_start_seconds(utterance),
        ),
        reverse=True,
    )
    selected_utterances = ranked_utterances[:ROLE_EVIDENCE_REPRESENTATIVE_UTTERANCES]
    chronological_utterances = sorted(
        selected_utterances,
        key=_role_evidence_start_seconds,
    )

    cleaned_utterances: list[dict[str, Any]] = []
    # Internal scoring fields help selection only; they should not reach the model.
    for utterance in chronological_utterances:
        cleaned_utterances.append(
            {
                key: value
                for key, value in utterance.items()
                if not str(key).startswith("_")
            }
        )

    return cleaned_utterances


def _role_evidence_start_seconds(utterance: dict[str, Any]) -> float:
    """Return one row's start time for ordering role evidence.

    Args:
        utterance: Candidate transcript row; missing start means untimed UI text.

    Returns:
        Start seconds, or `-1.0` when the row cannot be ordered precisely.
    """
    raw_start = utterance.get("start")
    # Missing start times should sort before normal timed rows.
    if raw_start is None:
        return -1.0

    try:
        return float(raw_start)
    except (TypeError, ValueError):
        return -1.0


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
