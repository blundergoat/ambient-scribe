"""
Tests for the session summary agent and endpoint.
"""

import json
from unittest.mock import MagicMock, patch

import httpx
from fastapi.testclient import TestClient

import api.server as api_server
from agents import SUMMARY_PROMPTS
from api.server import app, sessions

TEST_SESSION_ID = "00000000-0000-4000-8000-000000000099"


# =========================================================================
# Summary Prompt Tests
# =========================================================================


class TestSummaryPrompts:
    """Verify each mode's summary prompt contains expected section names."""

    def test_medical_prompt_mentions_soap(self):
        prompt = SUMMARY_PROMPTS["medical"]
        assert "Subjective" in prompt
        assert "Objective" in prompt
        assert "Assessment" in prompt
        assert "Plan" in prompt

    def test_meeting_prompt_mentions_action_items(self):
        prompt = SUMMARY_PROMPTS["meeting"]
        assert "Action Items" in prompt
        assert "Decisions" in prompt
        assert "Attendees" in prompt

    def test_interview_prompt_mentions_strengths(self):
        prompt = SUMMARY_PROMPTS["interview"]
        assert "Candidate Strengths" in prompt
        assert "Areas of Concern" in prompt

    def test_general_prompt_mentions_overview(self):
        prompt = SUMMARY_PROMPTS["general"]
        assert "Overview" in prompt
        assert "Key Points" in prompt

    def test_all_prompts_require_json(self):
        for mode, prompt in SUMMARY_PROMPTS.items():
            assert "valid JSON" in prompt, f"{mode} prompt missing JSON requirement"

    def test_all_prompts_require_citations(self):
        for mode, prompt in SUMMARY_PROMPTS.items():
            assert "[" in prompt and "MM:SS" in prompt, f"{mode} prompt missing citation format"


# =========================================================================
# Summary Endpoint Tests
# =========================================================================


class TestSummaryEndpoint:
    """Tests for the POST /session/{id}/summary endpoint."""

    def setup_method(self):
        sessions._sessions.clear()
        api_server._session_modes.clear()
        api_server._mercure_event_ids.clear()
        app.state.http_client = httpx.AsyncClient(timeout=5.0)

    def test_summary_404_on_empty_session(self):
        client = TestClient(app, raise_server_exceptions=False)
        response = client.post(f"/session/{TEST_SESSION_ID}/summary")
        assert response.status_code == 404

    def test_summary_returns_result_on_success(self):
        # Seed the session with segments
        sessions.append_segment(TEST_SESSION_ID, {
            "speaker_id": "spk_0", "text": "What brings you in?", "start": 0.0, "end": 2.0,
        })
        sessions.append_segment(TEST_SESSION_ID, {
            "speaker_id": "spk_1", "text": "I have chest pain.", "start": 2.5, "end": 4.0,
        })

        mock_summary = {
            "title": "Medical Consultation",
            "sections": [
                {"heading": "Subjective", "content": "Patient reports chest pain."},
            ],
            "key_points": ["Chest pain reported"],
            "duration_seconds": 4.0,
        }

        with patch("api.server._run_summary_generation", return_value=mock_summary):
            client = TestClient(app, raise_server_exceptions=False)
            response = client.post(f"/session/{TEST_SESSION_ID}/summary")

        assert response.status_code == 200
        data = response.json()
        assert data["session_id"] == TEST_SESSION_ID
        assert data["title"] == "Medical Consultation"
        assert len(data["sections"]) == 1
        assert data["sections"][0]["heading"] == "Subjective"
        assert len(data["key_points"]) == 1

    def test_summary_502_on_generation_failure(self):
        sessions.append_segment(TEST_SESSION_ID, {
            "speaker_id": "spk_0", "text": "Hello", "start": 0.0, "end": 1.0,
        })

        with patch("api.server._run_summary_generation", return_value=None):
            client = TestClient(app, raise_server_exceptions=False)
            response = client.post(f"/session/{TEST_SESSION_ID}/summary")

        assert response.status_code == 502

    def test_summary_invalid_session_id_returns_400(self):
        client = TestClient(app, raise_server_exceptions=False)
        response = client.post("/session/not-a-uuid/summary")
        assert response.status_code == 400

    def test_summary_uses_session_mode(self):
        """Summary should use the session's mode, not default."""
        sessions.append_segment(TEST_SESSION_ID, {
            "speaker_id": "spk_0", "text": "Let's review the agenda.", "start": 0.0, "end": 2.0,
        })
        api_server._session_modes[TEST_SESSION_ID] = "meeting"

        mock_summary = {
            "title": "Meeting Summary",
            "sections": [{"heading": "Attendees", "content": "2 participants"}],
            "key_points": [],
        }

        captured_mode = []
        original = api_server._run_summary_generation

        def capture_mode(sid, transcript, mode="medical"):
            captured_mode.append(mode)
            return mock_summary

        with patch("api.server._run_summary_generation", side_effect=capture_mode):
            client = TestClient(app, raise_server_exceptions=False)
            response = client.post(f"/session/{TEST_SESSION_ID}/summary")

        assert response.status_code == 200
        assert captured_mode[0] == "meeting"

    def test_summary_with_empty_sections(self):
        """Summary with no sections or key_points still returns 200."""
        sessions.append_segment(TEST_SESSION_ID, {
            "speaker_id": "spk_0", "text": "Brief.", "start": 0.0, "end": 1.0,
        })

        with patch("api.server._run_summary_generation", return_value={
            "title": "Brief",
            "sections": [],
            "key_points": [],
        }):
            client = TestClient(app, raise_server_exceptions=False)
            response = client.post(f"/session/{TEST_SESSION_ID}/summary")

        assert response.status_code == 200
        data = response.json()
        assert data["sections"] == []
        assert data["key_points"] == []


class TestRunSummaryGeneration:
    """Tests for the _run_summary_generation helper."""

    def test_returns_none_on_agent_exception(self):
        with patch("agents.create_summary_agent", side_effect=RuntimeError("boom")):
            result = api_server._run_summary_generation("sid", "transcript", "medical")
        assert result is None

    def test_parses_json_from_agent_response(self):
        mock_agent = MagicMock()
        mock_agent.return_value = '{"title": "Test", "sections": [], "key_points": []}'

        with patch("agents.create_summary_agent", return_value=mock_agent):
            result = api_server._run_summary_generation("sid", "transcript", "medical")

        assert result["title"] == "Test"

    def test_extracts_json_from_preamble(self):
        """Agent may include text before JSON — regex fallback should work."""
        mock_agent = MagicMock()
        mock_agent.return_value = 'Here is the summary:\n{"title": "Extracted", "sections": []}'

        with patch("agents.create_summary_agent", return_value=mock_agent):
            result = api_server._run_summary_generation("sid", "transcript", "medical")

        assert result["title"] == "Extracted"

    def test_returns_none_on_no_json(self):
        mock_agent = MagicMock()
        mock_agent.return_value = "I cannot generate a summary for this transcript."

        with patch("agents.create_summary_agent", return_value=mock_agent):
            result = api_server._run_summary_generation("sid", "transcript", "medical")

        assert result is None
