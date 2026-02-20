"""
Tests for the NeMo pipeline wrapper.

These tests verify the NemoPipeline and TranscriptionSession classes.
NeMo models are NOT loaded in tests — we test the wrapper logic only.
"""

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
