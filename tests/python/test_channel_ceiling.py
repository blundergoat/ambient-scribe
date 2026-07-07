"""Tests for the M17 separated-channel ceiling evaluator.

The real runner downloads PriMock57 audio and streams it through the live
WebSocket endpoint. These tests keep that work offline by covering the pure
helpers that map the manifest, combine channel payloads, and parse reports.
"""

from __future__ import annotations

import importlib.util
import sys
import uuid
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = REPO_ROOT / "scripts" / "eval-channel-ceiling.py"

spec = importlib.util.spec_from_file_location("eval_channel_ceiling", SCRIPT_PATH)
assert spec is not None
channel_ceiling = importlib.util.module_from_spec(spec)
assert spec.loader is not None
sys.modules[spec.name] = channel_ceiling
spec.loader.exec_module(channel_ceiling)


def test_source_urls_from_manifest_entry_maps_roles() -> None:
    """Manifest source paths become public doctor/patient audio URLs."""
    urls = channel_ceiling.source_urls_from_manifest_entry(
        [
            "audio/day1_consultation02_doctor.wav",
            "audio/day1_consultation02_patient.wav",
        ]
    )

    assert urls == {
        "DOCTOR": (
            "https://media.githubusercontent.com/media/babylonhealth/primock57/main/"
            "audio/day1_consultation02_doctor.wav"
        ),
        "PATIENT": (
            "https://media.githubusercontent.com/media/babylonhealth/primock57/main/"
            "audio/day1_consultation02_patient.wav"
        ),
    }


def test_select_fixture_sources_requires_unambiguous_queries() -> None:
    """Fixture queries must select one visible case for a trustworthy table."""
    sources = [
        channel_ceiling.FixtureChannelSource(
            fixture_name="primock57-day1-consultation02-red-skin",
            mixed_wav_path=Path("c02.wav"),
            source_urls_by_role={"DOCTOR": "d", "PATIENT": "p"},
        ),
        channel_ceiling.FixtureChannelSource(
            fixture_name="primock57-day1-consultation06-breath",
            mixed_wav_path=Path("c06.wav"),
            source_urls_by_role={"DOCTOR": "d", "PATIENT": "p"},
        ),
    ]

    selected = channel_ceiling.select_fixture_sources(
        sources,
        ["consultation06"],
        include_all=False,
    )

    assert [source.fixture_name for source in selected] == [
        "primock57-day1-consultation06-breath"
    ]


def test_select_fixture_sources_rejects_empty_query_without_all() -> None:
    """Empty fixture selection cannot silently start the unstable full run."""
    sources = [
        channel_ceiling.FixtureChannelSource(
            fixture_name="primock57-day1-consultation02-red-skin",
            mixed_wav_path=Path("c02.wav"),
            source_urls_by_role={"DOCTOR": "d", "PATIENT": "p"},
        )
    ]

    try:
        channel_ceiling.select_fixture_sources(sources, [], include_all=False)
    except ValueError as error:
        assert "pass fixture queries" in str(error)
    else:
        raise AssertionError("empty selection should require --all")


def test_combined_history_from_channels_sets_known_roles() -> None:
    """Channel transcripts become one scoreable history with fixed roles."""
    history = channel_ceiling.combined_history_from_channels(
        "fixture",
        {
            "PATIENT": {
                "segments": [
                    {"speaker_id": "spk_0", "start": 1.0, "end": 2.0, "text": "answer"}
                ]
            },
            "DOCTOR": {
                "segments": [
                    {"speaker_id": "spk_0", "start": 0.0, "end": 1.0, "text": "question"}
                ]
            },
        },
    )

    assert history == {
        "session_id": "fixture-channel-ceiling",
        "segments": [
            {
                "speaker_id": "doctor_spk_0",
                "role": "DOCTOR",
                "start": 0.0,
                "end": 1.0,
                "text": "question",
            },
            {
                "speaker_id": "patient_spk_0",
                "role": "PATIENT",
                "start": 1.0,
                "end": 2.0,
                "text": "answer",
            },
        ],
    }


def test_channel_session_id_is_valid_and_stable() -> None:
    """Eval channel IDs satisfy the same validation as browser sessions."""
    session_id = channel_ceiling.channel_session_id("fixture", "DOCTOR", "run")

    assert uuid.UUID(session_id)
    assert session_id == channel_ceiling.channel_session_id("fixture", "DOCTOR", "run")
    assert session_id != channel_ceiling.channel_session_id("fixture", "DOCTOR", "next")


def test_score_metric_parsers_read_transcript_quality_output() -> None:
    """Compact report rows can reuse the scorer's human-readable output."""
    score_text = "\n".join(
        [
            "reference vocabulary recall: 83.2%",
            "length ratio hyp/ref: 0.81",
            "word error rate: 72.5% (S=1, I=2, D=3, ref=10, hyp=9)",
            "word error rate (non-overlap): 33.0% (S=1, I=0, D=0, ref=3, hyp=3)",
            "word error rate (overlap): 91.0% (S=0, I=1, D=2, ref=3, hyp=2)",
        ]
    )

    assert channel_ceiling.percent_metric(score_text, "reference vocabulary recall") == 83.2
    assert channel_ceiling.percent_metric(score_text, "word error rate") == 72.5
    assert channel_ceiling.percent_metric(score_text, "word error rate (non-overlap)") == 33.0
    assert channel_ceiling.percent_metric(score_text, "word error rate (overlap)") == 91.0
    assert channel_ceiling.length_ratio_metric(score_text) == 0.81
