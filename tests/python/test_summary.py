"""
Tests for the medical session summary agent and endpoint.
"""

from unittest.mock import MagicMock, patch

import httpx
from fastapi.testclient import TestClient

import api.server as api_server
from agents import MEDICAL_SUMMARY_PROMPT
from api.server import app, sessions
from api.summary_generation import summary_generation_prompt

TEST_SESSION_ID = "00000000-0000-4000-8000-000000000099"


# =========================================================================
# Summary Prompt Tests
# =========================================================================


class TestSummaryPrompts:
    """Verify the medical summary prompt supports the consultation UI."""

    def test_medical_prompt_mentions_soap(self):
        assert "Subjective" in MEDICAL_SUMMARY_PROMPT
        assert "Objective" in MEDICAL_SUMMARY_PROMPT
        assert "Assessment" in MEDICAL_SUMMARY_PROMPT
        assert "Plan" in MEDICAL_SUMMARY_PROMPT

    def test_medical_prompt_requires_json(self):
        assert "valid JSON" in MEDICAL_SUMMARY_PROMPT

    def test_medical_prompt_requires_citations(self):
        assert "[" in MEDICAL_SUMMARY_PROMPT and "MM:SS" in MEDICAL_SUMMARY_PROMPT


class TestSummaryEndpoint:
    """Tests for the POST /session/{id}/summary endpoint."""

    def setup_method(self):
        sessions._sessions.clear()
        api_server._mercure_event_ids.clear()
        app.state.http_client = httpx.AsyncClient(timeout=5.0)

    def test_summary_404_on_empty_session(self):
        client = TestClient(app, raise_server_exceptions=False)
        response = client.post(f"/session/{TEST_SESSION_ID}/summary")
        assert response.status_code == 404

    def test_summary_returns_result_on_success(self):
        # Seed the session with segments
        sessions.append_segment(
            TEST_SESSION_ID,
            {
                "speaker_id": "spk_0",
                "text": "What brings you in?",
                "start": 0.0,
                "end": 2.0,
            },
        )
        sessions.append_segment(
            TEST_SESSION_ID,
            {
                "speaker_id": "spk_1",
                "text": "I have chest pain.",
                "start": 2.5,
                "end": 4.0,
            },
        )

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
        sessions.append_segment(
            TEST_SESSION_ID,
            {
                "speaker_id": "spk_0",
                "text": "Hello",
                "start": 0.0,
                "end": 1.0,
            },
        )

        with patch("api.server._run_summary_generation", return_value=None):
            client = TestClient(app, raise_server_exceptions=False)
            response = client.post(f"/session/{TEST_SESSION_ID}/summary")

        assert response.status_code == 502

    def test_summary_invalid_session_id_returns_400(self):
        client = TestClient(app, raise_server_exceptions=False)
        response = client.post("/session/not-a-uuid/summary")
        assert response.status_code == 400

    def test_summary_with_empty_sections(self):
        """Summary with no sections or key_points still returns 200."""
        sessions.append_segment(
            TEST_SESSION_ID,
            {
                "speaker_id": "spk_0",
                "text": "Brief.",
                "start": 0.0,
                "end": 1.0,
            },
        )

        with patch(
            "api.server._run_summary_generation",
            return_value={
                "title": "Brief",
                "sections": [],
                "key_points": [],
            },
        ):
            client = TestClient(app, raise_server_exceptions=False)
            response = client.post(f"/session/{TEST_SESSION_ID}/summary")

        assert response.status_code == 200
        data = response.json()
        assert data["sections"] == []
        assert data["key_points"] == []

    def test_summary_publishes_clinical_hints(self, monkeypatch):
        """Hints are published and returned when the transcript has review suggestions."""
        sessions.append_segment(
            TEST_SESSION_ID,
            {
                "speaker_id": "spk_0",
                "text": "I am prescribing naproxen while you continue lisinopril.",
                "start": 0.0,
                "end": 3.0,
            },
        )
        published_events = []

        async def fake_publish(topic, payload, event_id=None):
            published_events.append((topic, payload, event_id))
            return True

        monkeypatch.setattr(api_server, "CLINICAL_HINTS_ENABLED", True)
        monkeypatch.setattr(api_server, "publish_to_mercure", fake_publish)

        with patch(
            "api.server._run_summary_generation",
            return_value={
                "title": "Medication Review",
                "sections": [],
                "key_points": [],
            },
        ):
            client = TestClient(app, raise_server_exceptions=False)
            response = client.post(f"/session/{TEST_SESSION_ID}/summary")

        assert response.status_code == 200
        data = response.json()
        assert data["clinical_hints"][0]["type"] == "drug_interaction"
        assert any(topic.endswith("/hints") for topic, _, _ in published_events)

    def test_summary_returns_hints_when_mercure_publish_fails(self, monkeypatch):
        """HTTP fallback still lets the browser render hints after Mercure failure."""
        sessions.append_segment(
            TEST_SESSION_ID,
            {
                "speaker_id": "spk_0",
                "text": "Patient reports chest pain today.",
                "start": 0.0,
                "end": 3.0,
            },
        )

        async def failing_publish(topic, payload, event_id=None):
            return False

        monkeypatch.setattr(api_server, "CLINICAL_HINTS_ENABLED", True)
        monkeypatch.setattr(api_server, "publish_to_mercure", failing_publish)

        with patch(
            "api.server._run_summary_generation",
            return_value={
                "title": "Chest Pain",
                "sections": [],
                "key_points": [],
            },
        ):
            client = TestClient(app, raise_server_exceptions=False)
            response = client.post(f"/session/{TEST_SESSION_ID}/summary")

        assert response.status_code == 200
        assert response.json()["clinical_hints"][0]["type"] == "missing_objective"


class TestRunSummaryGeneration:
    """Tests for the _run_summary_generation helper."""

    def test_returns_none_on_agent_exception(self):
        with patch("agents.create_summary_agent", side_effect=RuntimeError("boom")):
            result = api_server._run_summary_generation("sid", "transcript")
        assert result is None

    def test_parses_json_from_agent_response(self):
        mock_agent = MagicMock()
        mock_agent.return_value = '{"title": "Test", "sections": [], "key_points": []}'

        with patch("agents.create_summary_agent", return_value=mock_agent):
            result = api_server._run_summary_generation("sid", "transcript")

        assert result["title"] == "Test"

    def test_extracts_json_from_preamble(self):
        """Agent may include text before JSON — regex fallback should work."""
        mock_agent = MagicMock()
        mock_agent.return_value = (
            'Here is the summary:\n{"title": "Extracted", "sections": []}'
        )

        with patch("agents.create_summary_agent", return_value=mock_agent):
            result = api_server._run_summary_generation("sid", "transcript")

        assert result["title"] == "Extracted"

    def test_returns_none_on_no_json(self):
        mock_agent = MagicMock()
        mock_agent.return_value = "I cannot generate a summary for this transcript."

        with patch("agents.create_summary_agent", return_value=mock_agent):
            result = api_server._run_summary_generation("sid", "transcript")

        assert result is None

    def test_summary_prompt_includes_retrieved_context(self):
        prompt = summary_generation_prompt(
            "Doctor: Patient has chest pain.",
            [
                {
                    "id": "chest-pain",
                    "title": "Chest pain documentation",
                    "snippet": "Document ECG and vitals.",
                    "provenance": "test KB",
                }
            ],
        )

        assert "Chest pain documentation" in prompt
        assert "Document ECG and vitals." in prompt
        assert "Doctor: Patient has chest pain." in prompt
