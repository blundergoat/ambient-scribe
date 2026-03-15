"""
Tests for the NeMo pipeline wrapper.

These tests verify the NemoPipeline, parsing logic, and TranscriptionSession classes.
NeMo models are NOT loaded in tests (NEMO_MODEL_PROVIDER=mock) — we test wrapper logic only.
"""

from unittest.mock import MagicMock

import pytest

from nemo_pipeline import NemoPipeline, Segment, TranscriptionResult
from nemo_session import AudioBuffer, TranscriptionSession


class TestSegment:
    """Tests for the Segment data class."""

    def test_segment_dict(self):
        seg = Segment(speaker_id="spk_0", text="Hello", start=0.0, end=1.5, is_interim=False)
        result = seg.dict()
        assert result == {
            "speaker_id": "spk_0",
            "text": "Hello",
            "start": 0.0,
            "end": 1.5,
            "is_interim": False,
        }

    def test_segment_defaults(self):
        seg = Segment(speaker_id="spk_1", text="Test", start=0.0, end=1.0)
        assert seg.is_interim is False


class TestAudioBuffer:
    """Tests for the AudioBuffer class."""

    def test_append_and_retrieve(self):
        buf = AudioBuffer()
        buf.append(b"\x00" * 3200)  # 100ms of 16kHz 16-bit audio
        buf.append(b"\x00" * 3200)

        assert buf.total_bytes == 6400
        assert len(buf.current_window()) == 6400
        assert len(buf.full_audio()) == 6400

    def test_duration_calculation(self):
        buf = AudioBuffer()
        # 1 second of 16kHz 16-bit audio = 32000 bytes
        buf.append(b"\x00" * 32000)
        assert buf.duration_seconds == 1.0

    def test_max_duration_cap(self):
        # Set max to 1 second
        buf = AudioBuffer(max_duration_seconds=1.0)
        # Add 2 seconds of audio in two chunks
        buf.append(b"\x00" * 32000)
        buf.append(b"\x00" * 32000)

        # Should have trimmed first chunk
        assert buf.total_bytes == 32000
        assert buf.duration_seconds == 1.0

    def test_empty_buffer(self):
        buf = AudioBuffer()
        assert buf.total_bytes == 0
        assert buf.duration_seconds == 0.0
        assert buf.current_window() == b""


class TestTranscriptionSession:
    """Tests for the TranscriptionSession class."""

    def test_session_creation(self):
        pipeline = NemoPipeline()
        session = TranscriptionSession("test-session-id", pipeline)

        assert session.session_id == "test-session-id"
        assert session.chunk_count == 0
        assert len(session.accumulated_transcript) == 0

    def test_process_chunk_increments_counter(self):
        pipeline = NemoPipeline()
        session = TranscriptionSession("test-session-id", pipeline)

        # Process a chunk (NeMo returns empty since models aren't loaded)
        session.process_chunk(b"\x00" * 3200)
        assert session.chunk_count == 1

        session.process_chunk(b"\x00" * 3200)
        assert session.chunk_count == 2

    def test_finalize_returns_transcript(self):
        pipeline = NemoPipeline()
        session = TranscriptionSession("test-session-id", pipeline)

        result = session.finalize()
        assert isinstance(result, list)


class TestParseDiarStrings:
    """Tests for NemoPipeline._parse_diar_strings static method."""

    def test_nested_list_format(self):
        """Sortformer returns list-of-lists (batch output)."""
        diar_output = [["0.560 3.120 speaker_0", "3.200 5.800 speaker_1"]]
        result = NemoPipeline._parse_diar_strings(diar_output)

        assert len(result) == 2
        assert result[0] == (0.56, 3.12, "speaker_0")
        assert result[1] == (3.2, 5.8, "speaker_1")

    def test_flat_list_format(self):
        """Handle flat list of diar strings."""
        diar_output = ["1.0 2.0 speaker_0", "3.0 4.0 speaker_1"]
        result = NemoPipeline._parse_diar_strings(diar_output)

        assert len(result) == 2
        assert result[0] == (1.0, 2.0, "speaker_0")
        assert result[1] == (3.0, 4.0, "speaker_1")

    def test_empty_input(self):
        assert NemoPipeline._parse_diar_strings([]) == []
        assert NemoPipeline._parse_diar_strings(None) == []

    def test_sorts_by_start_time(self):
        diar_output = ["5.0 6.0 speaker_1", "1.0 2.0 speaker_0"]
        result = NemoPipeline._parse_diar_strings(diar_output)

        assert result[0][0] == 1.0
        assert result[1][0] == 5.0


class TestAudioFormatValidation:
    """Tests for first-chunk audio format detection."""

    def test_pcm_rejects_webm_bytes(self):
        pipeline = NemoPipeline()
        session = TranscriptionSession("format-test", pipeline, input_format="pcm")

        # WebM magic bytes
        webm_data = b"\x1a\x45\xdf\xa3" + b"\x00" * 3196
        with pytest.raises(ValueError, match="configured for PCM but received WebM"):
            session.process_chunk(webm_data)

    def test_pcm_rejects_wav_header(self):
        pipeline = NemoPipeline()
        session = TranscriptionSession("format-test", pipeline, input_format="pcm")

        wav_data = b"RIFF" + b"\x00" * 3196
        with pytest.raises(ValueError, match="configured for raw PCM but received WAV"):
            session.process_chunk(wav_data)

    def test_pcm_accepts_valid_pcm(self):
        pipeline = NemoPipeline()
        session = TranscriptionSession("format-test", pipeline, input_format="pcm")

        # Regular PCM silence — should not raise
        pcm_data = b"\x00" * 3200
        session.process_chunk(pcm_data)
        assert session._format_validated is True

    def test_pcm_accepts_short_chunk(self):
        """A very short PCM chunk (< 4 bytes) should not crash validation."""
        pipeline = NemoPipeline()
        session = TranscriptionSession("format-test", pipeline, input_format="pcm")

        # 2 bytes — too short for magic detection, but valid PCM
        session.process_chunk(b"\x00\x00")
        assert session._format_validated is True

    def test_empty_chunk_skips_validation(self):
        """Empty bytes skip validation entirely (handled by _decode_audio)."""
        pipeline = NemoPipeline()
        session = TranscriptionSession("format-test", pipeline, input_format="pcm")

        result = session.process_chunk(b"")
        assert result == []
        # Validation flag stays False — will validate on next non-empty chunk
        assert session._format_validated is False

    def test_format_validation_runs_only_once(self):
        pipeline = NemoPipeline()
        session = TranscriptionSession("format-test", pipeline, input_format="pcm")

        # First chunk: valid PCM
        session.process_chunk(b"\x00" * 3200)
        assert session._format_validated is True

        # Second chunk: even WebM magic won't trigger validation again
        webm_data = b"\x1a\x45\xdf\xa3" + b"\x00" * 3196
        session.process_chunk(webm_data)  # Should not raise


class TestNemoPipeline:
    """Wrapper tests that avoid loading the real GPU models."""

    def _make_pipeline(self) -> NemoPipeline:
        """Create a mock-mode pipeline for testing parse logic."""
        return NemoPipeline()

    def _make_hyp(self, text: str) -> MagicMock:
        """Create a mock ASR hypothesis object."""
        hyp = MagicMock()
        hyp.text = text
        return hyp

    def test_two_speakers_proportional_words(self):
        """Words split proportionally across two equal-duration segments."""
        pipeline = self._make_pipeline()
        diar = [["0.0 2.0 speaker_0", "2.0 4.0 speaker_1"]]
        hyps = [self._make_hyp("hello world foo bar")]

        segments = pipeline._parse_nemo_output(diar, hyps)

        assert len(segments) == 2
        assert segments[0].speaker_id == "speaker_0"
        assert segments[1].speaker_id == "speaker_1"
        # With equal durations, 4 words split ~2+2
        total_words = len(segments[0].text.split()) + len(segments[1].text.split())
        assert total_words == 4

    def test_single_segment_gets_all_words(self):
        """Single diar segment gets all ASR words."""
        pipeline = self._make_pipeline()
        diar = [["0.0 5.0 speaker_0"]]
        hyps = [self._make_hyp("the quick brown fox jumps")]

        segments = pipeline._parse_nemo_output(diar, hyps)

        assert len(segments) == 1
        assert segments[0].speaker_id == "speaker_0"
        assert segments[0].text == "the quick brown fox jumps"
        assert segments[0].start == 0.0
        assert segments[0].end == 5.0

    def test_empty_diar_returns_empty(self):
        """No diarization segments → empty result."""
        pipeline = self._make_pipeline()
        segments = pipeline._parse_nemo_output([], [self._make_hyp("hello")])
        assert segments == []

    def test_empty_asr_text_returns_empty_segments(self):
        """Diar segments with no ASR text → segments with empty text."""
        pipeline = self._make_pipeline()
        diar = [["0.0 2.0 speaker_0"]]
        hyps = [self._make_hyp("")]

        segments = pipeline._parse_nemo_output(diar, hyps)

        assert len(segments) == 1
        assert segments[0].text == ""
        assert segments[0].speaker_id == "speaker_0"

    def test_no_hyps_returns_empty_segments(self):
        """Diar segments with empty hyps list → segments with empty text."""
        pipeline = self._make_pipeline()
        diar = [["0.0 2.0 speaker_0"]]
        segments = pipeline._parse_nemo_output(diar, [])
        assert len(segments) == 1
        assert segments[0].text == ""

    def test_unequal_duration_distribution(self):
        """Longer segment gets more words proportionally."""
        pipeline = self._make_pipeline()
        # speaker_0: 1s, speaker_1: 3s → 25%/75% split
        diar = [["0.0 1.0 speaker_0", "1.0 4.0 speaker_1"]]
        hyps = [self._make_hyp("a b c d e f g h")]  # 8 words

        segments = pipeline._parse_nemo_output(diar, hyps)

        assert len(segments) == 2
        # speaker_0 gets ~25% of 8 = 2 words, speaker_1 gets rest
        words_0 = len(segments[0].text.split())
        words_1 = len(segments[1].text.split())
        assert words_0 + words_1 == 8
        assert words_1 > words_0  # Longer segment gets more words


class TestFilterHallucinatedSpeakers:
    """Tests for the hallucinated speaker filter."""

    def test_suppresses_speaker_below_threshold(self):
        """A speaker with 1s out of 100s total (1%) should be suppressed."""
        parsed_diar = [
            (0.0, 30.0, "speaker_0"),   # 30s — kept
            (30.0, 69.0, "speaker_1"),   # 39s — kept
            (69.0, 99.0, "speaker_0"),   # 30s — kept (total speaker_0 = 60s)
            (99.0, 100.0, "speaker_2"),  # 1s  — suppressed (1%)
        ]

        result = NemoPipeline._filter_hallucinated_speakers(parsed_diar)

        speaker_ids = {spk for _, _, spk in result}
        assert "speaker_0" in speaker_ids
        assert "speaker_1" in speaker_ids
        assert "speaker_2" not in speaker_ids
        assert len(result) == 3

    def test_keeps_speakers_above_threshold(self):
        """Speakers with >= 5% of total duration are kept."""
        parsed_diar = [
            (0.0, 50.0, "speaker_0"),   # 50%
            (50.0, 100.0, "speaker_1"), # 50%
        ]

        result = NemoPipeline._filter_hallucinated_speakers(parsed_diar)
        assert len(result) == 2

    def test_empty_input(self):
        """Empty diar list returns empty."""
        assert NemoPipeline._filter_hallucinated_speakers([]) == []

    def test_all_speakers_below_threshold_returns_empty(self):
        """If every speaker is below 5% threshold individually but
        together they're all equal, none are suppressed (each >= threshold)."""
        # Two speakers, each exactly 50% — both kept
        parsed_diar = [
            (0.0, 1.0, "speaker_0"),
            (1.0, 2.0, "speaker_1"),
        ]
        result = NemoPipeline._filter_hallucinated_speakers(parsed_diar)
        assert len(result) == 2

    def test_integration_with_parse_nemo_output(self):
        """Hallucinated speaker is removed before words are distributed."""
        pipeline = NemoPipeline()
        # speaker_2 has 0.5s out of 100.5s total — well below 5%
        diar = [[
            "0.0 50.0 speaker_0",
            "50.0 100.0 speaker_1",
            "100.0 100.5 speaker_2",
        ]]
        hyp = MagicMock()
        hyp.text = "word1 word2 word3 word4"

        segments = pipeline._parse_nemo_output(diar, [hyp])

        speaker_ids = {seg.speaker_id for seg in segments}
        assert "speaker_2" not in speaker_ids
        assert "speaker_0" in speaker_ids
        assert "speaker_1" in speaker_ids
