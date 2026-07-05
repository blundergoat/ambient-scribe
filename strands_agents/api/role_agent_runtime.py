"""
Runtime role-agent execution for visible transcript batches.

The role queue calls this after raw NeMo segments are already visible in the
browser. It keeps Bedrock/Ollama agent calls, compact tool handling, and the
keyword fallback away from `server.py` while preserving the same role payload
the UI uses to relabel transcript cards.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from api.agent_observability import agent_metric_fields as _agent_metric_fields
from api.role_heuristics import heuristic_role_inference as _heuristic_role_inference
from tools.assign_roles import (
    clear_pending_role_segments,
    get_or_create_state,
    store_pending_role_segments,
)

logger = logging.getLogger(__name__)


def run_role_inference(
    session_id: str,
    segments: list[dict[str, Any]],
    role_evidence: dict[str, Any],
) -> dict | None:
    """Run the off-GPU role agent, then fall back to keyword labels.

    Args:
        session_id: Browser recording UUID whose transcript is being relabeled.
        segments: Latest visible rows; empty means only mapping state may change.
        role_evidence: Bounded per-speaker evidence; empty means the model sees no text.

    Returns:
        Role mapping payload for the browser, or None when raw labels should remain.
    """
    state = get_or_create_state(session_id)
    agent_payload, provider_unreachable = _run_role_agent(
        session_id, segments, role_evidence, state
    )

    # A successful tool call gives the clinician the strongest available labels.
    if agent_payload is not None:
        return agent_payload

    heuristic_payload = _run_role_heuristic_fallback(
        session_id, segments, provider_unreachable
    )

    # The fallback can still give usable labels while the cloud/CPU model recovers.
    if heuristic_payload is not None:
        return heuristic_payload

    # A confirmed outage should warn the browser even when no speaker labels exist.
    if provider_unreachable:
        return {"path": "none", "mapping": {}, "provider_unreachable": True}
    return None


def _run_role_agent(
    session_id: str,
    segments: list[dict[str, Any]],
    role_evidence: dict[str, Any],
    state: Any,
) -> tuple[dict[str, Any] | None, bool]:
    """Try one structured role-agent call for the user's latest transcript rows.

    Args:
        session_id: Browser recording UUID used for role state and pending segments.
        segments: Visible rows held server-side so the model does not echo them.
        role_evidence: Capped evidence sent to the model for this role decision.
        state: RoleMappingState updated by the `assign_roles` tool.

    Returns:
        `(payload, provider_unreachable)`; payload is None when fallback should run.
    """
    provider_unreachable = False

    try:
        from agents import MEDICAL_ROLE_INSTRUCTION, create_role_inference_agent

        agent = create_role_inference_agent()
        history_len_before = len(state.mapping_history)
        suppressed_flips_before = getattr(state, "suppressed_flip_count", 0)
        payload = {
            "session_id": session_id,
            "current_mapping": state.current_mapping,
            "mapping_history": state.mapping_history[-5:],
            "confirmed_overrides": state.confirmed_overrides,
            "role_evidence": role_evidence,
        }
        payload_json = json.dumps(payload, separators=(",", ":"))
        store_pending_role_segments(session_id, segments)
        try:
            agent_result = agent(f"{MEDICAL_ROLE_INSTRUCTION}\n\n{payload_json}")
        finally:
            clear_pending_role_segments(session_id)

        _record_role_agent_truncation_if_needed(session_id, state, agent_result)
        metric_fields = _agent_metric_fields(agent_result, "role-inference")
        metric_fields["role_input_chars"] = len(payload_json)

        # Suppressed flips are still valid tool decisions; fallback must not undo them.
        suppressed_flip_added = (
            getattr(state, "suppressed_flip_count", 0) > suppressed_flips_before
        )
        # The tool persists mapping state; new history or suppression proves a UI decision.
        if len(state.mapping_history) > history_len_before or suppressed_flip_added:
            return (
                {
                    "mapping": state.current_mapping,
                    "confidence": state.running_confidence,
                    "reasoning": "",
                    "_tool_invoked": True,
                    "path": "tool",
                    **metric_fields,
                },
                provider_unreachable,
            )

        logger.warning(
            "role_inference.tool_not_invoked session_id=%s",
            session_id,
            extra={
                "session_id": session_id,
                **metric_fields,
            },
        )
    except Exception as agent_error:
        provider_unreachable = _is_provider_unreachable(agent_error)
        _record_role_agent_truncation_if_needed(session_id, state, agent_error)
        logger.warning(
            (
                "role_inference.agent_failed_falling_back_to_heuristic "
                "session_id=%s %s: %s"
            ),
            session_id,
            type(agent_error).__name__,
            str(agent_error)[:200],
            exc_info=agent_error,
            extra={
                "session_id": session_id,
                "error_type": type(agent_error).__name__,
                "error": str(agent_error)[:200],
                "provider_unreachable": provider_unreachable,
            },
        )

    return None, provider_unreachable


def _run_role_heuristic_fallback(
    session_id: str,
    segments: list[dict[str, Any]],
    provider_unreachable: bool,
) -> dict[str, Any] | None:
    """Use keyword role labels when the model path misses or is unreachable.

    Args:
        session_id: Browser recording UUID used only for log joins.
        segments: Latest visible transcript rows to label by keyword evidence.
        provider_unreachable: True when the browser should also show an AI warning.

    Returns:
        Heuristic payload for the browser, or None when no label evidence exists.
    """
    try:
        heuristic_result = _heuristic_role_inference(segments, "")

        # No heuristic result means the UI should keep raw speaker labels for now.
        if heuristic_result is None:
            return None

        heuristic_result["path"] = "heuristic"
        heuristic_result["provider_unreachable"] = provider_unreachable
        logger.info(
            "role_inference.heuristic_used",
            extra={
                "session_id": session_id,
                "roles": len(heuristic_result.get("mapping", {})),
            },
        )
        return heuristic_result
    except Exception as heuristic_error:
        logger.error(
            "role_inference.heuristic_failed session_id=%s %s: %s",
            session_id,
            type(heuristic_error).__name__,
            str(heuristic_error)[:200],
            exc_info=heuristic_error,
            extra={
                "session_id": session_id,
                "error_type": type(heuristic_error).__name__,
                "error": str(heuristic_error)[:200],
            },
        )
    return None


def _is_provider_unreachable(exc: Exception) -> bool:
    """Classify model transport failures that should warn the browser.

    Args:
        exc: Exception from the Bedrock/Ollama role agent call.

    Returns:
        True for connection/timeout style failures; False for other agent errors.
    """
    error_text = f"{type(exc).__name__}: {exc}".lower()
    unreachable_markers = (
        "connect",
        "connection",
        "refused",
        "unreachable",
        "timed out",
        "timeout",
        "max retries",
        "failed to establish",
        "name or service not known",
        "nodename nor servname",
    )
    return any(marker in error_text for marker in unreachable_markers)


def _did_role_agent_hit_output_limit(agent_signal: Any) -> bool:
    """Return whether the role agent stopped before a usable role update.

    Args:
        agent_signal: AgentResult or exception; null-like values mean no model output exists.

    Returns:
        True when the session quality record should count a role truncation.
    """
    stop_reason = str(getattr(agent_signal, "stop_reason", "")).lower()
    signal_text = f"{type(agent_signal).__name__}: {agent_signal}".lower()

    return (
        stop_reason == "max_tokens"
        or "max_tokens truncation" in signal_text
        or "maximum token limit" in signal_text
    )


def _record_role_agent_truncation_if_needed(
    session_id: str,
    state: Any,
    agent_signal: Any,
) -> None:
    """Record a role truncation so final quality output explains raw labels.

    Args:
        session_id: Browser recording UUID whose role update was affected.
        state: RoleMappingState for this recording; missing methods mean no count is stored.
        agent_signal: AgentResult or exception inspected for max-token failure wording.
    """
    # Non-truncated responses should not inflate the quality warning counter.
    if not _did_role_agent_hit_output_limit(agent_signal):
        return

    # Older test doubles without the counter can still exercise the fallback path.
    if not hasattr(state, "record_role_agent_truncation"):
        return

    state.record_role_agent_truncation()
    logger.warning(
        "role_inference.truncated session_id=%s",
        session_id,
        extra={
            "session_id": session_id,
            "role_truncation_events": state.truncation_events,
        },
    )
