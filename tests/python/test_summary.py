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

    def test_medical_prompt_preserves_patient_uncertainty(self):
        """M00 fidelity: 'I don't know' must never become a definitive assertion."""
        assert "Never assert a clinical fact" in MEDICAL_SUMMARY_PROMPT
        assert "unclear or not established" in MEDICAL_SUMMARY_PROMPT
        assert "preserving the speaker's own certainty" in MEDICAL_SUMMARY_PROMPT
        assert "absence of mention is not a negative finding" in MEDICAL_SUMMARY_PROMPT
        assert "never answered is not a denial" in MEDICAL_SUMMARY_PROMPT
        assert "never examination findings" in MEDICAL_SUMMARY_PROMPT

    def test_medical_prompt_restricts_assessment_to_clinician_statements(self):
        """ADR-007 Option B: the Assessment section is scribe-true, never AI-inferred."""
        assert "Only diagnoses or differentials the clinician stated" in MEDICAL_SUMMARY_PROMPT
        assert "Never add" in MEDICAL_SUMMARY_PROMPT
        assert "no assessment was documented" in MEDICAL_SUMMARY_PROMPT

    def test_medical_prompt_keeps_patient_reports_out_of_objective(self):
        """Objective may hold only clinician-performed examination content (M03 item a)."""
        assert "Only clinician-performed examination findings" in MEDICAL_SUMMARY_PROMPT
        assert "Patient-reported symptoms belong in Subjective" in MEDICAL_SUMMARY_PROMPT


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


class TestCitationDropLogging:
    """M2 (summary UX): dropped citations are logged PHI-safe, never silently.

    Selection is already citation-driven (findings Q5); these tests pin the
    payload invariants and the logging contract for unresolvable IDs.
    """

    _SOURCE_ROWS = [
        {
            "segment_id": "corrected-0001",
            "role": "DOCTOR",
            "text": "Use the cream twice daily.",
            "start": 12.0,
            "end": 14.5,
        }
    ]

    def _summary(self, citations_per_section: list[list[str]]) -> SessionSummaryOutput:
        return SessionSummaryOutput(
            title="Skin Review",
            sections=[
                SummarySectionOutput(
                    heading=f"Section {index}",
                    content="Prose.",
                    citations=[SummaryCitationOutput(segment_id=sid) for sid in ids],
                )
                for index, ids in enumerate(citations_per_section)
            ],
            key_points=[],
        )

    def test_unresolved_citation_ids_are_logged_phi_safe(self, caplog) -> None:
        """Synthetic-shape IDs are listed; fabricated free text never reaches logs."""
        import logging

        fabricated = "patient said his name aloud"
        summary = self._summary([["corrected-9999", fabricated]])

        with caplog.at_level(logging.WARNING, logger="api.summary_generation"):
            validated = summary_with_validated_citations(
                summary, self._SOURCE_ROWS, session_id="sess-1"
            )

        assert validated.sections[0].citations == []
        drop_records = [r for r in caplog.records if "citations_dropped" in r.message]
        assert len(drop_records) == 1
        assert getattr(drop_records[0], "unresolved_citations") == 2
        assert getattr(drop_records[0], "unresolved_ids_sample") == ["corrected-9999"]
        assert fabricated not in caplog.text

    def test_valid_citations_produce_no_drop_log(self, caplog) -> None:
        import logging

        summary = self._summary([["corrected-0001"]])

        with caplog.at_level(logging.WARNING, logger="api.summary_generation"):
            validated = summary_with_validated_citations(
                summary, self._SOURCE_ROWS, session_id="sess-1"
            )

        assert [c.segment_id for c in validated.sections[0].citations] == ["corrected-0001"]
        assert not [r for r in caplog.records if "citations_dropped" in r.message]

    def test_blank_and_duplicate_drops_are_counted_separately(self, caplog) -> None:
        import logging

        summary = self._summary([["corrected-0001", "corrected-0001", "  "]])

        with caplog.at_level(logging.WARNING, logger="api.summary_generation"):
            summary_with_validated_citations(summary, self._SOURCE_ROWS, session_id="sess-1")

        record = next(r for r in caplog.records if "citations_dropped" in r.message)
        assert getattr(record, "duplicate_citations") == 1
        assert getattr(record, "blank_citations") == 1
        assert getattr(record, "unresolved_citations") == 0
        assert getattr(record, "unresolved_ids_sample") == []

    def test_same_id_cited_in_two_sections_is_kept_in_both(self) -> None:
        """Dedup is per section, so per-section provenance (M5) stays complete."""
        summary = self._summary([["corrected-0001"], ["corrected-0001"]])

        validated = summary_with_validated_citations(summary, self._SOURCE_ROWS)

        for section in validated.sections:
            assert [c.segment_id for c in section.citations] == ["corrected-0001"]
            assert section.citations[0].text == "Use the cream twice daily."


class TestInlineReferenceStripping:
    """M1 (summary UX): displayed prose carries no inline reference markers.

    Structured citations are untouched; only the bracket reference TEXT leaves
    the prose. Malformed or non-reference brackets degrade to plain text.
    """

    def _strip(self, content: str) -> str:
        from api.summary_generation import strip_inline_reference_text

        return strip_inline_reference_text(content)

    def test_single_time_reference_is_removed(self) -> None:
        """A lone [MM:SS] marker disappears without leaving double spaces."""
        assert self._strip("Headache began at midday [00:04].") == "Headache began at midday."

    def test_time_range_reference_is_removed(self) -> None:
        """[MM:SS-MM:SS] ranges vanish from the sentence."""
        assert (
            self._strip("Pain is throbbing [01:07-01:10] on the left side.")
            == "Pain is throbbing on the left side."
        )

    def test_multiple_references_in_one_sentence_are_removed(self) -> None:
        """Every reference in a sentence goes, not just the first."""
        assert (
            self._strip("Blurring in both eyes [00:27-00:29, 02:20-02:22] was noted [02:21].")
            == "Blurring in both eyes was noted."
        )

    def test_reference_at_sentence_boundary_keeps_punctuation(self) -> None:
        """A marker before the full stop leaves clean punctuation behind."""
        assert self._strip("Denies fever [02:51-02:52].") == "Denies fever."

    def test_segment_id_and_range_references_are_removed(self) -> None:
        """Legacy [corrected-XXXX] and 'to' ranges leave the prose."""
        assert (
            self._strip("Skin symptoms on arms [corrected-0011 to corrected-0025] persist [corrected-0041].")
            == "Skin symptoms on arms persist."
        )

    def test_mixed_id_and_time_reference_is_removed(self) -> None:
        """The observed [corrected-0010 00:27-00:28] hybrid form is stripped."""
        assert (
            self._strip("Complaint of dry, itchy skin [corrected-0010 00:27-00:28].")
            == "Complaint of dry, itchy skin."
        )

    def test_invalid_second_values_still_strip(self) -> None:
        """Legacy invalid MM:SS values like [02:76-02:79] are still references."""
        assert self._strip("Plan discussed [02:76-02:79].") == "Plan discussed."

    def test_malformed_brackets_degrade_to_plain_text(self) -> None:
        """Non-reference brackets are clinical text and must never be eaten."""
        content = "Patient described pain as [severe] and worsening."
        assert self._strip(content) == content

    def test_empty_and_plain_content_pass_through(self) -> None:
        """No brackets means no change and no crash."""
        assert self._strip("") == ""
        assert self._strip("No references here.") == "No references here."


    def test_unclosed_and_truncated_brackets_pass_through(self) -> None:
        """Verifier-suggested hardening: broken reference shapes are left alone."""
        for content in (
            "Pain noted [00:15 during examination.",
            "Reference [corrected- was cut off.",
            "Stray ] bracket only.",
        ):
            assert self._strip(content) == content

class TestSummaryDisplayTextCleaning:
    """The full summary payload leaves generation with reference-free prose."""

    def test_sections_and_key_points_are_cleaned_and_citations_untouched(self) -> None:
        """Stripping applies to prose while structured citations survive intact."""
        from api.summary_generation import (
            SessionSummaryOutput,
            SummaryCitationOutput,
            SummarySectionOutput,
            summary_with_clean_display_text,
        )

        summary = SessionSummaryOutput(
            title="Visit note",
            sections=[
                SummarySectionOutput(
                    heading="Subjective",
                    content="Headache since midday [00:04-00:05], throbbing [corrected-0012].",
                    citations=[SummaryCitationOutput(segment_id="corrected-0012")],
                )
            ],
            key_points=["Left-sided headache [01:07-01:10]"],
        )

        cleaned = summary_with_clean_display_text(summary)

        assert cleaned.sections[0].content == "Headache since midday, throbbing."
        assert cleaned.sections[0].citations[0].segment_id == "corrected-0012"
        assert cleaned.key_points == ["Left-sided headache"]
