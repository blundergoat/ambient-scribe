"""
Tests for the role inference tools and state management.
"""

import json

from agents import MEDICAL_ROLE_PROMPT
from api.server import _heuristic_role_inference
from tools.assign_roles import (
    RoleMapping,
    RoleMappingState,
    apply_role_mapping_result,
    assign_roles,
    cleanup_session,
    get_or_create_state,
)


class TestRoleMappingState:
    """Tests for the RoleMappingState class."""

    def test_initial_state(self):
        state = RoleMappingState()
        assert state.current_mapping == {}
        assert state.mapping_history == []
        assert state.running_confidence == 0.0

    def test_update_stores_mapping(self):
        state = RoleMappingState()
        mapping = {"spk_0": "DOCTOR", "spk_1": "PATIENT"}
        flip = state.update(mapping, confidence=0.7)

        assert state.current_mapping == mapping
        assert len(state.mapping_history) == 1
        assert not flip

    def test_running_confidence(self):
        state = RoleMappingState()
        state.update({"spk_0": "DOCTOR"}, confidence=0.6)
        state.update({"spk_0": "DOCTOR"}, confidence=0.8)
        state.update({"spk_0": "DOCTOR"}, confidence=1.0)

        assert abs(state.running_confidence - 0.8) < 0.01

    def test_no_flip_on_first_mapping(self):
        state = RoleMappingState()
        flip = state.update({"spk_0": "DOCTOR", "spk_1": "PATIENT"}, confidence=0.7)
        assert not flip

    def test_no_flip_on_same_mapping(self):
        state = RoleMappingState()
        state.update({"spk_0": "DOCTOR", "spk_1": "PATIENT"}, confidence=0.7)
        flip = state.update({"spk_0": "DOCTOR", "spk_1": "PATIENT"}, confidence=0.8)
        assert not flip

    def test_detects_full_speaker_flip(self):
        state = RoleMappingState()
        state.update({"spk_0": "DOCTOR", "spk_1": "PATIENT"}, confidence=0.7)
        flip = state.update({"spk_0": "PATIENT", "spk_1": "DOCTOR"}, confidence=0.9)
        assert flip


class TestSessionStateManagement:
    """Tests for the per-session state store."""

    def test_get_or_create_new_session(self):
        state = get_or_create_state("test-session-1")
        assert isinstance(state, RoleMappingState)
        assert state.current_mapping == {}

    def test_get_or_create_existing_session(self):
        state1 = get_or_create_state("test-session-2")
        state1.update({"spk_0": "DOCTOR"}, confidence=0.9)

        state2 = get_or_create_state("test-session-2")
        assert state2.current_mapping == {"spk_0": "DOCTOR"}

    def test_cleanup_session(self):
        get_or_create_state("test-session-3")
        cleanup_session("test-session-3")

        # After cleanup, should get a fresh state
        state = get_or_create_state("test-session-3")
        assert state.current_mapping == {}

    def test_cleanup_nonexistent_session(self):
        # Should not raise
        cleanup_session("nonexistent-session")


class TestRoleMapping:
    """Tests for the RoleMapping data class."""

    def test_creation(self):
        mapping = RoleMapping(
            mapping={"spk_0": "DOCTOR", "spk_1": "PATIENT"},
            attributed_segments=[],
            confidence=0.85,
            flip_detected=False,
            reasoning="Doctor asks clinical questions",
        )
        assert mapping.confidence == 0.85
        assert not mapping.flip_detected


class TestApplyRoleMappingResult:
    """Tests for the helper that persists agent output and attributes segments."""

    def test_apply_role_mapping_result_updates_state_and_segments(self):
        cleanup_session("apply-role-session")

        result = apply_role_mapping_result(
            session_id="apply-role-session",
            segments=[
                {
                    "speaker_id": "spk_0",
                    "text": "Good morning",
                    "start": 0.0,
                    "end": 1.0,
                },
                {
                    "speaker_id": "spk_1",
                    "text": "I've had chest pain",
                    "start": 1.5,
                    "end": 3.5,
                },
            ],
            mapping={"spk_0": "doctor", "spk_1": "patient"},
            confidence=0.8,
            reasoning="Doctor asks the opening clinical question.",
        )

        assert result.mapping == {"spk_0": "DOCTOR", "spk_1": "PATIENT"}
        assert result.attributed_segments[0]["role"] == "DOCTOR"
        assert result.attributed_segments[1]["role"] == "PATIENT"
        assert result.confidence == 0.8
        assert (
            get_or_create_state("apply-role-session").current_mapping == result.mapping
        )


class TestFlipDetection:
    """Tests for flip detection edge cases via apply_role_mapping_result."""

    def test_flip_detection_on_role_swap(self):
        """Detect a flip when the mapping reverses between two updates."""
        cleanup_session("flip-detect-session")
        state = get_or_create_state("flip-detect-session")

        mapping_a = {"spk_0": "DOCTOR", "spk_1": "PATIENT"}
        mapping_b = {"spk_0": "PATIENT", "spk_1": "DOCTOR"}

        state.update(mapping_a, confidence=0.8)
        flip = state.update(mapping_b, confidence=0.9)

        assert flip is True

    def test_no_flip_on_first_mapping(self):
        """First mapping ever recorded cannot be a flip."""
        cleanup_session("no-flip-first-session")
        state = get_or_create_state("no-flip-first-session")

        flip = state.update({"spk_0": "DOCTOR", "spk_1": "PATIENT"}, confidence=0.7)
        assert flip is False

    def test_no_flip_on_same_mapping(self):
        """Repeating the same mapping is not a flip."""
        cleanup_session("no-flip-same-session")
        state = get_or_create_state("no-flip-same-session")

        mapping = {"spk_0": "DOCTOR", "spk_1": "PATIENT"}
        state.update(mapping, confidence=0.7)
        flip = state.update(mapping, confidence=0.8)

        assert flip is False


class TestThreeSpeakerMapping:
    """Tests for role mapping with more than two speakers."""

    def test_three_speakers_mapping(self):
        """Three-speaker mapping applies correct roles to all segments."""
        cleanup_session("three-spk-session")

        segments = [
            {"speaker_id": "spk_0", "text": "Good morning", "start": 0.0, "end": 1.0},
            {
                "speaker_id": "spk_1",
                "text": "I have a headache",
                "start": 1.0,
                "end": 2.5,
            },
            {
                "speaker_id": "spk_2",
                "text": "Let me check vitals",
                "start": 2.5,
                "end": 4.0,
            },
        ]
        mapping = {"spk_0": "DOCTOR", "spk_1": "PATIENT", "spk_2": "NURSE"}

        result = apply_role_mapping_result(
            session_id="three-spk-session",
            segments=segments,
            mapping=mapping,
            confidence=0.85,
            reasoning="Three distinct speakers identified.",
        )

        assert result.attributed_segments[0]["role"] == "DOCTOR"
        assert result.attributed_segments[1]["role"] == "PATIENT"
        assert result.attributed_segments[2]["role"] == "NURSE"
        assert result.mapping == {
            "spk_0": "DOCTOR",
            "spk_1": "PATIENT",
            "spk_2": "NURSE",
        }


class TestConfidenceEWMA:
    """Tests for the EWMA confidence tracking window."""

    def test_confidence_ewma_last_5(self):
        """Running confidence should reflect only the last 5 readings."""
        cleanup_session("ewma-session")
        state = get_or_create_state("ewma-session")

        # First 5 readings at low confidence
        for _ in range(5):
            state.update({"spk_0": "DOCTOR"}, confidence=0.3)

        # Next 5 readings at high confidence
        for _ in range(5):
            state.update({"spk_0": "DOCTOR"}, confidence=0.9)

        # EWMA window is last 5, so should be ~0.9
        assert abs(state.running_confidence - 0.9) < 0.01


class TestNormalizeMapping:
    """Tests for the _normalize_mapping helper."""

    def test_normalize_mapping_uppercase(self):
        """Mixed-case role names should be uppercased."""
        from tools.assign_roles import _normalize_mapping

        result = _normalize_mapping({"spk_0": "doctor", "spk_1": "Patient"})
        assert result == {"spk_0": "DOCTOR", "spk_1": "PATIENT"}


class TestMedicalRolePrompt:
    """Verify the medical role prompt supports the live consultation UI."""

    def test_medical_prompt_mentions_doctor_patient(self):
        """Medical prompt must mention DOCTOR and PATIENT."""
        assert "DOCTOR" in MEDICAL_ROLE_PROMPT
        assert "PATIENT" in MEDICAL_ROLE_PROMPT

    def test_medical_prompt_mentions_tool_and_json_fallback(self):
        """Prompt supports the UI by allowing tool calls and JSON fallback."""
        assert "assign_roles" in MEDICAL_ROLE_PROMPT
        assert '"mapping"' in MEDICAL_ROLE_PROMPT
        assert '"confidence"' in MEDICAL_ROLE_PROMPT


class TestHeuristicRoleInference:
    """Tests for the keyword-based heuristic fallback classifier."""

    def test_medical_prescribe_keyword_assigns_doctor(self):
        """Speaker using 'prescribe' and 'mg' should be classified as DOCTOR."""
        segments = [
            {
                "speaker_id": "spk_0",
                "text": "I prescribe 10mg of ibuprofen",
                "start": 0.0,
                "end": 2.0,
            },
            {"speaker_id": "spk_1", "text": "Thank you", "start": 2.0, "end": 3.0},
        ]
        result = _heuristic_role_inference(segments, "")
        assert result is not None
        assert result["mapping"]["spk_0"] == "DOCTOR"
        assert result["confidence"] == 0.4

    def test_medical_pain_keyword_assigns_patient(self):
        """Speaker expressing pain should be classified as PATIENT."""
        segments = [
            {
                "speaker_id": "spk_0",
                "text": "What brings you in?",
                "start": 0.0,
                "end": 1.0,
            },
            {
                "speaker_id": "spk_1",
                "text": "I've been feeling pain in my chest and it hurts",
                "start": 1.0,
                "end": 3.0,
            },
        ]
        result = _heuristic_role_inference(segments, "")
        assert result is not None
        assert result["mapping"]["spk_1"] == "PATIENT"
        assert result["confidence"] == 0.4

    def test_empty_transcript_and_no_segments_returns_none(self):
        """Empty input should return None."""
        result = _heuristic_role_inference([], "")
        assert result is None

    def test_empty_transcript_with_whitespace_returns_none(self):
        """Whitespace-only transcript with no segments returns None."""
        result = _heuristic_role_inference([], "   ")
        assert result is None

    def test_medical_both_keywords_strongest_wins(self):
        """When a speaker uses both doctor and patient keywords, the higher count wins."""
        segments = [
            {
                "speaker_id": "spk_0",
                "text": "The diagnosis shows symptoms and I prescribe medication with dosage of 10mg",
                "start": 0.0,
                "end": 3.0,
            },
            {
                "speaker_id": "spk_1",
                "text": "I feel bad and my pain hurts",
                "start": 3.0,
                "end": 5.0,
            },
        ]
        result = _heuristic_role_inference(segments, "")
        assert result is not None
        # spk_0 has 5 doctor keywords vs 0 patient
        assert result["mapping"]["spk_0"] == "DOCTOR"
        # spk_1 has 3 patient keywords vs 0 doctor
        assert result["mapping"]["spk_1"] == "PATIENT"

    def test_three_speakers_stay_medical_roles(self):
        """Extra speakers remain patient-labelled until the UI has a stronger role."""
        segments = [
            {"speaker_id": "spk_0", "text": "First", "start": 0.0, "end": 1.0},
            {"speaker_id": "spk_1", "text": "Second", "start": 1.0, "end": 2.0},
            {"speaker_id": "spk_2", "text": "Third", "start": 2.0, "end": 3.0},
        ]
        result = _heuristic_role_inference(segments, "")
        assert result is not None
        assert result["mapping"]["spk_0"] == "DOCTOR"
        assert result["mapping"]["spk_1"] == "PATIENT"
        assert result["mapping"]["spk_2"] == "PATIENT"

    def test_medical_no_keywords_assigns_by_order(self):
        """Speakers with no keyword match get first-available roles."""
        segments = [
            {"speaker_id": "spk_0", "text": "Hello", "start": 0.0, "end": 1.0},
            {"speaker_id": "spk_1", "text": "Hi", "start": 1.0, "end": 2.0},
        ]
        result = _heuristic_role_inference(segments, "")
        assert result is not None
        # First speaker gets DOCTOR (first available), second gets PATIENT
        assert result["mapping"]["spk_0"] == "DOCTOR"
        assert result["mapping"]["spk_1"] == "PATIENT"

    def test_heuristic_with_transcript_only(self):
        """Segments empty but transcript non-empty — returns None (no speaker_ids)."""
        result = _heuristic_role_inference([], "some transcript")
        assert result is None


# =========================================================================
# Task 3.8 — @tool assign_roles tests
# =========================================================================


class TestAssignRolesTool:
    """Tests for the @tool-decorated assign_roles function."""

    _SEGMENTS = [
        {
            "speaker_id": "spk_0",
            "text": "What brings you in today?",
            "start": 0.0,
            "end": 2.0,
        },
        {
            "speaker_id": "spk_1",
            "text": "I have a bad headache",
            "start": 2.5,
            "end": 4.0,
        },
    ]

    def test_tool_returns_complete_structure(self):
        """assign_roles returns all required fields with correct values."""
        cleanup_session("tool-struct-session")

        result = assign_roles(
            session_id="tool-struct-session",
            mapping=json.dumps({"spk_0": "DOCTOR", "spk_1": "PATIENT"}),
            segments=json.dumps(self._SEGMENTS),
            confidence=0.85,
            reasoning="Doctor asks opening clinical question",
        )

        assert "mapping" in result
        assert "attributed_segments" in result
        assert "confidence" in result
        assert "flip_detected" in result
        assert "reasoning" in result
        assert result["mapping"] == {"spk_0": "DOCTOR", "spk_1": "PATIENT"}
        assert result["confidence"] == 0.85
        assert result["flip_detected"] is False
        assert result["reasoning"] == "Doctor asks opening clinical question"

    def test_tool_persists_state(self):
        """assign_roles updates mapping_history and current_mapping in state."""
        cleanup_session("tool-persist-session")

        assign_roles(
            session_id="tool-persist-session",
            mapping=json.dumps({"spk_0": "DOCTOR", "spk_1": "PATIENT"}),
            segments=json.dumps(self._SEGMENTS),
            confidence=0.75,
        )

        state = get_or_create_state("tool-persist-session")
        assert len(state.mapping_history) == 1
        assert state.current_mapping == {"spk_0": "DOCTOR", "spk_1": "PATIENT"}

        # Second call grows history
        assign_roles(
            session_id="tool-persist-session",
            mapping=json.dumps({"spk_0": "DOCTOR", "spk_1": "PATIENT"}),
            segments=json.dumps(self._SEGMENTS),
            confidence=0.9,
        )

        assert len(state.mapping_history) == 2

    def test_tool_preserves_all_segment_fields(self):
        """Original segment fields are preserved and role is added."""
        cleanup_session("tool-fields-session")

        result = assign_roles(
            session_id="tool-fields-session",
            mapping=json.dumps({"spk_0": "DOCTOR", "spk_1": "PATIENT"}),
            segments=json.dumps(self._SEGMENTS),
            confidence=0.8,
        )

        seg = result["attributed_segments"][0]
        assert seg["speaker_id"] == "spk_0"
        assert seg["text"] == "What brings you in today?"
        assert seg["start"] == 0.0
        assert seg["end"] == 2.0
        assert seg["role"] == "DOCTOR"

        seg2 = result["attributed_segments"][1]
        assert seg2["role"] == "PATIENT"

    def test_tool_detects_flip(self):
        """Second call with swapped roles sets flip_detected=True."""
        cleanup_session("tool-flip-session")

        # First call — establish mapping
        assign_roles(
            session_id="tool-flip-session",
            mapping=json.dumps({"spk_0": "DOCTOR", "spk_1": "PATIENT"}),
            segments=json.dumps(self._SEGMENTS),
            confidence=0.8,
        )

        # Second call — swap roles
        result = assign_roles(
            session_id="tool-flip-session",
            mapping=json.dumps({"spk_0": "PATIENT", "spk_1": "DOCTOR"}),
            segments=json.dumps(self._SEGMENTS),
            confidence=0.9,
        )

        assert result["flip_detected"] is True
