"""
Role mapping state and the compact Strands tool used by the scribe UI.

The role agent chooses DOCTOR/PATIENT labels, while this module owns the
server-side session memory that makes those labels stable in the transcript.
Pending transcript rows stay here instead of being echoed through the model,
so the browser still receives role-attributed rows without oversized tool calls.
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

    The queue publishes this after the agent or fallback chooses roles. Empty
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
        last_flip_suppressed: Whether the latest label swap was held for more evidence.
        pending_flip_mapping: Contrary mapping waiting for one more agreeing inference.
        pending_flip_count: Consecutive contrary mappings matching the pending flip.
        suppressed_flip_count: Flip proposals kept out of the visible transcript.
        truncation_events: Agent responses cut off before role labels reached the UI.
        quality_flip_snapshot: Flip counters at quality-record time; tail role
            churn after finalize is reported as the delta from this snapshot.
    """

    current_mapping: dict[str, str] = field(default_factory=dict)
    mapping_history: list[dict[str, str]] = field(default_factory=list)
    confidence_history: list[float] = field(default_factory=list)
    confirmed_overrides: dict[str, str] = field(default_factory=dict)
    last_flip_detected: bool = False
    last_flip_suppressed: bool = False
    pending_flip_mapping: dict[str, str] | None = None
    pending_flip_count: int = 0
    suppressed_flip_count: int = 0
    truncation_events: int = 0
    quality_flip_snapshot: dict[str, int] | None = None

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
        self,
        new_mapping: dict[str, str],
        confidence: float,
        session_id: str | None = None,
    ) -> bool:
        """Update the visible mapping and report whether labels flipped.

        Args:
            new_mapping: New speaker-to-role mapping; empty leaves no visible roles.
            confidence: Agent confidence for the mapping shown in the UI.
            session_id: Browser recording UUID for logs; None means direct tests or old callers.

        Returns:
            True when a label flip was detected, false when labels stayed stable.
        """
        previous_mapping = self.current_mapping
        changed_speakers = [
            speaker
            for speaker, new_role in new_mapping.items()
            if previous_mapping.get(speaker) != new_role
        ]
        flip_detected = self._is_role_label_flip(new_mapping)
        self.last_flip_detected = False
        self.last_flip_suppressed = False

        # A sudden complete swap can be diarization churn, so hold it for confirmation.
        if flip_detected and self._should_suppress_flip(new_mapping, confidence):
            self.suppressed_flip_count += 1
            self.last_flip_suppressed = True
            logger.warning(
                "role_mapping.flip_suppressed speakers=%s",
                ",".join(changed_speakers),
                extra={
                    "previous": previous_mapping,
                    "proposed": new_mapping,
                    "confidence": confidence,
                    "running_confidence": self.running_confidence,
                    "pending_flip_count": self.pending_flip_count,
                    "session_id": session_id,
                },
            )
            return False

        self.last_flip_detected = flip_detected

        # Accepted mappings clear any held contrary proposal from the UI.
        self.pending_flip_mapping = None
        self.pending_flip_count = 0

        self.mapping_history.append(new_mapping)
        self.confidence_history.append(confidence)
        self.current_mapping = new_mapping

        # A flip warning helps the browser explain sudden role relabeling.
        if flip_detected:
            logger.warning(
                "role_mapping.flip_detected speakers=%s",
                ",".join(changed_speakers),
                extra={
                    "previous": previous_mapping,
                    "current": new_mapping,
                    "session_id": session_id,
                },
            )

        return flip_detected

    def _should_suppress_flip(
        self, new_mapping: dict[str, str], confidence: float
    ) -> bool:
        """Return whether a visible role swap should wait for more evidence.

        Args:
            new_mapping: Proposed swapped labels; empty means there is no flip to suppress.
            confidence: Agent confidence; high confidence can override the waiting period.

        Returns:
            True when the browser should keep current labels for this update.
        """
        # First contrary mapping starts the confirmation window.
        if self.pending_flip_mapping != new_mapping:
            self.pending_flip_mapping = dict(new_mapping)
            self.pending_flip_count = 1
        else:
            self.pending_flip_count += 1

        confidence_margin = confidence - self.running_confidence

        # Repeated contrary evidence means the user should see the corrected roles.
        if self.pending_flip_count >= 2:
            return False

        # A strong confidence jump can correct an early wrong mapping immediately.
        if confidence_margin >= 0.09:
            return False

        return True

    def record_role_agent_truncation(self) -> None:
        """Count a model truncation that prevented a visible role update.

        The final quality row uses this so operators can see when the UI kept
        raw speaker labels because the role agent ran out of output tokens.
        """
        self.truncation_events += 1

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


# The label a consumer shows when no speaker role can be claimed. Storage and the
# browser both already treat this as "do not say Doctor or Patient".
UNKNOWN_SPEAKER_ROLE = "UNKNOWN"

# Per-session state store for role mappings.
_session_states: dict[str, RoleMappingState] = {}
_states_lock = threading.Lock()

# Per-session transcript rows available to the tool without model echo.
_pending_role_segments: dict[str, list[dict[str, Any]]] = {}
_pending_role_segments_lock = threading.Lock()


def get_or_create_state(session_id: str) -> RoleMappingState:
    """Get or create the role mapping state for one browser session.

    Args:
        session_id: Browser recording UUID; empty creates isolated but unusable state.

    Returns:
        RoleMappingState used to relabel this session's transcript.
    """
    with _states_lock:
        # First role update for a recording starts with no visible labels.
        if session_id not in _session_states:
            _session_states[session_id] = RoleMappingState()
        return _session_states[session_id]


def peek_state(session_id: str) -> RoleMappingState | None:
    """Return the session's role state only if it already exists.

    Post-visit request paths (e.g. a row correction after the reconnect grace
    window) must not resurrect empty state for a torn-down session, because
    publishing that fabricated state wipes the browser's earned role badge.

    Args:
        session_id: Browser recording UUID; unknown or cleaned-up IDs return None.

    Returns:
        The live RoleMappingState, or None when lifecycle cleanup already ran.
    """
    with _states_lock:
        # Absence is the signal: the visit's role state was already destroyed.
        return _session_states.get(session_id)


def store_pending_role_segments(
    session_id: str, segments: list[dict[str, Any]]
) -> None:
    """Remember transcript rows the role tool may label without model echo.

    Args:
        session_id: Browser recording UUID that owns these rows.
        segments: New visible transcript rows; empty means the tool can still update mapping only.
    """
    with _pending_role_segments_lock:
        _pending_role_segments[session_id] = [dict(segment) for segment in segments]


def pending_role_segments_for_session(session_id: str) -> list[dict[str, Any]]:
    """Return pending transcript rows for a compact tool invocation.

    Args:
        session_id: Browser recording UUID; unknown IDs return no rows for attribution.

    Returns:
        Copies of pending rows; empty means the tool updates roles without new transcript text.
    """
    with _pending_role_segments_lock:
        pending_segments = _pending_role_segments.get(session_id, [])
        return [dict(segment) for segment in pending_segments]


def clear_pending_role_segments(session_id: str) -> None:
    """Forget pending rows after the role agent finishes a call.

    Args:
        session_id: Browser recording UUID; unknown IDs are already clear.
    """
    with _pending_role_segments_lock:
        _pending_role_segments.pop(session_id, None)


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
    # Withdraw before overrides so a clinician's confirmed label still wins.
    normalized_mapping = _withdraw_omitted_speaker_roles(
        session_id, state, normalized_mapping
    )
    normalized_mapping = _apply_confirmed_overrides(
        session_id, state, normalized_mapping
    )
    flip_detected = state.did_update_mapping_detect_flip(
        normalized_mapping, confidence, session_id=session_id
    )
    applied_mapping = state.current_mapping

    return RoleMapping(
        mapping=applied_mapping,
        attributed_segments=_attribute_segments(segments, applied_mapping),
        confidence=state.running_confidence,
        flip_detected=flip_detected,
        reasoning=reasoning,
    )


def cleanup_session(session_id: str) -> None:
    """Remove role mapping and pending rows for a finished browser session.

    Called after a visit can no longer reconnect, preventing one user's labels
    from affecting a later recording.

    Args:
        session_id: Browser recording UUID to clean up.
    """
    with _states_lock:
        _session_states.pop(session_id, None)
    clear_pending_role_segments(session_id)


def _normalize_mapping(mapping: dict[str, str]) -> dict[str, str]:
    """Normalize agent output into the speaker-role labels shown in the UI."""
    normalized: dict[str, str] = {}
    # Each mapping entry controls how matching transcript lines are labeled.
    for speaker_id, role in mapping.items():
        normalized[str(speaker_id)] = str(role).upper()

    return normalized


def _withdraw_omitted_speaker_roles(
    session_id: str,
    state: RoleMappingState,
    mapping: dict[str, str],
) -> dict[str, str]:
    """Turn a dropped speaker label into an explicit withdrawal.

    The role agent is told to omit a speaker whose utterances give no clear
    evidence. Server state replaces its mapping wholesale, but the transcript
    store and the browser both merge only the keys they are given, so an omitted
    speaker would keep the confident DOCTOR or PATIENT label it had before -
    showing the clinician certainty the agent had just withdrawn.

    Naming the speaker as UNKNOWN says the same thing in a form every consumer
    already understands.

    Args:
        session_id: Browser recording UUID for logs; empty still withdraws.
        state: Role state holding the labels currently on screen.
        mapping: Agent-proposed labels; empty withdraws every known speaker.

    Returns:
        Mapping extended with UNKNOWN for each previously labelled speaker the
        agent no longer names.
    """
    withdrawn_mapping = dict(mapping)

    for speaker_id, previous_role in state.current_mapping.items():
        # A speaker the agent still names carries its own answer already.
        if speaker_id in withdrawn_mapping:
            continue
        # Re-stating an existing UNKNOWN is not a withdrawal worth logging.
        if previous_role == UNKNOWN_SPEAKER_ROLE:
            withdrawn_mapping[speaker_id] = UNKNOWN_SPEAKER_ROLE
            continue

        withdrawn_mapping[speaker_id] = UNKNOWN_SPEAKER_ROLE
        logger.info(
            "role_mapping.label_withdrawn session_id=%s speaker_id=%s",
            session_id,
            speaker_id,
            extra={
                "session_id": session_id,
                "speaker_id": speaker_id,
                "previous_role": previous_role,
            },
        )

    return withdrawn_mapping


def _apply_confirmed_overrides(
    session_id: str,
    state: RoleMappingState,
    mapping: dict[str, str],
) -> dict[str, str]:
    """Apply user-confirmed roles before transcript rows are relabeled.

    Args:
        session_id: Browser recording UUID whose role map is being updated.
        state: Role state containing manual corrections from the transcript UI.
        mapping: Agent-proposed speaker-role labels; empty still keeps overrides.

    Returns:
        Mapping with manual corrections preserved for the user's transcript.
    """
    # No manual correction means the agent proposal can be used as-is.
    if state.confirmed_overrides == {}:
        return mapping

    enforced_mapping = dict(mapping)
    # Each override represents a user correction from the transcript UI.
    for speaker_id, role in state.confirmed_overrides.items():
        normalized_speaker_id = str(speaker_id)
        normalized_role = str(role).upper()

        # A differing agent proposal must not undo the user's selected role.
        if enforced_mapping.get(normalized_speaker_id) != normalized_role:
            logger.info(
                "role_mapping.confirmed_override_preserved session_id=%s speaker_id=%s",
                session_id,
                normalized_speaker_id,
                extra={
                    "session_id": session_id,
                    "speaker_id": normalized_speaker_id,
                    "agent_role": enforced_mapping.get(normalized_speaker_id),
                    "confirmed_role": normalized_role,
                },
            )

        enforced_mapping[normalized_speaker_id] = normalized_role

    return enforced_mapping


def _attribute_segments(
    segments: list[dict[str, Any]],
    mapping: dict[str, str],
) -> list[dict[str, Any]]:
    """Apply visible role labels to raw transcript rows for Mercure output."""
    attributed_segments: list[dict[str, Any]] = []
    # Each raw segment becomes a role-labeled line for the browser transcript.
    for segment in segments:
        speaker_id = str(segment.get("speaker_id", ""))
        attributed_segments.append(
            {
                **segment,
                "role": mapping.get(speaker_id, UNKNOWN_SPEAKER_ROLE),
            }
        )

    return attributed_segments


@tool
def assign_roles(
    session_id: str,
    mapping: str,
    confidence: float,
    reasoning: str = "",
) -> dict:
    """Persist a speaker-role mapping and return a compact acknowledgement.

    Call this after analysing the bounded role evidence. Transcript rows are
    already buffered server-side for this session, so the model must not pass
    or receive segment text through the tool.

    Args:
        session_id: Browser recording UUID whose transcript labels should update.
        mapping: JSON string of speaker-role labels; empty keeps raw labels visible.
        confidence: Confidence from 0.0 to 1.0 shown in dev role state.
        reasoning: Short explanation; empty means no explanation is shown to reviewers.

    Returns:
        Compact mapping status; no transcript text is returned to the model.
    """
    # Tool callers send JSON strings; tests may pass dictionaries directly.
    if isinstance(mapping, str):
        parsed_mapping = json.loads(mapping)
    else:
        parsed_mapping = mapping

    result = apply_role_mapping_result(
        session_id=session_id,
        segments=pending_role_segments_for_session(session_id),
        mapping=parsed_mapping,
        confidence=confidence,
        reasoning=reasoning,
    )

    return {
        "mapping": result.mapping,
        "confidence": result.confidence,
        "flip_detected": result.flip_detected,
        "reasoning": result.reasoning,
    }
