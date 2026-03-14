"""
Tests for the NeMo pipeline wrapper.

These tests verify the NemoPipeline and TranscriptionSession classes.
NeMo models are NOT loaded in tests — we test the wrapper logic only.
"""

import wave

from nemo_pipeline import NemoPipeline, Segment, TranscriptionResult
from nemo_session import AudioBuffer, TranscriptionSession


def write_silence_wav(path, duration_seconds=3, sample_rate_hz=16000):
    """Create a simple PCM WAV fixture for wrapper tests."""
    total_frames = duration_seconds * sample_rate_hz
    with wave.open(str(path), "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(sample_rate_hz)
        wav_file.writeframes(b"\x00\x00" * total_frames)


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

    def test_process_chunk_filters_duplicate_segments(self):
        class DuplicatePipeline:
            def transcribe_buffer(self, audio_buffer):
                return TranscriptionResult(segments=[
                    Segment(
                        speaker_id="spk_0",
                        text="Hello there",
                        start=0.0,
                        end=1.0,
                    )
                ])

        session = TranscriptionSession("test-session-id", DuplicatePipeline())

        first_pass = session.process_chunk(b"\x00" * 3200)
        second_pass = session.process_chunk(b"\x00" * 3200)

        assert len(first_pass) == 1
        assert second_pass == []
        assert len(session.accumulated_transcript) == 1


class TestNemoPipeline:
    """Wrapper tests that avoid loading the real GPU models."""

    def test_transcribe_file_uses_diarization_segments(self, tmp_path, monkeypatch):
        audio_path = tmp_path / "sample.wav"
        write_silence_wav(audio_path, duration_seconds=4)

        pipeline = NemoPipeline(load_models=False)
        pipeline._models_loaded = True

        monkeypatch.setattr(
            pipeline,
            "_run_diarization",
            lambda _: [["0.000 1.000 speaker_0", "1.200 2.400 speaker_1"]],
        )

        clip_texts = iter(["Good morning", "Chest pain for two days"])
        monkeypatch.setattr(pipeline, "_transcribe_clip", lambda _: next(clip_texts))

        result = pipeline.transcribe_file(str(audio_path))

        assert [segment.speaker_id for segment in result.segments] == ["spk_0", "spk_1"]
        assert [segment.text for segment in result.segments] == [
            "Good morning",
            "Chest pain for two days",
        ]
        assert result.raw_output["audio_path"] == str(audio_path)
