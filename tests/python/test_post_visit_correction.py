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
from corrected_role_cues import prepare_corrected_source_segments
from post_visit_correction import (
    PostVisitCorrectionError,
    PostVisitTranscription,
    build_corrected_segments,
    run_post_visit_correction,
)

TEST_SESSION_ID = "00000000-0000-4000-8000-000000000301"


def identity_echo_live_rows() -> list[dict[str, object]]:
    """Return the consult-08 identity/echo scaffold: prompt, identity row, next doctor turn."""
    return [
        {
            "speaker_id": "speaker_2",
            "role": "DOCTOR",
            "text": "age, please?",
            "start": 11.6,
            "end": 11.65,
        },
        {
            "speaker_id": "speaker_2",
            "role": "DOCTOR",
            "text": "My name's Python and I'm 26. 26, okay.",
            "start": 14.0,
            "end": 15.89,
        },
        {
            "speaker_id": "speaker_2",
            "role": "DOCTOR",
            "text": "And how can I help",
            "start": 17.36,
            "end": 18.13,
        },
    ]


def identity_echo_words_and_timings(
    echo_start: float = 15.92,
    ack_end: float = 16.56,
) -> tuple[list[str], list[dict[str, object]]]:
    """Return the second-pass words plus per-word timings around the age echo.

    Args:
        echo_start: Estimated start of the doctor's echoed `26,`; the split boundary under test.
        ack_end: Estimated end of the trailing `okay.`; past the live row end in the real probe.

    Returns:
        `(corrected_words, word_timings)` aligned index by index, mirroring the probe artifact.
    """
    spans = [
        ("age,", 11.0, 11.3),
        ("please?", 11.3, 11.65),
        ("My", 13.44, 13.6),
        ("name's", 13.6, 13.76),
        ("Python", 13.92, 15.12),
        ("and", 15.12, 15.36),
        ("I'm", 15.36, 15.52),
        ("26.", 15.68, 15.76),
        ("26,", echo_start, max(echo_start, ack_end - 0.24)),
        ("okay.", max(echo_start, ack_end - 0.24), ack_end),
        ("And", 16.72, 16.88),
        ("how", 16.96, 17.04),
        ("can", 17.04, 17.2),
        ("I", 17.28, 17.44),
        ("help", 17.44, 17.6),
    ]
    words = [word for word, _start, _end in spans]
    timings = [{"word": word, "start": start, "end": end} for word, start, end in spans]
    return words, timings


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


def test_build_corrected_segments_splits_identity_echo_with_word_timings() -> None:
    """With trustworthy word timing, the age echo becomes a separate Doctor source chip."""
    corrected_words, word_timings = identity_echo_words_and_timings()
    corrected_rows = build_corrected_segments(
        corrected_words=corrected_words,
        live_segments=identity_echo_live_rows(),
        model_name="nvidia/parakeet-tdt-0.6b-v3",
        word_timings=word_timings,
    )

    assert [row["segment_id"] for row in corrected_rows] == [
        "corrected-0001",
        "corrected-0002-01",
        "corrected-0002-02",
        "corrected-0003",
    ]
    identity_part = corrected_rows[1]
    echo_part = corrected_rows[2]
    assert identity_part["role"] == "PATIENT"
    assert identity_part["text"] == "My name's Python and I'm 26."
    assert identity_part["start"] == 14.0
    assert identity_part["end"] == 15.76
    assert echo_part["role"] == "DOCTOR"
    assert echo_part["text"] == "26, okay."
    assert echo_part["start"] == 15.92
    assert echo_part["end"] == 16.56
    assert echo_part["role_source"] == "post_visit_alignment"
    assert corrected_rows[3]["start"] == 17.36
    assert set(echo_part.keys()) == set(corrected_rows[3].keys()) | {"role_source"}


def test_identity_echo_split_requires_positive_word_gap() -> None:
    """Continuous word timing means one voice, so the identity row must stay whole."""
    corrected_words, word_timings = identity_echo_words_and_timings(echo_start=15.76)
    corrected_rows = build_corrected_segments(
        corrected_words=corrected_words,
        live_segments=identity_echo_live_rows(),
        model_name="nvidia/parakeet-tdt-0.6b-v3",
        word_timings=word_timings,
    )

    assert [row["segment_id"] for row in corrected_rows] == [
        "corrected-0001",
        "corrected-0002",
        "corrected-0003",
    ]
    assert corrected_rows[1]["text"] == "My name's Python and I'm 26. 26, okay."


def test_identity_echo_split_requires_tail_to_reach_row_end() -> None:
    """A timed echo ending before the live row end is drift, so the row stays whole."""
    corrected_words, word_timings = identity_echo_words_and_timings(
        echo_start=15.78,
        ack_end=15.85,
    )
    corrected_rows = build_corrected_segments(
        corrected_words=corrected_words,
        live_segments=identity_echo_live_rows(),
        model_name="nvidia/parakeet-tdt-0.6b-v3",
        word_timings=word_timings,
    )

    assert [row["segment_id"] for row in corrected_rows] == [
        "corrected-0001",
        "corrected-0002",
        "corrected-0003",
    ]
    assert corrected_rows[1]["text"] == "My name's Python and I'm 26. 26, okay."


def test_identity_echo_split_skips_when_next_row_collides() -> None:
    """An echo span that overruns the next visible row keeps the safer unsplit row."""
    corrected_words, word_timings = identity_echo_words_and_timings(ack_end=17.5)
    corrected_rows = build_corrected_segments(
        corrected_words=corrected_words,
        live_segments=identity_echo_live_rows(),
        model_name="nvidia/parakeet-tdt-0.6b-v3",
        word_timings=word_timings,
    )

    assert [row["segment_id"] for row in corrected_rows] == [
        "corrected-0001",
        "corrected-0002",
        "corrected-0003",
    ]


def test_build_corrected_segments_splits_word_echo_with_word_timings() -> None:
    """The clinician's word echo ('twice. Twice, okay.') becomes its own Doctor chip."""
    corrected_words = "have you noticed any cracked skin? Yeah. I vomited twice. Twice, okay.".split()
    word_timings = [
        {"word": "have", "start": 155.0, "end": 155.2},
        {"word": "you", "start": 155.2, "end": 155.4},
        {"word": "noticed", "start": 155.4, "end": 155.8},
        {"word": "any", "start": 155.8, "end": 156.0},
        {"word": "cracked", "start": 156.0, "end": 156.4},
        {"word": "skin?", "start": 156.4, "end": 156.8},
        {"word": "Yeah.", "start": 160.32, "end": 160.96},
        {"word": "I", "start": 162.2, "end": 162.24},
        {"word": "vomited", "start": 162.24, "end": 162.88},
        {"word": "twice.", "start": 162.88, "end": 163.36},
        {"word": "Twice,", "start": 163.6, "end": 164.08},
        {"word": "okay.", "start": 164.32, "end": 164.64},
    ]
    corrected_rows = build_corrected_segments(
        corrected_words=corrected_words,
        live_segments=[
            {
                "speaker_id": "speaker_1",
                "role": "DOCTOR",
                "text": "have you noticed any cracked skin?",
                "start": 154.9,
                "end": 156.9,
            },
            {
                "speaker_id": "speaker_1",
                "role": "DOCTOR",
                "text": "Yeah. I vomited twice. Twice, okay.",
                "start": 162.2,
                "end": 163.4,
            },
        ],
        model_name="nvidia/parakeet-tdt-0.6b-v3",
        word_timings=word_timings,
    )

    assert [row["segment_id"] for row in corrected_rows] == [
        "corrected-0001",
        "corrected-0002-01",
        "corrected-0002-02",
    ]
    patient_part = corrected_rows[1]
    echo_part = corrected_rows[2]
    assert patient_part["role"] == "PATIENT"
    assert patient_part["text"] == "Yeah. I vomited twice."
    assert patient_part["end"] == 163.36
    assert echo_part["role"] == "DOCTOR"
    assert echo_part["text"] == "Twice, okay."
    assert echo_part["start"] == 163.6
    assert echo_part["end"] == 164.64


def test_word_echo_split_requires_patient_cue_in_first_part() -> None:
    """A word echo without patient evidence before it must stay one row."""
    corrected_words = "That was twice. Twice, okay.".split()
    word_timings = [
        {"word": "That", "start": 10.0, "end": 10.2},
        {"word": "was", "start": 10.2, "end": 10.4},
        {"word": "twice.", "start": 10.4, "end": 10.8},
        {"word": "Twice,", "start": 11.0, "end": 11.4},
        {"word": "okay.", "start": 11.5, "end": 11.9},
    ]
    corrected_rows = build_corrected_segments(
        corrected_words=corrected_words,
        live_segments=[
            {
                "speaker_id": "speaker_1",
                "role": "DOCTOR",
                "text": "That was twice. Twice, okay.",
                "start": 10.0,
                "end": 11.0,
            },
        ],
        model_name="nvidia/parakeet-tdt-0.6b-v3",
        word_timings=word_timings,
    )

    assert [row["segment_id"] for row in corrected_rows] == ["corrected-0001"]


def test_word_echo_split_ignores_filler_and_ack_duplicates() -> None:
    """'Yeah. Yeah, okay.' duplicates are conversation noise, never a split point."""
    corrected_words = "I vomited yesterday. Yeah. Yeah, okay.".split()
    word_timings = [
        {"word": "I", "start": 10.0, "end": 10.1},
        {"word": "vomited", "start": 10.1, "end": 10.5},
        {"word": "yesterday.", "start": 10.5, "end": 11.0},
        {"word": "Yeah.", "start": 11.1, "end": 11.4},
        {"word": "Yeah,", "start": 11.6, "end": 11.9},
        {"word": "okay.", "start": 12.0, "end": 12.3},
    ]
    corrected_rows = build_corrected_segments(
        corrected_words=corrected_words,
        live_segments=[
            {
                "speaker_id": "speaker_1",
                "role": "DOCTOR",
                "text": "I vomited yesterday. Yeah. Yeah, okay.",
                "start": 10.0,
                "end": 12.0,
            },
        ],
        model_name="nvidia/parakeet-tdt-0.6b-v3",
        word_timings=word_timings,
    )

    assert [row["segment_id"] for row in corrected_rows] == ["corrected-0001"]


def test_identity_echo_split_requires_row_words_in_stream() -> None:
    """Live-fallback rows have no ASR word timing, so they must never split."""
    corrected_words = "completely different second pass words here".split()
    word_timings = [
        {"word": word, "start": float(index), "end": float(index) + 0.5}
        for index, word in enumerate(corrected_words)
    ]
    corrected_rows = prepare_corrected_source_segments(
        [
            {
                "segment_id": "corrected-0002",
                "speaker_id": "speaker_2",
                "role": "DOCTOR",
                "text": "My name's Python and I'm 26. 26, okay.",
                "start": 14.0,
                "end": 15.89,
                "is_interim": False,
                "source": "post_visit_correction",
                "source_model": "nvidia/parakeet-tdt-0.6b-v3",
            }
        ],
        corrected_words=corrected_words,
        word_timings=word_timings,
    )

    assert [row["segment_id"] for row in corrected_rows] == ["corrected-0002"]
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


def test_run_post_visit_correction_splits_echo_when_transcriber_returns_timings() -> None:
    """A timing-aware second pass turns the age echo into its own Doctor chip end to end."""
    corrected_words, word_timings = identity_echo_words_and_timings()
    transcription = PostVisitTranscription(
        text=" ".join(corrected_words),
        word_timings=word_timings,
    )
    result = run_post_visit_correction(
        pcm_audio=(b"\0\0" * 160),
        live_segments=identity_echo_live_rows(),
        transcribe_audio_file=lambda _model, _path: transcription,
    )

    assert [row["segment_id"] for row in result.segments] == [
        "corrected-0001",
        "corrected-0002-01",
        "corrected-0002-02",
        "corrected-0003",
    ]
    assert result.segments[2]["role"] == "DOCTOR"
    assert result.segments[2]["text"] == "26, okay."


def test_run_post_visit_correction_drops_misaligned_word_timings() -> None:
    """Timing rows that disagree with the transcript words are ignored, never trusted."""
    corrected_words, word_timings = identity_echo_words_and_timings()
    transcription = PostVisitTranscription(
        text=" ".join(corrected_words),
        word_timings=word_timings[:-1],
    )
    result = run_post_visit_correction(
        pcm_audio=(b"\0\0" * 160),
        live_segments=identity_echo_live_rows(),
        transcribe_audio_file=lambda _model, _path: transcription,
    )

    assert [row["segment_id"] for row in result.segments] == [
        "corrected-0001",
        "corrected-0002",
        "corrected-0003",
    ]


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
