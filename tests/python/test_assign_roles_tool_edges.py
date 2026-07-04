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
)


class TestAssignRolesToolEdgeCases:
    """Edge cases for the @tool-decorated assign_roles function."""

    def test_empty_mapping(self):
        cleanup_session("tool-empty-map")
        result = assign_roles(
            session_id="tool-empty-map",
            mapping=json.dumps({}),
            segments=json.dumps([]),
            confidence=0.5,
        )
        assert result["mapping"] == {}
        assert result["attributed_segments"] == []

    def test_dict_mapping_instead_of_json_string(self):
        """When the LLM passes a dict directly (not JSON string)."""
        cleanup_session("tool-dict-input")
        result = assign_roles(
            session_id="tool-dict-input",
            mapping={"spk_0": "DOCTOR"},
            segments=[{"speaker_id": "spk_0", "text": "Hi", "start": 0.0, "end": 1.0}],
            confidence=0.7,
        )
        assert result["mapping"] == {"spk_0": "DOCTOR"}
        assert result["attributed_segments"][0]["role"] == "DOCTOR"

    def test_list_segments_instead_of_json_string(self):
        """When segments are passed as list directly."""
        cleanup_session("tool-list-segs")
        segs = [{"speaker_id": "spk_0", "text": "Hello", "start": 0.0, "end": 1.0}]
        result = assign_roles(
            session_id="tool-list-segs",
            mapping=json.dumps({"spk_0": "DOCTOR"}),
            segments=segs,
            confidence=0.8,
        )
        assert result["attributed_segments"][0]["role"] == "DOCTOR"

    def test_confidence_above_one(self):
        """Confidence > 1.0 is stored as-is (no clamping)."""
        cleanup_session("tool-high-conf")
        assign_roles(
            session_id="tool-high-conf",
            mapping=json.dumps({"spk_0": "DOCTOR"}),
            segments=json.dumps([]),
            confidence=1.5,
        )
        state = get_or_create_state("tool-high-conf")
        assert state.confidence_history[-1] == 1.5

    def test_confidence_zero(self):
        cleanup_session("tool-zero-conf")
        result = assign_roles(
            session_id="tool-zero-conf",
            mapping=json.dumps({"spk_0": "DOCTOR"}),
            segments=json.dumps([]),
            confidence=0.0,
        )
        assert result["confidence"] == 0.0

    def test_missing_speaker_id_in_segment(self):
        """Segment without speaker_id maps to UNKNOWN."""
        cleanup_session("tool-no-spk")
        result = assign_roles(
            session_id="tool-no-spk",
            mapping=json.dumps({"spk_0": "DOCTOR"}),
            segments=json.dumps([{"text": "No speaker", "start": 0.0, "end": 1.0}]),
            confidence=0.8,
        )
        assert result["attributed_segments"][0]["role"] == "UNKNOWN"

    def test_extra_segment_fields_preserved(self):
        """Non-standard fields in segments are preserved in output."""
        cleanup_session("tool-extra-fields")
        result = assign_roles(
            session_id="tool-extra-fields",
            mapping=json.dumps({"spk_0": "DOCTOR"}),
            segments=json.dumps(
                [
                    {
                        "speaker_id": "spk_0",
                        "text": "Hi",
                        "start": 0.0,
                        "end": 1.0,
                        "is_interim": False,
                        "segment_id": "abc-123",
                        "custom_field": "preserved",
                    }
                ]
            ),
            confidence=0.8,
        )
        seg = result["attributed_segments"][0]
        assert seg["custom_field"] == "preserved"
        assert seg["segment_id"] == "abc-123"
        assert seg["is_interim"] is False

    def test_invalid_json_mapping_raises(self):
        """Malformed JSON string should raise JSONDecodeError."""
        cleanup_session("tool-bad-json")
        with pytest.raises(json.JSONDecodeError):
            assign_roles(
                session_id="tool-bad-json",
                mapping="not valid json",
                segments=json.dumps([]),
                confidence=0.5,
            )

    def test_long_reasoning_stored(self):
        cleanup_session("tool-long-reason")
        long_reason = "x" * 5000
        result = assign_roles(
            session_id="tool-long-reason",
            mapping=json.dumps({"spk_0": "DOCTOR"}),
            segments=json.dumps([]),
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
        state.update({"spk_0": "DOCTOR", "spk_1": "PATIENT"}, 0.8)
        # Adding a third speaker — different key set, not a flip
        flip = state.update(
            {"spk_0": "DOCTOR", "spk_1": "PATIENT", "spk_2": "NURSE"}, 0.85
        )
        assert flip is False

    def test_speaker_removed_is_not_flip(self):
        state = RoleMappingState()
        state.update({"spk_0": "DOCTOR", "spk_1": "PATIENT", "spk_2": "NURSE"}, 0.8)
        flip = state.update({"spk_0": "DOCTOR", "spk_1": "PATIENT"}, 0.85)
        assert flip is False

    def test_single_speaker_role_change_is_not_flip(self):
        state = RoleMappingState()
        state.update({"spk_0": "DOCTOR", "spk_1": "PATIENT"}, 0.8)
        # Only one speaker changes — not a permutation
        flip = state.update({"spk_0": "NURSE", "spk_1": "PATIENT"}, 0.7)
        assert flip is False

    def test_three_speaker_two_swap_is_flip(self):
        state = RoleMappingState()
        state.update({"spk_0": "DOCTOR", "spk_1": "PATIENT", "spk_2": "NURSE"}, 0.8)
        # spk_0 and spk_1 swap, spk_2 unchanged
        flip = state.update(
            {"spk_0": "PATIENT", "spk_1": "DOCTOR", "spk_2": "NURSE"}, 0.9
        )
        assert flip is True

    def test_last_flip_detected_flag(self):
        state = RoleMappingState()
        state.update({"spk_0": "DOCTOR", "spk_1": "PATIENT"}, 0.8)
        assert state.last_flip_detected is False

        state.update({"spk_0": "PATIENT", "spk_1": "DOCTOR"}, 0.9)
        assert state.last_flip_detected is True

        # Next non-flip update clears the flag
        state.update({"spk_0": "PATIENT", "spk_1": "DOCTOR"}, 0.95)
        assert state.last_flip_detected is False

    def test_single_confidence_in_history(self):
        state = RoleMappingState()
        state.update({"spk_0": "DOCTOR"}, 0.75)
        assert abs(state.running_confidence - 0.75) < 0.01
