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

import logging
from dataclasses import dataclass, field

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

    @property
    def running_confidence(self) -> float:
        """Running average confidence across all invocations."""
        if not self.confidence_history:
            return 0.0
        return sum(self.confidence_history) / len(self.confidence_history)

    def update(self, new_mapping: dict[str, str], confidence: float) -> bool:
        """Update the mapping and detect flips.

        Args:
            new_mapping: The new speaker→role mapping from the agent.
            confidence: The agent's confidence in this mapping.

        Returns:
            True if a label flip was detected, False otherwise.
        """
        flip_detected = self._detect_flip(new_mapping)

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
        """Detect if the speaker labels have flipped (swapped roles).

        A flip occurs when spk_0 and spk_1 swap their assigned roles
        compared to the previous mapping.

        Args:
            new_mapping: The proposed new mapping.

        Returns:
            True if a flip was detected.
        """
        if not self.current_mapping:
            return False

        # Check if all speakers swapped roles
        for speaker, new_role in new_mapping.items():
            old_role = self.current_mapping.get(speaker)
            if old_role and old_role != new_role:
                # At least one role changed — check if it's a full swap
                other_speakers_swapped = all(
                    self.current_mapping.get(s) == new_mapping.get(
                        [k for k in new_mapping if k != s][0] if len(new_mapping) > 1 else s
                    )
                    for s in new_mapping
                    if s != speaker
                )
                return other_speakers_swapped

        return False


# Per-session state store for role mappings
_session_states: dict[str, RoleMappingState] = {}


def get_or_create_state(session_id: str) -> RoleMappingState:
    """Get or create the role mapping state for a session.

    Args:
        session_id: The session identifier.

    Returns:
        The RoleMappingState for this session.
    """
    if session_id not in _session_states:
        _session_states[session_id] = RoleMappingState()
    return _session_states[session_id]


def cleanup_session(session_id: str) -> None:
    """Remove the role mapping state for a session.

    Called when a WebSocket disconnects to prevent memory leaks.

    Args:
        session_id: The session identifier to clean up.
    """
    _session_states.pop(session_id, None)
