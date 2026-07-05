"""
Edge case tests for the @tool assign_roles function.
"""

import json

import pytest

from tools.assign_roles import (
    RoleMappingState,
    _attribute_segments,
    _normalize_mapping,
    assign_roles,
    cleanup_session,
    get_or_create_state,
    pending_role_segments_for_session,
    store_pending_role_segments,
)


class TestAssignRolesToolEdgeCases:
    """Edge cases for the @tool-decorated assign_roles function."""

    def test_empty_mapping(self):
        cleanup_session("tool-empty-map")
        result = assign_roles(
            session_id="tool-empty-map",
            mapping=json.dumps({}),
            confidence=0.5,
        )
        assert result["mapping"] == {}
        assert "attributed_segments" not in result

    def test_dict_mapping_instead_of_json_string(self):
        """When the LLM passes a dict directly (not JSON string)."""
        cleanup_session("tool-dict-input")
        store_pending_role_segments(
            "tool-dict-input",
            [{"speaker_id": "spk_0", "text": "Hi", "start": 0.0, "end": 1.0}],
        )
        result = assign_roles(
            session_id="tool-dict-input",
            mapping={"spk_0": "DOCTOR"},
            confidence=0.7,
        )
        assert result["mapping"] == {"spk_0": "DOCTOR"}
        assert get_or_create_state("tool-dict-input").current_mapping == {
            "spk_0": "DOCTOR"
        }

    def test_pending_segments_stay_server_side(self):
        """The tool reads rows from server state but does not echo them to the model."""
        cleanup_session("tool-pending-segs")
        store_pending_role_segments(
            "tool-pending-segs",
            [{"speaker_id": "spk_0", "text": "Hello", "start": 0.0, "end": 1.0}],
        )
        result = assign_roles(
            session_id="tool-pending-segs",
            mapping=json.dumps({"spk_0": "DOCTOR"}),
            confidence=0.8,
        )
        assert result["mapping"] == {"spk_0": "DOCTOR"}
        assert "attributed_segments" not in result
        assert pending_role_segments_for_session("tool-pending-segs")[0]["text"] == "Hello"

    def test_confidence_above_one(self):
        """Confidence > 1.0 is stored as-is (no clamping)."""
        cleanup_session("tool-high-conf")
        assign_roles(
            session_id="tool-high-conf",
            mapping=json.dumps({"spk_0": "DOCTOR"}),
            confidence=1.5,
        )
        state = get_or_create_state("tool-high-conf")
        assert state.confidence_history[-1] == 1.5

    def test_confidence_zero(self):
        cleanup_session("tool-zero-conf")
        result = assign_roles(
            session_id="tool-zero-conf",
            mapping=json.dumps({"spk_0": "DOCTOR"}),
            confidence=0.0,
        )
        assert result["confidence"] == 0.0

    def test_invalid_json_mapping_raises(self):
        """Malformed JSON string should raise JSONDecodeError."""
        cleanup_session("tool-bad-json")
        with pytest.raises(json.JSONDecodeError):
            assign_roles(
                session_id="tool-bad-json",
                mapping="not valid json",
                confidence=0.5,
            )

    def test_long_reasoning_stored(self):
        cleanup_session("tool-long-reason")
        long_reason = "x" * 5000
        result = assign_roles(
            session_id="tool-long-reason",
            mapping=json.dumps({"spk_0": "DOCTOR"}),
            confidence=0.8,
            reasoning=long_reason,
        )
        assert result["reasoning"] == long_reason


class TestNormalizeMappingEdges:
    """Edge cases for _normalize_mapping."""

    def test_empty_mapping(self):
        assert _normalize_mapping({}) == {}

    def test_numeric_speaker_id(self):
        result = _normalize_mapping({0: "doctor"})
        assert result == {"0": "DOCTOR"}

    def test_none_role_becomes_string(self):
        result = _normalize_mapping({"spk_0": None})
        assert result == {"spk_0": "NONE"}


class TestAttributeSegmentsEdges:
    """Edge cases for _attribute_segments."""

    def test_empty_segments(self):
        assert _attribute_segments([], {"spk_0": "DOCTOR"}) == []

    def test_unmapped_speaker(self):
        segs = [{"speaker_id": "spk_99", "text": "Unknown"}]
        result = _attribute_segments(segs, {"spk_0": "DOCTOR"})
        assert result[0]["role"] == "UNKNOWN"

    def test_empty_mapping(self):
        segs = [{"speaker_id": "spk_0", "text": "Hi"}]
        result = _attribute_segments(segs, {})
        assert result[0]["role"] == "UNKNOWN"


class TestFlipDetectionEdges:
    """Edge cases for flip detection in RoleMappingState."""

    def test_speaker_added_is_not_flip(self):
        state = RoleMappingState()
        state.did_update_mapping_detect_flip(
            {"spk_0": "DOCTOR", "spk_1": "PATIENT"}, 0.8
        )
        # Adding a third speaker - different key set, not a flip
        flip = state.did_update_mapping_detect_flip(
            {"spk_0": "DOCTOR", "spk_1": "PATIENT", "spk_2": "NURSE"}, 0.85
        )
        assert flip is False

    def test_speaker_removed_is_not_flip(self):
        state = RoleMappingState()
        state.did_update_mapping_detect_flip(
            {"spk_0": "DOCTOR", "spk_1": "PATIENT", "spk_2": "NURSE"}, 0.8
        )
        flip = state.did_update_mapping_detect_flip(
            {"spk_0": "DOCTOR", "spk_1": "PATIENT"}, 0.85
        )
        assert flip is False

    def test_single_speaker_role_change_is_not_flip(self):
        state = RoleMappingState()
        state.did_update_mapping_detect_flip(
            {"spk_0": "DOCTOR", "spk_1": "PATIENT"}, 0.8
        )
        # Only one speaker changes - not a permutation
        flip = state.did_update_mapping_detect_flip(
            {"spk_0": "NURSE", "spk_1": "PATIENT"}, 0.7
        )
        assert flip is False

    def test_three_speaker_two_swap_is_flip(self):
        state = RoleMappingState()
        state.did_update_mapping_detect_flip(
            {"spk_0": "DOCTOR", "spk_1": "PATIENT", "spk_2": "NURSE"}, 0.8
        )
        # spk_0 and spk_1 swap, spk_2 unchanged
        flip = state.did_update_mapping_detect_flip(
            {"spk_0": "PATIENT", "spk_1": "DOCTOR", "spk_2": "NURSE"}, 0.9
        )
        assert flip is True

    def test_last_flip_detected_flag(self):
        state = RoleMappingState()
        state.did_update_mapping_detect_flip(
            {"spk_0": "DOCTOR", "spk_1": "PATIENT"}, 0.8
        )
        assert state.last_flip_detected is False

        state.did_update_mapping_detect_flip(
            {"spk_0": "PATIENT", "spk_1": "DOCTOR"}, 0.9
        )
        assert state.last_flip_detected is True

        # Next non-flip update clears the flag
        state.did_update_mapping_detect_flip(
            {"spk_0": "PATIENT", "spk_1": "DOCTOR"}, 0.95
        )
        assert state.last_flip_detected is False

    def test_single_confidence_in_history(self):
        state = RoleMappingState()
        state.did_update_mapping_detect_flip({"spk_0": "DOCTOR"}, 0.75)
        assert abs(state.running_confidence - 0.75) < 0.01

    def test_canonical_speaker_ids_detect_same_speaker_role_swap(self):
        """Canonical IDs still report a flip when the same two visible speakers swap."""
        state = RoleMappingState()
        state.did_update_mapping_detect_flip(
            {"speaker_0": "DOCTOR", "speaker_1": "PATIENT"}, 0.8
        )

        flip = state.did_update_mapping_detect_flip(
            {"speaker_0": "PATIENT", "speaker_1": "DOCTOR"}, 0.9
        )

        assert flip is True

    def test_new_canonical_speaker_id_is_not_a_flip(self):
        """A new canonical speaker means the UI saw a participant change, not a swap."""
        state = RoleMappingState()
        state.did_update_mapping_detect_flip(
            {"speaker_0": "DOCTOR", "speaker_1": "PATIENT"}, 0.8
        )

        flip = state.did_update_mapping_detect_flip(
            {"speaker_0": "DOCTOR", "speaker_2": "PATIENT"}, 0.9
        )

        assert flip is False
