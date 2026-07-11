"""Session adapter contract for the M22 streaming engine.

These tests use a fake engine so the adapter's invariants - emit-once row
identity, speaker cap, fragment merge, held-tail drain at finalize, and the
windowed default staying untouched - are pinned without GPU or NeMo imports.
"""

from __future__ import annotations

import pytest

from nemo_session import TranscriptionSession
from nemo_streaming_engine import (
    EngineRow,
    StreamingSessionEngine,
    streaming_engine_enabled,
)


class FakeStreamingEngine:
    """Simulate the session-long engine for browser transcript adapter tests.

    Use it when a test needs visible rows, held-tail state, or speaker evidence
    without loading NeMo or using the clinician-facing GPU path.
    """

    def __init__(self, feed_batches, flush_rows=None):
        """Prepare the visible row batches returned as the browser sends audio."""
        self._feed_batches = list(feed_batches)
        self._flush_rows = list(flush_rows or [])
        self.pending_row_count = 1 if self._flush_rows else 0
        self.speaker_slot_voiced_frame_counts: dict[str, int] = {}
        self.diagnostics = type(
            "Diag", (), {"late_slot_births": 0, "revision_resyncs": 0, "steps": 0}
        )()

    def feed(self, pcm_audio):
        """Return the next rows when the simulated browser supplies audio."""
        # No prepared rows means the user has not produced stable transcript text yet.
        if self._feed_batches:
            return self._feed_batches.pop(0)
        return []

    def flush(self):
        """Return held rows after the simulated user presses Stop."""
        rows, self._flush_rows = self._flush_rows, []
        self.pending_row_count = 0
        return rows


def make_session(engine, monkeypatch=None, speaker_cap="2"):
    """Build the transcript session a browser recording would use in tests."""
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


def streaming_continuity_records(caplog) -> list:
    """Return diagnostic windows emitted for the simulated browser visit."""
    return [
        record
        for record in caplog.records
        if str(record.msg).startswith("nemo_session.window_continuity")
    ]


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

    def test_engine_rows_receive_medical_boost_correction(self, tmp_path, monkeypatch):
        """Streaming rows must pass the same lexicon seam as windowed rows."""
        lexicon_path = tmp_path / "medical_lexicon.txt"
        lexicon_path.write_text("metoprolol|metro pro lol\n", encoding="utf-8")
        monkeypatch.setenv("MEDICAL_BOOST_ENABLED", "1")
        monkeypatch.setenv("MEDICAL_LEXICON_PATH", str(lexicon_path))

        engine = FakeStreamingEngine(
            feed_batches=[
                [EngineRow("speaker_0", "continue metro pro lol daily", 0.5, 1.8)]
            ]
        )
        session = make_session(engine)

        segments = session.process_chunk(PCM_CHUNK)

        assert [seg.text for seg in segments] == ["continue metoprolol daily"]

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


class TestEngineSlotEvidence:
    """Keep fold diagnostics useful without exposing consultation wording.

    These tests cover the operator-only evidence used after a clinician spots
    wording under the wrong speaker; normal visits keep the evidence disabled.
    """

    def test_engine_reports_cumulative_voiced_frames_by_speaker_slot(self) -> None:
        """Expose frame totals an operator needs to compare speaker shares."""
        engine = StreamingSessionEngine.__new__(StreamingSessionEngine)
        engine._slot_frame_ledgers = {
            0: [(4, 0.24), (9, 0.72)],
            1: [],
            2: [(3, 0.56)],
        }

        assert engine.speaker_slot_voiced_frame_counts == {
            "speaker_0": 9,
            "speaker_2": 3,
        }

    def test_enabled_evidence_logs_slot_shares_and_phi_safe_folded_spans(
        self,
        caplog,
        monkeypatch,
    ) -> None:
        """Explain a wrong-speaker row without logging the words the user said."""
        monkeypatch.setenv("NEMO_STREAMING_SLOT_EVIDENCE", "1")
        caplog.set_level("INFO", logger="nemo_session")
        engine = FakeStreamingEngine(
            feed_batches=[
                [
                    EngineRow("speaker_0", "doctor wording", 0.0, 8.0),
                    EngineRow("speaker_1", "patient wording", 8.2, 15.0),
                    EngineRow("speaker_2", "short answer", 15.1, 15.3),
                ]
            ]
        )
        engine.speaker_slot_voiced_frame_counts = {
            "speaker_0": 100,
            "speaker_1": 80,
            "speaker_2": 3,
        }
        session = make_session(engine)

        session.process_chunk(PCM_CHUNK)

        evidence_record = streaming_continuity_records(caplog)[-1]
        marginal_slot = next(
            slot
            for slot in evidence_record.slot_share_evidence
            if slot["speaker_slot"] == "speaker_2"
        )
        assert marginal_slot == {
            "speaker_slot": "speaker_2",
            "window_voiced_frames": 3,
            "cumulative_voiced_frames": 3,
            "voiced_share": 0.0164,
            "cumulative_emitted_seconds": 0.2,
            "emitted_share": 0.0133,
            "fold_threshold_seconds": 1.5,
            "fold_decision": "fold_marginal",
        }
        assert evidence_record.folded_word_spans == [
            {
                "origin_speaker_slot": "speaker_2",
                "visible_speaker_slot": "speaker_0",
                "start_seconds": 15.1,
                "end_seconds": 15.3,
                "word_count": 2,
            }
        ]
        assert "doctor wording" not in str(evidence_record.slot_share_evidence)
        assert "patient wording" not in str(evidence_record.folded_word_spans)

    def test_disabled_evidence_omits_operator_only_fields(
        self,
        caplog,
        monkeypatch,
    ) -> None:
        """Keep ordinary visits free of detailed fold diagnostics by default."""
        monkeypatch.delenv("NEMO_STREAMING_SLOT_EVIDENCE", raising=False)
        caplog.set_level("INFO", logger="nemo_session")
        engine = FakeStreamingEngine(
            feed_batches=[[EngineRow("speaker_0", "ordinary row", 0.0, 1.0)]]
        )
        session = make_session(engine)

        session.process_chunk(PCM_CHUNK)

        ordinary_record = streaming_continuity_records(caplog)[-1]
        assert not hasattr(ordinary_record, "slot_share_evidence")
        assert not hasattr(ordinary_record, "folded_word_spans")


class TestEngineCrosstalkGuard:
    """Protect a real short turn without disabling phantom containment.

    These tests model the operator-enabled guard used when a clinician would
    otherwise see sustained Patient speech folded into the Doctor's row.
    """

    def test_enabled_guard_keeps_sustained_real_voice_separate(
        self,
        caplog,
        monkeypatch,
    ) -> None:
        """Keep a marginal row visible once its acoustic evidence proves a real voice."""
        monkeypatch.setenv("NEMO_STREAMING_CROSSTALK_GUARD", "1")
        monkeypatch.setenv("NEMO_STREAMING_SLOT_EVIDENCE", "1")
        caplog.set_level("INFO", logger="nemo_session")
        engine = FakeStreamingEngine(
            feed_batches=[
                [
                    EngineRow("speaker_0", "established doctor", 0.0, 8.0),
                    EngineRow("speaker_1", "established patient", 8.2, 15.0),
                    EngineRow("speaker_2", "short real answer", 15.1, 15.3),
                ]
            ]
        )
        engine.speaker_slot_voiced_frame_counts = {
            "speaker_0": 2000,
            "speaker_1": 1000,
            "speaker_2": 50,
        }
        session = make_session(engine)

        emitted = session.process_chunk(PCM_CHUNK)

        assert "speaker_2" in {segment.speaker_id for segment in emitted}
        assert session.quality_stats.phantom_speaker_merge_count == 0
        evidence_record = streaming_continuity_records(caplog)[-1]
        sustained_slot = next(
            slot
            for slot in evidence_record.slot_share_evidence
            if slot["speaker_slot"] == "speaker_2"
        )
        established_slot = next(
            slot
            for slot in evidence_record.slot_share_evidence
            if slot["speaker_slot"] == "speaker_0"
        )
        assert sustained_slot["fold_decision"] == "keep_sustained_voice"
        assert sustained_slot["sustained_voice_min_frames"] == 50
        assert sustained_slot["sustained_voice_min_share"] == 0.015
        assert established_slot["fold_decision"] == "keep_substantial"
        assert evidence_record.folded_word_spans == []

    def test_enabled_guard_still_folds_a_slot_below_the_voice_floor(
        self,
        monkeypatch,
    ) -> None:
        """Keep a weak phantom under the established card when it lacks sustained audio."""
        monkeypatch.setenv("NEMO_STREAMING_CROSSTALK_GUARD", "1")
        engine = FakeStreamingEngine(
            feed_batches=[
                [
                    EngineRow("speaker_0", "established doctor", 0.0, 8.0),
                    EngineRow("speaker_1", "established patient", 8.2, 15.0),
                    EngineRow("speaker_2", "decoder echo", 15.1, 15.3),
                ]
            ]
        )
        engine.speaker_slot_voiced_frame_counts = {
            "speaker_0": 1500,
            "speaker_1": 500,
            "speaker_2": 49,
        }
        session = make_session(engine)

        emitted = session.process_chunk(PCM_CHUNK)

        assert "speaker_2" not in {segment.speaker_id for segment in emitted}
        assert session.quality_stats.phantom_speaker_merge_count == 1

    def test_disabled_guard_preserves_the_existing_fold(
        self,
        monkeypatch,
    ) -> None:
        """Preserve exact release behavior when the operator leaves the guard off."""
        monkeypatch.delenv("NEMO_STREAMING_CROSSTALK_GUARD", raising=False)
        engine = FakeStreamingEngine(
            feed_batches=[
                [
                    EngineRow("speaker_0", "established doctor", 0.0, 8.0),
                    EngineRow("speaker_1", "established patient", 8.2, 15.0),
                    EngineRow("speaker_2", "short real answer", 15.1, 15.3),
                ]
            ]
        )
        engine.speaker_slot_voiced_frame_counts = {
            "speaker_0": 2000,
            "speaker_1": 1000,
            "speaker_2": 50,
        }
        session = make_session(engine)

        emitted = session.process_chunk(PCM_CHUNK)

        assert "speaker_2" not in {segment.speaker_id for segment in emitted}
        assert session.quality_stats.phantom_speaker_merge_count == 1


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


class TestEngineAwareStability:
    """The badge policy must judge each engine by its own failure modes."""

    def test_streaming_sessions_expose_engine_diagnostics(self):
        engine = FakeStreamingEngine(feed_batches=[])
        session = make_session(engine)
        assert session.engine_diagnostics is engine.diagnostics
        assert make_session(engine=None).engine_diagnostics is None

    def test_streaming_stability_ignores_phantom_folds(self):
        """Marginal-share folds are not identity churn on the streaming engine."""
        from api.role_inference_queue import (
            RoleInferenceServices,
            _build_role_stability,
        )

        engine = FakeStreamingEngine(feed_batches=[])
        session = make_session(engine)
        session.quality_stats.record_phantom_speaker_merges(2)
        session.quality_stats.record_window(32000)

        class FakeLifecycle:
            def get(self, _session_id):
                return session

        services = RoleInferenceServices(
            sessions=None,
            lifecycle=FakeLifecycle(),
            publish_to_mercure=None,
            run_role_inference=None,
            mercure_event_ids={},
        )

        stability = _build_role_stability("engine-test-session", services)
        assert stability["engine"] == "streaming"
        assert stability["level"] == "stable"

        # A late slot birth IS an identity anomaly on this engine.
        engine.diagnostics.late_slot_births = 1
        stability = _build_role_stability("engine-test-session", services)
        assert stability["level"] == "unstable"
