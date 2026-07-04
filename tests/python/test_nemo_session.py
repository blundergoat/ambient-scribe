"""
Tests for the transcription session — AudioBuffer accumulation and audio decoding.

NeMo models are NOT loaded in tests (NEMO_MODEL_PROVIDER=mock).
"""

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

from nemo_pipeline import NemoPipeline, TranscriptionResult
from nemo_session import AudioBuffer, TranscriptionSession


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


class TestAudioBuffer:
    """Tests for AudioBuffer accumulation behaviour."""

    def test_chunks_accumulate(self):
        """Each append call adds PCM data to the buffer."""
        buf = AudioBuffer()

        buf.append(b"chunk1")
        buf.append(b"chunk2")

        assert buf.current_window() == b"chunk1chunk2"

    def test_empty_buffer_on_creation(self):
        """New buffer starts with zero bytes."""
        buf = AudioBuffer()

        assert buf.total_bytes == 0
        assert buf.current_window() == b""

    def test_full_audio_returns_all_data(self):
        """full_audio() returns all accumulated PCM bytes."""
        buf = AudioBuffer()

        buf.append(b"aaa")
        buf.append(b"bbb")

        assert buf.full_audio() == b"aaabbb"

    def test_duration_seconds(self):
        """duration_seconds estimates from PCM byte count (16kHz, 16-bit)."""
        buf = AudioBuffer()

        # 16000 samples/s * 2 bytes/sample = 32000 bytes/s
        buf.append(b"\x00" * 32000)

        assert buf.duration_seconds == 1.0


class TestProcessChunk:
    """Tests for TranscriptionSession.process_chunk with PCM input (default)."""

    def test_process_chunk_accumulates_pcm_and_increments(self):
        """process_chunk with PCM input appends to buffer and increments counter."""
        pipeline = NemoPipeline()
        session = TranscriptionSession("test-session", pipeline, input_format="pcm")

        # Pipeline is in mock mode → transcribe_buffer returns empty result.
        session.process_chunk(b"chunk-data-1")
        assert session.chunk_count == 1
        assert b"chunk-data-1" in session.buffer.current_window()

        session.process_chunk(b"chunk-data-2")
        assert session.chunk_count == 2
        assert b"chunk-data-2" in session.buffer.current_window()

    def test_process_chunk_returns_segments(self):
        """process_chunk returns new segments from the pipeline."""
        pipeline = NemoPipeline()
        session = TranscriptionSession("test-session", pipeline, input_format="pcm")

        mock_result = TranscriptionResult(
            segments=[
                MagicMock(speaker_id="spk_0", start=0.0, end=1.0, text="hello"),
            ]
        )

        with patch.object(pipeline, "transcribe_buffer", return_value=mock_result):
            segments = session.process_chunk(b"\x00" * 3200)

        assert len(segments) == 1

    def test_empty_chunk_skipped(self):
        """Empty audio bytes are skipped without appending to buffer."""
        pipeline = NemoPipeline()
        session = TranscriptionSession("test-session", pipeline, input_format="pcm")

        result = session.process_chunk(b"")
        assert result == []
        assert session.buffer.total_bytes == 0
        # chunk_count is incremented even for empty chunks
        assert session.chunk_count == 1


class TestProcessChunkWebM:
    """Tests for TranscriptionSession.process_chunk with WebM input."""

    @patch.object(
        TranscriptionSession, "_decode_webm_chunk", return_value=b"\x00" * 3200
    )
    def test_process_chunk_webm_decodes_and_accumulates(self, mock_decode):
        """process_chunk with webm input decodes via _decode_webm_chunk and appends PCM."""
        pipeline = NemoPipeline()
        session = TranscriptionSession("test-session", pipeline, input_format="webm")

        session.process_chunk(b"\x1a\x45\xdf\xa3fake-webm")
        assert session.chunk_count == 1
        assert session.buffer.total_bytes == 3200

    @patch.object(TranscriptionSession, "_decode_webm_chunk", return_value=b"")
    def test_process_chunk_webm_decode_failure_skips(self, mock_decode):
        """When _decode_webm_chunk returns empty bytes, chunk is skipped."""
        pipeline = NemoPipeline()
        session = TranscriptionSession("test-session", pipeline, input_format="webm")

        result = session.process_chunk(b"\x1a\x45\xdf\xa3bad-data")
        assert result == []
        assert session.buffer.total_bytes == 0


class TestDecodeWebmChunk:
    """Tests for TranscriptionSession._decode_webm_chunk."""

    @patch("nemo_session.subprocess.run")
    def test_successful_decode(self, mock_run):
        """ffmpeg succeeds → returns raw PCM bytes."""
        pipeline = NemoPipeline()
        session = TranscriptionSession("test-session", pipeline, input_format="webm")

        # Mock subprocess.run to succeed, then mock Path.read_bytes for the output
        mock_run.return_value = MagicMock(returncode=0, stderr=b"ok")

        with patch("nemo_session.Path.read_bytes", return_value=b"\x00" * 1600):
            result = session._decode_webm_chunk(b"fake-webm")

        assert result == b"\x00" * 1600

        # Verify ffmpeg called with s16le output format
        call_args = mock_run.call_args[0][0]
        assert call_args[0] == "ffmpeg"
        assert "-f" in call_args
        assert "s16le" in call_args

    @patch("nemo_session.subprocess.run")
    def test_decode_ffmpeg_failure_returns_empty(self, mock_run):
        """ffmpeg fails → returns empty bytes."""
        pipeline = NemoPipeline()
        session = TranscriptionSession("test-session", pipeline, input_format="webm")

        mock_run.side_effect = subprocess.CalledProcessError(
            1, "ffmpeg", stderr=b"decode error"
        )

        result = session._decode_webm_chunk(b"bad-data")
        assert result == b""

    @patch("nemo_session.subprocess.run")
    def test_decode_ffmpeg_timeout_returns_empty(self, mock_run):
        """ffmpeg hangs → returns empty bytes after timeout."""
        pipeline = NemoPipeline()
        session = TranscriptionSession("test-session", pipeline, input_format="webm")

        mock_run.side_effect = subprocess.TimeoutExpired("ffmpeg", 30)

        result = session._decode_webm_chunk(b"data")
        assert result == b""


class TestFinalize:
    """Tests for TranscriptionSession.finalize."""

    def test_finalize_with_empty_buffer(self):
        """Finalize with no chunks returns empty accumulated transcript."""
        pipeline = NemoPipeline()
        session = TranscriptionSession("test-session", pipeline)

        result = session.finalize()
        assert isinstance(result, list)
        assert len(result) == 0

    def test_finalize_returns_accumulated_transcript(self):
        """Finalize with buffered audio runs final pass and returns all segments."""
        pipeline = NemoPipeline()
        session = TranscriptionSession("test-session", pipeline, input_format="pcm")

        # Simulate having processed a chunk
        session.buffer.append(b"\x00" * 3200)

        mock_result = TranscriptionResult(
            segments=[
                MagicMock(speaker_id="spk_0", start=0.0, end=1.0, text="hello"),
            ]
        )

        with patch.object(pipeline, "transcribe_buffer", return_value=mock_result):
            result = session.finalize()

        assert len(result) == 1


class TestInputFormatValidation:
    """Tests for input format configuration and validation."""

    def test_unsupported_format_raises(self):
        """Unsupported input_format raises ValueError on construction."""
        pipeline = NemoPipeline()
        import pytest

        with pytest.raises(ValueError, match="Unsupported transcription input format"):
            TranscriptionSession("test-session", pipeline, input_format="mp3")

    def test_pcm_format_passthrough(self):
        """PCM format returns raw audio bytes directly from _decode_audio."""
        pipeline = NemoPipeline()
        session = TranscriptionSession("test-session", pipeline, input_format="pcm")

        result = session._decode_audio(b"raw-pcm-data")
        assert result == b"raw-pcm-data"

    def test_webm_magic_mismatch_on_pcm_raises(self):
        """Sending WebM data to a PCM-configured session raises ValueError."""
        pipeline = NemoPipeline()
        session = TranscriptionSession("test-session", pipeline, input_format="pcm")

        import pytest

        with pytest.raises(ValueError, match="Audio format mismatch"):
            session.process_chunk(b"\x1a\x45\xdf\xa3webm-container-data")
