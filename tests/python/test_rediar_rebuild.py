"""Default-off rebuild lane inside post-visit correction.

These tests pin the NEMO_CORRECTION_REDIARIZATION flag contract: flag-off
corrections stay byte-identical to today's artifact, and flag-on runs may only
change row-level roles that the frozen ADR-013 two-witness policy approves.
The decision-table branches themselves are pinned by
test_rediar_span_comparer.py; here we pin the runtime leg around them.
"""

from __future__ import annotations

import asyncio
import threading
from unittest.mock import patch

import httpx
import pytest
from concurrent.futures import ThreadPoolExecutor
from fastapi.testclient import TestClient

import api.server as api_server
import rediar_rebuild
from api import source_integrity
from api.server import app, lifecycle, sessions
from nemo_pipeline import NemoPipeline
from nemo_session import TranscriptionSession
from post_visit_correction import (
    PostVisitCorrectionResult,
    PostVisitTranscription,
    run_post_visit_correction,
)
from rediar_rebuild import (
    REDIAR_MAX_AUDIO_SECONDS,
    RediarRebuildResult,
    correction_rediarization_enabled,
    run_rediar_rebuild_leg,
)

TEST_SESSION_ID = "00000000-0000-4000-8000-000000000401"

REDIARIZATION_FLAG = "NEMO_CORRECTION_REDIARIZATION"


@pytest.fixture(autouse=True)
def clear_rediar_state(monkeypatch: pytest.MonkeyPatch):
    """Isolate each test's flag value, sessions, and correction executor."""
    monkeypatch.delenv(REDIARIZATION_FLAG, raising=False)
    executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="test-rediar")
    original_executor = api_server.nemo_executor
    api_server.nemo_executor = executor
    api_server.correction_single_flight = asyncio.Semaphore(1)
    sessions._sessions.clear()
    lifecycle.clear()
    api_server._mercure_event_ids.clear()
    source_integrity._terminal_watermarks.clear()
    app.state.nemo_pipeline = NemoPipeline()
    app.state.nemo_input_format = "pcm"
    app.state.http_client = httpx.AsyncClient(timeout=5.0)

    yield

    sessions._sessions.clear()
    lifecycle.clear()
    source_integrity._terminal_watermarks.clear()
    api_server.nemo_executor = original_executor
    executor.shutdown(wait=False)


# ---------------------------------------------------------------------------
# Flag parsing: the lane must default off in every environment shape.
# ---------------------------------------------------------------------------


def test_flag_defaults_off_when_unset() -> None:
    """An absent flag keeps every clinician visit on today's correction path."""
    assert correction_rediarization_enabled() is False


@pytest.mark.parametrize("configured_value", ["", "0", "false", "no", "off", " "])
def test_flag_stays_off_for_disabled_values(
    monkeypatch: pytest.MonkeyPatch, configured_value: str
) -> None:
    """Explicit off values behave exactly like an absent flag."""
    monkeypatch.setenv(REDIARIZATION_FLAG, configured_value)
    assert correction_rediarization_enabled() is False


@pytest.mark.parametrize("configured_value", ["1", "true", "yes", "on", " TRUE "])
def test_flag_turns_on_for_enabled_values(
    monkeypatch: pytest.MonkeyPatch, configured_value: str
) -> None:
    """Operator-enabled values open the lane, mirroring the crosstalk guard."""
    monkeypatch.setenv(REDIARIZATION_FLAG, configured_value)
    assert correction_rediarization_enabled() is True


# ---------------------------------------------------------------------------
# Synthetic session pieces shared by the leg tests.
# ---------------------------------------------------------------------------


def _live_row(segment_id, speaker_id, role, start, end, text="live words"):
    """One settled live row as the correction endpoint stores it."""
    return {
        "segment_id": segment_id,
        "speaker_id": speaker_id,
        "role": role,
        "text": text,
        "start": start,
        "end": end,
    }


def _synthetic_visit():
    """A dyadic visit whose fold span hides doctor words on the patient chip.

    The doctor chip speaker_0 owns clean speech at [0, 10]; the patient chip
    speaker_1 owns [30, 40]. The live session folded a doctor utterance at
    [66.6, 67.0] into speaker_1's chip, so the words render on the wrong card.
    """
    live_rows = [
        _live_row("live-0001", "speaker_0", "DOCTOR", 0.0, 10.0),
        _live_row("live-0002", "speaker_1", "PATIENT", 30.0, 40.0),
        _live_row("live-0900", "speaker_1", "PATIENT", 66.5, 67.5),
    ]
    fold_spans = [
        {
            "origin_speaker_slot": "speaker_3",
            "visible_speaker_slot": "speaker_1",
            "start_seconds": 66.6,
            "end_seconds": 67.0,
            "word_count": 3,
        }
    ]
    # The rebuilt voice diar_A speaks the doctor's clean block AND the span.
    rebuilt_turns = [
        {"speaker_id": "diar_A", "start": 0.0, "end": 10.0},
        {"speaker_id": "diar_B", "start": 30.0, "end": 40.0},
        {"speaker_id": "diar_A", "start": 66.5, "end": 67.2},
    ]
    word_timings = (
        [
            {"word": f"doctor{i}", "start": 0.5 + i, "end": 1.0 + i}
            for i in range(9)
        ]
        + [
            {"word": f"patient{i}", "start": 30.5 + i, "end": 31.0 + i}
            for i in range(9)
        ]
        + [
            {"word": "span1", "start": 66.65, "end": 66.75},
            {"word": "span2", "start": 66.8, "end": 66.95},
        ]
    )
    return live_rows, fold_spans, rebuilt_turns, word_timings


def _label_by_slot(labels_by_slot):
    """Return a wording-witness seam labeling rebuilt rows by their slot."""

    def label_rebuilt_rows(rebuilt_rows):
        return [
            {**row, "role": labels_by_slot.get(str(row["speaker_id"]), "UNKNOWN")}
            for row in rebuilt_rows
        ]

    return label_rebuilt_rows


def _failing_diarizer(*_args, **_kwargs):
    """A diarizer seam whose invocation itself fails the calling test."""
    raise AssertionError("diarization must not run for this scenario")


# ---------------------------------------------------------------------------
# The rebuild leg: gates, policy application, and failure containment.
# ---------------------------------------------------------------------------


def test_leg_skips_without_fold_spans() -> None:
    """A visit that never folded has nothing to repair and skips diarization."""
    live_rows, _spans, _turns, word_timings = _synthetic_visit()

    result = run_rediar_rebuild_leg(
        audio_path="unused.wav",
        audio_duration_seconds=70.0,
        word_timings=word_timings,
        live_segments=live_rows,
        fold_spans=[],
        diarize_turns=_failing_diarizer,
    )

    assert result.status == "skipped_no_fold_spans"
    assert result.row_exceptions == {}


def test_leg_skips_without_word_timings() -> None:
    """Without visit-relative word timings no rebuilt rows can exist."""
    live_rows, fold_spans, _turns, _timings = _synthetic_visit()

    result = run_rediar_rebuild_leg(
        audio_path="unused.wav",
        audio_duration_seconds=70.0,
        word_timings=None,
        live_segments=live_rows,
        fold_spans=fold_spans,
        diarize_turns=_failing_diarizer,
    )

    assert result.status == "skipped_no_word_timings"
    assert result.row_exceptions == {}


def test_leg_gates_on_the_frozen_capacity_envelope() -> None:
    """Audio beyond the proven envelope must not attempt a rebuild."""
    live_rows, fold_spans, _turns, word_timings = _synthetic_visit()

    result = run_rediar_rebuild_leg(
        audio_path="unused.wav",
        audio_duration_seconds=REDIAR_MAX_AUDIO_SECONDS + 0.1,
        word_timings=word_timings,
        live_segments=live_rows,
        fold_spans=fold_spans,
        diarize_turns=_failing_diarizer,
    )

    assert result.status == "skipped_envelope"
    assert result.row_exceptions == {}


def test_leg_replaces_role_on_policy_approved_span() -> None:
    """Both witnesses agree, so the span's row takes the linked chip's role."""
    live_rows, fold_spans, rebuilt_turns, word_timings = _synthetic_visit()

    result = run_rediar_rebuild_leg(
        audio_path="unused.wav",
        audio_duration_seconds=70.0,
        word_timings=word_timings,
        live_segments=live_rows,
        fold_spans=fold_spans,
        diarize_turns=lambda _path: rebuilt_turns,
        label_rebuilt_rows=_label_by_slot({"diar_A": "DOCTOR", "diar_B": "PATIENT"}),
    )

    assert result.status == "completed"
    assert [d.decision for d in result.decisions] == ["replace_role"]
    assert result.row_exceptions == {"live-0900": "DOCTOR"}


def test_leg_routes_review_when_linked_chip_never_settled() -> None:
    """A linked chip without a settled role sends the span to review, not a guess."""
    live_rows, fold_spans, rebuilt_turns, word_timings = _synthetic_visit()
    # The doctor chip never earned a visible role in this visit.
    unsettled_rows = [
        {**row, "role": "UNKNOWN"} if row["speaker_id"] == "speaker_0" else row
        for row in live_rows
    ]

    result = run_rediar_rebuild_leg(
        audio_path="unused.wav",
        audio_duration_seconds=70.0,
        word_timings=word_timings,
        live_segments=unsettled_rows,
        fold_spans=fold_spans,
        diarize_turns=lambda _path: rebuilt_turns,
        label_rebuilt_rows=_label_by_slot({"diar_A": "DOCTOR", "diar_B": "PATIENT"}),
    )

    assert result.status == "completed"
    assert [d.decision for d in result.decisions] == ["route_review"]
    assert result.row_exceptions == {"live-0900": "UNKNOWN"}


def test_leg_keeps_clean_visit_untouched_on_same_voice_churn() -> None:
    """A fold that was one voice's cache churn decides keep-live everywhere."""
    live_rows, fold_spans, _turns, word_timings = _synthetic_visit()
    # The rebuilt span voice links to the fold target itself: benign churn.
    churn_turns = [
        {"speaker_id": "diar_B", "start": 30.0, "end": 40.0},
        {"speaker_id": "diar_B", "start": 66.5, "end": 67.2},
    ]

    result = run_rediar_rebuild_leg(
        audio_path="unused.wav",
        audio_duration_seconds=70.0,
        word_timings=word_timings,
        live_segments=live_rows,
        fold_spans=fold_spans,
        diarize_turns=lambda _path: churn_turns,
        label_rebuilt_rows=_label_by_slot({"diar_B": "PATIENT"}),
    )

    assert result.status == "completed"
    assert [d.decision for d in result.decisions] == ["keep_live_same_voice"]
    assert result.row_exceptions == {}


def test_leg_contains_diarizer_failure_without_losing_correction() -> None:
    """A rebuild failure must never take the corrected transcript with it."""
    live_rows, fold_spans, _turns, word_timings = _synthetic_visit()

    def broken_diarizer(_path):
        raise RuntimeError("sortformer failed to load")

    result = run_rediar_rebuild_leg(
        audio_path="unused.wav",
        audio_duration_seconds=70.0,
        word_timings=word_timings,
        live_segments=live_rows,
        fold_spans=fold_spans,
        diarize_turns=broken_diarizer,
    )

    assert result.status == "failed"
    assert result.row_exceptions == {}


def test_word_assignment_snaps_only_within_the_frozen_tolerance() -> None:
    """Gap words join the nearest turn inside 0.5s; farther words stay out."""
    turns = [{"speaker_id": "diar_A", "start": 1.0, "end": 2.0}]
    word_timings = [
        {"word": "inside", "start": 1.2, "end": 1.4},
        {"word": "near", "start": 2.2, "end": 2.4},  # center 2.3, gap 0.3
        {"word": "far", "start": 2.8, "end": 3.4},  # center 3.1, gap 1.1
    ]

    rebuilt_rows = rediar_rebuild.rebuilt_rows_from_word_timings(word_timings, turns)

    assert len(rebuilt_rows) == 1
    assert rebuilt_rows[0]["speaker_id"] == "diar_A"
    assert rebuilt_rows[0]["text"] == "inside near"


# ---------------------------------------------------------------------------
# run_post_visit_correction: flag-off identity and flag-on attachment.
# ---------------------------------------------------------------------------


def _rich_transcriber(word_timings):
    """A transcriber seam returning fixed words with visit-relative timings."""
    text = " ".join(t["word"] for t in word_timings)

    def transcribe(_model, _path):
        return PostVisitTranscription(
            text=text,
            word_timings=list(word_timings),
            word_confidences=None,
        )

    return transcribe


def test_flag_off_correction_is_byte_identical_with_fold_spans_present() -> None:
    """Passing fold spans while the flag is off must not change one byte."""
    live_rows, fold_spans, _turns, word_timings = _synthetic_visit()
    pcm_audio = b"\0\0" * 16000

    def _leg_must_not_run(**_kwargs):
        raise AssertionError("rebuild leg must not run while the flag is off")

    baseline = run_post_visit_correction(
        pcm_audio=pcm_audio,
        live_segments=live_rows,
        transcribe_audio_file=_rich_transcriber(word_timings),
    )
    with_fold_spans = run_post_visit_correction(
        pcm_audio=pcm_audio,
        live_segments=live_rows,
        transcribe_audio_file=_rich_transcriber(word_timings),
        fold_spans=fold_spans,
        rediar_leg=_leg_must_not_run,
    )

    assert source_integrity.canonical_rows_hash(
        with_fold_spans.segments
    ) == source_integrity.canonical_rows_hash(baseline.segments)
    assert with_fold_spans.rediarization is None


def test_flag_on_correction_attaches_rebuild_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Flag-on corrections carry the leg's decisions out for the endpoint."""
    monkeypatch.setenv(REDIARIZATION_FLAG, "1")
    live_rows, fold_spans, _turns, word_timings = _synthetic_visit()
    sentinel = RediarRebuildResult(status="completed")
    received_kwargs = {}

    def recording_leg(**kwargs):
        received_kwargs.update(kwargs)
        return sentinel

    result = run_post_visit_correction(
        pcm_audio=b"\0\0" * 16000,
        live_segments=live_rows,
        transcribe_audio_file=_rich_transcriber(word_timings),
        fold_spans=fold_spans,
        rediar_leg=recording_leg,
    )

    assert result.rediarization is sentinel
    assert received_kwargs["fold_spans"] == fold_spans
    assert received_kwargs["live_segments"] == live_rows
    assert [t["word"] for t in received_kwargs["word_timings"]] == [
        t["word"] for t in word_timings
    ]


def test_flag_on_rebuild_runs_on_the_correction_thread(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The rebuild leg shares the single correction executor slot: no new concurrency."""
    monkeypatch.setenv(REDIARIZATION_FLAG, "1")
    live_rows, fold_spans, _turns, word_timings = _synthetic_visit()
    thread_idents = {}

    def recording_transcriber(_model, _path):
        thread_idents["transcribe"] = threading.get_ident()
        return PostVisitTranscription(
            text=" ".join(t["word"] for t in word_timings),
            word_timings=list(word_timings),
            word_confidences=None,
        )

    def recording_leg(**_kwargs):
        thread_idents["rebuild"] = threading.get_ident()
        return RediarRebuildResult(status="completed")

    run_post_visit_correction(
        pcm_audio=b"\0\0" * 16000,
        live_segments=live_rows,
        transcribe_audio_file=recording_transcriber,
        fold_spans=fold_spans,
        rediar_leg=recording_leg,
    )

    assert thread_idents["rebuild"] == thread_idents["transcribe"]


# ---------------------------------------------------------------------------
# nemo_session: fold spans are retained for every visit, evidence flag or not.
# ---------------------------------------------------------------------------


def test_session_retains_fold_spans_without_evidence_flag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Normal visits must keep fold spans for correction-time repair."""
    monkeypatch.delenv("NEMO_STREAMING_SLOT_EVIDENCE", raising=False)
    transcription_session = TranscriptionSession(
        TEST_SESSION_ID,
        pipeline=NemoPipeline(),
        input_format="pcm",
    )
    transcription_session._speaker_cap = 2

    from nemo_session import Segment

    dominant_rows = [
        Segment(
            segment_id="live-0001",
            speaker_id="speaker_0",
            text="a sustained consultation voice speaking for a long time",
            start=0.0,
            end=30.0,
        ),
        Segment(
            segment_id="live-0002",
            speaker_id="speaker_1",
            text="the second consultation voice answering at length",
            start=30.0,
            end=55.0,
        ),
    ]
    marginal_row = Segment(
        segment_id="live-0003",
        speaker_id="speaker_3",
        text="three folded words",
        start=66.6,
        end=67.0,
    )

    transcription_session._cap_engine_speaker_slots(dominant_rows + [marginal_row])
    first_pass_spans = list(transcription_session.folded_word_spans)
    transcription_session._cap_engine_speaker_slots(
        [
            Segment(
                segment_id="live-0004",
                speaker_id="speaker_3",
                text="two more",
                start=70.0,
                end=70.4,
            )
        ]
    )

    assert len(first_pass_spans) == 1
    assert first_pass_spans[0]["origin_speaker_slot"] == "speaker_3"
    assert first_pass_spans[0]["start_seconds"] == 66.6
    # Retention is cumulative across emission ticks, not last-tick-only.
    assert len(transcription_session.folded_word_spans) == 2


# ---------------------------------------------------------------------------
# Endpoint wiring: exceptions out through the existing lanes, ADR-006 intact.
# ---------------------------------------------------------------------------


def _attest_terminal_visit(session_id: str = TEST_SESSION_ID) -> None:
    """Freeze the stored rows as the finalized visit, like Stop does."""
    source_integrity.record_terminal_watermark(
        session_id,
        sessions.get_segments(session_id),
        audio_seconds=1.0,
        trimmed_seconds=0.0,
        role_revision=0,
        role_settlement="settled",
    )


def _register_stopped_session_with_audio(session_id: str = TEST_SESSION_ID):
    """Register retained PCM like the post-finalize grace window."""
    transcription_session = TranscriptionSession(
        session_id,
        pipeline=NemoPipeline(),
        input_format="pcm",
    )
    transcription_session.buffer.append(b"\0\0" * 16000)
    asyncio.run(lifecycle.register(session_id, transcription_session))
    return transcription_session


def test_correction_endpoint_applies_approved_row_exceptions() -> None:
    """Replace decisions reach live rows and the corrected artifact together."""
    sessions.append_segment(
        TEST_SESSION_ID,
        _live_row("live-0001", "speaker_1", "PATIENT", 66.5, 67.5, "span words here"),
    )
    _register_stopped_session_with_audio()
    _attest_terminal_visit()

    corrected_rows = [
        {
            "segment_id": "corrected-0001",
            "speaker_id": "speaker_1",
            "role": "PATIENT",
            "text": "span words here",
            "start": 66.5,
            "end": 67.5,
            "is_interim": False,
            "source": "post_visit_correction",
            "source_model": "test-model",
        }
    ]
    from rediar_rebuild import SpanDecision

    rediar_result = RediarRebuildResult(
        status="completed",
        decisions=(
            SpanDecision(
                span_start=66.6,
                span_end=67.0,
                fold_target_slot="speaker_1",
                decision="replace_role",
                linked_live_slot="speaker_0",
                replacement_role="DOCTOR",
                evidence={},
            ),
        ),
        row_exceptions={"live-0001": "DOCTOR"},
    )
    correction_result = PostVisitCorrectionResult(
        segments=corrected_rows,
        model_name="test-model",
        word_count=3,
        rediarization=rediar_result,
    )

    with patch(
        "api.server.run_post_visit_correction", return_value=correction_result
    ):
        client = TestClient(app, raise_server_exceptions=False)
        response = client.post(f"/session/{TEST_SESSION_ID}/correction")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ready"
    assert payload["rediarization"]["replacements"] == 1
    assert payload["rediarization"]["row_exceptions"] == 1
    # The live row took the exception through the existing auto-row lane.
    stored_rows = sessions.get_segments(TEST_SESSION_ID)
    assert stored_rows[0]["role"] == "DOCTOR"
    # The stored corrected artifact shows the repaired role at the span.
    stored_corrected = sessions.get_corrected_segments(TEST_SESSION_ID)
    assert stored_corrected[0]["role"] == "DOCTOR"
    # Role-only repair keeps the attested lineage intact.
    watermark = source_integrity._terminal_watermarks[TEST_SESSION_ID]
    assert watermark.correction_status == "attested_corrected"


def test_correction_endpoint_omits_rediarization_field_when_flag_off() -> None:
    """Flag-off responses stay byte-identical: no new keys appear."""
    sessions.append_segment(
        TEST_SESSION_ID,
        _live_row("live-0001", "speaker_1", "PATIENT", 0.0, 1.0, "preview words"),
    )
    _register_stopped_session_with_audio()
    _attest_terminal_visit()

    correction_result = PostVisitCorrectionResult(
        segments=[
            {
                "segment_id": "corrected-0001",
                "speaker_id": "speaker_1",
                "role": "PATIENT",
                "text": "preview words",
                "start": 0.0,
                "end": 1.0,
                "is_interim": False,
                "source": "post_visit_correction",
                "source_model": "test-model",
            }
        ],
        model_name="test-model",
        word_count=2,
    )

    with patch(
        "api.server.run_post_visit_correction", return_value=correction_result
    ):
        client = TestClient(app, raise_server_exceptions=False)
        response = client.post(f"/session/{TEST_SESSION_ID}/correction")

    assert response.status_code == 200
    assert "rediarization" not in response.json()


def test_correction_endpoint_trimmed_buffer_bypasses_rebuild(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ADR-006: trimmed visits refuse correction before any rebuild can start."""
    monkeypatch.setenv(REDIARIZATION_FLAG, "1")
    transcription_session = TranscriptionSession(
        TEST_SESSION_ID,
        pipeline=NemoPipeline(),
        input_format="pcm",
        max_buffer_duration=1.0,
    )
    transcription_session.buffer.append(b"\0\0" * 16000)
    transcription_session.buffer.append(b"\0\0" * 16000)
    asyncio.run(lifecycle.register(TEST_SESSION_ID, transcription_session))
    sessions.append_segment(
        TEST_SESSION_ID,
        _live_row("live-0001", "speaker_0", "PATIENT", 0.0, 1.0, "early history"),
    )
    _attest_terminal_visit()

    with patch(
        "api.server.run_post_visit_correction",
        side_effect=AssertionError("correction must not run on a trimmed buffer"),
    ):
        client = TestClient(app, raise_server_exceptions=False)
        response = client.post(f"/session/{TEST_SESSION_ID}/correction")

    assert response.status_code == 200
    assert response.json()["status"] == "unavailable"
    assert response.json()["reason_category"] == "retention_window"
