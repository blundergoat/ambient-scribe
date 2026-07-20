"""Session adapter contract for the M22 streaming engine.

These tests use a fake engine so the adapter's invariants - emit-once row
identity, speaker cap, fragment merge, held-tail drain at finalize, and the
windowed default staying untouched - are pinned without GPU or NeMo imports.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from nemo_session import TranscriptionSession
from nemo_streaming_engine import (
    EngineRow,
    StreamingSessionEngine,
    configured_max_transcript_hold_seconds,
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
        self.emission_decision_evidence: dict = {}
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

    @pytest.mark.parametrize(
        ("configured_seconds", "expected_seconds"),
        [
            (None, 0.0),
            ("", 0.0),
            ("0", 0.0),
            ("-5", 0.0),
            ("nan", 0.0),
            ("10", 10.0),
            ("10.5", 10.5),
        ],
    )
    def test_transcript_hold_bound_is_positive_or_safely_off(
        self,
        monkeypatch,
        configured_seconds,
        expected_seconds,
    ) -> None:
        """Accept a positive operator limit and keep missing or unsafe values off."""
        # An absent setting represents an ordinary visit using strict chronological release.
        if configured_seconds is None:
            monkeypatch.delenv(
                "NEMO_STREAMING_MAX_TRANSCRIPT_HOLD_SECONDS", raising=False
            )
        else:
            monkeypatch.setenv(
                "NEMO_STREAMING_MAX_TRANSCRIPT_HOLD_SECONDS",
                configured_seconds,
            )

        assert configured_max_transcript_hold_seconds() == expected_seconds


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


def make_release_evidence_engine(
    *,
    step_clock_seconds: float,
    pending_rows: list[EngineRow],
    unstable_word_starts_by_slot: dict[int, list[float]],
    last_activity_by_slot: dict[int, float],
    max_transcript_hold_seconds: float = 0.0,
) -> StreamingSessionEngine:
    """Build only the release state used to explain a clinician-visible transcript pause.

    Tests use this CPU-only engine when they need real frontier decisions without loading NeMo.

    Args:
        max_transcript_hold_seconds: Operator-selected release bound; zero keeps the user-visible
            transcript on the unbounded release policy.
    """
    engine = StreamingSessionEngine.__new__(StreamingSessionEngine)
    engine._streamer = SimpleNamespace(_offset_chunk_start_time=step_clock_seconds)
    engine._ordered_pending = list(pending_rows)
    engine._word_logs = {
        slot_index: [SimpleNamespace(start=word_start) for word_start in word_starts]
        for slot_index, word_starts in unstable_word_starts_by_slot.items()
    }
    engine._emitted_word_counts = {
        slot_index: 0 for slot_index in unstable_word_starts_by_slot
    }
    engine._slot_last_burst_end = dict(last_activity_by_slot)
    engine._max_transcript_hold_seconds = max_transcript_hold_seconds
    from nemo_streaming_engine import EngineDiagnostics

    engine.diagnostics = EngineDiagnostics()
    return engine


class TestEngineEmissionDecisionEvidence:
    """Explain why stable rows reached the browser now or remained held.

    These contracts keep diagnostics PHI-safe and pin the active-vs-dormant frontier decision.
    """

    def test_active_unstable_slot_names_the_frontier_hold(self) -> None:
        """Show an operator when an active slot blocks a clock-ready transcript row."""
        engine = make_release_evidence_engine(
            step_clock_seconds=30.0,
            pending_rows=[EngineRow("speaker_1", "private wording", 10.0, 11.0)],
            unstable_word_starts_by_slot={0: [5.0, 6.0]},
            last_activity_by_slot={0: 28.0},
        )

        released_rows = engine._release_ordered_rows(final=False)

        assert released_rows == []
        assert engine.emission_decision_evidence == {
            "decision": "hold_stability_frontier",
            "step_clock_seconds": 30.0,
            "clock_horizon_seconds": 25.0,
            "stability_frontier_seconds": 5.0,
            "release_horizon_seconds": 5.0,
            "stability_hold_seconds": 20.0,
            "max_transcript_hold_seconds": 0.0,
            "bounded_release_applied": False,
            "pending_rows_before": 1,
            "clock_ready_rows": 1,
            "released_rows": 0,
            "pending_rows_after": 1,
            "unstable_slots": [
                {
                    "speaker_slot": "speaker_0",
                    "unemitted_words": 2,
                    "oldest_unstable_start_seconds": 5.0,
                    "last_activity_seconds": 28.0,
                    "inactive_seconds": 2.0,
                    "excluded_as_dormant": False,
                }
            ],
        }
        assert "private wording" not in str(engine.emission_decision_evidence)

    def test_active_mutable_tail_names_why_no_stable_row_exists(self) -> None:
        """Show an operator when every heard word is still revisable and no row exists."""
        engine = make_release_evidence_engine(
            step_clock_seconds=30.0,
            pending_rows=[],
            unstable_word_starts_by_slot={0: [24.0, 25.0]},
            last_activity_by_slot={0: 29.0},
        )

        released_rows = engine._release_ordered_rows(final=False)

        assert released_rows == []
        evidence = engine.emission_decision_evidence
        assert evidence["decision"] == "hold_mutable_tail"
        assert evidence["pending_rows_before"] == 0
        assert evidence["clock_ready_rows"] == 0
        assert evidence["unstable_slots"][0]["unemitted_words"] == 2

    def test_dormant_small_tail_stops_pinning_visible_rows(self) -> None:
        """Release a ready row once the only older unstable tail is safely dormant."""
        engine = make_release_evidence_engine(
            step_clock_seconds=30.0,
            pending_rows=[EngineRow("speaker_1", "private wording", 10.0, 11.0)],
            unstable_word_starts_by_slot={0: [5.0, 6.0]},
            last_activity_by_slot={0: 10.0},
        )

        released_rows = engine._release_ordered_rows(final=False)

        assert [row.start for row in released_rows] == [10.0]
        evidence = engine.emission_decision_evidence
        assert evidence["decision"] == "release_ready_rows"
        assert evidence["stability_frontier_seconds"] is None
        assert evidence["released_rows"] == 1
        assert evidence["pending_rows_after"] == 0
        assert evidence["unstable_slots"][0]["excluded_as_dormant"] is True

    def test_enabled_adapter_log_carries_only_emission_counts_and_times(
        self,
        caplog,
        monkeypatch,
    ) -> None:
        """Expose one release decision for a named replay without logging consultation words."""
        monkeypatch.setenv("NEMO_STREAMING_SLOT_EVIDENCE", "1")
        caplog.set_level("INFO", logger="nemo_session")
        engine = FakeStreamingEngine(feed_batches=[])
        engine.emission_decision_evidence = {
            "decision": "hold_stability_frontier",
            "pending_rows_before": 7,
            "released_rows": 0,
        }
        session = make_session(engine)

        session.process_chunk(PCM_CHUNK)

        evidence_record = streaming_continuity_records(caplog)[-1]
        assert evidence_record.emission_decision_evidence == {
            "decision": "hold_stability_frontier",
            "pending_rows_before": 7,
            "released_rows": 0,
        }
        assert "private wording" not in str(evidence_record.emission_decision_evidence)


class TestBoundedTranscriptRelease:
    """Keep long-turn transcript delivery bounded without exposing mutable wording.

    These CPU contracts represent a clinician watching stable rows arrive during a long answer.
    The normal off state retains strict chronological release for ordinary user visits.
    """

    def test_default_off_keeps_clock_ready_rows_behind_the_frontier(self) -> None:
        """Keep existing visible ordering when the operator has not enabled a hold bound."""
        engine = make_release_evidence_engine(
            step_clock_seconds=30.0,
            pending_rows=[EngineRow("speaker_1", "stable later row", 10.0, 11.0)],
            unstable_word_starts_by_slot={0: [5.0, 6.0]},
            last_activity_by_slot={0: 28.0},
        )

        released_rows = engine._release_ordered_rows(final=False)

        assert released_rows == []
        assert (
            engine.emission_decision_evidence["decision"] == "hold_stability_frontier"
        )

    def test_elapsed_bound_releases_only_stable_clock_ready_rows(self) -> None:
        """Show stable wording after ten seconds while a newer future row still waits."""
        engine = make_release_evidence_engine(
            step_clock_seconds=30.0,
            pending_rows=[
                EngineRow("speaker_1", "stable ready row", 10.0, 11.0),
                EngineRow("speaker_1", "future stable row", 28.0, 29.0),
            ],
            unstable_word_starts_by_slot={0: [5.0, 6.0]},
            last_activity_by_slot={0: 28.0},
            max_transcript_hold_seconds=10.0,
        )

        released_rows = engine._release_ordered_rows(final=False)

        assert [row.start for row in released_rows] == [10.0]
        assert [row.start for row in engine._ordered_pending] == [28.0]
        assert engine._emitted_word_counts == {0: 0}
        evidence = engine.emission_decision_evidence
        assert evidence["decision"] == "release_bounded_stability_frontier"
        assert evidence["stability_hold_seconds"] == 20.0
        assert evidence["max_transcript_hold_seconds"] == 10.0
        assert evidence["bounded_release_applied"] is True

    def test_next_browser_tick_releases_stable_rows_before_overshoot(self) -> None:
        """Show ready wording now when the next browser audio tick would exceed the bound."""
        engine = make_release_evidence_engine(
            step_clock_seconds=30.0,
            pending_rows=[
                EngineRow("speaker_1", "stable ready row", 20.0, 21.0),
                EngineRow("speaker_1", "future stable row", 28.0, 29.0),
            ],
            unstable_word_starts_by_slot={0: [15.2, 16.0]},
            last_activity_by_slot={0: 29.0},
            max_transcript_hold_seconds=10.0,
        )

        released_rows = engine._release_ordered_rows(final=False)

        assert [row.start for row in released_rows] == [20.0]
        assert [row.start for row in engine._ordered_pending] == [28.0]
        evidence = engine.emission_decision_evidence
        assert evidence["stability_hold_seconds"] == 9.8
        assert evidence["bounded_release_applied"] is True

    def test_next_browser_tick_cannot_release_without_stable_wording(self) -> None:
        """Keep startup silent when the user has spoken but every heard word is revisable."""
        engine = make_release_evidence_engine(
            step_clock_seconds=30.0,
            pending_rows=[],
            unstable_word_starts_by_slot={0: [15.2, 16.0]},
            last_activity_by_slot={0: 29.0},
            max_transcript_hold_seconds=10.0,
        )

        released_rows = engine._release_ordered_rows(final=False)

        assert released_rows == []
        evidence = engine.emission_decision_evidence
        assert evidence["decision"] == "hold_mutable_tail"
        assert evidence["clock_ready_rows"] == 0
        assert evidence["bounded_release_applied"] is False

    def test_recent_frontier_still_protects_spoken_order(self) -> None:
        """Keep a stable row waiting while its older wording is still within the bound."""
        engine = make_release_evidence_engine(
            step_clock_seconds=30.0,
            pending_rows=[EngineRow("speaker_1", "stable later row", 21.0, 22.0)],
            unstable_word_starts_by_slot={0: [20.0, 21.0]},
            last_activity_by_slot={0: 29.0},
            max_transcript_hold_seconds=10.0,
        )

        released_rows = engine._release_ordered_rows(final=False)

        assert released_rows == []
        evidence = engine.emission_decision_evidence
        assert evidence["decision"] == "hold_stability_frontier"
        assert evidence["stability_hold_seconds"] == 5.0
        assert evidence["bounded_release_applied"] is False


class FakeDiarPredictionStream:
    """Minimal cumulative diarizer prediction tensor for CPU pairwise tests.

    It implements only the operations `_record_diar_activity` performs on the
    real stream: shape queries, new-frame slicing, per-slot thresholding, and
    active-index extraction. Frames append over time like NeMo's stream.
    """

    def __init__(self, frame_rows):
        """Store per-frame slot activations, e.g. [[1.0, 0.0], [0.0, 1.0]]."""
        self._frame_rows = list(frame_rows)
        self.ndim = 3

    def extend(self, frame_rows):
        """Append later-step frames the way the cumulative stream grows."""
        self._frame_rows.extend(frame_rows)

    def size(self, dimension):
        """Return the (1, frames, slots) shape the sampler expects."""
        if dimension == 1:
            return len(self._frame_rows)
        if dimension == 2:
            return len(self._frame_rows[0]) if self._frame_rows else 0
        return 1

    def __getitem__(self, key):
        """Serve the sampler's `preds[0, seen:, :]` new-frame slice."""
        _, frame_slice, _ = key
        return _FakeFrameWindow(self._frame_rows[frame_slice])


class _FakeFrameWindow:
    """One already-sliced batch of new frames."""

    def __init__(self, frame_rows):
        self._frame_rows = frame_rows

    def __getitem__(self, key):
        """Serve the sampler's `new_preds[:, slot_index]` column read."""
        _, slot_index = key
        return _FakeSlotColumn([row[slot_index] for row in self._frame_rows])


class _FakeSlotColumn:
    """One slot's activation values across the new frames."""

    def __init__(self, values):
        self._values = values

    def __gt__(self, threshold):
        return _FakeActiveIndices(
            [index for index, value in enumerate(self._values) if value > threshold]
        )


class _FakeActiveIndices:
    """Active-frame indices supporting nonzero/numel/flatten/tolist."""

    def __init__(self, indices):
        self._indices = indices

    def nonzero(self):
        return self

    def numel(self):
        return len(self._indices)

    def flatten(self):
        return self

    def tolist(self):
        return list(self._indices)


def make_diar_activity_engine(prediction_stream) -> StreamingSessionEngine:
    """Build only the diar-sampling state used by pairwise activity tests."""
    engine = StreamingSessionEngine.__new__(StreamingSessionEngine)
    engine._streamer = SimpleNamespace(
        instance_manager=SimpleNamespace(
            diar_states=SimpleNamespace(diar_pred_out_stream=prediction_stream)
        )
    )
    engine._slot_frame_ledgers = {}
    engine._slot_last_burst_end = {}
    engine._slot_pair_co_active_frames = {}
    engine._diar_frames_seen = 0
    engine._frame_len_sec = 0.08
    return engine


class TestEnginePairwiseSlotActivity:
    """Count when two cache slots speak together versus alone, bounded and PHI-free.

    These CPU contracts pin the M02 diagnostic counters that later distinguish a
    harmful real-turn fold from a benign duplicate without transcript wording.
    """

    def test_pairwise_counters_accumulate_co_active_frames(self) -> None:
        """Two slots sharing two frames yield one pair counter of exactly two."""
        stream = FakeDiarPredictionStream(
            [[1.0, 1.0], [1.0, 0.0], [0.0, 1.0], [1.0, 1.0], [0.0, 0.0]]
        )
        engine = make_diar_activity_engine(stream)

        engine._record_diar_activity()

        assert engine._slot_pair_co_active_frames == {(0, 1): 2}
        assert engine.speaker_slot_pair_co_active_frame_counts == {
            "speaker_0|speaker_1": 2
        }
        # Per-slot ledgers keep their existing totals beside the new pair count.
        assert engine._slot_frame_ledgers[0][-1][0] == 3
        assert engine._slot_frame_ledgers[1][-1][0] == 3

    def test_pairwise_counters_normalize_slot_order_keys(self) -> None:
        """Non-adjacent slots report one lower-index-first normalized key."""
        stream = FakeDiarPredictionStream([[1.0, 0.0, 1.0], [1.0, 0.0, 1.0]])
        engine = make_diar_activity_engine(stream)

        engine._record_diar_activity()

        assert engine._slot_pair_co_active_frames == {(0, 2): 2}
        assert engine.speaker_slot_pair_co_active_frame_counts == {
            "speaker_0|speaker_2": 2
        }

    def test_pairwise_counters_ignore_repeated_snapshots(self) -> None:
        """Re-sampling an unchanged stream adds nothing; new frames add only deltas."""
        stream = FakeDiarPredictionStream([[1.0, 1.0], [1.0, 1.0]])
        engine = make_diar_activity_engine(stream)

        engine._record_diar_activity()
        engine._record_diar_activity()

        assert engine._slot_pair_co_active_frames == {(0, 1): 2}

        stream.extend([[1.0, 1.0], [1.0, 0.0]])
        engine._record_diar_activity()

        assert engine._slot_pair_co_active_frames == {(0, 1): 3}

    def test_pairwise_counters_cover_more_than_two_slots(self) -> None:
        """Three co-active slots produce every pair with its own frame count."""
        stream = FakeDiarPredictionStream(
            [[1.0, 1.0, 1.0], [1.0, 1.0, 1.0], [0.0, 1.0, 1.0]]
        )
        engine = make_diar_activity_engine(stream)

        engine._record_diar_activity()

        assert engine._slot_pair_co_active_frames == {
            (0, 1): 2,
            (0, 2): 2,
            (1, 2): 3,
        }

    def test_pairwise_counters_cover_four_slot_frames(self) -> None:
        """All four diarizer slots voicing at once yield every one of the six pairs."""
        stream = FakeDiarPredictionStream([[1.0, 1.0, 1.0, 1.0], [1.0, 1.0, 0.0, 1.0]])
        engine = make_diar_activity_engine(stream)

        engine._record_diar_activity()

        assert engine._slot_pair_co_active_frames == {
            (0, 1): 2,
            (0, 2): 1,
            (0, 3): 2,
            (1, 2): 1,
            (1, 3): 2,
            (2, 3): 1,
        }

    def test_property_returns_a_defensive_copy(self) -> None:
        """Mutating the returned mapping cannot corrupt the engine's counters."""
        stream = FakeDiarPredictionStream([[1.0, 1.0]])
        engine = make_diar_activity_engine(stream)
        engine._record_diar_activity()

        snapshot = engine.speaker_slot_pair_co_active_frame_counts
        snapshot["speaker_0|speaker_1"] = 999

        assert engine.speaker_slot_pair_co_active_frame_counts == {
            "speaker_0|speaker_1": 1
        }

    def test_close_releases_pairwise_counters_and_frame_ledgers(self) -> None:
        """Session close drops every pairwise counter beside the existing ledgers."""
        from nemo_streaming_engine import EngineDiagnostics

        engine = StreamingSessionEngine.__new__(StreamingSessionEngine)
        engine._word_logs = {0: []}
        engine._emitted_word_counts = {0: 0}
        engine._slot_last_burst_end = {0: 1.0}
        engine._ordered_pending = []
        engine.diagnostics = EngineDiagnostics()
        engine._slot_frame_ledgers = {0: [(4, 0.24)]}
        engine._slot_token_counts = {0: 2}
        engine._slot_pair_co_active_frames = {(0, 1): 7}
        engine._buffer_iter = None
        engine._buffer = None
        engine._streamer = None

        engine.close()

        assert engine._slot_pair_co_active_frames == {}
        assert engine._slot_frame_ledgers == {}


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

    def test_enabled_evidence_logs_pairwise_deltas_and_exclusive_frames(
        self,
        caplog,
        monkeypatch,
    ) -> None:
        """Report per-window co-active and exclusive frames as counts only."""
        monkeypatch.setenv("NEMO_STREAMING_SLOT_EVIDENCE", "1")
        caplog.set_level("INFO", logger="nemo_session")
        engine = FakeStreamingEngine(
            feed_batches=[
                [
                    EngineRow("speaker_0", "doctor wording", 0.0, 8.0),
                    EngineRow("speaker_2", "short answer", 8.2, 8.4),
                ],
                [EngineRow("speaker_0", "more doctor wording", 9.0, 12.0)],
            ]
        )
        engine.speaker_slot_voiced_frame_counts = {"speaker_0": 100, "speaker_2": 30}
        engine.speaker_slot_pair_co_active_frame_counts = {"speaker_0|speaker_2": 12}
        engine.diar_sample_frame_total = 400
        session = make_session(engine)

        session.process_chunk(PCM_CHUNK)

        first_record = streaming_continuity_records(caplog)[-1]
        assert first_record.pairwise_slot_evidence == {
            "window_sample_frames": 400,
            "cumulative_sample_frames": 400,
            "pairs": [
                {
                    "speaker_slot_pair": "speaker_0|speaker_2",
                    "window_co_active_frames": 12,
                    "cumulative_co_active_frames": 12,
                    "window_exclusive_frames": {"speaker_0": 88, "speaker_2": 18},
                }
            ],
        }

        # The next browser window adds voice only for speaker_0 and no co-activity,
        # so the pair reports a zero co-active delta with one-sided exclusive frames.
        engine.speaker_slot_voiced_frame_counts = {"speaker_0": 150, "speaker_2": 30}
        engine.diar_sample_frame_total = 700
        session.process_chunk(PCM_CHUNK)

        second_record = streaming_continuity_records(caplog)[-1]
        assert second_record.pairwise_slot_evidence == {
            "window_sample_frames": 300,
            "cumulative_sample_frames": 700,
            "pairs": [
                {
                    "speaker_slot_pair": "speaker_0|speaker_2",
                    "window_co_active_frames": 0,
                    "cumulative_co_active_frames": 12,
                    "window_exclusive_frames": {"speaker_0": 50, "speaker_2": 0},
                }
            ],
        }
        assert "doctor wording" not in str(second_record.pairwise_slot_evidence)

    def test_pairwise_evidence_stays_absent_without_engine_counters(
        self,
        caplog,
        monkeypatch,
    ) -> None:
        """An engine without pairwise counters yields no fabricated zero evidence."""
        monkeypatch.setenv("NEMO_STREAMING_SLOT_EVIDENCE", "1")
        caplog.set_level("INFO", logger="nemo_session")
        engine = FakeStreamingEngine(
            feed_batches=[[EngineRow("speaker_0", "ordinary row", 0.0, 1.0)]]
        )
        session = make_session(engine)

        session.process_chunk(PCM_CHUNK)

        record = streaming_continuity_records(caplog)[-1]
        assert not hasattr(record, "pairwise_slot_evidence")

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
        engine.speaker_slot_pair_co_active_frame_counts = {"speaker_0|speaker_1": 5}
        engine.diar_sample_frame_total = 100
        session = make_session(engine)

        session.process_chunk(PCM_CHUNK)

        ordinary_record = streaming_continuity_records(caplog)[-1]
        assert not hasattr(ordinary_record, "slot_share_evidence")
        assert not hasattr(ordinary_record, "folded_word_spans")
        assert not hasattr(ordinary_record, "emission_decision_evidence")
        assert not hasattr(ordinary_record, "pairwise_slot_evidence")


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
