"""
Tests for the role inference tools and state management.
"""

from tools.assign_roles import RoleMapping, RoleMappingState, get_or_create_state, cleanup_session


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
