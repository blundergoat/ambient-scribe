"""
Tests for scenario fixture validation.

Ensures all scenario definitions are structurally valid and have proper
expectedEndState assertions. This serves as the automated gate for the
scenario runner — the actual browser execution is manual, but the fixture
integrity is machine-checkable.
"""

import json
from pathlib import Path

import pytest

SCENARIOS_PATH = Path(__file__).parent.parent / "fixtures" / "scribe" / "scenarios.json"

VALID_EVENT_TYPES = {"segment", "role_update", "ws_disconnect", "ws_reconnect", "control", "finalized"}


@pytest.fixture
def scenarios():
    """Load and parse scenarios.json."""
    data = json.loads(SCENARIOS_PATH.read_text())
    return data["scenarios"]


class TestScenarioFixtureIntegrity:
    """Validate scenario fixture structure."""

    def test_scenarios_file_exists(self):
        assert SCENARIOS_PATH.exists(), f"Missing {SCENARIOS_PATH}"

    def test_scenarios_is_valid_json(self):
        data = json.loads(SCENARIOS_PATH.read_text())
        assert "scenarios" in data
        assert len(data["scenarios"]) > 0

    def test_each_scenario_has_required_fields(self, scenarios):
        for s in scenarios:
            assert "id" in s, f"Scenario missing id"
            assert "name" in s, f"Scenario {s.get('id')} missing name"
            assert "events" in s, f"Scenario {s['id']} missing events"
            assert "expectedEndState" in s, f"Scenario {s['id']} missing expectedEndState"

    def test_all_scenario_ids_unique(self, scenarios):
        ids = [s["id"] for s in scenarios]
        assert len(ids) == len(set(ids)), f"Duplicate scenario IDs: {[x for x in ids if ids.count(x) > 1]}"

    def test_all_events_have_valid_types(self, scenarios):
        for s in scenarios:
            for i, event in enumerate(s["events"]):
                assert "type" in event, f"Scenario {s['id']} event {i} missing type"
                assert event["type"] in VALID_EVENT_TYPES, (
                    f"Scenario {s['id']} event {i} has invalid type '{event['type']}'"
                )

    def test_segment_events_have_required_data(self, scenarios):
        for s in scenarios:
            for i, event in enumerate(s["events"]):
                if event["type"] != "segment":
                    continue
                data = event.get("data", {})
                assert "speaker_id" in data, f"Scenario {s['id']} segment {i} missing speaker_id"
                assert "text" in data, f"Scenario {s['id']} segment {i} missing text"
                assert "start" in data, f"Scenario {s['id']} segment {i} missing start"
                assert "end" in data, f"Scenario {s['id']} segment {i} missing end"

    def test_expected_end_state_has_segment_count(self, scenarios):
        for s in scenarios:
            expected = s["expectedEndState"]
            assert "segmentCount" in expected, f"Scenario {s['id']} missing segmentCount"
            assert isinstance(expected["segmentCount"], int), f"Scenario {s['id']} segmentCount not int"

    def test_segment_count_matches_actual_events(self, scenarios):
        for s in scenarios:
            segment_events = [e for e in s["events"] if e["type"] == "segment"]
            expected = s["expectedEndState"]["segmentCount"]
            assert expected == len(segment_events), (
                f"Scenario {s['id']}: expectedEndState.segmentCount={expected} "
                f"but has {len(segment_events)} segment events"
            )

    def test_role_mapping_keys_appear_in_segments(self, scenarios):
        for s in scenarios:
            expected = s["expectedEndState"]
            if not expected.get("roleMapping"):
                continue
            speaker_ids = {
                e["data"]["speaker_id"]
                for e in s["events"]
                if e["type"] == "segment" and "data" in e
            }
            for spk in expected["roleMapping"]:
                assert spk in speaker_ids, (
                    f"Scenario {s['id']}: roleMapping has {spk} but no segment uses it"
                )

    def test_content_check_text_exists_in_events(self, scenarios):
        for s in scenarios:
            check = s["expectedEndState"].get("contentCheck")
            if not check:
                continue
            segment_texts = [
                e["data"]["text"]
                for e in s["events"]
                if e["type"] == "segment" and "data" in e
            ]
            if "firstSegmentText" in check:
                assert check["firstSegmentText"] == segment_texts[0], (
                    f"Scenario {s['id']}: firstSegmentText doesn't match first segment"
                )
            if "lastSegmentText" in check:
                assert check["lastSegmentText"] == segment_texts[-1], (
                    f"Scenario {s['id']}: lastSegmentText doesn't match last segment"
                )

    def test_max_duration_is_positive(self, scenarios):
        for s in scenarios:
            max_dur = s["expectedEndState"].get("maxDurationMs")
            if max_dur is not None:
                assert max_dur > 0, f"Scenario {s['id']}: maxDurationMs must be positive"

    def test_at_least_8_scenarios(self, scenarios):
        """Milestone requires 8+ scenarios."""
        assert len(scenarios) >= 8
