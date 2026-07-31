"""
End-to-end contracts for per-row word confidence (0.4.0 confidence spike, phase 2).

Every lane that shows transcript rows to the clinician - live streaming
emission, windowed emission, post-visit corrected rows, storage restore, and
the browser summary round-trip - must carry an additive `confidence` value,
and rows without one must keep rendering exactly as before.
"""

from __future__ import annotations

import sqlite3

import pytest

from nemo_confidence import (
    row_confidence_for_word_share,
    transcript_row_confidence,
    word_confidences_for_display_words,
)
from nemo_pipeline import NemoPipeline, Segment
from nemo_segment_cleanup import merge_adjacent_fragments_for_display
from nemo_session import TranscriptionSession
from nemo_streaming_engine import EngineRow, StreamingSessionEngine
from post_visit_correction import (
    build_corrected_segments,
    run_post_visit_correction,
    stamp_corrected_row_confidence,
)
from post_visit_word_timing import PostVisitTranscription, validated_word_confidences
from session import SessionStore
from storage import SqliteBackend
from api.summary_request import (
    BrowserVisibleSegment,
    normalise_browser_visible_segments,
)


class FakeHypothesis:
    """NeMo-shaped hypothesis stub carrying text and word confidence."""

    def __init__(self, text: str, word_confidence: list[float] | None) -> None:
        self.text = text
        self.word_confidence = word_confidence
        self.timestamp = None


class TestConfidenceHelpers:
    """The shared joins that turn word confidence into row confidence."""

    def test_row_confidence_is_the_minimum_of_known_values(self):
        """One weakly heard word marks the whole row for the clinician's attention."""
        assert transcript_row_confidence([0.9, 0.7231, None, 0.85]) == 0.7231

    def test_row_confidence_is_absent_without_any_values(self):
        """Rows with no measured words keep today's unstyled rendering."""
        assert transcript_row_confidence([]) is None
        assert transcript_row_confidence([None, None]) is None

    def test_word_join_requires_one_value_per_display_word(self):
        """A drifted confidence list must never style the wrong transcript rows."""
        aligned = FakeHypothesis("hello there", [0.9, 0.8])
        misaligned = FakeHypothesis("hello there", [0.9])
        missing = FakeHypothesis("hello there", None)

        assert word_confidences_for_display_words(aligned, ["hello", "there"]) == [0.9, 0.8]
        assert word_confidences_for_display_words(misaligned, ["hello", "there"]) is None
        assert word_confidences_for_display_words(missing, ["hello", "there"]) is None

    def test_word_share_maps_visible_span_onto_raw_values(self):
        """Windowed rows read the raw-word stretch matching their visible share."""
        raw_values = [0.9, 0.8, 0.4, 0.95]

        # First half of four visible words reads the first half of raw values.
        assert row_confidence_for_word_share(raw_values, 0, 2, 4) == 0.8
        # The weak third raw word lands in the second visible half.
        assert row_confidence_for_word_share(raw_values, 2, 4, 4) == 0.4

    def test_word_share_handles_absent_or_empty_allocations(self):
        """Rows with no words or no values stay unmeasured instead of guessing."""
        assert row_confidence_for_word_share(None, 0, 2, 4) is None
        assert row_confidence_for_word_share([0.9], 2, 2, 4) is None
        assert row_confidence_for_word_share([0.9], 0, 1, 0) is None


class TestSegmentPayload:
    """Segment.dict emits confidence additively for Mercure and storage."""

    def test_measured_segment_publishes_confidence(self):
        """A measured row's value reaches the browser payload."""
        segment = Segment(
            speaker_id="speaker_0", text="hi", start=0.0, end=1.0, confidence=0.7412
        )
        assert segment.dict()["confidence"] == 0.7412

    def test_unmeasured_segment_omits_the_key(self):
        """Legacy rows keep their exact pre-confidence payload shape."""
        segment = Segment(speaker_id="speaker_0", text="hi", start=0.0, end=1.0)
        assert "confidence" not in segment.dict()

    def test_fragment_merge_keeps_the_weakest_part(self):
        """Merging two fragments shows the clinician the weaker hearing of the pair."""
        rows = [
            Segment(speaker_id="s0", text="so", start=0.0, end=0.4, confidence=0.9),
            Segment(speaker_id="s0", text="tired", start=0.5, end=0.8, confidence=0.6),
        ]
        merged = merge_adjacent_fragments_for_display(rows)

        assert len(merged) == 1
        assert merged[0].confidence == 0.6

    def test_fragment_merge_tolerates_unmeasured_parts(self):
        """A merge with an unmeasured part keeps whatever evidence exists."""
        one_measured = merge_adjacent_fragments_for_display(
            [
                Segment(speaker_id="s0", text="so", start=0.0, end=0.4),
                Segment(speaker_id="s0", text="tired", start=0.5, end=0.8, confidence=0.6),
            ]
        )
        none_measured = merge_adjacent_fragments_for_display(
            [
                Segment(speaker_id="s0", text="so", start=0.0, end=0.4),
                Segment(speaker_id="s0", text="tired", start=0.5, end=0.8),
            ]
        )

        assert one_measured[0].confidence == 0.6
        assert none_measured[0].confidence is None


def make_bookkeeping_engine() -> StreamingSessionEngine:
    """Build an engine with only the word-log state the absorb/emit path uses.

    Bypasses __init__ so no torch/NeMo import or GPU model is needed; the
    hypothesis-side behaviour is what these tests pin.
    """
    engine = object.__new__(StreamingSessionEngine)
    engine.session_id = "confidence-test"
    engine._word_logs = {}
    engine._emitted_word_counts = {}
    engine._slot_token_counts = {}
    engine._slot_frame_ledgers = {}
    engine._slot_last_burst_end = {}
    engine._frame_len_sec = 0.08
    engine._ordered_pending = []
    from nemo_streaming_engine import EngineDiagnostics

    engine.diagnostics = EngineDiagnostics()

    class StubState:
        previous_hypothesis: list = []

    class StubInstanceManager:
        batch_asr_states = [StubState()]

    class StubStreamer:
        instance_manager = StubInstanceManager()
        _offset_chunk_start_time = 0.0

    engine._streamer = StubStreamer()
    return engine


def absorb_hypothesis(engine: StreamingSessionEngine, hypothesis: FakeHypothesis) -> None:
    """Feed one per-slot hypothesis through the real absorb path."""
    engine._streamer.instance_manager.batch_asr_states[0].previous_hypothesis = [hypothesis]
    engine._absorb_step_hypotheses()


class TestEngineWordConfidence:
    """The streaming word log carries and refreshes per-word confidence."""

    def test_emitted_row_confidence_is_the_group_minimum(self):
        """A live row emits with the weakest word its span contains."""
        engine = make_bookkeeping_engine()
        absorb_hypothesis(engine, FakeHypothesis("I have headaches", [0.9, 0.95, 0.62]))

        rows = engine._emit_stable_words(mutable_tail_words=0)

        assert [row.text for row in rows] == ["I have headaches"]
        assert rows[0].confidence == 0.62

    def test_later_steps_refresh_confidence_of_held_words(self):
        """Held words firm up as NeMo hears more context, before the row emits."""
        engine = make_bookkeeping_engine()
        absorb_hypothesis(engine, FakeHypothesis("I have head", [0.9, 0.4, 0.5]))
        # NeMo revised the tail word and now hears the middle word clearly.
        absorb_hypothesis(engine, FakeHypothesis("I have headaches", [0.9, 0.95, 0.62]))

        rows = engine._emit_stable_words(mutable_tail_words=0)

        assert rows[0].text == "I have headaches"
        assert rows[0].confidence == 0.62

    def test_misaligned_confidence_is_ignored_not_misapplied(self):
        """A drifted per-step list is dropped so held words keep their last good values."""
        engine = make_bookkeeping_engine()
        absorb_hypothesis(engine, FakeHypothesis("I have headaches", [0.9, 0.95, 0.62]))
        # A drifted list (two values for three words) must not shift rows.
        absorb_hypothesis(engine, FakeHypothesis("I have headaches", [0.1, 0.1]))

        rows = engine._emit_stable_words(mutable_tail_words=0)

        assert rows[0].confidence == 0.62

    def test_words_without_confidence_emit_unmeasured_rows(self):
        """A decode without confidence emits rows that render exactly as today."""
        engine = make_bookkeeping_engine()
        absorb_hypothesis(engine, FakeHypothesis("I have headaches", None))

        rows = engine._emit_stable_words(mutable_tail_words=0)

        assert rows[0].confidence is None


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


PCM_CHUNK = b"\x00\x01" * 1600  # 0.1s of 16 kHz 16-bit audio


def make_session(engine) -> TranscriptionSession:
    """Build a session on the mock pipeline with the fake engine injected."""
    import os

    os.environ["NEMO_MODEL_PROVIDER"] = "mock"
    return TranscriptionSession(
        "confidence-session",
        pipeline=NemoPipeline(),
        input_format="pcm",
        streaming_engine=engine,
    )


class TestSessionEmission:
    """Live emission publishes row confidence through Segment payloads."""

    def test_engine_row_confidence_reaches_the_browser_payload(self):
        """Live streaming confidence travels engine -> session -> browser payload."""
        engine = FakeStreamingEngine(
            [[EngineRow("speaker_0", "I have headaches since midday", 0.0, 2.0, 0.71)]]
        )
        session = make_session(engine)

        payloads = [segment.dict() for segment in session.process_chunk(PCM_CHUNK)]

        assert payloads[0]["confidence"] == 0.71

    def test_unmeasured_engine_rows_stay_without_the_key(self):
        """Unmeasured live rows never gain a confidence key on the way out."""
        engine = FakeStreamingEngine(
            [[EngineRow("speaker_0", "I have headaches since midday", 0.0, 2.0)]]
        )
        session = make_session(engine)

        payloads = [segment.dict() for segment in session.process_chunk(PCM_CHUNK)]

        assert "confidence" not in payloads[0]


class TestWindowedShareMapping:
    """The windowed engine maps raw-word confidence onto proportional rows."""

    def test_rows_receive_their_own_stretch_of_raw_confidence(self):
        """Windowed transcript cards each reflect their own stretch of audio."""
        pipeline = NemoPipeline()
        segments = pipeline._parse_nemo_output(
            ["0.0 2.0 speaker_0", "2.0 4.0 speaker_1"],
            [FakeHypothesis("one two three four", [0.9, 0.8, 0.4, 0.95])],
        )

        assert [segment.text for segment in segments] == ["one two", "three four"]
        assert [segment.confidence for segment in segments] == [0.8, 0.4]

    def test_rows_stay_unmeasured_when_the_decode_carried_none(self):
        """A confidence-less windowed decode leaves cards unstyled."""
        pipeline = NemoPipeline()
        segments = pipeline._parse_nemo_output(
            ["0.0 2.0 speaker_0"],
            [FakeHypothesis("one two", None)],
        )

        assert segments[0].confidence is None


class TestCorrectedRowConfidence:
    """Post-visit corrected rows locate their words and carry confidence."""

    def test_injected_transcriber_confidence_reaches_corrected_rows(self):
        """After Stop, corrected rows carry the second-pass hearing quality."""
        result = run_post_visit_correction(
            pcm_audio=(b"\0\0" * 160),
            live_segments=[
                {
                    "speaker_id": "speaker_0",
                    "role": "DOCTOR",
                    "text": "hello there friend",
                    "start": 0.0,
                    "end": 1.0,
                }
            ],
            transcribe_audio_file=lambda _model, _path: PostVisitTranscription(
                text="hello there friend",
                word_confidences=[0.9, 0.61, 0.8],
            ),
        )

        assert result.segments[0]["confidence"] == 0.61

    def test_misaligned_confidence_leaves_corrected_rows_unmeasured(self):
        """Corrected rows refuse values that do not pair with their words."""
        result = run_post_visit_correction(
            pcm_audio=(b"\0\0" * 160),
            live_segments=[
                {
                    "speaker_id": "speaker_0",
                    "role": "DOCTOR",
                    "text": "hello there friend",
                    "start": 0.0,
                    "end": 1.0,
                }
            ],
            transcribe_audio_file=lambda _model, _path: PostVisitTranscription(
                text="hello there friend",
                word_confidences=[0.9, 0.61],
            ),
        )

        assert all("confidence" not in row for row in result.segments)

    def test_unscaffolded_fallback_rows_are_stamped_too(self):
        """Even a no-live-transcript correction carries row confidence."""
        segments = build_corrected_segments(
            corrected_words=["hello", "there"],
            live_segments=[],
            model_name="test-model",
            word_confidences=[0.9, 0.66],
        )

        assert segments[0]["confidence"] == 0.66

    def test_rows_not_owned_by_asr_words_stay_unmeasured(self):
        """Live-fallback rows keep rendering plain - their text was never re-heard."""
        rows = [{"segment_id": "corrected-0001", "text": "totally different words"}]

        stamped = stamp_corrected_row_confidence(rows, ["hello", "there"], [0.9, 0.8])

        assert "confidence" not in stamped[0]

    def test_validated_word_confidences_requires_one_value_per_word(self):
        """Only value lists pairing 1:1 with display words are trusted."""
        assert validated_word_confidences([0.9, 0.8], ["hi", "there"]) == [0.9, 0.8]
        assert validated_word_confidences([0.9], ["hi", "there"]) is None
        assert validated_word_confidences(None, ["hi"]) is None


LEGACY_SCHEMA_WITHOUT_CONFIDENCE = """
    CREATE TABLE sessions (
        id TEXT PRIMARY KEY,
        created_at REAL NOT NULL,
        last_accessed_at REAL NOT NULL
    );
    CREATE TABLE segments (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        session_id TEXT NOT NULL,
        speaker_id TEXT,
        text TEXT,
        start REAL,
        end REAL,
        is_interim INTEGER DEFAULT 0,
        role TEXT,
        position INTEGER NOT NULL,
        segment_id TEXT,
        role_source TEXT
    );
    CREATE TABLE row_role_overrides (
        session_id TEXT NOT NULL,
        segment_id TEXT NOT NULL,
        role TEXT NOT NULL,
        PRIMARY KEY (session_id, segment_id)
    );
    CREATE TABLE corrected_segments (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        session_id TEXT NOT NULL,
        speaker_id TEXT,
        text TEXT,
        start REAL,
        end REAL,
        is_interim INTEGER DEFAULT 0,
        role TEXT,
        position INTEGER NOT NULL,
        segment_id TEXT,
        role_source TEXT,
        source TEXT,
        source_model TEXT
    );
"""


class TestStorageRoundTrip:
    """Both storage backends keep confidence, and absent stays absent."""

    def test_sqlite_round_trips_live_and_corrected_confidence(self, tmp_path):
        """A restored session shows the same row confidence the visit recorded."""
        backend = SqliteBackend(db_path=str(tmp_path / "confidence.db"))
        try:
            backend.append_segment(
                "visit-1",
                {"speaker_id": "s0", "text": "hi", "start": 0.0, "end": 1.0, "confidence": 0.72},
            )
            backend.append_segment(
                "visit-1", {"speaker_id": "s0", "text": "again", "start": 1.0, "end": 2.0}
            )
            backend.replace_corrected_segments(
                "visit-1",
                [
                    {
                        "segment_id": "corrected-0001",
                        "speaker_id": "s0",
                        "text": "hi",
                        "start": 0.0,
                        "end": 1.0,
                        "confidence": 0.61,
                    }
                ],
            )

            live_rows = backend.get_segments("visit-1")
            corrected_rows = backend.get_corrected_segments("visit-1")
        finally:
            backend.close()

        assert live_rows[0]["confidence"] == 0.72
        assert "confidence" not in live_rows[1]
        assert corrected_rows[0]["confidence"] == 0.61

    def test_sqlite_upgrades_databases_created_before_confidence(self, tmp_path):
        """Pre-confidence dev databases upgrade in place - no reset, no data loss."""
        db_path = str(tmp_path / "legacy.db")
        legacy_connection = sqlite3.connect(db_path)
        legacy_connection.executescript(LEGACY_SCHEMA_WITHOUT_CONFIDENCE)
        legacy_connection.commit()
        legacy_connection.close()

        backend = SqliteBackend(db_path=db_path)
        try:
            backend.append_segment(
                "visit-1",
                {"speaker_id": "s0", "text": "hi", "start": 0.0, "end": 1.0, "confidence": 0.9},
            )
            backend.replace_corrected_segments(
                "visit-1",
                [{"speaker_id": "s0", "text": "hi", "start": 0.0, "end": 1.0, "confidence": 0.8}],
            )

            live_rows = backend.get_segments("visit-1")
            corrected_rows = backend.get_corrected_segments("visit-1")
        finally:
            backend.close()

        assert live_rows[0]["confidence"] == 0.9
        assert corrected_rows[0]["confidence"] == 0.8

    def test_memory_store_passes_confidence_through_unchanged(self):
        """The default in-memory store keeps confidence without any schema work."""
        store = SessionStore()
        store.append_segment(
            "visit-1",
            {"speaker_id": "s0", "text": "hi", "start": 0.0, "end": 1.0, "confidence": 0.7},
        )
        store.append_segment(
            "visit-1", {"speaker_id": "s0", "text": "again", "start": 1.0, "end": 2.0}
        )

        rows = store.get_segments("visit-1")

        assert rows[0]["confidence"] == 0.7
        assert "confidence" not in rows[1]


class TestBrowserRoundTrip:
    """The summary snapshot echoes confidence so restores keep it."""

    def test_measured_browser_rows_keep_confidence(self):
        """A summary request echoes row confidence so a server restore keeps it."""
        rows = normalise_browser_visible_segments(
            [BrowserVisibleSegment(text="hi", segment_id="seg-0001", confidence=0.73)]
        )
        assert rows[0]["confidence"] == 0.73

    def test_unmeasured_browser_rows_drop_the_key(self):
        """Unmeasured rows stay absent-is-absent through the browser round-trip."""
        rows = normalise_browser_visible_segments(
            [BrowserVisibleSegment(text="hi", segment_id="seg-0001")]
        )
        assert "confidence" not in rows[0]


class TestConfidencePrecision:
    """Stored values keep the four-decimal display precision."""

    def test_row_confidence_rounds_to_four_decimals(self):
        """Stored values keep display precision without payload noise."""
        assert transcript_row_confidence([0.123456]) == 0.1235
        assert pytest.approx(transcript_row_confidence([0.66666]), abs=1e-9) == 0.6667
