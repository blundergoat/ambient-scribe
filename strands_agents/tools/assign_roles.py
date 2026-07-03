"""
Role assignment tool — programmatic state management for speaker→role mapping.

=============================================================================
WHAT THIS FILE DOES
=============================================================================

This tool handles the STATE MANAGEMENT side of role assignment:
  - Persists the speaker→role mapping across agent invocations
  - Detects diarization label flips by comparing mapping history
  - Returns structured output via Pydantic (not free-text)
  - Tracks confidence as a running average over time

The LLM AGENT decides the roles (via its system prompt). This tool manages
the persistence and validation of those decisions.

=============================================================================
WHY THIS IS A TOOL (NOT JUST A PROMPT)
=============================================================================

Without this tool, the agent would need to:
  - Remember its own mapping history in context (grows unboundedly)
  - Detect label flips via text reasoning (unreliable)
  - Output unstructured JSON (parsing errors)

The tool encapsulates state management so the agent focuses on reasoning.
"""

from __future__ import annotations

import json
import logging
import threading
from dataclasses import dataclass, field
from typing import Any

from strands import tool

logger = logging.getLogger(__name__)


@dataclass
class RoleMapping:
    """The result of a role assignment decision."""

    mapping: dict[str, str]              # {"spk_0": "DOCTOR", "spk_1": "PATIENT"}
    attributed_segments: list[dict]      # Segments with roles applied
    confidence: float                    # 0.0-1.0, running average
    flip_detected: bool = False          # True if labels swapped since last call
    reasoning: str = ""                  # Brief explanation of assignment logic


@dataclass
class RoleMappingState:
    """Per-session state for role mapping, managed by the tool."""

    current_mapping: dict[str, str] = field(default_factory=dict)
    mapping_history: list[dict[str, str]] = field(default_factory=list)
    confidence_history: list[float] = field(default_factory=list)
    confirmed_overrides: dict[str, str] = field(default_factory=dict)
    last_flip_detected: bool = False

    @property
    def running_confidence(self) -> float:
        """EWMA confidence over the last 5 invocations."""
        if not self.confidence_history:
            return 0.0
        recent = self.confidence_history[-5:]
        return sum(recent) / len(recent)

    def update(self, new_mapping: dict[str, str], confidence: float) -> bool:
        """Update the mapping and detect flips.

        Args:
            new_mapping: The new speaker→role mapping from the agent.
            confidence: The agent's confidence in this mapping.

        Returns:
            True if a label flip was detected, False otherwise.
        """
        flip_detected = self._detect_flip(new_mapping)
        self.last_flip_detected = flip_detected

        self.mapping_history.append(new_mapping)
        self.confidence_history.append(confidence)
        self.current_mapping = new_mapping

        if flip_detected:
            logger.warning("role_mapping.flip_detected", extra={
                "previous": self.mapping_history[-2] if len(self.mapping_history) > 1 else {},
                "current": new_mapping,
            })

        return flip_detected

    def _detect_flip(self, new_mapping: dict[str, str]) -> bool:
        """Detect a role permutation across the same speaker IDs.

        A flip occurs when at least two existing speakers change roles, every
        changed speaker gets a different role than before, and the set of roles
        assigned across those changed speakers is preserved.

        Args:
            new_mapping: The proposed new mapping.

        Returns:
            True if a flip was detected.
        """
        if not self.current_mapping or set(new_mapping) != set(self.current_mapping):
            return False

        changed_speakers = [
            speaker
            for speaker, new_role in new_mapping.items()
            if self.current_mapping.get(speaker) != new_role
        ]
        if len(changed_speakers) < 2:
            return False

        previous_roles = {self.current_mapping[speaker] for speaker in changed_speakers}
        next_roles = {new_mapping[speaker] for speaker in changed_speakers}

        return previous_roles == next_roles and all(
            self.current_mapping[speaker] != new_mapping[speaker]
            for speaker in changed_speakers
        )


# Per-session state store for role mappings
_session_states: dict[str, RoleMappingState] = {}
_states_lock = threading.Lock()


def get_or_create_state(session_id: str) -> RoleMappingState:
    """Get or create the role mapping state for a session.

    Args:
        session_id: The session identifier.

    Returns:
        The RoleMappingState for this session.
    """
    with _states_lock:
        if session_id not in _session_states:
            _session_states[session_id] = RoleMappingState()
        return _session_states[session_id]


def apply_role_mapping_result(
    session_id: str,
    segments: list[dict[str, Any]],
    mapping: dict[str, str],
    confidence: float,
    reasoning: str = "",
) -> RoleMapping:
    """Persist a mapping decision and return attributed segment payloads."""
    state = get_or_create_state(session_id)
    normalized_mapping = _normalize_mapping(mapping)
    flip_detected = state.update(normalized_mapping, confidence)

    return RoleMapping(
        mapping=normalized_mapping,
        attributed_segments=_attribute_segments(segments, normalized_mapping),
        confidence=state.running_confidence,
        flip_detected=flip_detected,
        reasoning=reasoning,
    )


def cleanup_session(session_id: str) -> None:
    """Remove the role mapping state for a session.

    Called when a WebSocket disconnects to prevent memory leaks.

    Args:
        session_id: The session identifier to clean up.
    """
    with _states_lock:
        _session_states.pop(session_id, None)


def _normalize_mapping(mapping: dict[str, str]) -> dict[str, str]:
    """Normalize agent output into the expected speaker->role shape."""
    normalized: dict[str, str] = {}
    for speaker_id, role in mapping.items():
        normalized[str(speaker_id)] = str(role).upper()

    return normalized


def _attribute_segments(
    segments: list[dict[str, Any]],
    mapping: dict[str, str],
) -> list[dict[str, Any]]:
    """Apply the current mapping to a list of raw transcript segments."""
    attributed_segments: list[dict[str, Any]] = []
    for segment in segments:
        speaker_id = str(segment.get("speaker_id", ""))
        attributed_segments.append({
            **segment,
            "role": mapping.get(speaker_id, "UNKNOWN"),
        })

    return attributed_segments


@tool
def assign_roles(
    session_id: str,
    mapping: str,
    segments: str,
    confidence: float,
    reasoning: str = "",
) -> dict:
    """Persist a speaker-to-role mapping and return attributed segments.

    Call this tool after analysing the transcript to commit your role decisions.
    The tool handles state persistence, flip detection, and segment attribution.

    Args:
        session_id: The session identifier.
        mapping: JSON string of speaker-to-role mapping, e.g. '{"spk_0": "DOCTOR", "spk_1": "PATIENT"}'.
        segments: JSON string of transcript segments to attribute, each with speaker_id, text, start, end.
        confidence: Your confidence in this mapping (0.0 to 1.0).
        reasoning: Brief explanation of your role assignment logic.
    """
    parsed_mapping = json.loads(mapping) if isinstance(mapping, str) else mapping
    parsed_segments = json.loads(segments) if isinstance(segments, str) else segments

    result = apply_role_mapping_result(
        session_id=session_id,
        segments=parsed_segments,
        mapping=parsed_mapping,
        confidence=confidence,
        reasoning=reasoning,
    )

    return {
        "mapping": result.mapping,
        "attributed_segments": result.attributed_segments,
        "confidence": result.confidence,
        "flip_detected": result.flip_detected,
        "reasoning": result.reasoning,
    }
