"""
Tests for the medical session summary agent and endpoint.
"""

from unittest.mock import MagicMock, patch

import httpx
from fastapi.testclient import TestClient

import api.server as api_server
from agents import MEDICAL_SUMMARY_PROMPT
from api.server import app, sessions
from api.summary_generation import (
    SessionSummaryOutput,
    SummaryCitationOutput,
    SummarySectionOutput,
    summary_generation_prompt,
    summary_with_validated_citations,
)

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

    def test_medical_prompt_requires_structured_schema(self):
        """Summary prompt asks for the schema the browser can render."""
        assert "structured summary schema" in MEDICAL_SUMMARY_PROMPT

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
        assert "clinical_hints" not in data

    def test_summary_uses_browser_visible_segments_from_request(self):
        """Summaries can use only the transcript rows visible in the browser."""
        mock_summary = {
            "title": "Partial Transcript",
            "sections": [
                {"heading": "Subjective", "content": "Patient reports visible rash."},
            ],
            "key_points": ["Visible transcript text only"],
        }

        with patch("api.server._run_summary_generation", return_value=mock_summary) as summary_runner:
            client = TestClient(app, raise_server_exceptions=False)
            response = client.post(
                f"/session/{TEST_SESSION_ID}/summary",
                json={
                    "segments": [
                        {
                            "speaker_id": "spk_1",
                            "role": "PATIENT",
                            "text": "I have sore red skin.",
                            "start": 16.0,
                            "end": 20.0,
                        }
                    ]
                },
            )

        assert response.status_code == 200
        summary_runner.assert_called_once()
        assert "[PATIENT] I have sore red skin." in summary_runner.call_args.args[1]
        assert sessions.get_segments(TEST_SESSION_ID)[0]["text"] == "I have sore red skin."

    def test_summary_prefers_corrected_segments_when_available(self):
        """Corrected post-visit rows outrank stale browser-visible preview text."""
        sessions.append_segment(
            TEST_SESSION_ID,
            {
                "speaker_id": "spk_0",
                "role": "PATIENT",
                "text": "browser preview typo",
                "start": 0.0,
                "end": 1.0,
                "segment_id": "live-0001",
            },
        )
        sessions.replace_corrected_segments(
            TEST_SESSION_ID,
            [
                {
                    "speaker_id": "spk_0",
                    "role": "DOCTOR",
                    "text": "corrected post visit text",
                    "start": 0.0,
                    "end": 1.0,
                    "segment_id": "corrected-0001",
                }
            ],
        )

        mock_summary = {
            "title": "Corrected Transcript",
            "sections": [],
            "key_points": [],
        }

        with patch("api.server._run_summary_generation", return_value=mock_summary) as summary_runner:
            client = TestClient(app, raise_server_exceptions=False)
            response = client.post(
                f"/session/{TEST_SESSION_ID}/summary",
                json={
                    "segments": [
                        {
                            "speaker_id": "spk_0",
                            "role": "PATIENT",
                            "text": "browser preview typo",
                            "start": 0.0,
                            "end": 1.0,
                            "segment_id": "live-0001",
                        }
                    ]
                },
            )

        assert response.status_code == 200
        summary_runner.assert_called_once()
        transcript = summary_runner.call_args.args[1]
        assert "[DOCTOR] corrected post visit text" in transcript
        assert "browser preview typo" not in transcript
        citation_rows = summary_runner.call_args.args[2]
        assert citation_rows[0]["segment_id"] == "corrected-0001"
        assert sessions.get_segments(TEST_SESSION_ID)[0]["text"] == "browser preview typo"

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

class TestRunSummaryGeneration:
    """Tests for the _run_summary_generation helper."""

    def test_returns_none_on_agent_exception(self):
        with patch("agents.create_summary_agent", side_effect=RuntimeError("boom")):
            result = api_server._run_summary_generation("sid", "transcript")
        assert result is None

    def test_uses_structured_output_from_agent_response(self):
        """Validated summary output becomes the browser payload."""
        mock_agent = MagicMock()
        mock_agent.return_value.structured_output = SessionSummaryOutput(
            title="Test", sections=[], key_points=[]
        )

        with patch("agents.create_summary_agent", return_value=mock_agent):
            result = api_server._run_summary_generation("sid", "transcript")

        assert result["title"] == "Test"
        assert mock_agent.call_args.kwargs["structured_output_model"] is SessionSummaryOutput

    def test_returns_none_without_structured_output(self):
        """Unstructured model text should make the browser show a retryable failure."""
        mock_agent = MagicMock()
        mock_agent.return_value.structured_output = None

        with patch("agents.create_summary_agent", return_value=mock_agent):
            result = api_server._run_summary_generation("sid", "transcript")

        assert result is None

    def test_returns_none_on_plain_text_output(self):
        """Legacy plain text output is rejected now that the schema is enforced."""
        mock_agent = MagicMock()
        mock_agent.return_value.structured_output = "I cannot generate a summary."

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

    def test_summary_prompt_includes_corrected_source_ids_for_citations(self):
        """Corrected transcript rows are exposed as stable citation sources."""
        prompt = summary_generation_prompt(
            "[DOCTOR] Please use the cream.",
            [],
            citation_segments=[
                {
                    "segment_id": "corrected-0001",
                    "role": "DOCTOR",
                    "text": "Please use the cream.",
                    "start": 65.2,
                    "end": 67.8,
                }
            ],
        )

        assert "source IDs" in prompt
        assert '{"segment_id": "seg-0001"}' in prompt
        assert "[source:corrected-0001 01:05-01:07 DOCTOR] Please use the cream." in prompt

    def test_summary_prompt_falls_back_when_corrected_rows_are_not_citable(self):
        """Unidentified corrected rows still produce an uncited note prompt."""
        prompt = summary_generation_prompt(
            "[DOCTOR] Please use the cream.",
            [],
            citation_segments=[
                {
                    "segment_id": "",
                    "role": "DOCTOR",
                    "text": "Please use the cream.",
                    "start": 65.2,
                    "end": 67.8,
                }
            ],
        )

        assert "[DOCTOR] Please use the cream." in prompt
        assert "Use only these source IDs" not in prompt

    def test_summary_citation_validation_omits_invalid_and_duplicate_ids(self):
        """Only source IDs that map to corrected rows reach the browser payload."""
        structured_summary = SessionSummaryOutput(
            title="Skin Review",
            sections=[
                SummarySectionOutput(
                    heading="Plan",
                    content="Use topical treatment.",
                    citations=[
                        SummaryCitationOutput(segment_id="corrected-0001", text="model text ignored"),
                        SummaryCitationOutput(segment_id="missing-9999"),
                        SummaryCitationOutput(segment_id="corrected-0001"),
                    ],
                )
            ],
            key_points=["Treatment discussed"],
        )

        validated_summary = summary_with_validated_citations(
            structured_summary,
            [
                {
                    "segment_id": "corrected-0001",
                    "role": "DOCTOR",
                    "text": "Use the cream twice daily.",
                    "start": 12.0,
                    "end": 14.5,
                }
            ],
        )

        citations = validated_summary.sections[0].citations
        assert len(citations) == 1
        assert citations[0].segment_id == "corrected-0001"
        assert citations[0].text == "Use the cream twice daily."
        assert citations[0].role == "DOCTOR"
        assert citations[0].start == 12.0
        assert citations[0].end == 14.5

    def test_summary_citation_validation_strips_citations_without_sources(self):
        """Legacy summaries cannot leak model-invented citation IDs."""
        structured_summary = SessionSummaryOutput(
            title="Legacy",
            sections=[
                SummarySectionOutput(
                    heading="Subjective",
                    content="Patient reports pain.",
                    citations=[SummaryCitationOutput(segment_id="seg-0001")],
                )
            ],
        )

        validated_summary = summary_with_validated_citations(structured_summary, [])

        assert validated_summary.sections[0].citations == []

    def test_run_summary_generation_hydrates_valid_citations(self):
        """The route helper returns trusted citation details in the payload."""
        mock_agent = MagicMock()
        mock_agent.return_value.structured_output = SessionSummaryOutput(
            title="Cited",
            sections=[
                SummarySectionOutput(
                    heading="Plan",
                    content="Use treatment.",
                    citations=[SummaryCitationOutput(segment_id="corrected-0001")],
                )
            ],
            key_points=[],
        )

        with patch("agents.create_summary_agent", return_value=mock_agent):
            result = api_server._run_summary_generation(
                "sid",
                "[DOCTOR] Use treatment.",
                [
                    {
                        "segment_id": "corrected-0001",
                        "role": "DOCTOR",
                        "text": "Use treatment.",
                        "start": 3.0,
                        "end": 4.0,
                    }
                ],
            )

        assert result["sections"][0]["citations"] == [
            {
                "segment_id": "corrected-0001",
                "start": 3.0,
                "end": 4.0,
                "role": "DOCTOR",
                "text": "Use treatment.",
            }
        ]
