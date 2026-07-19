"""Protect the corrected transcript used for the clinician's final note.

These tests cover second-pass ASR, row alignment, GPU failure recovery, and
the correction API. They keep healthy visits unchanged while ensuring a failed
correction falls back visibly instead of blocking the user's summary.
"""

from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
import json
import logging
from pathlib import Path
import threading
import time
from types import SimpleNamespace
from unittest.mock import patch

import httpx
import numpy as np
import pytest
import soundfile
from fastapi.testclient import TestClient

import api.server as api_server
import post_visit_correction as correction_module
from api import source_integrity
from api.server import app, lifecycle, sessions
from corrected_role_cues import apply_role_cue_cleanup, infer_role_from_corrected_text
from nemo_pipeline import NemoPipeline
from nemo_session import TranscriptionSession
from corrected_role_cues import prepare_corrected_source_segments
from post_visit_correction import (
    PostVisitCorrectionError,
    PostVisitCorrectionResult,
    PostVisitTranscription,
    build_corrected_segments,
    run_post_visit_correction,
)

TEST_SESSION_ID = "00000000-0000-4000-8000-000000000301"


class StubPostVisitAsrModel:
    """Return prepared ASR results without loading NeMo or using the GPU.

    Tests use this model to reproduce what happens after the user stops a
    visit. Recorded paths prove whether the correction stayed one-shot or was
    divided into bounded chunks.
    """

    def __init__(self, prepared_results: list[object]) -> None:
        """Store one result or exception for each expected transcribe call."""
        self.prepared_results = list(prepared_results)
        self.transcribed_audio_paths: list[str] = []

    def transcribe(self, audio_paths: list[str], **_options: object) -> list[object]:
        """Return the next prepared result for the audio path shown to the model."""
        self.transcribed_audio_paths.append(audio_paths[0])
        prepared_result = self.prepared_results.pop(0)
        # A prepared exception represents the GPU/model failure seen by the user.
        if isinstance(prepared_result, Exception):
            raise prepared_result

        return [SimpleNamespace(text=str(prepared_result))]


def _write_silent_test_wav(audio_path: Path, duration_seconds: float) -> None:
    """Write a small 16 kHz WAV that follows the browser's retained-audio contract."""
    sample_count = int(16000 * duration_seconds)
    soundfile.write(audio_path, np.zeros(sample_count, dtype=np.float32), 16000)


def _stub_post_visit_model_dependencies(
    monkeypatch: pytest.MonkeyPatch,
    asr_model: StubPostVisitAsrModel,
) -> None:
    """Route correction through the prepared model and deterministic evidence."""
    monkeypatch.setattr(
        correction_module, "_load_post_visit_asr_model", lambda _name: asr_model
    )
    monkeypatch.setattr(
        correction_module, "enable_word_confidence_decoding", lambda _model: None
    )
    monkeypatch.setattr(
        correction_module,
        "word_timings_from_hypothesis",
        lambda hypothesis, _duration: [
            {"word": hypothesis.text, "start": 0.1, "end": 0.2}
        ],
    )
    monkeypatch.setattr(
        correction_module,
        "word_confidences_for_display_words",
        lambda _hypothesis, words: [0.9] * len(words),
    )


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
    executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="test-correction")
    original_executor = api_server.nemo_executor
    original_correction_single_flight = getattr(
        api_server,
        "correction_single_flight",
        None,
    )
    api_server.nemo_executor = executor
    api_server.correction_single_flight = asyncio.Semaphore(1)
    sessions._sessions.clear()
    lifecycle.clear()
    api_server._mercure_event_ids.clear()
    # Terminal attestations belong to one visit; stale ones would let a test
    # summarize another test's "finalized" transcript.
    source_integrity._terminal_watermarks.clear()
    app.state.nemo_pipeline = NemoPipeline()
    app.state.nemo_input_format = "pcm"
    app.state.http_client = httpx.AsyncClient(timeout=5.0)

    yield

    sessions._sessions.clear()
    lifecycle.clear()
    api_server._mercure_event_ids.clear()
    source_integrity._terminal_watermarks.clear()
    executor.shutdown(wait=False, cancel_futures=True)
    api_server.nemo_executor = original_executor
    # Older server code has no correction guard, so the failing test restores that shape.
    if original_correction_single_flight is None:
        del api_server.correction_single_flight
    else:
        api_server.correction_single_flight = original_correction_single_flight


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
        model_name="nvidia/parakeet-unified-en-0.6b",
    )

    assert [row["role"] for row in corrected_rows] == ["DOCTOR", "PATIENT"]
    assert corrected_rows[0]["segment_id"] == "corrected-0001"
    assert corrected_rows[1]["source"] == "post_visit_correction"
    assert corrected_rows[1]["source_model"] == "nvidia/parakeet-unified-en-0.6b"
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
        model_name="nvidia/parakeet-unified-en-0.6b",
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
        model_name="nvidia/parakeet-unified-en-0.6b",
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
        model_name="nvidia/parakeet-unified-en-0.6b",
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
        model_name="nvidia/parakeet-unified-en-0.6b",
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
        model_name="nvidia/parakeet-unified-en-0.6b",
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
        model_name="nvidia/parakeet-unified-en-0.6b",
    )

    assert [row["role"] for row in corrected_rows] == ["DOCTOR", "DOCTOR", "PATIENT"]
    assert corrected_rows[1]["role_source"] == "post_visit_alignment"


def test_build_corrected_segments_keeps_identity_echo_unsplit_without_word_timing() -> (
    None
):
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
        model_name="nvidia/parakeet-unified-en-0.6b",
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
        model_name="nvidia/parakeet-unified-en-0.6b",
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
        model_name="nvidia/parakeet-unified-en-0.6b",
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
        model_name="nvidia/parakeet-unified-en-0.6b",
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
        model_name="nvidia/parakeet-unified-en-0.6b",
        word_timings=word_timings,
    )

    assert [row["segment_id"] for row in corrected_rows] == [
        "corrected-0001",
        "corrected-0002",
        "corrected-0003",
    ]


def test_build_corrected_segments_splits_word_echo_with_word_timings() -> None:
    """The clinician's word echo ('twice. Twice, okay.') becomes its own Doctor chip."""
    corrected_words = (
        "have you noticed any cracked skin? Yeah. I vomited twice. Twice, okay.".split()
    )
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
        model_name="nvidia/parakeet-unified-en-0.6b",
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
        model_name="nvidia/parakeet-unified-en-0.6b",
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
        model_name="nvidia/parakeet-unified-en-0.6b",
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
                "source_model": "nvidia/parakeet-unified-en-0.6b",
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
        corrected_words=(
            "Have you had difficulty with bright lights? Yeah, well,"
        ).split(),
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
        model_name="nvidia/parakeet-unified-en-0.6b",
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


def test_build_corrected_segments_preserves_live_row_when_asr_drops_patient_text() -> (
    None
):
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
        model_name="nvidia/parakeet-unified-en-0.6b",
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
        corrected_words=("first anchor oh patient answer next doctor question").split(),
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
        model_name="nvidia/parakeet-unified-en-0.6b",
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
        model_name="nvidia/parakeet-unified-en-0.6b",
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


def test_run_post_visit_correction_splits_echo_when_transcriber_returns_timings() -> (
    None
):
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


def test_short_visit_uses_the_original_audio_path_once(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A normal short visit keeps the exact one-shot model path used before M09."""
    audio_path = tmp_path / "short-visit.wav"
    _write_silent_test_wav(audio_path, 0.5)
    asr_model = StubPostVisitAsrModel(["short"])
    _stub_post_visit_model_dependencies(monkeypatch, asr_model)
    monkeypatch.setattr(correction_module, "_ONE_SHOT_MAX_AUDIO_SECONDS", 1.0)

    transcription = correction_module.transcribe_audio_with_nemo(
        "test-model", str(audio_path)
    )

    assert transcription.text == "short"
    assert transcription.attempts == 1
    assert transcription.retried is False
    assert transcription.chunk_count == 1
    assert asr_model.transcribed_audio_paths == [str(audio_path)]
    assert audio_path.exists()


def test_long_visit_uses_ordered_chunks_on_one_model(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A capacity-risk visit is recombined in order without retaining scratch WAVs."""
    audio_path = tmp_path / "long-visit.wav"
    # A whole-multiple duration keeps three full chunks with no trailing sliver.
    _write_silent_test_wav(audio_path, 3.0)
    asr_model = StubPostVisitAsrModel(["first", "second", "third"])
    _stub_post_visit_model_dependencies(monkeypatch, asr_model)
    monkeypatch.setattr(correction_module, "_ONE_SHOT_MAX_AUDIO_SECONDS", 1.0)
    monkeypatch.setattr(correction_module, "_AUDIO_CHUNK_SECONDS", 1.0)
    # Scale the sliver-fold threshold with the 1.0s test chunks so slicing stays exact.
    monkeypatch.setattr(correction_module, "_MIN_FINAL_CHUNK_SECONDS", 0.6)

    transcription = correction_module.transcribe_audio_with_nemo(
        "test-model", str(audio_path)
    )

    assert transcription.text == "first second third"
    assert transcription.word_timings == [
        {"word": "first", "start": 0.1, "end": 0.2},
        {"word": "second", "start": 1.1, "end": 1.2},
        {"word": "third", "start": 2.1, "end": 2.2},
    ]
    assert transcription.word_confidences == [0.9, 0.9, 0.9]
    assert transcription.chunk_count == 3
    assert len(asr_model.transcribed_audio_paths) == 3
    assert all(
        Path(chunk_path) != audio_path
        for chunk_path in asr_model.transcribed_audio_paths
    )
    assert all(
        not Path(chunk_path).exists()
        for chunk_path in asr_model.transcribed_audio_paths
    )


def test_long_visit_removes_scratch_chunks_after_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A failed long correction leaves no temporary patient audio on disk."""
    audio_path = tmp_path / "long-failed-visit.wav"
    # A whole-multiple duration keeps full chunks so the failure is not sliver-related.
    _write_silent_test_wav(audio_path, 3.0)
    asr_model = StubPostVisitAsrModel(["first", RuntimeError("CUDA out of memory")])
    _stub_post_visit_model_dependencies(monkeypatch, asr_model)
    monkeypatch.setattr(correction_module, "_ONE_SHOT_MAX_AUDIO_SECONDS", 1.0)
    monkeypatch.setattr(correction_module, "_AUDIO_CHUNK_SECONDS", 1.0)
    # Scale the sliver-fold threshold with the 1.0s test chunks so slicing stays exact.
    monkeypatch.setattr(correction_module, "_MIN_FINAL_CHUNK_SECONDS", 0.6)

    with pytest.raises(PostVisitCorrectionError):
        correction_module.transcribe_audio_with_nemo("test-model", str(audio_path))

    assert len(asr_model.transcribed_audio_paths) == 2
    assert all(
        not Path(chunk_path).exists()
        for chunk_path in asr_model.transcribed_audio_paths
    )
    assert audio_path.exists()


class _ChunkSizeRecordingAsrModel(StubPostVisitAsrModel):
    """Record each chunk WAV's sample count before its scratch file is deleted.

    Sliver-fold tests use this to prove the trailing seconds of a visit ride
    inside the previous chunk rather than becoming a separately failable file.
    """

    def __init__(self, prepared_results: list[object]) -> None:
        """Track chunk sizes alongside the prepared per-chunk results."""
        super().__init__(prepared_results)
        self.transcribed_frame_counts: list[int] = []

    def transcribe(self, audio_paths: list[str], **options: object) -> list[object]:
        """Measure the chunk the user's note is built from, then delegate."""
        self.transcribed_frame_counts.append(soundfile.info(audio_paths[0]).frames)
        return super().transcribe(audio_paths, **options)


def test_tiny_final_sliver_folds_into_previous_chunk(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A sub-threshold trailing sliver must not become its own failable chunk.

    Field failure 2026-07-11: a 541.56s visit chunked as 180/180/180/1.56s and
    the 1.56s tail decoded empty, discarding an otherwise healthy correction.
    """
    audio_path = tmp_path / "sliver-tail-visit.wav"
    # 2.5s with 1.0s chunks reproduces the field shape 180/180/180/1.56 at test scale.
    _write_silent_test_wav(audio_path, 2.5)
    asr_model = _ChunkSizeRecordingAsrModel(["first", "second tail", "unused spare"])
    _stub_post_visit_model_dependencies(monkeypatch, asr_model)
    monkeypatch.setattr(correction_module, "_ONE_SHOT_MAX_AUDIO_SECONDS", 1.0)
    monkeypatch.setattr(correction_module, "_AUDIO_CHUNK_SECONDS", 1.0)
    # raising=False keeps this red test runnable before the fold constant exists.
    monkeypatch.setattr(
        correction_module, "_MIN_FINAL_CHUNK_SECONDS", 0.6, raising=False
    )

    transcription = correction_module.transcribe_audio_with_nemo(
        "test-model", str(audio_path)
    )

    # The 0.5s remainder rides with the second chunk instead of risking an empty veto.
    assert transcription.chunk_count == 2
    assert len(asr_model.transcribed_audio_paths) == 2
    assert asr_model.transcribed_frame_counts == [16000, 24000]
    assert transcription.text == "first second tail"


def test_full_chunk_empty_decode_still_falls_back(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A full-size chunk decoding empty still fails loudly to the live transcript."""
    audio_path = tmp_path / "empty-middle-visit.wav"
    _write_silent_test_wav(audio_path, 3.0)
    asr_model = StubPostVisitAsrModel(["first", "", "third"])
    _stub_post_visit_model_dependencies(monkeypatch, asr_model)
    monkeypatch.setattr(correction_module, "_ONE_SHOT_MAX_AUDIO_SECONDS", 1.0)
    monkeypatch.setattr(correction_module, "_AUDIO_CHUNK_SECONDS", 1.0)
    # Scale the sliver-fold threshold with the 1.0s test chunks so slicing stays exact.
    monkeypatch.setattr(correction_module, "_MIN_FINAL_CHUNK_SECONDS", 0.6)

    with pytest.raises(PostVisitCorrectionError) as raised_error:
        correction_module.transcribe_audio_with_nemo("test-model", str(audio_path))

    # The user keeps the live-transcript fallback with the safe category, never partial rows.
    assert raised_error.value.reason_category == "empty_result"
    assert raised_error.value.failed_chunk_index == 2
    assert raised_error.value.chunk_count_planned == 3


def test_device_not_ready_retries_once_on_the_same_model(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """The observed transient gets one reclaimed retry without reloading the model."""
    audio_path = tmp_path / "transient-visit.wav"
    _write_silent_test_wav(audio_path, 0.5)
    asr_model = StubPostVisitAsrModel(
        [RuntimeError("CUDA driver error: device not ready"), "recovered"]
    )
    _stub_post_visit_model_dependencies(monkeypatch, asr_model)
    reclaimed_attempts: list[bool] = []
    backoffs: list[float] = []
    monkeypatch.setattr(
        correction_module,
        "_reclaim_cuda_memory",
        lambda: reclaimed_attempts.append(True),
    )
    monkeypatch.setattr(correction_module.time, "sleep", backoffs.append)

    transcription = correction_module.transcribe_audio_with_nemo(
        "test-model", str(audio_path)
    )

    assert transcription.text == "recovered"
    assert transcription.attempts == 2
    assert transcription.retried is True
    assert len(asr_model.transcribed_audio_paths) == 2
    assert reclaimed_attempts == [True]
    assert backoffs == [correction_module._TRANSIENT_RETRY_BACKOFF_SECONDS]


@pytest.mark.parametrize(
    "fatal_error",
    [
        RuntimeError("CUDA out of memory"),
        RuntimeError("CUDA error: an illegal memory access was encountered"),
    ],
)
def test_fatal_cuda_failure_never_retries(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    fatal_error: RuntimeError,
) -> None:
    """Capacity and corrupted-device failures fall back after one model call."""
    audio_path = tmp_path / "fatal-visit.wav"
    _write_silent_test_wav(audio_path, 0.5)
    asr_model = StubPostVisitAsrModel([fatal_error])
    _stub_post_visit_model_dependencies(monkeypatch, asr_model)
    reclaimed_attempts: list[bool] = []
    monkeypatch.setattr(
        correction_module,
        "_reclaim_cuda_memory",
        lambda: reclaimed_attempts.append(True),
    )

    with pytest.raises(PostVisitCorrectionError) as raised_error:
        correction_module.transcribe_audio_with_nemo("test-model", str(audio_path))

    assert raised_error.value.attempts == 1
    assert raised_error.value.retried is False
    assert len(asr_model.transcribed_audio_paths) == 1
    assert reclaimed_attempts == []


def test_second_device_not_ready_returns_two_attempt_metadata(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Two transient failures stop after the promised retry and explain the fallback."""
    audio_path = tmp_path / "repeated-transient.wav"
    _write_silent_test_wav(audio_path, 0.5)
    asr_model = StubPostVisitAsrModel(
        [
            RuntimeError("CUDA driver error: device not ready"),
            RuntimeError("cuda DRIVER error: DEVICE NOT READY"),
        ]
    )
    _stub_post_visit_model_dependencies(monkeypatch, asr_model)
    monkeypatch.setattr(correction_module, "_reclaim_cuda_memory", lambda: None)
    monkeypatch.setattr(correction_module.time, "sleep", lambda _seconds: None)

    with pytest.raises(PostVisitCorrectionError) as raised_error:
        correction_module.transcribe_audio_with_nemo("test-model", str(audio_path))

    assert raised_error.value.attempts == 2
    assert raised_error.value.retried is True
    assert raised_error.value.reason_category == "gpu_transient"
    assert len(asr_model.transcribed_audio_paths) == 2


def test_model_restore_failure_never_retries(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A model that cannot load falls back before the user waits for a retry."""
    audio_path = tmp_path / "model-load-failure.wav"
    _write_silent_test_wav(audio_path, 0.5)
    reclaimed_attempts: list[bool] = []

    def fail_model_restore(_model_name: str) -> object:
        """Reproduce a correction checkpoint that cannot be restored for this visit."""
        raise PostVisitCorrectionError(
            "Correction model could not load.",
            reason_category="model_load_failed",
        )

    monkeypatch.setattr(
        correction_module, "_load_post_visit_asr_model", fail_model_restore
    )
    monkeypatch.setattr(
        correction_module,
        "_reclaim_cuda_memory",
        lambda: reclaimed_attempts.append(True),
    )

    with pytest.raises(PostVisitCorrectionError) as raised_error:
        correction_module.transcribe_audio_with_nemo("test-model", str(audio_path))

    assert raised_error.value.attempts == 0
    assert raised_error.value.retried is False
    assert raised_error.value.reason_category == "model_load_failed"
    assert reclaimed_attempts == []


def test_cached_cuda_memory_is_released_before_model_restore(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A long streamed visit clears stale correction cache before loading its model."""
    audio_path = tmp_path / "post-stream-visit.wav"
    _write_silent_test_wav(audio_path, 0.5)
    asr_model = StubPostVisitAsrModel(["corrected text"])
    model_restore_steps: list[str] = []
    _stub_post_visit_model_dependencies(monkeypatch, asr_model)

    def load_model_after_cache_release(_model_name: str) -> StubPostVisitAsrModel:
        """Record when the correction checkpoint begins loading for the user's note."""
        model_restore_steps.append("load_model")
        return asr_model

    monkeypatch.setattr(
        correction_module,
        "_release_cached_cuda_memory_before_model_restore",
        lambda: model_restore_steps.append("release_cache"),
        raising=False,
    )
    monkeypatch.setattr(
        correction_module,
        "_load_post_visit_asr_model",
        load_model_after_cache_release,
    )

    correction_module.transcribe_audio_with_nemo("test-model", str(audio_path))

    assert model_restore_steps == ["release_cache", "load_model"]


def test_healthy_short_correction_keeps_pre_m09_rows_byte_identical(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A healthy short visit produces the same stored rows users received before M09."""
    retained_audio = b"\0\0" * 160
    live_segments = [
        {
            "speaker_id": "speaker_0",
            "role": "PATIENT",
            "text": "preview typo",
            "start": 0.0,
            "end": 1.0,
            "segment_id": "live-0001",
        }
    ]
    baseline_result = run_post_visit_correction(
        pcm_audio=retained_audio,
        live_segments=live_segments,
        model_name="test-model",
        transcribe_audio_file=lambda _model, _path: "corrected patient text",
    )
    asr_model = StubPostVisitAsrModel(["corrected patient text"])
    monkeypatch.setattr(
        correction_module, "_load_post_visit_asr_model", lambda _name: asr_model
    )
    monkeypatch.setattr(
        correction_module, "enable_word_confidence_decoding", lambda _model: None
    )
    monkeypatch.setattr(
        correction_module, "word_timings_from_hypothesis", lambda *_args: None
    )
    monkeypatch.setattr(
        correction_module,
        "word_confidences_for_display_words",
        lambda *_args: None,
    )

    current_result = run_post_visit_correction(
        pcm_audio=retained_audio,
        live_segments=live_segments,
        model_name="test-model",
    )

    assert current_result.segments == baseline_result.segments
    assert current_result.word_count == baseline_result.word_count
    assert len(asr_model.transcribed_audio_paths) == 1


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
    _attest_terminal_visit()

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
    assert correction_payload["attempted"] is True
    assert correction_payload["attempts"] == 1
    assert correction_payload["retried"] is False
    assert correction_payload["chunk_count"] == 1
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
    _attest_terminal_visit(corrected_attested=True)

    client = TestClient(app, raise_server_exceptions=False)
    response = client.post(f"/session/{TEST_SESSION_ID}/correction")

    assert response.status_code == 200
    assert response.json()["reused"] is True
    assert response.json()["model"] == "test-model"
    assert response.json()["attempted"] is False
    assert response.json()["attempts"] == 0
    assert response.json()["retried"] is False


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


def test_corrected_transcript_endpoint_returns_empty_artifact_before_correction() -> (
    None
):
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
    _attest_terminal_visit()

    client = TestClient(app, raise_server_exceptions=False)
    response = client.post(f"/session/{TEST_SESSION_ID}/correction")

    assert response.status_code == 200
    assert response.json()["status"] == "unavailable"
    assert response.json()["source"] == "live_segments"
    assert response.json()["attempted"] is False
    assert response.json()["attempts"] == 0
    assert response.json()["reason_category"] == "audio_expired"
    assert sessions.get_corrected_segments(TEST_SESSION_ID) == []


def test_correction_endpoint_falls_back_when_buffer_trimmed() -> None:
    """Tail-only retained audio must not be aligned against the full-visit scaffold."""
    transcription_session = TranscriptionSession(
        TEST_SESSION_ID,
        pipeline=NemoPipeline(),
        input_format="pcm",
        max_buffer_duration=1.0,
    )
    # Two seconds against a one-second cap trims the visit's opening audio.
    transcription_session.buffer.append(b"\0\0" * 16000)
    transcription_session.buffer.append(b"\0\0" * 16000)
    asyncio.run(lifecycle.register(TEST_SESSION_ID, transcription_session))
    sessions.append_segment(
        TEST_SESSION_ID,
        {
            "speaker_id": "speaker_0",
            "role": "PATIENT",
            "text": "early history the buffer no longer holds",
            "start": 0.0,
            "end": 1.0,
            "segment_id": "live-0001",
        },
    )
    _attest_terminal_visit()

    client = TestClient(app, raise_server_exceptions=False)
    response = client.post(f"/session/{TEST_SESSION_ID}/correction")

    assert response.status_code == 200
    assert response.json()["status"] == "unavailable"
    assert response.json()["source"] == "live_segments"
    assert response.json()["reason_category"] == "retention_window"
    assert "retention window" in response.json()["detail"]
    assert sessions.get_corrected_segments(TEST_SESSION_ID) == []


def test_correction_endpoint_sanitizes_gpu_failure_metadata(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A failed correction explains fallback without showing CUDA internals in the UI."""
    _seed_stopped_session_with_audio()
    sessions.append_segment(
        TEST_SESSION_ID,
        {
            "speaker_id": "speaker_0",
            "role": "PATIENT",
            "text": "live text remains usable",
            "start": 0.0,
            "end": 1.0,
            "segment_id": "live-0001",
        },
    )
    _attest_terminal_visit()
    raw_gpu_error = "CUDA driver error: device not ready while decoding secret text"
    caplog.set_level(logging.WARNING, logger="api.server")

    with patch(
        "api.server.run_post_visit_correction",
        side_effect=PostVisitCorrectionError(
            raw_gpu_error,
            attempts=2,
            retried=True,
            reason_category="gpu_transient",
            failed_chunk_index=2,
            chunk_count_planned=3,
        ),
    ):
        client = TestClient(app, raise_server_exceptions=False)
        response = client.post(f"/session/{TEST_SESSION_ID}/correction")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "unavailable"
    assert payload["attempted"] is True
    assert payload["attempts"] == 2
    assert payload["retried"] is True
    assert payload["reason_category"] == "gpu_transient"
    assert payload["failed_chunk_index"] == 2
    assert payload["chunk_count_planned"] == 3
    assert raw_gpu_error not in response.text
    assert "live transcript" in payload["detail"].lower()
    unavailable_log = next(
        record
        for record in caplog.records
        if "correction.unavailable" in record.message
    )
    assert unavailable_log.attempts == 2
    assert unavailable_log.retried is True
    assert unavailable_log.reason_category == "gpu_transient"
    assert unavailable_log.failed_chunk_index == 2
    assert unavailable_log.chunk_count_planned == 3
    assert raw_gpu_error not in unavailable_log.message


def test_recovered_transient_stores_rows_and_attempt_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A user whose first GPU call recovers receives corrected rows after one retry."""
    _seed_stopped_session_with_audio()
    sessions.append_segment(
        TEST_SESSION_ID,
        {
            "speaker_id": "speaker_0",
            "role": "PATIENT",
            "text": "preview text",
            "start": 0.0,
            "end": 1.0,
            "segment_id": "live-0001",
        },
    )
    _attest_terminal_visit()
    asr_model = StubPostVisitAsrModel(
        [RuntimeError("CUDA driver error: device not ready"), "corrected text"]
    )
    _stub_post_visit_model_dependencies(monkeypatch, asr_model)
    reclaimed_attempts: list[bool] = []
    monkeypatch.setattr(
        correction_module,
        "_reclaim_cuda_memory",
        lambda: reclaimed_attempts.append(True),
    )
    monkeypatch.setattr(correction_module.time, "sleep", lambda _seconds: None)

    client = TestClient(app, raise_server_exceptions=False)
    response = client.post(f"/session/{TEST_SESSION_ID}/correction")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ready"
    assert payload["attempts"] == 2
    assert payload["retried"] is True
    assert (
        sessions.get_corrected_segments(TEST_SESSION_ID)[0]["text"] == "corrected text"
    )
    assert len(asr_model.transcribed_audio_paths) == 2
    assert reclaimed_attempts == [True]


def test_duplicate_correction_clicks_execute_one_correction() -> None:
    """Two quick Summarise clicks share one corrected artifact for the same visit."""
    _seed_stopped_session_with_audio()
    _attest_terminal_visit()
    correction_call_count = 0
    correction_started = threading.Event()
    allow_correction_to_finish = threading.Event()

    def delayed_correction(**_correction_inputs: object) -> PostVisitCorrectionResult:
        """Hold the first correction long enough for a duplicate browser click to queue."""
        nonlocal correction_call_count
        correction_call_count += 1
        correction_started.set()
        allow_correction_to_finish.wait(timeout=2.0)
        return _stub_correction_result("one shared correction")

    async def request_correction_twice() -> list[dict]:
        """Submit the same post-stop browser action twice on one event loop."""
        first_request = asyncio.create_task(
            api_server.correct_session_transcript(TEST_SESSION_ID)
        )
        await asyncio.to_thread(correction_started.wait, 2.0)
        second_request = asyncio.create_task(
            api_server.correct_session_transcript(TEST_SESSION_ID)
        )
        await asyncio.sleep(0.02)
        allow_correction_to_finish.set()
        return await asyncio.gather(first_request, second_request)

    with patch("api.server.run_post_visit_correction", side_effect=delayed_correction):
        responses = asyncio.run(request_correction_twice())

    assert correction_call_count == 1
    assert [response["status"] for response in responses] == ["ready", "ready"]
    assert sorted(response["reused"] for response in responses) == [False, True]


def test_force_correction_reruns_after_waiting_for_existing_work() -> None:
    """An explicit QA force request replaces existing rows instead of reusing them."""
    _seed_stopped_session_with_audio()
    sessions.replace_corrected_segments(
        TEST_SESSION_ID,
        _stub_correction_result("previous correction").segments,
    )
    _attest_terminal_visit()

    with patch(
        "api.server.run_post_visit_correction",
        return_value=_stub_correction_result("forced correction"),
    ) as correction_runner:
        response = asyncio.run(
            api_server.correct_session_transcript(
                TEST_SESSION_ID,
                api_server.CorrectionRequest(force=True),
            )
        )

    assert response["status"] == "ready"
    assert response["reused"] is False
    assert correction_runner.call_count == 1
    assert (
        sessions.get_corrected_segments(TEST_SESSION_ID)[0]["text"]
        == "forced correction"
    )


def test_corrections_for_different_sessions_are_single_flight() -> None:
    """Two clinicians can queue corrections without both GPU workers restoring a model."""
    other_session_id = "00000000-0000-4000-8000-000000000302"
    _seed_stopped_session_with_audio(TEST_SESSION_ID)
    _seed_stopped_session_with_audio(other_session_id)
    _attest_terminal_visit(TEST_SESSION_ID)
    _attest_terminal_visit(other_session_id)
    active_correction_count = 0
    maximum_active_corrections = 0
    correction_count_lock = threading.Lock()

    def measured_correction(**_correction_inputs: object) -> PostVisitCorrectionResult:
        """Measure whether two user corrections enter the GPU lane together."""
        nonlocal active_correction_count, maximum_active_corrections
        with correction_count_lock:
            active_correction_count += 1
            maximum_active_corrections = max(
                maximum_active_corrections,
                active_correction_count,
            )
        time.sleep(0.05)
        with correction_count_lock:
            active_correction_count -= 1
        return _stub_correction_result("serialized correction")

    async def request_both_sessions() -> list[dict]:
        """Represent two users clicking Summarise at nearly the same time."""
        return await asyncio.gather(
            api_server.correct_session_transcript(TEST_SESSION_ID),
            api_server.correct_session_transcript(other_session_id),
        )

    with patch("api.server.run_post_visit_correction", side_effect=measured_correction):
        responses = asyncio.run(request_both_sessions())

    assert [response["status"] for response in responses] == ["ready", "ready"]
    assert maximum_active_corrections == 1


def _stub_correction_result(corrected_text: str) -> PostVisitCorrectionResult:
    """Build the corrected row a user would receive after a successful test request."""
    return PostVisitCorrectionResult(
        segments=[
            {
                "speaker_id": "speaker_0",
                "role": "PATIENT",
                "text": corrected_text,
                "start": 0.0,
                "end": 1.0,
                "segment_id": "corrected-0001",
                "source": "post_visit_correction",
                "source_model": "test-model",
            }
        ],
        model_name="test-model",
        word_count=len(corrected_text.split()),
    )


def _attest_terminal_visit(
    session_id: str = TEST_SESSION_ID,
    *,
    corrected_attested: bool = False,
) -> None:
    """Freeze the currently stored rows as the finalized visit, like Stop does.

    Correction and summary now refuse pre-terminal sources, so route tests
    attest the visit they just seeded. `corrected_attested` marks an existing
    corrected artifact as already attested, the state a retry click reuses.
    """
    watermark = source_integrity.record_terminal_watermark(
        session_id,
        sessions.get_segments(session_id),
        audio_seconds=1.0,
        trimmed_seconds=0.0,
        role_revision=0,
        role_settlement="settled",
    )
    # A reused corrected artifact is only honest once this visit attested it.
    if corrected_attested:
        watermark.correction_status = "attested_corrected"


def _seed_stopped_session_with_audio(session_id: str = TEST_SESSION_ID) -> None:
    """Register a session with retained PCM like the post-finalize grace window."""
    transcription_session = TranscriptionSession(
        session_id,
        pipeline=NemoPipeline(),
        input_format="pcm",
    )
    transcription_session.buffer.append(b"\0\0" * 160)
    asyncio.run(lifecycle.register(session_id, transcription_session))


# =========================================================================
# M01 source-integrity fixture integrity (0.4.0-improve-prime).
# The fixture freezes the consult-3.1 race the user experienced: the browser
# gave up waiting, correction snapshotted a pre-terminal transcript, and the
# generated note silently omitted the emergency instructions the clinician
# spoke last. These tests only validate the frozen specimen itself; they do
# NOT make that unsafe behavior an expected production contract - M02 will
# turn the same fixture into red lifecycle tests for the fix.
# =========================================================================

_SOURCE_INTEGRITY_FIXTURE_PATH = (
    Path(__file__).resolve().parents[1]
    / "fixtures"
    / "scribe"
    / "source-integrity-day3c01.json"
)


def _load_source_integrity_fixture() -> dict:
    """Load the frozen race fixture; a missing file fails the suite loudly.

    Returns:
        Parsed fixture dict; never empty because the builder fails the build
        before writing an incomplete specimen.
    """
    with open(_SOURCE_INTEGRITY_FIXTURE_PATH, encoding="utf-8") as fixture_file:
        return json.load(fixture_file)


def _fixture_event(fixture: dict, event_name: str) -> dict:
    """Return one lifecycle event by name so tests read like the user's timeline."""
    # Each lifecycle event appears exactly once in the frozen ordering.
    for event in fixture["lifecycle_events"]:
        if event["event"] == event_name:
            return event

    raise AssertionError(f"fixture is missing lifecycle event {event_name!r}")


def _joined_row_text(rows: list[dict]) -> str:
    """Join row wording the way a clinician would read it, lowercased for anchors."""
    return " ".join(str(row.get("text", "")).lower() for row in rows)


def _anchor_present(anchor: dict, region_text: str) -> bool:
    """Report whether any declared ASR spelling of one clinical meaning appears."""
    return any(variant in region_text for variant in anchor["variants"])


def test_source_integrity_fixture_freezes_pre_terminal_correction_race() -> None:
    """The frozen timeline shows correction ran before the transcript was terminal.

    This is the shape the user hit: Stop/timeout fired, correction captured an
    early snapshot, and later rows still arrived before finalization.
    """
    fixture = _load_source_integrity_fixture()
    substrate = fixture["substrate"]

    early_request = _fixture_event(fixture, "early_correction_request")
    late_rows = _fixture_event(fixture, "late_live_rows")["rows"]
    finalization_rows = _fixture_event(fixture, "finalization_rows")["rows"]
    terminal_quality = _fixture_event(fixture, "terminal_quality")

    # The early snapshot must match its own declared row count exactly.
    assert len(early_request["rows"]) == early_request["at_live_row_count"]
    assert len(early_request["rows"]) == substrate["early_live_row_count"]

    # Later deliveries must genuinely grow the visit to the terminal count.
    terminal_row_total = (
        len(early_request["rows"]) + len(late_rows) + len(finalization_rows)
    )
    assert terminal_row_total == substrate["terminal_live_row_count"]
    assert terminal_quality["stored_segments"] == terminal_row_total
    assert len(early_request["rows"]) < terminal_row_total

    # Every frozen row keeps the identity a correction join would rely on.
    for row in early_request["rows"] + late_rows + finalization_rows:
        assert str(row.get("segment_id", "")) != ""
        assert float(row["end"]) >= float(row["start"])

    # The historical incident stays pinned as observed metadata: the real
    # session recorded 214 early vs 278 terminal rows, and its corrected
    # artifact ended long before the live transcript did.
    incident = fixture["historical_incident"]
    assert incident["early_correction_rows"] == 214
    assert incident["terminal_live_rows"] == 278
    assert (
        incident["corrected_artifact_last_end_seconds"]
        < incident["terminal_live_last_end_seconds"]
    )
    # Row artifacts for the incident were never retained; the fixture must say
    # so instead of quietly presenting substrate rows as the incident's rows.
    assert incident["row_artifacts_retained"] is False


def test_source_integrity_fixture_keeps_emergency_tail_after_the_cutoff() -> None:
    """Every emergency instruction lives only after the pre-terminal cutoff.

    This is why the race matters clinically: a note built from the early
    snapshot cannot contain what the clinician said while wrapping up.
    """
    fixture = _load_source_integrity_fixture()
    early_text = _joined_row_text(
        _fixture_event(fixture, "early_correction_request")["rows"]
    )
    tail_text = _joined_row_text(
        _fixture_event(fixture, "late_live_rows")["rows"]
        + _fixture_event(fixture, "finalization_rows")["rows"]
    )

    # Each anchor meaning must sit in its expected region and nowhere else,
    # e.g. "we do" (antihistamines found) may only exist after the cutoff.
    for anchor_name, anchor in fixture["anchors"].items():
        expected_region_text = (
            tail_text if anchor["expected_region"] == "tail" else early_text
        )
        other_region_text = (
            early_text if anchor["expected_region"] == "tail" else tail_text
        )
        assert _anchor_present(anchor, expected_region_text), anchor_name
        assert not _anchor_present(anchor, other_region_text), anchor_name


def test_source_integrity_fixture_partial_artifact_lacks_the_emergency_plan() -> None:
    """The pre-terminal corrected artifact ends before every emergency anchor.

    This mirrors what the user's note was actually built from: an artifact
    that joined cleanly yet never saw the visit's ending.
    """
    fixture = _load_source_integrity_fixture()
    partial = fixture["corrected_artifacts"]["partial_pre_terminal"]["rows"]
    cutoff_seconds = fixture["substrate"]["pre_terminal_cutoff_seconds"]

    assert len(partial) == fixture["substrate"]["partial_corrected_row_count"]
    # No partial row may extend past the cutoff the incident recorded.
    assert all(float(row["end"]) <= cutoff_seconds for row in partial)

    partial_text = _joined_row_text(partial)
    # None of the tail-only emergency meanings may appear in the partial lane.
    for anchor_name, anchor in fixture["anchors"].items():
        if anchor["expected_region"] != "tail":
            continue
        assert not _anchor_present(anchor, partial_text), anchor_name


def test_source_integrity_fixture_full_input_can_still_drop_the_reversal_row() -> None:
    """A correction fed the whole visit can still silently lose one key row.

    The dropped row is the patient's "we do" - the moment antihistamines
    become available. Row counts and input identity alone cannot catch this,
    which is exactly why M02's coverage check must map every meaningful input
    row to output.
    """
    fixture = _load_source_integrity_fixture()
    full_rows = fixture["corrected_artifacts"]["full_terminal"]["rows"]
    variant = fixture["corrected_artifacts"]["full_input_dropped_row_variant"]
    variant_rows = variant["rows"]

    # Exactly one row is missing from an otherwise complete output.
    assert len(variant_rows) == len(full_rows) - 1

    full_ids = {str(row["segment_id"]) for row in full_rows}
    variant_ids = {str(row["segment_id"]) for row in variant_rows}
    missing_ids = full_ids - variant_ids
    assert missing_ids == {variant["dropped_segment_id"]}

    # The lost row must be the availability reversal itself, so this variant
    # proves a clinically meaningful silent drop rather than a random one.
    dropped_row = next(
        row
        for row in full_rows
        if str(row["segment_id"]) == variant["dropped_segment_id"]
    )
    assert "we do" in str(dropped_row["text"]).lower()


def test_role_cues_keep_doctor_question_tail_do_for_you() -> None:
    """Consult 5.3: the question tail `do for you?` must not borrow the answer's label.

    The patient cue lived entirely in the NEXT row's text; the fragment itself
    contributed nothing, yet it was reassigned to Patient.
    """
    role = infer_role_from_corrected_text(
        "do for you?",
        next_text="I've been feeling very anxious for months.",
        next_role="PATIENT",
    )

    assert role is None


def test_role_cues_keep_doctor_question_tail_else_outside_work() -> None:
    """Consult 5.3: `else outside work?` is the doctor's question tail, not an answer."""
    role = infer_role_from_corrected_text(
        "else outside work?",
        next_text="I have hobbies but no time for them lately.",
        next_role="PATIENT",
    )

    assert role is None


def test_role_cues_keep_doctor_recap_say_were_going() -> None:
    """Consult 5.3: `say were going` must not inherit Patient from a neighbor row
    whose cue words the fragment does not share."""
    role = infer_role_from_corrected_text(
        "say were going",
        previous_text="when I get stressed I just can't sleep.",
        previous_role="PATIENT",
    )

    assert role is None


def test_role_cues_still_join_patient_continuations_that_share_cue_words() -> None:
    """A fragment completing the patient's own cue phrase keeps its borrowed label.

    "had a headache" after "I've" continues the patient's "ive had" wording -
    the fragment contributes the cue's own words, which is the positive
    same-turn evidence the 5.3 question tails lacked.
    """
    role = infer_role_from_corrected_text(
        "had a headache since",
        previous_text="Um, I've",
        previous_role="PATIENT",
    )

    assert role == "PATIENT"


# --- M05: consult 3.1 corrected-lane role no-regression pin ---

_DAY3C01_ROLE_FIXTURE_PATH = (
    Path(__file__).resolve().parents[1]
    / "fixtures"
    / "scribe"
    / "role-noregression-day3c01.json"
)


def test_role_cue_cleanup_is_idempotent_on_retained_day3c01_tail() -> None:
    """Re-running cue cleanup over the retained 3.1 corrected tail changes nothing.

    The tail holds the availability reversal, the mother/999 instruction, the
    stay-with advice, and the folded 'antihistamines around? Yes, yes.' debt
    row. A cue edit that starts moving any of them must fail here instead of
    silently relabelling the frozen visit.
    """
    fixture = json.loads(_DAY3C01_ROLE_FIXTURE_PATH.read_text(encoding="utf-8"))
    retained_roles = {
        row["segment_id"]: row["role"] for row in fixture["corrected_tail_rows"]
    }

    cleaned_rows = apply_role_cue_cleanup(
        [dict(row) for row in fixture["corrected_tail_rows"]]
    )

    assert {row["segment_id"]: row["role"] for row in cleaned_rows} == retained_roles


def test_day3c01_corrected_tail_ownership_is_pinned() -> None:
    """Emergency-tail rows stay DOCTOR; the reversal stays PATIENT-owned.

    corrected-0250 is the documented exception: the patient side's 'Yes, yes.'
    folded onto the clinician's question row - upstream identity debt this
    fixture preserves as DOCTOR rather than hiding behind a confident flip.
    """
    fixture = json.loads(_DAY3C01_ROLE_FIXTURE_PATH.read_text(encoding="utf-8"))
    rows_by_id = {row["segment_id"]: row for row in fixture["corrected_tail_rows"]}

    # The spoken emergency disposition is clinician speech end to end.
    for segment_id in fixture["anchors"]["corrected_emergency_tail_doctor"]:
        assert rows_by_id[segment_id]["role"] == "DOCTOR", segment_id

    # The availability reversal belongs to the patient side, except the
    # folded answer row documented as debt.
    for segment_id in fixture["anchors"]["corrected_availability_reversal"]:
        expected_role = "DOCTOR" if segment_id == "corrected-0250" else "PATIENT"
        assert rows_by_id[segment_id]["role"] == expected_role, segment_id
