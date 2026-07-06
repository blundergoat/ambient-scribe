"""Session adapter contract for the M22 streaming engine.

These tests use a fake engine so the adapter's invariants - emit-once row
identity, speaker cap, fragment merge, held-tail drain at finalize, and the
windowed default staying untouched - are pinned without GPU or NeMo imports.
"""

from __future__ import annotations

import pytest

from nemo_session import TranscriptionSession
from nemo_streaming_engine import EngineRow, streaming_engine_enabled


class FakeStreamingEngine:
    """Scripted engine: each feed() pops the next prepared row batch."""

    def __init__(self, feed_batches, flush_rows=None):
        self._feed_batches = list(feed_batches)
        self._flush_rows = list(flush_rows or [])
        self.pending_row_count = 1 if self._flush_rows else 0
        self.diagnostics = type(
            "Diag", (), {"late_slot_births": 0, "revision_resyncs": 0, "steps": 0}
        )()

    def feed(self, pcm_audio):
        if self._feed_batches:
            return self._feed_batches.pop(0)
        return []

    def flush(self):
        rows, self._flush_rows = self._flush_rows, []
        self.pending_row_count = 0
        return rows


def make_session(engine, monkeypatch=None, speaker_cap="2"):
    """Build a session on the mock pipeline with the fake engine injected."""
    import os

    os.environ["NEMO_MODEL_PROVIDER"] = "mock"
    os.environ["NEMO_SPEAKER_CAP"] = speaker_cap
    from nemo_pipeline import NemoPipeline

    return TranscriptionSession(
        "engine-test-session",
        pipeline=NemoPipeline(),
        input_format="pcm",
        streaming_engine=engine,
    )


PCM_CHUNK = b"\x00\x01" * 1600  # 0.1s of 16 kHz 16-bit audio


class TestEngineSelection:
    """The process-level flag chooses the engine exactly once, safely."""

    def test_flag_defaults_to_windowed(self, monkeypatch):
        monkeypatch.delenv("NEMO_SESSION_ENGINE", raising=False)
        assert streaming_engine_enabled() is False

    def test_flag_enables_streaming_only_on_exact_value(self, monkeypatch):
        monkeypatch.setenv("NEMO_SESSION_ENGINE", "streaming")
        assert streaming_engine_enabled() is True
        monkeypatch.setenv("NEMO_SESSION_ENGINE", "STREAMING")
        assert streaming_engine_enabled() is True
        # Typos fall back to windowed so the inference path cannot change by accident.
        monkeypatch.setenv("NEMO_SESSION_ENGINE", "streaming-fast")
        assert streaming_engine_enabled() is False

    def test_mock_pipeline_refuses_engine_construction(self):
        import os

        os.environ["NEMO_MODEL_PROVIDER"] = "mock"
        from nemo_pipeline import NemoPipeline

        with pytest.raises(RuntimeError):
            NemoPipeline().create_streaming_engine("s1")


class TestEngineRowEmission:
    """Engine rows flow through the shared cap/merge/identity machinery."""

    def test_rows_get_sequential_segment_ids_and_advance_the_mark(self):
        engine = FakeStreamingEngine(
            feed_batches=[
                [
                    EngineRow("speaker_0", "hello there", 0.5, 1.8),
                    EngineRow("speaker_1", "hi doctor", 2.0, 3.1),
                ],
                [EngineRow("speaker_0", "how can I help", 3.5, 5.0)],
            ]
        )
        session = make_session(engine)

        first = session.process_chunk(PCM_CHUNK)
        second = session.process_chunk(PCM_CHUNK)

        assert [seg.segment_id for seg in first] == ["seg-0001", "seg-0002"]
        assert [seg.segment_id for seg in second] == ["seg-0003"]
        assert session.accumulated_transcript[-1].text == "how can I help"
        assert session.quality_stats.emitted_segment_count == 3

    def test_speaker_cap_folds_marginal_slots_into_the_dominant_voice(self):
        engine = FakeStreamingEngine(
            feed_batches=[
                [
                    EngineRow("speaker_0", "the doctor speaking at length", 0.0, 8.0),
                    EngineRow("speaker_1", "the patient replying in detail", 8.2, 15.0),
                    EngineRow("speaker_3", "uh", 15.1, 15.3),
                ]
            ]
        )
        session = make_session(engine)

        emitted = session.process_chunk(PCM_CHUNK)

        speaker_ids = {segment.speaker_id for segment in emitted}
        assert "speaker_3" not in speaker_ids
        assert speaker_ids <= {"speaker_0", "speaker_1"}
        assert session.quality_stats.phantom_speaker_merge_count == 1

    def test_cap_disabled_keeps_every_engine_slot(self):
        engine = FakeStreamingEngine(
            feed_batches=[
                [
                    EngineRow("speaker_0", "one", 0.0, 1.0),
                    EngineRow("speaker_3", "two", 1.2, 2.0),
                ]
            ]
        )
        session = make_session(engine, speaker_cap="0")

        emitted = session.process_chunk(PCM_CHUNK)

        assert {segment.speaker_id for segment in emitted} == {
            "speaker_0",
            "speaker_3",
        }

    def test_adjacent_same_speaker_fragments_merge_before_identity(self):
        engine = FakeStreamingEngine(
            feed_batches=[
                [
                    EngineRow("speaker_0", "I have", 1.0, 1.4),
                    EngineRow("speaker_0", "a terrible headache", 1.45, 2.6),
                ]
            ]
        )
        session = make_session(engine)

        emitted = session.process_chunk(PCM_CHUNK)

        assert len(emitted) == 1
        assert "terrible headache" in emitted[0].text
        assert emitted[0].segment_id == "seg-0001"

    def test_finalize_drains_engine_flush_rows(self):
        engine = FakeStreamingEngine(
            feed_batches=[[EngineRow("speaker_0", "opening words", 0.0, 1.5)]],
            flush_rows=[EngineRow("speaker_1", "the held tail row", 90.0, 92.0)],
        )
        session = make_session(engine)
        session.process_chunk(PCM_CHUNK)

        tail = session.finalize()

        assert len(tail) == 1
        assert tail[0].text == "the held tail row"
        assert tail[0].segment_id == "seg-0002"
        assert session.accumulated_transcript[-1].text == "the held tail row"

    def test_windowed_default_never_touches_the_engine_path(self):
        session = make_session(engine=None)
        # The mock pipeline returns no segments; the point is that the call
        # routes through the windowed `_transcribe_unemitted` without error.
        assert session.process_chunk(PCM_CHUNK) == []
        assert session._streaming_engine is None


class TestEngineQualityLabel:
    """Quality artifacts must say which engine produced their numbers."""

    def test_engine_name_reflects_the_active_engine(self):
        streaming_session = make_session(FakeStreamingEngine(feed_batches=[]))
        windowed_session = make_session(engine=None)

        assert streaming_session.engine_name == "streaming"
        assert windowed_session.engine_name == "windowed"

    def test_substantial_late_slots_are_admitted_not_folded(self):
        """A real voice arriving late must never fold into another speaker."""
        engine = FakeStreamingEngine(
            feed_batches=[
                [
                    EngineRow("speaker_0", "established doctor voice here", 0.0, 5.0),
                    EngineRow("speaker_1", "established second doctor slot", 5.2, 9.0),
                ],
                [
                    # A third slot with substantial speech - a genuine voice.
                    EngineRow("speaker_2", "a genuine late-spawning voice", 10.0, 30.0),
                ],
            ]
        )
        session = make_session(engine)

        session.process_chunk(PCM_CHUNK)
        second = session.process_chunk(PCM_CHUNK)

        # Corrupting attribution is worse than a third visible ID.
        assert {segment.speaker_id for segment in second} == {"speaker_2"}
        assert session.quality_stats.phantom_speaker_merge_count == 0
