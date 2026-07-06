"""Tests for post-stop transcript correction before summary generation."""

from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

import httpx
import pytest
from fastapi.testclient import TestClient

import api.server as api_server
from api.server import app, lifecycle, sessions
from corrected_role_cues import infer_role_from_corrected_text
from nemo_pipeline import NemoPipeline
from nemo_session import TranscriptionSession
from post_visit_correction import (
    PostVisitCorrectionError,
    build_corrected_segments,
    run_post_visit_correction,
)

TEST_SESSION_ID = "00000000-0000-4000-8000-000000000301"


@pytest.fixture(autouse=True)
def clear_correction_state():
    """Keep correction endpoint tests isolated from other browser sessions."""
    executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="test-correction")
    original_executor = api_server.nemo_executor
    api_server.nemo_executor = executor
    sessions._sessions.clear()
    lifecycle.clear()
    api_server._mercure_event_ids.clear()
    app.state.nemo_pipeline = NemoPipeline()
    app.state.nemo_input_format = "pcm"
    app.state.http_client = httpx.AsyncClient(timeout=5.0)

    yield

    sessions._sessions.clear()
    lifecycle.clear()
    api_server._mercure_event_ids.clear()
    executor.shutdown(wait=False, cancel_futures=True)
    api_server.nemo_executor = original_executor


def test_build_corrected_segments_uses_live_rows_as_role_scaffold() -> None:
    """Second-pass words keep the row roles and timings the user reviewed."""
    corrected_rows = build_corrected_segments(
        corrected_words="hello there patient has migraine aura".split(),
        live_segments=[
            {
                "speaker_id": "speaker_0",
                "role": "DOCTOR",
                "text": "hello there",
                "start": 1.0,
                "end": 3.0,
            },
            {
                "speaker_id": "speaker_1",
                "role": "PATIENT",
                "text": "headache with zig zag lines",
                "start": 3.5,
                "end": 7.0,
            },
        ],
        model_name="nvidia/parakeet-tdt-0.6b-v3",
    )

    assert [row["role"] for row in corrected_rows] == ["DOCTOR", "PATIENT"]
    assert corrected_rows[0]["segment_id"] == "corrected-0001"
    assert corrected_rows[1]["source"] == "post_visit_correction"
    assert corrected_rows[1]["source_model"] == "nvidia/parakeet-tdt-0.6b-v3"
    assert corrected_rows[0]["start"] == 1.0


def test_build_corrected_segments_anchors_opening_question_to_doctor() -> None:
    """Doctor opener words should not slide into the patient's first symptom row."""
    corrected_rows = build_corrected_segments(
        corrected_words=(
            "Hello. Hello there. It's uh Dr. Steve here. How can I help you "
            "this afternoon? Oh, I've just got a terrible headache since midday."
        ).split(),
        live_segments=[
            {
                "speaker_id": "speaker_3",
                "role": "PATIENT",
                "text": "Hello,",
                "start": 1.6,
                "end": 1.65,
            },
            {
                "speaker_id": "speaker_3",
                "role": "DOCTOR",
                "text": "Hello there, it's Dr. Steed here. How can I help",
                "start": 3.92,
                "end": 5.09,
            },
            {
                "speaker_id": "speaker_3",
                "role": "PATIENT",
                "text": "This afternoon.",
                "start": 6.0,
                "end": 6.05,
            },
            {
                "speaker_id": "speaker_3",
                "role": "PATIENT",
                "text": "I've had a headache since midday. On",
                "start": 9.52,
                "end": 11.81,
            },
        ],
        model_name="nvidia/parakeet-tdt-0.6b-v3",
    )

    assert corrected_rows[1]["role"] == "DOCTOR"
    assert corrected_rows[1]["text"].endswith("help you")
    assert corrected_rows[2]["role"] == "DOCTOR"
    assert corrected_rows[2]["text"] == "this afternoon?"
    assert corrected_rows[3]["role"] == "PATIENT"
    assert corrected_rows[3]["text"].startswith("Oh, I've")


def test_build_corrected_segments_keeps_empathy_phrase_with_doctor() -> None:
    """Doctor empathy and follow-up prompts should not remain patient-labeled."""
    corrected_rows = build_corrected_segments(
        corrected_words=(
            "I just feel like I need to vomit. I'm sorry to hear that. "
            "Um can you tell me a bit more about the headache?"
        ).split(),
        live_segments=[
            {
                "speaker_id": "speaker_3",
                "role": "PATIENT",
                "text": "I just feel like I",
                "start": 14.0,
                "end": 15.17,
            },
            {
                "speaker_id": "speaker_3",
                "role": "PATIENT",
                "text": "need to vomit",
                "start": 15.92,
                "end": 15.97,
            },
            {
                "speaker_id": "speaker_3",
                "role": "PATIENT",
                "text": "I'm sorry to hear this.",
                "start": 17.2,
                "end": 18.53,
            },
            {
                "speaker_id": "speaker_3",
                "role": "PATIENT",
                "text": "Can",
                "start": 18.48,
                "end": 18.53,
            },
            {
                "speaker_id": "speaker_0",
                "role": "DOCTOR",
                "text": "you tell me a bit more about the headache?",
                "start": 18.48,
                "end": 19.41,
            },
        ],
        model_name="nvidia/parakeet-tdt-0.6b-v3",
    )

    assert corrected_rows[1]["text"] == "need to vomit."
    assert corrected_rows[1]["role"] == "PATIENT"
    assert corrected_rows[2]["text"] == "I'm sorry to hear that."
    assert corrected_rows[2]["role"] == "DOCTOR"
    assert corrected_rows[2]["role_source"] == "post_visit_alignment"
    assert corrected_rows[3]["role"] == "DOCTOR"
    assert corrected_rows[3]["text"] == "Um can"


def test_build_corrected_segments_relabels_patient_first_person_source_rows() -> None:
    """Patient symptom rows should not keep a Doctor chip after correction."""
    corrected_rows = build_corrected_segments(
        corrected_words=(
            "it affected? Mostly like my chest, my my hands, my arms,"
        ).split(),
        live_segments=[
            {
                "speaker_id": "speaker_0",
                "role": "DOCTOR",
                "text": "it affected? Mostly like my chest, my my",
                "start": 29.76,
                "end": 31.81,
            },
            {
                "speaker_id": "speaker_0",
                "role": "DOCTOR",
                "text": "hands, my arms,",
                "start": 31.76,
                "end": 31.81,
            },
        ],
        model_name="nvidia/parakeet-tdt-0.6b-v3",
    )

    assert [row["role"] for row in corrected_rows] == ["DOCTOR", "PATIENT", "PATIENT"]
    assert [row["text"] for row in corrected_rows] == [
        "it affected?",
        "Mostly like my chest, my my",
        "hands, my arms,",
    ]
    assert corrected_rows[0]["role_source"] == "post_visit_alignment"
    assert corrected_rows[1]["role_source"] == "post_visit_alignment"
    assert corrected_rows[2]["role_source"] == "post_visit_alignment"


def test_build_corrected_segments_relabels_patient_identity_and_body_rows() -> None:
    """Patient identity and body-location answers should not stay Doctor-owned."""
    corrected_rows = build_corrected_segments(
        corrected_words=(
            "please? My name's Python and I'm 26. "
            "Um, whereabouts in your skin? All over my arms"
        ).split(),
        live_segments=[
            {
                "speaker_id": "speaker_0",
                "role": "DOCTOR",
                "text": "please? My name's Python and I'm 26.",
                "start": 14.0,
                "end": 15.89,
            },
            {
                "speaker_id": "speaker_0",
                "role": "DOCTOR",
                "text": "Um, whereabouts in your skin? All over my arms",
                "start": 37.52,
                "end": 39.81,
            },
        ],
        model_name="nvidia/parakeet-tdt-0.6b-v3",
    )

    assert [row["role"] for row in corrected_rows] == [
        "DOCTOR",
        "PATIENT",
        "DOCTOR",
        "PATIENT",
    ]
    assert [row["text"] for row in corrected_rows] == [
        "please?",
        "My name's Python and I'm 26.",
        "Um, whereabouts in your skin?",
        "All over my arms",
    ]


def test_build_corrected_segments_relabels_short_answer_after_doctor_question() -> None:
    """A standalone acknowledgement after a Doctor question belongs to the patient."""
    corrected_rows = build_corrected_segments(
        corrected_words=("Have you vomited at all? Yeah.").split(),
        live_segments=[
            {
                "speaker_id": "speaker_0",
                "role": "DOCTOR",
                "text": "Have you vomited at all?",
                "start": 157.0,
                "end": 160.0,
            },
            {
                "speaker_id": "speaker_0",
                "role": "DOCTOR",
                "text": "Yeah.",
                "start": 160.96,
                "end": 161.01,
            },
        ],
        model_name="nvidia/parakeet-tdt-0.6b-v3",
    )

    assert [row["role"] for row in corrected_rows] == ["DOCTOR", "PATIENT"]
    assert corrected_rows[1]["role_source"] == "post_visit_alignment"


def test_build_corrected_segments_relabels_prompt_fragment_as_doctor() -> None:
    """A separated clinician question should not stay under a Patient source chip."""
    corrected_rows = build_corrected_segments(
        corrected_words=(
            "Whereabouts in your skin is it affected? Mostly like my chest"
        ).split(),
        live_segments=[
            {
                "speaker_id": "speaker_0",
                "role": "DOCTOR",
                "text": "Whereabouts in your skin",
                "start": 29.76,
                "end": 31.81,
            },
            {
                "speaker_id": "speaker_2",
                "role": "PATIENT",
                "text": "is it affected?",
                "start": 31.76,
                "end": 31.81,
            },
            {
                "speaker_id": "speaker_2",
                "role": "PATIENT",
                "text": "Mostly like my chest",
                "start": 34.16,
                "end": 36.45,
            },
        ],
        model_name="nvidia/parakeet-tdt-0.6b-v3",
    )

    assert [row["role"] for row in corrected_rows] == ["DOCTOR", "DOCTOR", "PATIENT"]
    assert corrected_rows[1]["role_source"] == "post_visit_alignment"


def test_build_corrected_segments_keeps_identity_echo_unsplit_without_word_timing() -> None:
    """Identity rows with echoed age stay intact until split timing is trustworthy."""
    corrected_rows = build_corrected_segments(
        corrected_words=("My name's Python and I'm 26. 26, okay.").split(),
        live_segments=[
            {
                "speaker_id": "speaker_0",
                "role": "DOCTOR",
                "text": "My name's Python and I'm 26. 26, okay.",
                "start": 14.0,
                "end": 15.89,
            },
        ],
        model_name="nvidia/parakeet-tdt-0.6b-v3",
    )

    assert [row["segment_id"] for row in corrected_rows] == ["corrected-0001"]
    assert [row["role"] for row in corrected_rows] == ["PATIENT"]
    assert corrected_rows[0]["text"] == "My name's Python and I'm 26. 26, okay."


def test_build_corrected_segments_keeps_answer_with_filler_as_patient() -> None:
    """A short answer plus filler after a Doctor question is still patient-owned."""
    corrected_rows = build_corrected_segments(
        corrected_words=("Have you had difficulty with bright lights? Yeah, well,").split(),
        live_segments=[
            {
                "speaker_id": "speaker_0",
                "role": "DOCTOR",
                "text": "Have you had difficulty with bright lights?",
                "start": 145.04,
                "end": 146.85,
            },
            {
                "speaker_id": "speaker_0",
                "role": "DOCTOR",
                "text": "Yeah, well,",
                "start": 148.48,
                "end": 148.53,
            },
        ],
        model_name="nvidia/parakeet-tdt-0.6b-v3",
    )

    assert [row["role"] for row in corrected_rows] == ["DOCTOR", "PATIENT"]
    assert corrected_rows[1]["role_source"] == "post_visit_alignment"


def test_role_cues_keep_patient_answer_when_doctor_acknowledgement_trails() -> None:
    """A trailing doctor acknowledgement should not steal the patient's answer."""
    role = infer_role_from_corrected_text(
        "I don't know, really. It just happened. Okay."
    )

    assert role == "PATIENT"


def test_role_cues_keep_doctor_question_when_patient_filler_trails() -> None:
    """A doctor question should stay doctor-owned when patient filler follows."""
    role = infer_role_from_corrected_text(
        "Can you tell me about the headache? Well, you know"
    )

    assert role == "DOCTOR"


def test_role_cues_keep_short_doctor_prompt_before_patient_answer() -> None:
    """A short clinician prompt should not borrow the following Patient answer label."""
    role = infer_role_from_corrected_text(
        "age, please?",
        next_text="My name's Python and I'm 26.",
        next_role="PATIENT",
    )

    assert role == "DOCTOR"


def test_role_cues_keep_tell_me_prompt_as_doctor() -> None:
    """A clinician prompt with recap text should stay Doctor when scaffold is weak."""
    role = infer_role_from_corrected_text("stressful. Um tell me more about your skin")

    assert role == "DOCTOR"


def test_build_corrected_segments_preserves_live_row_when_asr_drops_patient_text() -> None:
    """Dropped second-pass words should keep the patient's visible request row."""
    corrected_rows = build_corrected_segments(
        corrected_words=(
            "Um okay. Well, let's try our best. Let's try and get you"
        ).split(),
        live_segments=[
            {
                "speaker_id": "speaker_0",
                "role": "DOCTOR",
                "text": "Okay,",
                "start": 40.88,
                "end": 40.93,
            },
            {
                "speaker_id": "speaker_2",
                "role": "PATIENT",
                "text": "and I",
                "start": 43.12,
                "end": 43.17,
            },
            {
                "speaker_id": "speaker_2",
                "role": "PATIENT",
                "text": "just want you to do something.",
                "start": 43.12,
                "end": 44.13,
            },
            {
                "speaker_id": "speaker_0",
                "role": "DOCTOR",
                "text": "well, let's try best.",
                "start": 45.36,
                "end": 45.41,
            },
            {
                "speaker_id": "speaker_0",
                "role": "DOCTOR",
                "text": "Let's try and get you",
                "start": 46.48,
                "end": 47.65,
            },
        ],
        model_name="nvidia/parakeet-tdt-0.6b-v3",
    )

    assert corrected_rows[1]["role"] == "PATIENT"
    assert corrected_rows[1]["text"] == "and I"
    assert corrected_rows[2]["role"] == "PATIENT"
    assert corrected_rows[2]["text"] == "just want you to do something."
    assert corrected_rows[3]["role"] == "DOCTOR"
    assert corrected_rows[3]["text"].startswith("Well, let's try our best.")


def test_build_corrected_segments_keeps_anchors_after_consumed_short_row() -> None:
    """A consumed short row should not collapse the visit into proportional source rows."""
    corrected_rows = build_corrected_segments(
        corrected_words=(
            "first anchor oh patient answer next doctor question"
        ).split(),
        live_segments=[
            {
                "speaker_id": "speaker_0",
                "role": "DOCTOR",
                "text": "first anchor oh",
                "start": 1.0,
                "end": 2.0,
            },
            {
                "speaker_id": "speaker_2",
                "role": "PATIENT",
                "text": "oh",
                "start": 2.1,
                "end": 2.2,
            },
            {
                "speaker_id": "speaker_2",
                "role": "PATIENT",
                "text": "patient answer",
                "start": 3.0,
                "end": 4.0,
            },
            {
                "speaker_id": "speaker_0",
                "role": "DOCTOR",
                "text": "next doctor question",
                "start": 5.0,
                "end": 6.0,
            },
        ],
        model_name="nvidia/parakeet-tdt-0.6b-v3",
    )

    assert corrected_rows[0]["text"] == "first anchor oh"
    assert corrected_rows[1]["role"] == "PATIENT"
    assert corrected_rows[1]["text"] == "oh"
    assert corrected_rows[2]["role"] == "PATIENT"
    assert corrected_rows[2]["text"] == "patient answer"
    assert corrected_rows[3]["role"] == "DOCTOR"
    assert corrected_rows[3]["text"] == "next doctor question"


def test_build_corrected_segments_keeps_patient_tail_before_doctor_prompt() -> None:
    """A tiny patient continuation should not start the next doctor source chip."""
    corrected_rows = build_corrected_segments(
        corrected_words=(
            "No but it's worse when I move okay is that when you move your neck"
        ).split(),
        live_segments=[
            {
                "speaker_id": "speaker_2",
                "role": "PATIENT",
                "text": "No, but it's worse when I",
                "start": 76.72,
                "end": 77.49,
            },
            {
                "speaker_id": "speaker_0",
                "role": "DOCTOR",
                "text": "Okay. Is that when you move",
                "start": 80.08,
                "end": 80.13,
            },
            {
                "speaker_id": "speaker_0",
                "role": "DOCTOR",
                "text": "your neck?",
                "start": 80.88,
                "end": 80.93,
            },
        ],
        model_name="nvidia/parakeet-tdt-0.6b-v3",
    )

    assert corrected_rows[0]["role"] == "PATIENT"
    assert corrected_rows[0]["text"] == "No but it's worse when I move"
    assert corrected_rows[1]["role"] == "DOCTOR"
    assert corrected_rows[1]["text"] == "okay is that when you move"
    assert corrected_rows[2]["role"] == "DOCTOR"
    assert corrected_rows[2]["text"] == "your neck"


def test_run_post_visit_correction_rejects_empty_audio() -> None:
    """A stopped visit without retained audio must fall back to live rows."""
    with pytest.raises(PostVisitCorrectionError, match="No retained audio"):
        run_post_visit_correction(
            pcm_audio=b"",
            live_segments=[],
            transcribe_audio_file=lambda _model, _path: "ignored",
        )


def test_run_post_visit_correction_uses_injected_transcriber() -> None:
    """The correction builder can be tested without loading GPU ASR models."""
    result = run_post_visit_correction(
        pcm_audio=(b"\0\0" * 160),
        live_segments=[
            {
                "speaker_id": "speaker_0",
                "role": "DOCTOR",
                "text": "hello there",
                "start": 0.0,
                "end": 1.0,
            }
        ],
        transcribe_audio_file=lambda _model, _path: "hello there",
    )

    assert result.word_count == 2
    assert result.segments[0]["text"] == "hello there"


def test_correction_endpoint_stores_corrected_rows_and_summary_prefers_them() -> None:
    """A post-stop correction artifact becomes the source for summaries."""
    _seed_stopped_session_with_audio()
    sessions.append_segment(
        TEST_SESSION_ID,
        {
            "speaker_id": "speaker_0",
            "role": "PATIENT",
            "text": "preview typo",
            "start": 0.0,
            "end": 1.0,
            "segment_id": "live-0001",
        },
    )

    with patch(
        "api.server.run_post_visit_correction",
        return_value=api_server.run_post_visit_correction(
            pcm_audio=(b"\0\0" * 160),
            live_segments=sessions.get_segments(TEST_SESSION_ID),
            transcribe_audio_file=lambda _model, _path: "corrected doctor text",
        ),
    ):
        client = TestClient(app, raise_server_exceptions=False)
        correction_response = client.post(
            f"/session/{TEST_SESSION_ID}/correction",
            json={"segments": sessions.get_segments(TEST_SESSION_ID)},
        )

    assert correction_response.status_code == 200
    correction_payload = correction_response.json()
    assert correction_payload["status"] == "ready"
    corrected_rows = sessions.get_corrected_segments(TEST_SESSION_ID)
    assert corrected_rows[0]["text"] == "corrected doctor text"

    with patch(
        "api.server._run_summary_generation",
        return_value={"title": "Corrected", "sections": [], "key_points": []},
    ) as summary_runner:
        summary_response = client.post(f"/session/{TEST_SESSION_ID}/summary")

    assert summary_response.status_code == 200
    assert "[PATIENT] corrected doctor text" in summary_runner.call_args.args[1]
    assert summary_runner.call_args.args[2][0]["segment_id"] == "corrected-0001"


def test_correction_endpoint_reuses_existing_corrected_rows() -> None:
    """Retrying Summarise should not rerun ASR once corrected rows exist."""
    sessions.replace_corrected_segments(
        TEST_SESSION_ID,
        [
            {
                "speaker_id": "speaker_0",
                "role": "DOCTOR",
                "text": "already corrected",
                "start": 0.0,
                "end": 1.0,
                "segment_id": "corrected-0001",
                "source_model": "test-model",
            }
        ],
    )

    client = TestClient(app, raise_server_exceptions=False)
    response = client.post(f"/session/{TEST_SESSION_ID}/correction")

    assert response.status_code == 200
    assert response.json()["reused"] is True
    assert response.json()["model"] == "test-model"


def test_corrected_transcript_endpoint_returns_scoreable_rows() -> None:
    """Local QA can fetch the exact corrected rows used for the note."""
    sessions.replace_corrected_segments(
        TEST_SESSION_ID,
        [
            {
                "speaker_id": "speaker_0",
                "role": "DOCTOR",
                "text": "already corrected",
                "start": 0.0,
                "end": 1.0,
                "segment_id": "corrected-0001",
                "source": "post_visit_correction",
                "source_model": "test-model",
            }
        ],
    )

    client = TestClient(app, raise_server_exceptions=False)
    response = client.get(f"/session/{TEST_SESSION_ID}/corrected-transcript")

    assert response.status_code == 200
    payload = response.json()
    assert payload["source"] == "corrected_segments"
    assert payload["duration_seconds"] == 1.0
    assert payload["segments"][0]["segment_id"] == "corrected-0001"
    assert payload["segments"][0]["text"] == "already corrected"


def test_corrected_transcript_endpoint_returns_empty_artifact_before_correction() -> None:
    """Before correction runs, QA gets an empty artifact rather than an error."""
    client = TestClient(app, raise_server_exceptions=False)
    response = client.get(f"/session/{TEST_SESSION_ID}/corrected-transcript")

    assert response.status_code == 200
    payload = response.json()
    assert payload["source"] == "missing"
    assert payload["segments"] == []
    assert payload["message"] == "Corrected transcript not found"


def test_correction_endpoint_falls_back_when_audio_is_missing() -> None:
    """Expired session audio should not block a live-preview summary."""
    sessions.append_segment(
        TEST_SESSION_ID,
        {
            "speaker_id": "speaker_1",
            "role": "PATIENT",
            "text": "live text",
            "start": 0.0,
            "end": 1.0,
            "segment_id": "live-0001",
        },
    )

    client = TestClient(app, raise_server_exceptions=False)
    response = client.post(f"/session/{TEST_SESSION_ID}/correction")

    assert response.status_code == 200
    assert response.json()["status"] == "unavailable"
    assert response.json()["source"] == "live_segments"
    assert sessions.get_corrected_segments(TEST_SESSION_ID) == []


def _seed_stopped_session_with_audio() -> None:
    """Register a session with retained PCM like the post-finalize grace window."""
    transcription_session = TranscriptionSession(
        TEST_SESSION_ID,
        pipeline=NemoPipeline(),
        input_format="pcm",
    )
    transcription_session.buffer.append(b"\0\0" * 160)
    asyncio.run(lifecycle.register(TEST_SESSION_ID, transcription_session))
