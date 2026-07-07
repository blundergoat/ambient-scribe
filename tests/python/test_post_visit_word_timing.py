"""Tests for second-pass word-timing extraction behind corrected-row splits."""

from __future__ import annotations

from post_visit_word_timing import (
    estimate_post_visit_word_timings,
    word_timings_from_hypothesis,
)


class FakeHypothesis:
    """Minimal stand-in for a NeMo hypothesis so timing paths run without GPU models."""

    def __init__(self, words=None, timestamp=None, length=None, text=""):
        self.words = words
        self.timestamp = timestamp
        self.length = length
        self.text = text


def native_timestamp_payload() -> dict[str, list[dict[str, float | int | str]]]:
    """Return the consult-08 echo region exactly as the native probe reported it."""
    return {
        "word": [
            {"word": "I'm", "start": 15.04, "end": 15.36, "start_offset": 188, "end_offset": 192},
            {"word": "26.", "start": 15.36, "end": 15.92, "start_offset": 192, "end_offset": 199},
            {"word": "26,", "start": 16.16, "end": 16.56, "start_offset": 202, "end_offset": 207},
            {"word": "okay.", "start": 16.8, "end": 16.96, "start_offset": 210, "end_offset": 212},
        ],
        "timestep": [],
    }


def test_word_timings_prefer_native_entries() -> None:
    """NeMo's own word times win over the proportional estimate when both exist."""
    hypothesis = FakeHypothesis(
        words=["I'm", "26.", "26,", "okay."],
        timestamp=native_timestamp_payload(),
    )

    timings = word_timings_from_hypothesis(hypothesis, audio_duration_seconds=60.0)

    assert [timing["word"] for timing in timings] == ["I'm", "26.", "26,", "okay."]
    assert timings[2]["start"] == 16.16
    assert timings[3]["end"] == 16.96


def test_word_timings_fall_back_to_proportional_for_token_tensor() -> None:
    """Timing-less older hypotheses still get the estimated word spread."""
    hypothesis = FakeHypothesis(
        words=["hello", "there"],
        timestamp=[10.0, 20.0, 30.0, 40.0],
        length=100.0,
    )

    timings = word_timings_from_hypothesis(hypothesis, audio_duration_seconds=10.0)

    assert [timing["word"] for timing in timings] == ["hello", "there"]
    # Proportional spread: word one owns the first half of the token frames.
    assert timings[0]["start"] == 1.0
    assert timings[1]["end"] > timings[1]["start"]


def test_word_timings_reject_malformed_native_entries() -> None:
    """Native entries missing time values must not silently become zero-time words."""
    hypothesis = FakeHypothesis(
        words=["hello"],
        timestamp={"word": [{"word": "hello", "start_offset": 1, "end_offset": 2}]},
    )

    timings = word_timings_from_hypothesis(hypothesis, audio_duration_seconds=10.0)

    assert timings == []


def test_proportional_estimator_ignores_native_timestamp_mapping() -> None:
    """The tensor-era estimator never tries to float() the native mapping's keys."""
    hypothesis = FakeHypothesis(
        words=["hello"],
        timestamp=native_timestamp_payload(),
        length=100.0,
    )

    assert estimate_post_visit_word_timings(hypothesis, audio_duration_seconds=10.0) == []
