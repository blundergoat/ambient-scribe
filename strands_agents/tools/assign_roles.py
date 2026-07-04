"""
Role-mapping tool used by the Strands agent.

The agent decides which raw speaker is DOCTOR or PATIENT; this module stores
that decision, detects diarization flips, and returns attributed segments. The
browser uses the result to replace raw `spk_*` labels with clinical roles.
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
    """
    Role assignment result ready for browser relabeling.

    The worker publishes this after the agent or tool chooses roles. Empty
    mappings leave transcript lines on raw speaker labels so the UI does not
    overstate uncertain attribution.

    Attributes:
        mapping: Speaker-to-role labels such as `spk_0 -> DOCTOR`.
        attributed_segments: Transcript segments with role labels added.
        confidence: Running confidence shown by role state and dev tools.
        flip_detected: True when diarization labels likely swapped.
        reasoning: Short explanation for clinical review/debug surfaces.
    """

    mapping: dict[str, str]  # {"spk_0": "DOCTOR", "spk_1": "PATIENT"}
    attributed_segments: list[dict]  # Segments with roles applied
    confidence: float  # 0.0-1.0, running average
    flip_detected: bool = False  # True if labels swapped since last call
    reasoning: str = ""  # Brief explanation of assignment logic


@dataclass
class RoleMappingState:
    """
    Per-session memory for visible speaker role labels.

    It keeps mapping and confidence history across agent calls so a browser
    recording can get steadier DOCTOR/PATIENT labels over time. Empty state
    means the UI should keep raw speaker labels until evidence arrives.

    Attributes:
        current_mapping: Latest speaker-to-role labels shown in the transcript.
        mapping_history: Recent mappings used to detect label flips.
        confidence_history: Recent confidence values used for the running score.
        confirmed_overrides: User-selected labels the agent should respect.
        last_flip_detected: Whether the latest update detected a label swap.
    """

    current_mapping: dict[str, str] = field(default_factory=dict)
    mapping_history: list[dict[str, str]] = field(default_factory=list)
    confidence_history: list[float] = field(default_factory=list)
    confirmed_overrides: dict[str, str] = field(default_factory=dict)
    last_flip_detected: bool = False

    @property
    def running_confidence(self) -> float:
        """Average recent confidence for the role badge shown to users.

        Returns:
            Average of up to five recent scores; `0.0` means no role evidence yet.
        """
        # No agent decisions means the browser should not show confident roles.
        if not self.confidence_history:
            return 0.0
        recent = self.confidence_history[-5:]
        return sum(recent) / len(recent)

    def did_update_mapping_detect_flip(
        self, new_mapping: dict[str, str], confidence: float
    ) -> bool:
        """Update the visible mapping and report whether labels flipped.

        Args:
            new_mapping: New speaker-to-role mapping; empty leaves no visible roles.
            confidence: Agent confidence for the mapping shown in the UI.

        Returns:
            True when a label flip was detected, false when labels stayed stable.
        """
        flip_detected = self._is_role_label_flip(new_mapping)
        self.last_flip_detected = flip_detected

        self.mapping_history.append(new_mapping)
        self.confidence_history.append(confidence)
        self.current_mapping = new_mapping

        # A flip warning helps the browser explain sudden role relabeling.
        if flip_detected:
            logger.warning(
                "role_mapping.flip_detected",
                extra={
                    "previous": self.mapping_history[-2]
                    if len(self.mapping_history) > 1
                    else {},
                    "current": new_mapping,
                },
            )

        return flip_detected

    update = did_update_mapping_detect_flip

    def _is_role_label_flip(self, new_mapping: dict[str, str]) -> bool:
        """Detect a role permutation across the same speaker IDs.

        A flip occurs when at least two existing speakers change roles, every
        changed speaker gets a different role than before, and the set of roles
        assigned across those changed speakers is preserved.

        Args:
            new_mapping: Proposed speaker-to-role labels; empty means no flip.

        Returns:
            True if the same speakers swapped visible roles.
        """
        # Different speaker sets mean the UI is seeing a new participant, not a flip.
        if not self.current_mapping or set(new_mapping) != set(self.current_mapping):
            return False

        changed_speakers = [
            speaker
            for speaker, new_role in new_mapping.items()
            if self.current_mapping.get(speaker) != new_role
        ]
        # One changed speaker is a correction, not a two-way diarization swap.
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
    """Persist a mapping decision and return attributed segment payloads.

    Args:
        session_id: Recording UUID whose transcript should be relabeled.
        segments: New transcript segments; empty returns no attributed lines.
        mapping: Speaker-to-role labels; empty leaves all segment roles UNKNOWN.
        confidence: Agent confidence for this mapping.
        reasoning: Optional explanation shown in logs/dev review; empty means no explanation.

    Returns:
        RoleMapping ready for Mercure publication and browser relabeling.
    """
    state = get_or_create_state(session_id)
    normalized_mapping = _normalize_mapping(mapping)
    flip_detected = state.did_update_mapping_detect_flip(normalized_mapping, confidence)

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
    # Each mapping entry controls how matching transcript lines are labeled.
    for speaker_id, role in mapping.items():
        normalized[str(speaker_id)] = str(role).upper()

    return normalized


def _attribute_segments(
    segments: list[dict[str, Any]],
    mapping: dict[str, str],
) -> list[dict[str, Any]]:
    """Apply the current mapping to a list of raw transcript segments."""
    attributed_segments: list[dict[str, Any]] = []
    # Each raw segment becomes a role-labeled line for the browser transcript.
    for segment in segments:
        speaker_id = str(segment.get("speaker_id", ""))
        attributed_segments.append(
            {
                **segment,
                "role": mapping.get(speaker_id, "UNKNOWN"),
            }
        )

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

    Returns:
        Mapping payload the browser uses to relabel transcript segments.
    """
    # Tool callers send JSON strings; tests may pass dictionaries directly.
    if isinstance(mapping, str):
        parsed_mapping = json.loads(mapping)
    else:
        parsed_mapping = mapping
    # Empty segment JSON means the UI receives a mapping without new attributed text.
    if isinstance(segments, str):
        parsed_segments = json.loads(segments)
    else:
        parsed_segments = segments

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
