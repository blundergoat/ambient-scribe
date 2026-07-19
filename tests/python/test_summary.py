"""
Tests for the medical session summary agent and endpoint.
"""

import json
import logging
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from fastapi.testclient import TestClient

import api.server as api_server
from agents import MEDICAL_SUMMARY_PROMPT
from api import source_integrity
from api.server import app, sessions
from api.summary_generation import (
    SessionSummaryOutput,
    SummaryCitationOutput,
    SummarySectionOutput,
    source_index_text,
    summary_generation_prompt,
    summary_with_validated_citations,
)
from api.summary_request import (
    SUMMARY_TRANSCRIPT_ELISION_MARKER,
    SUMMARY_TRANSCRIPT_MAX_CHARS,
    SummaryRequest,
    build_summary_context,
    select_summary_segments,
    transcript_text_from_segments,
)
from session import SessionStore

TEST_SESSION_ID = "00000000-0000-4000-8000-000000000099"


def _summary_rows(
    count: int,
    *,
    text_chars: int = 24,
    id_prefix: str = "seg",
) -> list[dict]:
    """Build identified clinical rows for summary budget tests."""
    return [
        {
            "segment_id": f"{id_prefix}-{index:04d}",
            "speaker_id": "speaker_0" if index % 2 else "speaker_1",
            "role": "PATIENT" if index % 2 else "DOCTOR",
            "text": f"clinical row {index} " + ("x" * text_chars),
            "start": float(index),
            "end": float(index) + 0.8,
        }
        for index in range(1, count + 1)
    ]


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
        """v2: citations are unit ids in each claim, never rows or timestamps."""
        assert "source_unit_ids" in MEDICAL_SUMMARY_PROMPT
        # M13: sections are clinician-register synthesis, one theme per claim.
        assert "one theme, one claim" in MEDICAL_SUMMARY_PROMPT
        assert "MM:SS" not in MEDICAL_SUMMARY_PROMPT

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
        assert (
            "Only diagnoses or differentials the clinician stated"
            in MEDICAL_SUMMARY_PROMPT
        )
        assert "Never add" in MEDICAL_SUMMARY_PROMPT
        assert "no assessment was documented" in MEDICAL_SUMMARY_PROMPT

    def test_medical_prompt_keeps_patient_reports_out_of_objective(self):
        """Objective may hold only clinician-performed examination content (M03 item a)."""
        assert "Only clinician-performed examination findings" in MEDICAL_SUMMARY_PROMPT
        assert (
            "Patient-reported symptoms belong in Subjective" in MEDICAL_SUMMARY_PROMPT
        )


class TestSummaryEndpoint:
    """Tests for the POST /session/{id}/summary endpoint."""

    def setup_method(self):
        sessions._sessions.clear()
        api_server._mercure_event_ids.clear()
        # A leftover attestation would let one test summarize another test's
        # "finalized" visit, so every case starts unattested.
        source_integrity._terminal_watermarks.clear()
        app.state.http_client = httpx.AsyncClient(timeout=5.0)

    @staticmethod
    def _attest_summary_source(*, corrected_attested: bool = False) -> None:
        """Attest the seeded rows as the finalized visit, like Stop does.

        Summaries now refuse pre-terminal sources. Live-lane tests attest with
        a bounded correction failure (the honest live-fallback state); tests
        seeding corrected rows mark them attested so they may feed the note.
        """
        watermark = source_integrity.record_terminal_watermark(
            TEST_SESSION_ID,
            sessions.get_segments(TEST_SESSION_ID),
            audio_seconds=1.0,
            trimmed_seconds=0.0,
            role_revision=0,
            role_settlement="settled",
        )
        if corrected_attested:
            watermark.correction_status = "attested_corrected"
            return
        # Live rows may feed a note only as the visible post-failure fallback.
        watermark.correction_status = "unavailable:correction_error"
        watermark.fallback_reason = "correction_error"

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

        self._attest_summary_source()

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
        assert data["transcript_source"] == "session_store"
        assert data["transcript_truncated"] is False
        assert data["original_transcript_chars"] == data["kept_transcript_chars"]

    def test_summary_uses_browser_visible_segments_from_request(self):
        """Summaries can use only the transcript rows visible in the browser."""
        # The finalized visit already stored this row; the browser resends the
        # same row (identity-equal) with the roles the clinician can see.
        sessions.append_segment(
            TEST_SESSION_ID,
            {
                "speaker_id": "spk_1",
                "role": "PATIENT",
                "text": "I have sore red skin.",
                "start": 16.0,
                "end": 20.0,
                "segment_id": "live-0001",
            },
        )
        self._attest_summary_source()
        mock_summary = {
            "title": "Partial Transcript",
            "sections": [
                {"heading": "Subjective", "content": "Patient reports visible rash."},
            ],
            "key_points": ["Visible transcript text only"],
        }

        with patch(
            "api.server._run_summary_generation", return_value=mock_summary
        ) as summary_runner:
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
                            "segment_id": "live-0001",
                        }
                    ]
                },
            )

        assert response.status_code == 200
        summary_runner.assert_called_once()
        assert "[PATIENT] I have sore red skin." in summary_runner.call_args.args[1]
        assert (
            sessions.get_segments(TEST_SESSION_ID)[0]["text"] == "I have sore red skin."
        )

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

        self._attest_summary_source(corrected_attested=True)

        mock_summary = {
            "title": "Corrected Transcript",
            "sections": [],
            "key_points": [],
        }

        with patch(
            "api.server._run_summary_generation", return_value=mock_summary
        ) as summary_runner:
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
        assert (
            sessions.get_segments(TEST_SESSION_ID)[0]["text"] == "browser preview typo"
        )

    def test_summary_blocks_over_limit_visits_instead_of_truncating(self):
        """A visit over the note input limit gets no silently shortened draft.

        M02 contract: silent truncation once dropped the end of a long visit's
        note input; the user now sees an explicit note-unavailable state while
        the full transcript stays reviewable.
        """
        rows = _summary_rows(90, text_chars=420)
        # The finalized visit already stored every row the browser resends.
        for row in rows:
            sessions.append_segment(TEST_SESSION_ID, row)
        self._attest_summary_source()

        with (
            patch("api.server._run_summary_generation") as summary_runner,
            patch(
                "api.server.publish_to_mercure",
                new_callable=AsyncMock,
                return_value=True,
            ) as publisher,
        ):
            client = TestClient(app, raise_server_exceptions=False)
            response = client.post(
                f"/session/{TEST_SESSION_ID}/summary",
                json={"segments": rows},
            )

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "blocked"
        assert data["reason"] == "source_exceeds_note_limit"
        # No model call and no published note may exist for a blocked source.
        summary_runner.assert_not_called()
        publisher.assert_not_awaited()

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

        self._attest_summary_source()

        with patch("api.server._run_summary_generation", return_value=None):
            client = TestClient(app, raise_server_exceptions=False)
            response = client.post(f"/session/{TEST_SESSION_ID}/summary")

        assert response.status_code == 502

    def test_summary_502_carries_reason_on_output_limit(self):
        """An output-limit failure names itself so the browser can be honest.

        A generic 502 means "provider problem"; this one must carry
        reason=note_output_limit and a detail that never suggests the model
        is unavailable (M10; M07 blocker B3).
        """
        sessions.append_segment(
            TEST_SESSION_ID,
            {
                "speaker_id": "spk_0",
                "text": "Hello",
                "start": 0.0,
                "end": 1.0,
            },
        )

        self._attest_summary_source()

        with patch(
            "api.server._run_summary_generation",
            return_value={"status": "failed", "reason": "note_output_limit"},
        ):
            client = TestClient(app, raise_server_exceptions=False)
            response = client.post(f"/session/{TEST_SESSION_ID}/summary")

        assert response.status_code == 502
        body = response.json()
        assert body["reason"] == "note_output_limit"
        assert "output limit" in body["detail"]
        assert "unavailable" not in body["detail"].lower()

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

        self._attest_summary_source()

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


class TestSummaryContextSelection:
    """M08 keeps one whole-row input contract across every summary source."""

    def test_under_budget_selection_preserves_exact_rows_and_text(self) -> None:
        """Normal consultations keep their pre-M08 ordering and formatting."""
        rows = _summary_rows(3)

        selection = select_summary_segments(rows, transcript_text_from_segments)

        assert selection.selected_segments == rows
        assert selection.formatted_text == transcript_text_from_segments(rows)
        assert selection.original_chars == selection.kept_chars
        assert selection.original_segments == selection.kept_segments == 3
        assert selection.truncated is False

    def test_over_budget_selection_keeps_complete_opening_and_tail_rows(self) -> None:
        """The selector inserts one marker and never slices a clinical row."""
        rows = _summary_rows(10, text_chars=24)
        rows[-2]["text"] = "Assessment: suspected Lyme disease."
        rows[-1]["text"] = "Plan: arrange Lyme blood tests and follow-up."

        selection = select_summary_segments(
            rows,
            transcript_text_from_segments,
            max_chars=240,
        )

        selected_ids = [row["segment_id"] for row in selection.selected_segments]
        assert selection.truncated is True
        assert selection.formatted_text.count(SUMMARY_TRANSCRIPT_ELISION_MARKER) == 1
        assert len(selection.formatted_text) <= 240
        assert selected_ids[0] == "seg-0001"
        assert selected_ids[-1] == "seg-0010"
        assert len(selected_ids) < len(rows)
        assert rows[-2]["text"] in selection.formatted_text
        assert rows[-1]["text"] in selection.formatted_text
        for row in selection.selected_segments:
            assert row["text"] in selection.formatted_text

    def test_under_budget_corrected_prompt_is_unchanged_and_fully_aligned(self) -> None:
        """Citable corrected rows keep the existing full source-index prompt."""
        backend = SessionStore()
        rows = _summary_rows(3, id_prefix="corrected")
        backend.replace_corrected_segments("corrected-under-budget", rows)

        context = build_summary_context("corrected-under-budget", None, backend)
        prompt_with_selection = summary_generation_prompt(
            context.transcript,
            [],
            citation_segments=context.citation_segments,
            citation_source_index=context.citation_source_index,
        )
        legacy_prompt = summary_generation_prompt(
            context.transcript,
            [],
            citation_segments=rows,
        )

        assert context.source == "corrected_segments"
        assert context.complete_segments == rows
        assert context.selected_segments == rows
        assert context.citation_segments == rows
        assert context.citation_source_index == source_index_text(rows)
        assert context.transcript_truncated is False
        assert context.original_transcript_chars == context.kept_transcript_chars
        assert prompt_with_selection == legacy_prompt

    def test_over_budget_corrected_rows_align_prompt_citations_and_fidelity(
        self,
    ) -> None:
        """The old uncapped citation path cannot reintroduce omitted rows."""
        backend = SessionStore()
        rows = _summary_rows(90, text_chars=420, id_prefix="corrected")
        backend.replace_corrected_segments("corrected-over-budget", rows)

        context = build_summary_context("corrected-over-budget", None, backend)

        selected_ids = [row["segment_id"] for row in context.selected_segments]
        citation_ids = [row["segment_id"] for row in context.citation_segments]
        assert context.transcript_truncated is True
        assert selected_ids == citation_ids
        assert selected_ids[0] == "corrected-0001"
        assert selected_ids[-1] == "corrected-0090"
        assert len(selected_ids) < len(rows)
        assert context.citation_source_index is not None
        assert SUMMARY_TRANSCRIPT_ELISION_MARKER in context.citation_source_index
        assert len(context.citation_source_index) <= SUMMARY_TRANSCRIPT_MAX_CHARS
        for segment_id in selected_ids:
            assert f"source:{segment_id} " in context.citation_source_index
        omitted_ids = {
            row["segment_id"] for row in rows if row["segment_id"] not in selected_ids
        }
        assert omitted_ids
        assert all(
            f"source:{segment_id} " not in context.citation_source_index
            for segment_id in omitted_ids
        )

    def test_over_budget_browser_and_session_sources_keep_the_tail(self) -> None:
        """Both uncited lanes use the same whole-row opening/tail selector."""
        rows = _summary_rows(90, text_chars=420)

        browser_backend = SessionStore()
        browser_request = SummaryRequest.model_validate({"segments": rows})
        browser_context = build_summary_context(
            "browser-over-budget", browser_request, browser_backend
        )

        stored_backend = SessionStore()
        for row in rows:
            stored_backend.append_segment("stored-over-budget", row)
        stored_context = build_summary_context(
            "stored-over-budget", None, stored_backend
        )

        for context, source in (
            (browser_context, "browser_visible_segments"),
            (stored_context, "session_store"),
        ):
            selected_ids = [row["segment_id"] for row in context.selected_segments]
            assert context.source == source
            assert context.transcript_truncated is True
            assert context.citation_segments == []
            assert context.citation_source_index is None
            assert selected_ids[0] == "seg-0001"
            assert selected_ids[-1] == "seg-0090"
            assert SUMMARY_TRANSCRIPT_ELISION_MARKER in context.transcript
            assert context.kept_transcript_chars <= SUMMARY_TRANSCRIPT_MAX_CHARS

    def test_truncation_warning_occurs_only_when_rows_are_omitted(self, caplog) -> None:
        """The warning is quiet for normal notes and structured for real elision."""
        backend = SessionStore()
        backend.append_segment("short-session", _summary_rows(1)[0])

        with caplog.at_level(logging.WARNING, logger="api.summary_request"):
            build_summary_context("short-session", None, backend)
        assert "summary.transcript_truncated" not in caplog.text

        long_backend = SessionStore()
        for row in _summary_rows(90, text_chars=420):
            long_backend.append_segment("long-session", row)
        with caplog.at_level(logging.WARNING, logger="api.summary_request"):
            build_summary_context("long-session", None, long_backend)

        records = [
            record
            for record in caplog.records
            if "summary.transcript_truncated" in record.message
        ]
        assert len(records) == 1
        assert getattr(records[0], "source") == "session_store"
        assert getattr(records[0], "original_chars") > getattr(records[0], "kept_chars")
        assert getattr(records[0], "original_segments") > getattr(
            records[0], "kept_segments"
        )


class TestRunSummaryGeneration:
    """Tests for the _run_summary_generation helper."""

    def test_returns_none_on_agent_exception(self):
        with patch("agents.create_summary_agent", side_effect=RuntimeError("boom")):
            result = api_server._run_summary_generation("sid", "transcript")
        assert result is None

    def test_uses_structured_output_from_agent_response(self):
        """Validated v2 summary output becomes the browser payload."""
        from api.summary_generation import SessionSummaryV2Output

        mock_agent = MagicMock()
        mock_agent.return_value.structured_output = SessionSummaryV2Output(
            title="Test", sections=[], key_points=[]
        )

        with patch("agents.create_summary_agent", return_value=mock_agent):
            result = api_server._run_summary_generation("sid", "transcript")

        assert result["title"] == "Test"
        assert result["schema_version"] == 2
        assert (
            mock_agent.call_args.kwargs["structured_output_model"]
            is SessionSummaryV2Output
        )

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
        assert (
            "[source:corrected-0001 01:05-01:07 DOCTOR] Please use the cream." in prompt
        )

    def test_summary_prompt_uses_preselected_corrected_source_index(self):
        """A bounded source index cannot be replaced by the old uncapped formatter."""
        rows = _summary_rows(3, id_prefix="corrected")
        selected_source = "\n".join(
            (
                source_index_text([rows[0]]),
                SUMMARY_TRANSCRIPT_ELISION_MARKER,
                source_index_text([rows[-1]]),
            )
        )

        prompt = summary_generation_prompt(
            transcript_text_from_segments([rows[0], rows[-1]]),
            [],
            citation_segments=[rows[0], rows[-1]],
            citation_source_index=selected_source,
        )

        assert selected_source in prompt
        assert "source:corrected-0002 " not in prompt

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
                        SummaryCitationOutput(
                            segment_id="corrected-0001", text="model text ignored"
                        ),
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
        """Opaque provider keys map back to server-owned stable unit ids."""
        from api.summary_generation import SessionSummaryV2Output

        mock_agent = MagicMock()

        def _provider_result(prompt, *, structured_output_model):
            result = MagicMock()
            result.structured_output = structured_output_model.model_validate(
                {
                    "title": "Cited",
                    "sections": [
                        {
                            "heading": "Plan",
                            "claims": [
                                {
                                    "text": "Use treatment.",
                                    "evidence_basis": "source_unit",
                                    "source_unit_ids": ["source-0001"],
                                }
                            ],
                        }
                    ],
                    "key_points": [],
                }
            )
            return result

        mock_agent.side_effect = _provider_result

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

        plan_claim = result["sections"][0]["claims"][0]
        assert plan_claim["claim_id"] == "plan-01"
        assert plan_claim["source_unit_ids"] == ["unit-0001-0001"]
        provider_model = mock_agent.call_args.kwargs["structured_output_model"]
        assert provider_model is not SessionSummaryV2Output
        assert "[unit:source-0001 " in mock_agent.call_args.args[0]
        assert result["source_units"] == [
            {
                "unit_id": "unit-0001-0001",
                "role": "DOCTOR",
                "start": 3.0,
                "end": 4.0,
                "rows": [
                    {
                        "segment_id": "corrected-0001",
                        "start": 3.0,
                        "end": 4.0,
                        "text": "Use treatment.",
                    }
                ],
                "context_before": [],
                "context_after": [],
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

        assert [c.segment_id for c in validated.sections[0].citations] == [
            "corrected-0001"
        ]
        assert not [r for r in caplog.records if "citations_dropped" in r.message]

    def test_blank_and_duplicate_drops_are_counted_separately(self, caplog) -> None:
        import logging

        summary = self._summary([["corrected-0001", "corrected-0001", "  "]])

        with caplog.at_level(logging.WARNING, logger="api.summary_generation"):
            summary_with_validated_citations(
                summary, self._SOURCE_ROWS, session_id="sess-1"
            )

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
        assert (
            self._strip("Headache began at midday [00:04].")
            == "Headache began at midday."
        )

    def test_time_range_reference_is_removed(self) -> None:
        """[MM:SS-MM:SS] ranges vanish from the sentence."""
        assert (
            self._strip("Pain is throbbing [01:07-01:10] on the left side.")
            == "Pain is throbbing on the left side."
        )

    def test_multiple_references_in_one_sentence_are_removed(self) -> None:
        """Every reference in a sentence goes, not just the first."""
        assert (
            self._strip(
                "Blurring in both eyes [00:27-00:29, 02:20-02:22] was noted [02:21]."
            )
            == "Blurring in both eyes was noted."
        )

    def test_reference_at_sentence_boundary_keeps_punctuation(self) -> None:
        """A marker before the full stop leaves clean punctuation behind."""
        assert self._strip("Denies fever [02:51-02:52].") == "Denies fever."

    def test_segment_id_and_range_references_are_removed(self) -> None:
        """Legacy [corrected-XXXX] and 'to' ranges leave the prose."""
        assert (
            self._strip(
                "Skin symptoms on arms [corrected-0011 to corrected-0025] persist [corrected-0041]."
            )
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


class TestSchemaV2MidImplementationProof:
    """M06 gate: the model cannot cite rows, and the 5.3 turn is one unit."""

    def _palpitation_rows(self) -> list[dict]:
        """The retained 5.3 quote rows plus their real neighbors' shape.

        Texts for corrected-0416..0418 are the frozen manifest specimen rows
        (c09/c10); the surrounding rows reproduce the artifact's role pattern
        so unit construction sees the real turn boundaries.
        """
        manifest_path = (
            Path(__file__).resolve().parents[1]
            / "fixtures"
            / "scribe"
            / "note-review-detector-specimens.json"
        )
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        c10 = next(s for s in manifest["specimens"] if s["id"] == "c10")
        quote_rows = [
            {
                "segment_id": row["segment_id"],
                "role": "DOCTOR",
                "text": row["text"],
                "start": row["confidence"] and 416.0 or 416.0,
                "end": 443.0,
            }
            for row in c10["source_rows"]
        ]
        return [
            {
                "segment_id": "corrected-0413",
                "role": "OTHER",
                "text": "That's great. I look forward",
                "start": 410.0,
                "end": 412.0,
            },
            *quote_rows,
        ]

    def test_53_palpitation_turn_builds_one_complete_unit(self):
        """The quote-bearing rows 0416-0418 land together in ONE DOCTOR unit."""
        from api.summary_generation import build_source_units

        units = build_source_units(self._palpitation_rows())

        doctor_units = [unit for unit in units if unit["role"] == "DOCTOR"]
        assert len(doctor_units) == 1
        row_ids = [row["segment_id"] for row in doctor_units[0]["rows"]]
        assert row_ids == ["corrected-0416", "corrected-0417", "corrected-0418"]

    def test_model_cannot_cite_an_arbitrary_first_row(self):
        """A row id like corrected-0416 is not a unit id: dropped + downgraded."""
        from api.summary_generation import (
            ClaimOutput,
            ClaimSectionOutput,
            SessionSummaryV2Output,
            build_source_units,
            validated_v2_summary,
        )

        source_units = build_source_units(self._palpitation_rows())
        cited_by_row = SessionSummaryV2Output(
            title="",
            sections=[
                ClaimSectionOutput(
                    heading="Assessment",
                    claims=[
                        ClaimOutput(
                            text="Palpitations discussed.",
                            evidence_basis="source_unit",
                            source_unit_ids=["corrected-0416"],
                        )
                    ],
                )
            ],
            key_points=[],
        )

        validated = validated_v2_summary(cited_by_row, source_units)

        claim = validated.sections[0].claims[0]
        assert claim.source_unit_ids == []
        assert claim.evidence_basis == "none"

    def test_provider_schema_rejects_fabricated_merged_unit_range(self):
        """The provider boundary accepts only request-local citation keys."""
        from pydantic import ValidationError

        from api.summary_generation import _provider_summary_output_model

        source_units = [
            {"unit_id": "unit-0087-0089"},
            {"unit_id": "unit-0090-0094"},
        ]
        output_model = _provider_summary_output_model(source_units)
        payload = {
            "title": "",
            "sections": [
                {
                    "heading": "Subjective",
                    "claims": [
                        {
                            "text": "Symptoms were discussed.",
                            "evidence_basis": "source_unit",
                            "source_unit_ids": ["source-0001"],
                        }
                    ],
                }
            ],
            "key_points": [],
        }

        assert output_model.model_validate(payload).sections[0].claims[
            0
        ].source_unit_ids == ["source-0001"]
        payload["sections"][0]["claims"][0]["source_unit_ids"] = ["unit-0087-0094"]

        with pytest.raises(ValidationError):
            output_model.model_validate(payload)

    def test_prompt_enumerates_units_never_row_ids(self):
        """The provider sees opaque keys, never stable unit or row ids."""
        from api.summary_generation import (
            build_source_units,
            summary_generation_prompt_v2,
        )

        source_units = build_source_units(self._palpitation_rows())
        prompt = summary_generation_prompt_v2("[DOCTOR] text", [], source_units)

        assert "[unit:source-0001" in prompt
        assert "unit-0416-0418" not in prompt
        assert "[source:" not in prompt
        assert "segment_id" not in prompt

    def test_draft_rank_keeps_fidelity_primary_then_prefers_provenance(self):
        """More citations can win only when clinical-safety counts tie."""
        from api.summary_generation import (
            ClaimOutput,
            SessionSummaryV2Output,
            _draft_rank,
        )

        uncited = SessionSummaryV2Output(
            key_points=[ClaimOutput(text="Claim", evidence_basis="none")]
        )
        cited = SessionSummaryV2Output(
            key_points=[
                ClaimOutput(
                    text="Claim",
                    evidence_basis="source_unit",
                    source_unit_ids=["unit-0001-0001"],
                )
            ]
        )

        assert _draft_rank(
            uncited, violation_count=1, has_source_units=True
        ) < _draft_rank(cited, violation_count=4, has_source_units=True)
        assert _draft_rank(
            cited, violation_count=1, has_source_units=True
        ) < _draft_rank(uncited, violation_count=1, has_source_units=True)

    def test_no_units_prompt_reserves_absence_for_bounded_negatives(self):
        """The uncited fallback prompt must not invite absence-labelled positives."""
        from api.summary_generation import summary_generation_prompt_v2

        prompt = summary_generation_prompt_v2("[DOCTOR] text", [], [])

        assert "`none` for every statement about what WAS said" in prompt
        assert "strictly for bounded negatives" in prompt
        assert "Never mark a positive clinical statement" in prompt

    def test_system_prompt_demands_clinician_register_sections(self):
        """Sections must read as clinical synthesis, not per-utterance extraction.

        The 22-sentence "Patient reports..." wall was rejected by the user
        (M13); the Key Points register - compressed, clinically ordered,
        multi-unit citations - is the contract for sections too.
        """
        from agents.summary_agent import MEDICAL_SUMMARY_PROMPT

        assert "Write like a clinician, not a transcriber" in MEDICAL_SUMMARY_PROMPT
        assert (
            "Never write one sentence per transcript utterance"
            in MEDICAL_SUMMARY_PROMPT
        )
        assert (
            "cites EVERY source unit that supports any part" in MEDICAL_SUMMARY_PROMPT
        )
        assert "pertinent negatives" in MEDICAL_SUMMARY_PROMPT
        assert "ORDERED ATOMIC CLAIMS" not in MEDICAL_SUMMARY_PROMPT
        # Key Points keep their own count and hedge discipline (first live run
        # compressed four takeaways into two bullets and revived "triggered by").
        assert "Key Points are 3-5 bullets" in MEDICAL_SUMMARY_PROMPT
        assert 'must never compress into "triggered by"' in MEDICAL_SUMMARY_PROMPT
        assert "the patient's own symptom words" in MEDICAL_SUMMARY_PROMPT

    def test_system_prompt_summarises_partial_visits_instead_of_refusing(self):
        """A short or interrupted transcript must still yield a real note.

        Weeks of v1 short-consult notes were good; the v2 claims frame made the
        model refuse a 2.5-minute visit as "insufficient" (session 4a499eb7).
        The system prompt now says partial coverage is normal and refusal is
        reserved for transcripts with no clinical content at all.
        """
        from agents.summary_agent import MEDICAL_SUMMARY_PROMPT

        assert "may cover only part" in MEDICAL_SUMMARY_PROMPT
        assert (
            "A partial or interrupted transcript is still summarised"
            in MEDICAL_SUMMARY_PROMPT
        )
        assert "no clinical content at all" in MEDICAL_SUMMARY_PROMPT
        assert "too short or uninformative" not in MEDICAL_SUMMARY_PROMPT

    def test_verbatim_quote_verifies_inside_the_cited_unit(self):
        """The full-turn quote produces quote_state verified, exact match only."""
        from api.summary_generation import (
            ClaimOutput,
            build_source_units,
            _claim_quote_state,
        )

        source_units = build_source_units(self._palpitation_rows())
        unit_id = next(
            unit["unit_id"] for unit in source_units if unit["role"] == "DOCTOR"
        )
        verified_claim = ClaimOutput(
            text="The clinician said it is 'more likely to be associated with anxiety'.",
            evidence_basis="source_unit",
            source_unit_ids=[unit_id],
        )
        mismatched_claim = ClaimOutput(
            text="The clinician said it is 'most likely associated with anxiety'.",
            evidence_basis="source_unit",
            source_unit_ids=[unit_id],
        )

        verified_state, verified_reasons = _claim_quote_state(
            verified_claim, source_units
        )
        mismatch_state, mismatch_reasons = _claim_quote_state(
            mismatched_claim, source_units
        )

        assert (verified_state, verified_reasons) == ("verified", [])
        assert mismatch_state == "not_matched"
        assert mismatch_reasons[0]["reason"] == "quote_not_matched"


def test_run_summary_generation_maps_output_limit_to_named_failure(monkeypatch):
    """MaxTokensReachedException becomes the named marker, not a bare None.

    The generic catch-all keeps returning None for everything else, so only
    the output-limit case earns the honest browser copy.
    """
    from strands.types.exceptions import MaxTokensReachedException

    from api import summary_generation

    def _raise_output_limit(session_id, prompt, source_units):
        raise MaxTokensReachedException(
            "Model stopped generating due to maximum token limit."
        )

    monkeypatch.setattr(
        summary_generation, "_generate_validated_v2_draft", _raise_output_limit
    )
    result = summary_generation.run_summary_generation(
        "m10-test", "DOCTOR: hello", [], [], None
    )
    assert result == {"status": "failed", "reason": "note_output_limit"}

    def _raise_generic(session_id, prompt, source_units):
        raise RuntimeError("anything else")

    monkeypatch.setattr(
        summary_generation, "_generate_validated_v2_draft", _raise_generic
    )
    assert (
        summary_generation.run_summary_generation(
            "m10-test", "DOCTOR: hello", [], [], None
        )
        is None
    )
