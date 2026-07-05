"""
Tests for the transcription session - AudioBuffer accumulation and audio decoding.

NeMo models are NOT loaded in tests (NEMO_MODEL_PROVIDER=mock).
"""

import logging
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

from nemo_pipeline import NemoPipeline, Segment, TranscriptionResult
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

        assert buf.full_audio() == b"chunk1chunk2"

    def test_empty_buffer_on_creation(self):
        """New buffer starts with zero bytes."""
        buf = AudioBuffer()

        assert buf.total_bytes == 0
        assert buf.full_audio() == b""

    def test_audio_from_absolute_offset(self):
        """audio_from slices by absolute session offset, surviving head trims."""
        buf = AudioBuffer(max_duration_seconds=1.0)

        buf.append(b"a" * 32000)
        assert buf.audio_from(16000) == b"a" * 16000

        # A second chunk trims the first; absolute offsets must stay aligned.
        buf.append(b"b" * 32000)
        assert buf.audio_from(32000) == b"b" * 32000
        assert buf.end_seconds == 2.0

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
        assert b"chunk-data-1" in session.buffer.full_audio()

        session.process_chunk(b"chunk-data-2")
        assert session.chunk_count == 2
        assert b"chunk-data-2" in session.buffer.full_audio()

    def test_process_chunk_returns_stable_segments(self):
        """Segments ending clear of the buffer edge are emitted immediately."""
        pipeline = NemoPipeline()
        session = TranscriptionSession("test-session", pipeline, input_format="pcm")

        stable_result = TranscriptionResult(
            segments=[Segment(speaker_id="spk_0", start=0.0, end=1.0, text="hello")]
        )

        # Five seconds of audio leaves the 0-1s segment well clear of the edge.
        with patch.object(pipeline, "transcribe_buffer", return_value=stable_result):
            segments = session.process_chunk(b"\x00" * 32000 * 5)

        assert [segment.text for segment in segments] == ["hello"]
        assert session.accumulated_transcript == segments

    def test_process_chunk_holds_back_unstable_tail(self):
        """A segment still touching the buffer edge waits for the next pass."""
        pipeline = NemoPipeline()
        session = TranscriptionSession("test-session", pipeline, input_format="pcm")

        edge_result = TranscriptionResult(
            segments=[Segment(speaker_id="spk_0", start=0.0, end=4.8, text="still talking")]
        )

        # The segment ends within a second of the 5s buffer edge → held back.
        with patch.object(pipeline, "transcribe_buffer", return_value=edge_result):
            segments = session.process_chunk(b"\x00" * 32000 * 5)

        assert segments == []
        assert session.accumulated_transcript == []

    def test_adjacent_same_speaker_fragments_merge_before_emission(self):
        """Same-speaker fragments become one readable transcript card."""
        pipeline = NemoPipeline()
        session = TranscriptionSession("test-session", pipeline, input_format="pcm")

        fragmented_result = TranscriptionResult(
            segments=[
                Segment(speaker_id="spk_0", start=0.0, end=0.4, text="hello"),
                Segment(speaker_id="spk_0", start=0.4, end=1.2, text="can you help"),
            ]
        )

        with patch.object(pipeline, "transcribe_buffer", return_value=fragmented_result):
            segments = session.process_chunk(b"\x00" * 32000 * 5)

        assert [(segment.start, segment.end, segment.text) for segment in segments] == [
            (0.0, 1.2, "hello can you help")
        ]
        assert session.quality_stats.emitted_segment_count == 1

    def test_punctuation_joins_gain_spaces_before_emission(self):
        """Missing sentence spaces are fixed before the user sees the card."""
        pipeline = NemoPipeline()
        session = TranscriptionSession("test-session", pipeline, input_format="pcm")

        joined_text_result = TranscriptionResult(
            segments=[
                Segment(
                    speaker_id="spk_0",
                    start=0.0,
                    end=1.2,
                    text="headache started.My U.S.A. trip ended.Patient agreed",
                )
            ]
        )

        # The clinician has just recorded one row where ASR glued two sentence starts.
        with patch.object(pipeline, "transcribe_buffer", return_value=joined_text_result):
            segments = session.process_chunk(b"\x00" * 32000 * 5)

        assert [segment.text for segment in segments] == [
            "headache started. My U.S.A. trip ended. Patient agreed"
        ]

    def test_alternating_speaker_fragments_stay_separate(self):
        """Ping-pong speaker fragments keep separate cards for role truth."""
        pipeline = NemoPipeline()
        session = TranscriptionSession("test-session", pipeline, input_format="pcm")

        ping_pong_result = TranscriptionResult(
            segments=[
                Segment(speaker_id="spk_0", start=0.0, end=0.35, text="hello"),
                Segment(speaker_id="spk_1", start=0.35, end=0.65, text="can"),
                Segment(speaker_id="spk_0", start=0.65, end=0.95, text="you"),
            ]
        )

        with patch.object(pipeline, "transcribe_buffer", return_value=ping_pong_result):
            segments = session.process_chunk(b"\x00" * 32000 * 5)

        assert [(segment.speaker_id, segment.text) for segment in segments] == [
            ("spk_0", "hello"),
            ("spk_1", "can"),
            ("spk_0", "you"),
        ]

    def test_same_speaker_fragments_after_pause_stay_separate(self):
        """A pause means the user heard two turns, not one fragment."""
        pipeline = NemoPipeline()
        session = TranscriptionSession("test-session", pipeline, input_format="pcm")

        separated_result = TranscriptionResult(
            segments=[
                Segment(speaker_id="spk_0", start=0.0, end=0.4, text="yes"),
                Segment(speaker_id="spk_0", start=1.2, end=1.6, text="okay"),
            ]
        )

        with patch.object(pipeline, "transcribe_buffer", return_value=separated_result):
            segments = session.process_chunk(b"\x00" * 32000 * 5)

        assert [segment.text for segment in segments] == ["yes", "okay"]

    def test_window_audio_stays_sample_aligned(self):
        """Fractional marks must never hand NeMo half a 16-bit sample."""
        pipeline = NemoPipeline()
        session = TranscriptionSession("test-session", pipeline, input_format="pcm")

        window_parities: list[int] = []

        def record_parity(audio_buffer):
            window_parities.append(len(audio_buffer) % 2)
            # An end like 2.111111 puts the next window start on an odd byte
            # unless offsets are computed in whole samples.
            return TranscriptionResult(
                segments=[
                    Segment(speaker_id="spk_0", start=0.0, end=2.111111, text="line")
                ]
            )

        with patch.object(pipeline, "transcribe_buffer", side_effect=record_parity):
            session.process_chunk(b"\x00" * 32000 * 5)
            session.process_chunk(b"\x00" * 32000 * 5)
            session.finalize()

        assert window_parities == [0, 0, 0]

    def test_emitted_audio_is_not_transcribed_again(self):
        """The next pass only sees audio past the emission mark, once emitted."""
        pipeline = NemoPipeline()
        session = TranscriptionSession("test-session", pipeline, input_format="pcm")

        window_lengths: list[int] = []

        def record_window(audio_buffer):
            window_lengths.append(len(audio_buffer))
            return TranscriptionResult(
                segments=[
                    Segment(speaker_id="spk_0", start=0.0, end=1.0, text="line")
                ]
            )

        with patch.object(pipeline, "transcribe_buffer", side_effect=record_window):
            first = session.process_chunk(b"\x00" * 32000 * 5)
            second = session.process_chunk(b"\x00" * 32000 * 5)

        assert len(first) == 1 and len(second) == 1
        # The second window starts near the mark, not at the session start.
        assert window_lengths[1] < window_lengths[0] + 32000 * 5
        # Emitted lines never repeat: the second segment sits after the first.
        assert session.accumulated_transcript[1].start > 0.0

    def test_window_speaker_ids_follow_the_anchor(self):
        """A window whose diarizer swapped IDs is remapped via the overlap."""
        pipeline = NemoPipeline()
        session = TranscriptionSession("test-session", pipeline, input_format="pcm")

        first_pass = TranscriptionResult(
            segments=[
                Segment(speaker_id="spk_0", start=0.0, end=1.0, text="doctor line"),
                Segment(speaker_id="spk_1", start=1.0, end=2.0, text="patient line"),
            ]
        )
        # The second window re-reads the anchor tail but labels voices inversely:
        # the anchor's voice (spk_1 "patient line") now carries the spk_0 label.
        swapped_pass = TranscriptionResult(
            segments=[
                Segment(speaker_id="spk_0", start=0.0, end=0.6, text="patient line"),
                Segment(speaker_id="spk_1", start=0.6, end=2.0, text="doctor again"),
            ]
        )

        with patch.object(
            pipeline, "transcribe_buffer", side_effect=[first_pass, swapped_pass]
        ):
            session.process_chunk(b"\x00" * 32000 * 5)
            tail = session.process_chunk(b"\x00" * 32000 * 5)

        # Only the genuinely new line emits, relabeled back to the doctor's ID.
        assert [(segment.speaker_id, segment.text) for segment in tail] == [
            ("spk_0", "doctor again")
        ]

    def test_empty_chunk_skipped(self):
        """Empty audio bytes are skipped without appending to buffer."""
        pipeline = NemoPipeline()
        session = TranscriptionSession("test-session", pipeline, input_format="pcm")

        result = session.process_chunk(b"")
        assert result == []
        assert session.buffer.total_bytes == 0
        # chunk_count is incremented even for empty chunks
        assert session.chunk_count == 1

    def test_quality_stats_track_windows_and_held_tail(self):
        """Quality counters explain what text was visible when the user stopped."""
        pipeline = NemoPipeline()
        session = TranscriptionSession("test-session", pipeline, input_format="pcm")

        edge_result = TranscriptionResult(
            segments=[Segment(speaker_id="spk_0", start=0.0, end=4.8, text="held")]
        )

        with patch.object(pipeline, "transcribe_buffer", return_value=edge_result):
            session.process_chunk(b"\x00" * 32000 * 5)
            session.finalize()

        assert session.quality_stats.window_seconds == [5.0, 5.0]
        assert session.quality_stats.held_segment_count == 1
        assert session.quality_stats.emitted_segment_count == 1

    def test_quality_stats_track_speaker_anchor_remaps(self):
        """Quality counters show when window labels were corrected for the UI."""
        pipeline = NemoPipeline()
        session = TranscriptionSession("test-session", pipeline, input_format="pcm")

        first_pass = TranscriptionResult(
            segments=[
                Segment(speaker_id="spk_0", start=0.0, end=1.0, text="doctor line"),
                Segment(speaker_id="spk_1", start=1.0, end=2.0, text="patient line"),
            ]
        )
        swapped_pass = TranscriptionResult(
            segments=[
                Segment(speaker_id="spk_0", start=0.0, end=0.6, text="patient line"),
                Segment(speaker_id="spk_1", start=0.6, end=2.0, text="doctor again"),
            ]
        )

        with patch.object(
            pipeline, "transcribe_buffer", side_effect=[first_pass, swapped_pass]
        ):
            session.process_chunk(b"\x00" * 32000 * 5)
            session.process_chunk(b"\x00" * 32000 * 5)

        assert session.quality_stats.speaker_anchor_remap_count == 2

    def test_third_window_speaker_merges_to_nearest_known_voice(self):
        """A stray third ID stays on the nearest visible speaker card."""
        pipeline = NemoPipeline()
        session = TranscriptionSession("test-session", pipeline, input_format="pcm")

        first_pass = TranscriptionResult(
            segments=[
                Segment(speaker_id="spk_0", start=0.0, end=1.0, text="doctor line"),
                Segment(speaker_id="spk_1", start=1.0, end=2.0, text="patient line"),
            ]
        )
        phantom_pass = TranscriptionResult(
            segments=[
                Segment(speaker_id="spk_1", start=0.0, end=0.4, text="patient line"),
                Segment(
                    speaker_id="spk_2",
                    start=0.4,
                    end=1.6,
                    text="patient continues",
                ),
            ]
        )

        with patch.object(
            pipeline, "transcribe_buffer", side_effect=[first_pass, phantom_pass]
        ):
            session.process_chunk(b"\x00" * 32000 * 5)
            tail = session.process_chunk(b"\x00" * 32000 * 5)

        assert [(segment.speaker_id, segment.text) for segment in tail] == [
            ("spk_1", "patient continues")
        ]
        assert session.quality_stats.phantom_speaker_merge_count == 1

    def test_low_overlap_window_keeps_known_speaker_id(self):
        """A later clean segment keeps its known speaker when overlap gives no vote."""
        pipeline = NemoPipeline()
        session = TranscriptionSession("test-session", pipeline, input_format="pcm")

        first_pass = TranscriptionResult(
            segments=[
                Segment(speaker_id="spk_0", start=0.0, end=1.0, text="doctor line"),
                Segment(speaker_id="spk_1", start=1.0, end=2.0, text="patient line"),
            ]
        )
        low_overlap_pass = TranscriptionResult(
            segments=[
                Segment(speaker_id="spk_0", start=1.0, end=2.0, text="doctor later")
            ]
        )

        with patch.object(
            pipeline, "transcribe_buffer", side_effect=[first_pass, low_overlap_pass]
        ):
            session.process_chunk(b"\x00" * 32000 * 5)
            tail = session.process_chunk(b"\x00" * 32000 * 5)

        assert [(segment.speaker_id, segment.text) for segment in tail] == [
            ("spk_0", "doctor later")
        ]

    def test_speaker_cap_disabled_allows_third_visible_speaker(self, monkeypatch):
        """Cap-disabled mode keeps a third ID for rare multi-person visits."""
        monkeypatch.setenv("NEMO_SPEAKER_CAP", "0")
        pipeline = NemoPipeline()
        session = TranscriptionSession("test-session", pipeline, input_format="pcm")

        first_pass = TranscriptionResult(
            segments=[
                Segment(speaker_id="spk_0", start=0.0, end=1.0, text="doctor line"),
                Segment(speaker_id="spk_1", start=1.0, end=2.0, text="patient line"),
            ]
        )
        third_speaker_pass = TranscriptionResult(
            segments=[
                Segment(speaker_id="spk_2", start=1.0, end=2.0, text="carer adds")
            ]
        )

        with patch.object(
            pipeline, "transcribe_buffer", side_effect=[first_pass, third_speaker_pass]
        ):
            session.process_chunk(b"\x00" * 32000 * 5)
            tail = session.process_chunk(b"\x00" * 32000 * 5)

        assert [(segment.speaker_id, segment.text) for segment in tail] == [
            ("spk_2", "carer adds")
        ]
        assert session.quality_stats.phantom_speaker_merge_count == 0


class TestRowIdentityAtEmission:
    """Every row the clinician sees carries a stable, unique `segment_id`.

    Row IDs are what per-row role corrections attach to, so they must be
    minted exactly once per visible row and stay identical between the live
    Mercure payloads and the finalize history rebuild.
    """

    def test_emitted_rows_get_sequential_ids_across_chunks_and_finalize(self) -> None:
        """IDs continue across chunk and Stop boundaries without reuse."""
        pipeline = NemoPipeline()
        session = TranscriptionSession("test-session", pipeline, input_format="pcm")

        first_pass = TranscriptionResult(
            segments=[
                Segment(speaker_id="spk_0", start=0.0, end=1.0, text="doctor line"),
                Segment(speaker_id="spk_1", start=1.0, end=2.0, text="patient line"),
            ]
        )
        tail_pass = TranscriptionResult(
            segments=[
                Segment(speaker_id="spk_0", start=2.0, end=4.9, text="closing line"),
            ]
        )

        with patch.object(
            pipeline, "transcribe_buffer", side_effect=[first_pass, tail_pass]
        ):
            live_rows = session.process_chunk(b"\x00" * 32000 * 5)
            tail_rows = session.finalize()

        assert [row.segment_id for row in live_rows] == ["seg-0001", "seg-0002"]
        assert [row.segment_id for row in tail_rows] == ["seg-0003"]
        # The finalize history rebuild reuses the same objects, so the IDs the
        # browser saw live are the IDs stored history keeps.
        assert [row.segment_id for row in session.accumulated_transcript] == [
            "seg-0001",
            "seg-0002",
            "seg-0003",
        ]

    def test_merged_fragments_share_one_row_id(self) -> None:
        """Fragments merged into one visible row must be one correctable row."""
        pipeline = NemoPipeline()
        session = TranscriptionSession("test-session", pipeline, input_format="pcm")

        fragment_pass = TranscriptionResult(
            segments=[
                Segment(speaker_id="spk_0", start=0.0, end=0.4, text="Hello,"),
                Segment(speaker_id="spk_0", start=0.5, end=1.2, text="can you hear me"),
            ]
        )

        with patch.object(pipeline, "transcribe_buffer", return_value=fragment_pass):
            emitted_rows = session.process_chunk(b"\x00" * 32000 * 5)

        # One merged visible row means exactly one row ID to correct.
        assert len(emitted_rows) == 1
        assert emitted_rows[0].segment_id == "seg-0001"


class TestWindowContinuityLog:
    """Per-window continuity logs explain seam identity drift to eval runs.

    A clinician replaying a consult sends ~5s chunks; each chunk lands one
    window row that eval tooling turns into `window-continuity.jsonl`. These
    tests pin the log's mapping evidence and its no-transcript-text rule.
    """

    def test_window_log_reports_mapping_reasons_and_counts(self, caplog) -> None:
        """A swapped second window logs the map, reasons, votes, and row counts."""
        pipeline = NemoPipeline()
        session = TranscriptionSession("test-session", pipeline, input_format="pcm")

        first_pass = TranscriptionResult(
            segments=[
                Segment(speaker_id="spk_0", start=0.0, end=1.0, text="doctor line"),
                Segment(speaker_id="spk_1", start=1.0, end=2.0, text="patient line"),
            ]
        )
        # The second window re-reads the anchor tail with inverted labels, the
        # same drift that shows a doctor question on a Patient card in the UI.
        swapped_pass = TranscriptionResult(
            segments=[
                Segment(speaker_id="spk_0", start=0.0, end=0.6, text="patient line"),
                Segment(speaker_id="spk_1", start=0.6, end=2.0, text="doctor again"),
            ]
        )

        with caplog.at_level(logging.INFO, logger="nemo_session"):
            with patch.object(
                pipeline, "transcribe_buffer", side_effect=[first_pass, swapped_pass]
            ):
                session.process_chunk(b"\x00" * 32000 * 5)
                session.process_chunk(b"\x00" * 32000 * 5)

        continuity_records = [
            record
            for record in caplog.records
            if str(record.msg).startswith("nemo_session.window_continuity")
        ]
        assert len(continuity_records) == 2

        first_window, second_window = continuity_records
        # The opening window introduces both consultation voices as new.
        assert first_window.window_index == 1
        assert first_window.mapping_reasons == {
            "spk_0": "new_visible",
            "spk_1": "new_visible",
        }
        assert first_window.window_remaps == 0
        assert first_window.emitted_rows == 2

        # The swapped window records how both labels were corrected for the UI.
        assert second_window.window_index == 2
        assert second_window.phase == "chunk"
        assert second_window.speaker_id_map == {"spk_0": "spk_1", "spk_1": "spk_0"}
        assert second_window.mapping_reasons == {
            "spk_0": "overlap_vote",
            "spk_1": "two_speaker_swap",
        }
        assert second_window.overlap_votes == [
            {
                "window_speaker_id": "spk_0",
                "known_speaker_id": "spk_1",
                "overlap_seconds": 0.5,
            }
        ]
        assert second_window.window_remaps == 2
        assert second_window.emitted_rows == 1
        assert second_window.emitted_from_seconds == 2.0
        assert second_window.emitted_until_seconds == 3.5

    def test_window_log_never_carries_transcript_text(self, caplog) -> None:
        """Clinical speech stays out of process logs; only IDs and counts appear."""
        pipeline = NemoPipeline()
        session = TranscriptionSession("test-session", pipeline, input_format="pcm")

        sensitive_pass = TranscriptionResult(
            segments=[
                Segment(
                    speaker_id="spk_0",
                    start=0.0,
                    end=1.0,
                    text="patient mentions hiv status",
                ),
            ]
        )

        with caplog.at_level(logging.INFO, logger="nemo_session"):
            with patch.object(
                pipeline, "transcribe_buffer", return_value=sensitive_pass
            ):
                session.process_chunk(b"\x00" * 32000 * 5)

        continuity_records = [
            record
            for record in caplog.records
            if str(record.msg).startswith("nemo_session.window_continuity")
        ]
        assert continuity_records != []

        # Every logged field must be free of the spoken words the user heard.
        for record in continuity_records:
            assert "hiv" not in str(record.__dict__).lower()

    def test_finalize_window_is_logged_with_finalize_phase(self, caplog) -> None:
        """Pressing Stop drains the tail and logs it as the finalize window."""
        pipeline = NemoPipeline()
        session = TranscriptionSession("test-session", pipeline, input_format="pcm")

        chunk_pass = TranscriptionResult(
            segments=[
                Segment(speaker_id="spk_0", start=0.0, end=1.0, text="doctor line"),
            ]
        )
        tail_pass = TranscriptionResult(
            segments=[
                Segment(speaker_id="spk_0", start=1.0, end=4.9, text="tail line"),
            ]
        )

        with caplog.at_level(logging.INFO, logger="nemo_session"):
            with patch.object(
                pipeline, "transcribe_buffer", side_effect=[chunk_pass, tail_pass]
            ):
                session.process_chunk(b"\x00" * 32000 * 5)
                session.finalize()

        phases = [
            record.phase
            for record in caplog.records
            if str(record.msg).startswith("nemo_session.window_continuity")
        ]
        assert phases == ["chunk", "finalize"]


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
        """Finalize with no chunks returns no tail segments."""
        pipeline = NemoPipeline()
        session = TranscriptionSession("test-session", pipeline)

        result = session.finalize()
        assert isinstance(result, list)
        assert len(result) == 0

    def test_finalize_drains_the_held_back_tail(self):
        """Finalize emits the edge segment that streaming held back."""
        pipeline = NemoPipeline()
        session = TranscriptionSession("test-session", pipeline, input_format="pcm")

        edge_result = TranscriptionResult(
            segments=[Segment(speaker_id="spk_0", start=0.0, end=4.8, text="last words")]
        )

        with patch.object(pipeline, "transcribe_buffer", return_value=edge_result):
            held = session.process_chunk(b"\x00" * 32000 * 5)
            tail = session.finalize()

        assert held == []
        assert [segment.text for segment in tail] == ["last words"]
        assert session.accumulated_transcript == tail


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
