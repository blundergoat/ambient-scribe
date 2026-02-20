"""
Tests for the transcription session — WebM accumulation and ffmpeg conversion.

NeMo models are NOT loaded in tests (NEMO_MODEL_PROVIDER=mock).
"""

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

from nemo_pipeline import NemoPipeline
from nemo_session import TranscriptionSession


class TestConvertWebmToWav:
    """Tests for TranscriptionSession._convert_webm_to_wav static method."""

    @patch("nemo_session.subprocess.run")
    def test_successful_conversion(self, mock_run):
        """ffmpeg succeeds → returns path to WAV file."""
        mock_run.return_value = MagicMock(
            returncode=0,
            stderr=b"ffmpeg output",
        )

        result = TranscriptionSession._convert_webm_to_wav(b"fake-webm-data")

        assert result is not None
        assert result.endswith(".wav")
        # Verify ffmpeg was called with correct args
        call_args = mock_run.call_args[0][0]
        assert call_args[0] == "ffmpeg"
        assert "-ar" in call_args
        assert "16000" in call_args
        assert "-ac" in call_args
        assert "1" in call_args

        # Cleanup
        Path(result).unlink(missing_ok=True)

    @patch("nemo_session.subprocess.run")
    def test_ffmpeg_failure_returns_none(self, mock_run):
        """ffmpeg fails → returns None."""
        mock_run.side_effect = subprocess.CalledProcessError(
            1, "ffmpeg", stderr=b"error decoding"
        )

        result = TranscriptionSession._convert_webm_to_wav(b"bad-data")
        assert result is None

    @patch("nemo_session.subprocess.run")
    def test_ffmpeg_timeout_returns_none(self, mock_run):
        """ffmpeg hangs → returns None after timeout."""
        mock_run.side_effect = subprocess.TimeoutExpired("ffmpeg", 30)

        result = TranscriptionSession._convert_webm_to_wav(b"data")
        assert result is None


class TestWebmAccumulation:
    """Tests for WebM byte accumulation in TranscriptionSession."""

    def test_chunks_accumulate(self):
        """Each process_chunk call extends the WebM accumulator."""
        pipeline = NemoPipeline()
        session = TranscriptionSession("test-session", pipeline)

        session._webm_accumulator.extend(b"chunk1")
        session._webm_accumulator.extend(b"chunk2")

        assert bytes(session._webm_accumulator) == b"chunk1chunk2"

    def test_empty_accumulator_on_creation(self):
        """New session starts with empty WebM accumulator."""
        pipeline = NemoPipeline()
        session = TranscriptionSession("test-session", pipeline)

        assert len(session._webm_accumulator) == 0

    @patch.object(TranscriptionSession, "_convert_webm_to_wav", return_value=None)
    def test_process_chunk_accumulates_and_increments(self, mock_convert):
        """process_chunk accumulates bytes and increments counter even when ffmpeg fails."""
        pipeline = NemoPipeline()
        session = TranscriptionSession("test-session", pipeline)

        session.process_chunk(b"chunk-data-1")
        assert session.chunk_count == 1
        assert b"chunk-data-1" in session._webm_accumulator

        session.process_chunk(b"chunk-data-2")
        assert session.chunk_count == 2
        assert b"chunk-data-2" in session._webm_accumulator

    def test_finalize_with_empty_accumulator(self):
        """Finalize with no chunks returns empty accumulated transcript."""
        pipeline = NemoPipeline()
        session = TranscriptionSession("test-session", pipeline)

        result = session.finalize()
        assert isinstance(result, list)
        assert len(result) == 0
