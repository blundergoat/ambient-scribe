"""
Edge case tests for the @tool assign_roles function.
"""

import json

import pytest

from tools.assign_roles import (
    UNKNOWN_SPEAKER_ROLE,
    RoleMappingState,
    _attribute_segments,
    _normalize_mapping,
    apply_role_mapping_result,
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
        assert (
            pending_role_segments_for_session("tool-pending-segs")[0]["text"] == "Hello"
        )

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


class TestOmittedSpeakerLabelsAreWithdrawn:
    """The role agent is told to omit a speaker it cannot judge.

    Server state replaces its mapping wholesale, but the transcript store and the
    browser merge only the keys they receive. Without an explicit withdrawal the
    omitted speaker keeps the confident DOCTOR or PATIENT label it had a moment
    ago - showing certainty the agent had just given up.
    """

    def test_dropped_speaker_becomes_unknown(self):
        """A label the agent stops asserting stops being shown."""
        session_id = "withdraw-basic"
        cleanup_session(session_id)
        apply_role_mapping_result(
            session_id, [], {"speaker_0": "DOCTOR", "speaker_1": "PATIENT"}, 0.9
        )

        result = apply_role_mapping_result(session_id, [], {"speaker_0": "DOCTOR"}, 0.9)

        assert result.mapping["speaker_0"] == "DOCTOR"
        assert result.mapping["speaker_1"] == UNKNOWN_SPEAKER_ROLE
        cleanup_session(session_id)

    def test_every_consumer_receives_the_withdrawn_key(self):
        """Merge-only consumers can only clear a label they are actually sent."""
        session_id = "withdraw-key-present"
        cleanup_session(session_id)
        apply_role_mapping_result(
            session_id, [], {"speaker_0": "DOCTOR", "speaker_1": "PATIENT"}, 0.9
        )

        result = apply_role_mapping_result(session_id, [], {}, 0.5)

        # Absent keys are exactly the bug; both speakers must be named.
        assert set(result.mapping) == {"speaker_0", "speaker_1"}
        assert set(result.mapping.values()) == {UNKNOWN_SPEAKER_ROLE}
        cleanup_session(session_id)

    def test_a_clinician_override_survives_withdrawal(self):
        """A user's confirmed label outranks the agent giving up on that speaker."""
        session_id = "withdraw-override"
        cleanup_session(session_id)
        state = get_or_create_state(session_id)
        apply_role_mapping_result(
            session_id, [], {"speaker_0": "DOCTOR", "speaker_1": "PATIENT"}, 0.9
        )
        state.confirmed_overrides = {"speaker_1": "PATIENT"}

        result = apply_role_mapping_result(session_id, [], {"speaker_0": "DOCTOR"}, 0.9)

        assert result.mapping["speaker_1"] == "PATIENT"
        cleanup_session(session_id)

    def test_withdrawal_is_not_reported_as_a_role_flip(self):
        """Giving up on a label is not the same as two speakers swapping."""
        session_id = "withdraw-not-flip"
        cleanup_session(session_id)
        apply_role_mapping_result(
            session_id, [], {"speaker_0": "DOCTOR", "speaker_1": "PATIENT"}, 0.9
        )

        result = apply_role_mapping_result(session_id, [], {"speaker_0": "DOCTOR"}, 0.9)

        assert result.flip_detected is False
        cleanup_session(session_id)

    def test_a_new_speaker_is_added_without_withdrawing_the_others(self):
        """Diarization finding a third voice must not blank the first two."""
        session_id = "withdraw-new-speaker"
        cleanup_session(session_id)
        apply_role_mapping_result(
            session_id, [], {"speaker_0": "DOCTOR", "speaker_1": "PATIENT"}, 0.9
        )

        result = apply_role_mapping_result(
            session_id,
            [],
            {"speaker_0": "DOCTOR", "speaker_1": "PATIENT", "speaker_2": "PATIENT"},
            0.9,
        )

        assert result.mapping == {
            "speaker_0": "DOCTOR",
            "speaker_1": "PATIENT",
            "speaker_2": "PATIENT",
        }
        cleanup_session(session_id)

    def test_withdrawn_rows_render_as_unknown(self):
        """The transcript row for a withdrawn speaker stops claiming a role."""
        session_id = "withdraw-rows"
        cleanup_session(session_id)
        apply_role_mapping_result(
            session_id, [], {"speaker_0": "DOCTOR", "speaker_1": "PATIENT"}, 0.9
        )

        result = apply_role_mapping_result(
            session_id,
            [{"speaker_id": "speaker_1", "text": "it started on Tuesday"}],
            {"speaker_0": "DOCTOR"},
            0.9,
        )

        assert result.attributed_segments[0]["role"] == UNKNOWN_SPEAKER_ROLE
        cleanup_session(session_id)
