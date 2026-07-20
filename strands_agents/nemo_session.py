"""
Per-recording audio state for live transcription.

Each browser WebSocket owns one `TranscriptionSession` and shares the process
wide NeMo pipeline. The session buffers PCM or WebM chunks, runs synchronous
GPU work from the API executor, and returns only transcript lines the user has
not already seen.
"""

from __future__ import annotations

import logging
import os
import subprocess
import tempfile
import time
from collections import deque
from dataclasses import replace
from pathlib import Path

from nemo_pipeline import NemoPipeline, Segment
from nemo_segment_cleanup import (
    merge_adjacent_fragments_for_display,
    shift_segment_to_session_time,
)
from session_quality import TranscriptionQualityStats

logger = logging.getLogger(__name__)

# 16 kHz mono 16-bit PCM - the browser audio contract.
_BYTES_PER_SECOND = 16000 * 2
# Audio replayed before the emission mark so the model has acoustic context and
# the new window's speaker labels can be matched against the previous window.
_WINDOW_CONTEXT_SECONDS = 0.5
# Segments ending this close to the buffer edge are usually still mid-utterance
# and would be revised by the next pass, so they wait one more chunk.
_UNSTABLE_TAIL_SECONDS = 1.0
# Minimum unheard audio a segment must contain to count as new. Context
# re-reads jitter a few hundred ms past the mark; without this floor they
# would re-emit the previous utterance's text at every window seam.
_MIN_NEW_AUDIO_SECONDS = 0.3
# Medical visits are usually dyadic; set NEMO_SPEAKER_CAP=0 to keep every ID.
_DEFAULT_SPEAKER_CAP = 2
# Operator-only evidence is absent from normal clinician visits unless explicitly enabled.
_STREAMING_SLOT_EVIDENCE_FLAG = "NEMO_STREAMING_SLOT_EVIDENCE"
# Operators can trial the confirmed bleed guard without changing ordinary visit bytes.
_STREAMING_CROSSTALK_GUARD_FLAG = "NEMO_STREAMING_CROSSTALK_GUARD"
# Roughly four seconds of cumulative voice distinguishes a sustained turn from a decoder blip.
_SUSTAINED_VOICE_MIN_FRAMES = 50
# A visit-share floor stops long recordings from promoting accumulated low-level slot noise.
_SUSTAINED_VOICE_MIN_SHARE = 0.015


def _streaming_slot_evidence_enabled() -> bool:
    """Return whether this replay should explain wrong-speaker fold decisions.

    Operators enable it for a named QA specimen; normal clinician visits keep the extra log
    fields absent.
    """
    # An unset or empty flag keeps routine visit logs free of detailed slot evidence.
    configured_value = os.environ.get(_STREAMING_SLOT_EVIDENCE_FLAG, "").strip().lower()
    return configured_value in {"1", "true", "yes", "on"}


def _streaming_crosstalk_guard_enabled() -> bool:
    """Return whether this QA run should protect sustained voices from a wrong-speaker fold.

    Operators enable it for measured replays; an unset or empty value preserves release behavior.
    """
    # An unset or empty flag keeps the current fold policy for ordinary clinician visits.
    configured_value = (
        os.environ.get(_STREAMING_CROSSTALK_GUARD_FLAG, "").strip().lower()
    )
    return configured_value in {"1", "true", "yes", "on"}


def _speaker_cap_from_env() -> int | None:
    """Return the configured visible-speaker cap for one browser session.

    Returns:
        Positive cap value, or None when operators intentionally allow all speakers.
    """
    raw_value = os.environ.get("NEMO_SPEAKER_CAP", str(_DEFAULT_SPEAKER_CAP)).strip()

    # Empty or disabled values mean the UI can show every speaker NeMo emits.
    if raw_value == "" or raw_value.lower() in {"0", "off", "none", "disabled"}:
        return None

    try:
        configured_cap = int(raw_value)
    except ValueError:
        logger.warning(
            "nemo_session.speaker_cap.invalid value=%s default=%s",
            raw_value,
            _DEFAULT_SPEAKER_CAP,
        )
        return _DEFAULT_SPEAKER_CAP

    # Non-positive numeric values use the same cap-disabled behavior as zero.
    if configured_cap <= 0:
        return None

    return configured_cap


def _segment_overlap_seconds(first_segment: Segment, second_segment: Segment) -> float:
    """Return overlap seconds between two timestamped transcript rows.

    Returns:
        Seconds of shared time; zero means the user heard these rows separately.
    """
    return max(
        0.0,
        min(first_segment.end, second_segment.end)
        - max(first_segment.start, second_segment.start),
    )


def _segment_gap_seconds(first_segment: Segment, second_segment: Segment) -> float:
    """Return the silent gap between two transcript rows.

    Returns:
        Seconds between rows; zero means they overlap or touch in the visible timeline.
    """
    # First segment ending before the second creates a forward gap.
    if first_segment.end < second_segment.start:
        return second_segment.start - first_segment.end

    # Second segment ending before the first creates the same gap in reverse.
    if second_segment.end < first_segment.start:
        return first_segment.start - second_segment.end

    return 0.0


class AudioBuffer:
    """Accumulates session audio and serves absolute-offset windows of it.

    Timestamps across the session are absolute (seconds since the first chunk),
    so window reads take absolute byte offsets and stay correct even after the
    safety cap trims old audio from the front.
    """

    def __init__(self, max_duration_seconds: float = 900.0) -> None:
        """Initialize the audio buffer.

        Args:
            max_duration_seconds: Maximum audio duration to retain (default: 15 minutes).
                                  Safety cap to prevent unbounded memory growth.
        """
        self._chunks: deque[bytes] = deque()
        self._total_bytes: int = 0
        self._trimmed_bytes: int = 0
        self._max_bytes: int = int(max_duration_seconds * _BYTES_PER_SECOND)

    def append(self, pcm_audio: bytes) -> None:
        """Append PCM audio to the buffer.

        Args:
            pcm_audio: Raw PCM audio bytes (16kHz mono, 16-bit signed int)
        """
        self._chunks.append(pcm_audio)
        self._total_bytes += len(pcm_audio)

        # Safety cap: if buffer exceeds max, trim from the beginning
        while self._total_bytes > self._max_bytes and len(self._chunks) > 1:
            removed = self._chunks.popleft()
            self._total_bytes -= len(removed)
            self._trimmed_bytes += len(removed)

    def audio_from(self, absolute_start_byte: int) -> bytes:
        """Return audio from an absolute session byte offset to the buffer end.

        Args:
            absolute_start_byte: Offset in bytes since the session's first chunk.

        Returns:
            PCM bytes from that offset; empty means nothing new to transcribe.
        """
        relative_start = max(0, absolute_start_byte - self._trimmed_bytes)
        # 16-bit samples: an odd start or length would hand NeMo half a sample.
        relative_start &= ~1
        window = b"".join(self._chunks)[relative_start:]
        if len(window) % 2:
            window = window[:-1]
        return window

    def full_audio(self) -> bytes:
        """Return all retained audio (for final processing on session end).

        Returns:
            All PCM bytes still held for the session.
        """
        return b"".join(self._chunks)

    @property
    def end_seconds(self) -> float:
        """Absolute session time of the newest buffered audio sample.

        Returns:
            Seconds since the first chunk; `0.0` means no audio has arrived.
        """
        return (self._trimmed_bytes + self._total_bytes) / _BYTES_PER_SECOND

    @property
    def duration_seconds(self) -> float:
        """Estimated audio duration shown by live-session diagnostics.

        Returns:
            Seconds of buffered audio; `0.0` means the user has not sent audio yet.
        """
        return self._total_bytes / (16000 * 2)  # 16kHz, 16-bit

    @property
    def total_bytes(self) -> int:
        """Raw buffered audio size used to decide whether finalization can run.

        Returns:
            Byte count; `0` means ending the session reuses existing transcript text.
        """
        return self._total_bytes

    @property
    def trimmed_seconds(self) -> float:
        """Absolute session time of the oldest audio still retained.

        Returns:
            Seconds trimmed from the front; `0.0` means the whole visit is retained.
        """
        return self._trimmed_bytes / _BYTES_PER_SECOND


class TranscriptionSession:
    """Manages audio accumulation and NeMo processing across WebSocket chunks.

    One instance per WebSocket connection. References the shared NemoPipeline
    singleton - does NOT load models.

    Audio strategy (windowed emission):
      - Accumulates decoded PCM in AudioBuffer with absolute session timing.
      - Each process_chunk call transcribes only audio past the emission mark
        (plus a short context lead), so every stretch of speech is emitted to
        the browser exactly once - no re-transcription of the whole session.
      - Segments ending near the buffer edge are held one chunk because the
        next pass usually revises them; finalize() drains that held tail.
      - Speaker IDs from each window are matched to the previous window via
        the context-overlap segment so labels stay continuous across windows.

    All processing methods are SYNCHRONOUS (GPU-bound). Callers must use
    asyncio.run_in_executor() to avoid blocking the event loop.
    """

    def __init__(
        self,
        session_id: str,
        pipeline: NemoPipeline,
        input_format: str = "pcm",
        max_buffer_duration: float = 900.0,
        streaming_engine=None,
    ) -> None:
        """Create a new transcription session.

        Args:
            session_id: Unique session identifier (UUID)
            pipeline: Shared NemoPipeline singleton (loaded at startup)
            input_format: Audio input format ("pcm" or "webm")
            max_buffer_duration: Maximum audio buffer duration in seconds.
            streaming_engine: Optional session-long streaming engine (M22).
                None keeps the windowed emission path unchanged.
        """
        self.session_id = session_id
        self.pipeline = pipeline
        self._streaming_engine = streaming_engine
        self.input_format = input_format.lower()
        self.buffer = AudioBuffer(max_duration_seconds=max_buffer_duration)
        self.accumulated_transcript: list[Segment] = []
        self.chunk_count: int = 0
        self.started_at: float = time.time()
        self.quality_stats: TranscriptionQualityStats = TranscriptionQualityStats()
        self._emitted_until_seconds: float = 0.0
        self._format_validated: bool = False
        self._speaker_cap: int | None = _speaker_cap_from_env()
        # Per-window continuity evidence for the M20 diagnostics log; holds
        # only speaker IDs, timings, and counts - never transcript text.
        self._last_window_continuity: dict = {}
        self._window_overlap_votes: list[dict] = []
        # Emitted-row counter behind the stable `segment_id` each visible row
        # gets, so clinician corrections can target exactly one transcript row.
        self._emitted_row_count: int = 0
        # Streaming-engine bookkeeping: cumulative speech per cache slot feeds
        # the speaker cap; per-tick phantom merges feed the continuity log.
        self._engine_slot_durations: dict[str, float] = {}
        self._engine_window_phantom_merges: int = 0
        # A named QA replay can retain count-only fold evidence; normal visits leave it absent.
        self._should_log_streaming_slot_evidence = _streaming_slot_evidence_enabled()
        # An operator-enabled replay may retain a sustained real voice under its own source chip.
        self._should_guard_streaming_crosstalk = _streaming_crosstalk_guard_enabled()
        self._previous_speaker_slot_voiced_frames: dict[str, int] = {}
        self._previous_speaker_slot_pair_frames: dict[str, int] = {}
        self._previous_diar_sample_frames: int = 0
        self._latest_slot_share_evidence: list[dict] = []
        self._latest_folded_word_spans: list[dict] = []
        self._latest_slot_pair_evidence: dict | None = None

        # Unsupported formats mean the browser and server audio contracts diverged.
        if self.input_format not in {"pcm", "webm"}:
            raise ValueError(
                f"Unsupported transcription input format: {self.input_format}"
            )

        logger.info(
            "transcription_session.created",
            extra={
                "session_id": session_id,
                "input_format": self.input_format,
            },
        )

    @property
    def engine_name(self) -> str:
        """Transcription engine label recorded in quality artifacts.

        Returns:
            `streaming` when the M22 session-long engine drives emission;
            `windowed` for the legacy per-window path.
        """
        return "streaming" if self._streaming_engine is not None else "windowed"

    @property
    def engine_diagnostics(self):
        """Streaming-engine identity diagnostics, or None on the windowed path."""
        return getattr(self._streaming_engine, "diagnostics", None)

    def process_chunk(self, raw_audio: bytes) -> list[Segment]:
        """Process a single audio chunk through the NeMo pipeline.

        SYNCHRONOUS - must be called via run_in_executor().

        Flow:
          1. Validate/decode the chunk and append PCM to the buffer
          2. Transcribe only the window past the emission mark
          3. Hold back the still-changing tail; emit the stable rest once

        Args:
            raw_audio: Raw PCM (or WebM) audio bytes from the browser WebSocket

        Returns:
            Newly emitted transcript segments the browser has not seen before.
        """
        self.chunk_count += 1
        chunk_started_at = time.time()

        if not self._format_validated and raw_audio != b"":
            self._validate_audio_format(raw_audio)
            self._format_validated = True

        pcm_audio = self._decode_audio(raw_audio)
        if pcm_audio == b"":
            logger.info(
                "transcription_session.chunk_skipped",
                extra={
                    "session_id": self.session_id,
                    "reason": "empty_after_decode",
                },
            )
            return []

        self.buffer.append(pcm_audio)

        # The streaming engine owns speaker identity for the whole session;
        # the windowed path re-derives it per window and stitches (M22 flag).
        if self._streaming_engine is not None:
            self.quality_stats.record_window(len(pcm_audio))
            new_segments = self._emit_engine_rows(
                self._streaming_engine.feed(pcm_audio), is_finalize=False
            )
        else:
            new_segments = self._transcribe_unemitted(hold_unstable_tail=True)
        self.accumulated_transcript.extend(new_segments)

        duration_ms = int((time.time() - chunk_started_at) * 1000)
        logger.info(
            "transcription_session.chunk_processed",
            extra={
                "session_id": self.session_id,
                "chunk_number": self.chunk_count,
                "audio_seconds": round(self.buffer.duration_seconds, 1),
                "segments_returned": len(new_segments),
                "duration_ms": duration_ms,
            },
        )

        return new_segments

    def finalize(self) -> list[Segment]:
        """Drain the held-back tail when the session ends.

        Called when the WebSocket disconnects. Transcribes the remaining
        unemitted audio without holding anything back. The caller publishes
        the returned tail; the full session transcript stays available on
        `accumulated_transcript`.

        Returns:
            Only the segments the browser has not been sent yet.
        """
        logger.info(
            "transcription_session.finalizing",
            extra={
                "session_id": self.session_id,
                "total_chunks": self.chunk_count,
                "audio_seconds": round(self.buffer.duration_seconds, 1),
                "duration_seconds": round(time.time() - self.started_at, 1),
            },
        )

        if self.buffer.total_bytes == 0:
            return []

        if self._streaming_engine is not None:
            tail_segments = self._emit_engine_rows(
                self._streaming_engine.flush(), is_finalize=True
            )
        else:
            tail_segments = self._transcribe_unemitted(hold_unstable_tail=False)
        self.accumulated_transcript.extend(tail_segments)

        return tail_segments

    def _transcribe_unemitted(self, *, hold_unstable_tail: bool) -> list[Segment]:
        """Transcribe audio past the emission mark and emit each span once.

        The window starts slightly before the mark so the model has acoustic
        context and the overlap can anchor speaker-label continuity. Segments
        are emitted only once: the mark advances past everything returned.

        Args:
            hold_unstable_tail: True keeps segments that end near the buffer
                edge for the next pass, because they are usually mid-utterance.

        Returns:
            Newly emitted segments in chronological order; empty means no new
            stable speech yet.
        """
        window_start_seconds = max(
            0.0, self._emitted_until_seconds - _WINDOW_CONTEXT_SECONDS
        )
        emitted_from_seconds = self._emitted_until_seconds
        self._last_window_continuity = {}
        # Whole samples only: an odd byte offset would split a 16-bit sample and
        # NeMo rejects buffers that are not a multiple of the element size.
        window_start_byte = int(window_start_seconds * 16000) * 2
        # The buffer may have trimmed past the requested start; the returned audio
        # then begins at the retained head, so the timeline shift must use that
        # actual start or new speech is timestamped too early.
        effective_window_start_seconds = max(
            window_start_seconds, self.buffer.trimmed_seconds
        )
        window_audio = self.buffer.audio_from(window_start_byte)
        # No new audio means the user has not produced another transcribable window.
        if window_audio == b"":
            return []
        self.quality_stats.record_window(len(window_audio))

        result = self.pipeline.transcribe_buffer(window_audio)
        window_segments: list[Segment] = []
        # Each NeMo row is shifted from window-local time to the user's session timeline.
        for segment in sorted(result.segments, key=lambda segment: segment.start):
            window_segments.append(
                shift_segment_to_session_time(segment, effective_window_start_seconds)
            )
        window_segments = self._continue_anchor_speakers(window_segments)

        # Segments fully inside already-emitted audio are the context replay.
        fresh_segments = [
            segment
            for segment in window_segments
            if segment.end > self._emitted_until_seconds + _MIN_NEW_AUDIO_SECONDS
        ]

        # The buffer edge is still being spoken; those lines firm up next pass.
        held_segment_count = 0
        if hold_unstable_tail:
            buffer_end_seconds = self.buffer.end_seconds
            while (
                fresh_segments
                and fresh_segments[-1].end > buffer_end_seconds - _UNSTABLE_TAIL_SECONDS
            ):
                fresh_segments.pop()
                held_segment_count += 1

        # A user may have just started a replay and received word-sized same-speaker cards.
        fresh_segments = merge_adjacent_fragments_for_display(fresh_segments)
        # Rows are identified after merging so one visible row carries one ID.
        fresh_segments = self._assign_row_identity(fresh_segments)

        self.quality_stats.record_segment_flow(
            emitted_segments=len(fresh_segments),
            held_segments=held_segment_count,
        )

        # Empty fresh segments mean the browser has no new stable text yet.
        if fresh_segments:
            self._emitted_until_seconds = max(
                self._emitted_until_seconds, fresh_segments[-1].end
            )

        self._log_window_continuity(
            window_start_seconds=window_start_seconds,
            emitted_from_seconds=emitted_from_seconds,
            emitted_rows=len(fresh_segments),
            held_rows=held_segment_count,
            is_finalize=not hold_unstable_tail,
        )

        return fresh_segments

    def _emit_engine_rows(
        self, engine_rows: list, *, is_finalize: bool
    ) -> list[Segment]:
        """Turn streaming-engine rows into emitted transcript segments (M22).

        Engine rows arrive with session-absolute times and cache-stable
        speaker slots, so window-time shifting and anchor stitching are
        bypassed by design. Everything downstream of identity is shared with
        the windowed path: speaker cap, fragment merge, row IDs, quality
        counters, and the continuity diagnostics log.

        Args:
            engine_rows: Stabilized rows from the engine's feed/flush.
            is_finalize: True when draining the held tail at session end.

        Returns:
            Newly emitted segments in chronological order.
        """
        emitted_from_seconds = self._emitted_until_seconds
        window_segments = [
            Segment(
                speaker_id=row.speaker_slot,
                # Streaming rows bypass the windowed ASR path, so the medical
                # boost must be applied here or drug/condition variants reach
                # the transcript, summary, and download uncorrected.
                text=self.pipeline.visible_text(row.text),
                start=row.start,
                end=row.end,
                # Row confidence describes the audio span, so it survives the
                # text normalisation above unchanged.
                confidence=row.confidence,
            )
            for row in engine_rows
        ]
        window_segments.sort(key=lambda segment: segment.start)
        window_segments = self._cap_engine_speaker_slots(window_segments)

        fresh_segments = merge_adjacent_fragments_for_display(window_segments)
        fresh_segments = self._assign_row_identity(fresh_segments)

        engine = self._streaming_engine
        held_rows = getattr(engine, "pending_row_count", 0) if engine is not None else 0
        self.quality_stats.record_segment_flow(
            emitted_segments=len(fresh_segments),
            held_segments=held_rows,
        )

        if fresh_segments:
            self._emitted_until_seconds = max(
                self._emitted_until_seconds, fresh_segments[-1].end
            )

        diagnostics = getattr(engine, "diagnostics", None)
        self._last_window_continuity = {
            "engine": "streaming",
            "raw_speaker_ids": sorted(
                {segment.speaker_id for segment in window_segments}
            ),
            "known_speaker_ids": sorted(self._engine_slot_durations),
            "speaker_id_map": {},
            "overlap_votes": [],
            "mapping_reasons": {},
            "window_remaps": 0,
            "window_phantom_merges": self._engine_window_phantom_merges,
            "late_slot_births": getattr(diagnostics, "late_slot_births", 0),
            "revision_resyncs": getattr(diagnostics, "revision_resyncs", 0),
        }
        # An operator-enabled replay carries count-only evidence for this browser audio window.
        if self._should_log_streaming_slot_evidence:
            self._last_window_continuity["slot_share_evidence"] = list(
                self._latest_slot_share_evidence
            )
            self._last_window_continuity["folded_word_spans"] = list(
                self._latest_folded_word_spans
            )
            # Pairwise co-activity appears only when the engine can measure it.
            if self._latest_slot_pair_evidence is not None:
                self._last_window_continuity["pairwise_slot_evidence"] = dict(
                    self._latest_slot_pair_evidence
                )
            # The engine's count/time-only reason explains a named replay's visible pause.
            self._last_window_continuity["emission_decision_evidence"] = dict(
                getattr(engine, "emission_decision_evidence", {}) or {}
            )
        self._log_window_continuity(
            window_start_seconds=emitted_from_seconds,
            emitted_from_seconds=emitted_from_seconds,
            emitted_rows=len(fresh_segments),
            held_rows=held_rows,
            is_finalize=is_finalize,
        )

        return fresh_segments

    def _cap_engine_speaker_slots(self, segments: list[Segment]) -> list[Segment]:
        """Fold engine speaker slots beyond the visible cap into dominant voices.

        The streaming diarizer exposes up to four cache slots; dyadic visits
        show at most `NEMO_SPEAKER_CAP` of them. Marginal slots (phantoms)
        are folded into the dominant slot by cumulative speech time, counted
        as phantom merges exactly like the windowed engine's cap.

        Args:
            segments: Chronological engine rows for this emission tick.

        Returns:
            The same rows with capped speaker IDs.
        """
        self._engine_window_phantom_merges = 0
        self._latest_slot_share_evidence = []
        self._latest_folded_word_spans = []
        self._latest_slot_pair_evidence = None

        # Each newly stable row updates the same cumulative duration used by the fold threshold.
        for segment in segments:
            self._engine_slot_durations[segment.speaker_id] = (
                self._engine_slot_durations.get(segment.speaker_id, 0.0)
                + max(0.0, segment.end - segment.start)
            )

        # Cap disabled means the UI shows every cache slot the engine emits.
        if self._speaker_cap is None:
            self._capture_streaming_slot_evidence(
                fold_threshold_seconds=None,
                substantial_speaker_slots=[],
                sustained_voice_speaker_slots=set(),
                folded_word_spans=[],
            )
            return segments

        # Fold only MARGINAL slots (hallucination-scale, mirroring the
        # windowed engine's share filter). Substantial voices always pass:
        # folding a real voice into another slot corrupts attribution far
        # worse than a third raw ID, which the role mapping labels anyway.
        # (An eager first-to-establish pinning policy did exactly that on
        # c03, where the doctor's speech spans two early cache slots.)
        total_speech = sum(self._engine_slot_durations.values())
        marginal_below = max(1.5, 0.05 * total_speech)
        sustained_voice_speaker_slots = self._sustained_voice_speaker_slots()
        substantial_slots = [
            slot
            for slot, duration in self._engine_slot_durations.items()
            if duration >= marginal_below or slot in sustained_voice_speaker_slots
        ]

        capped_segments: list[Segment] = []
        folded_word_spans: list[dict] = []
        # Each stable row either keeps its cache slot or enters a visible consultation voice.
        for segment in segments:
            # Substantial voices remain separate so the user can correct their role if needed.
            if segment.speaker_id in substantial_slots or not substantial_slots:
                capped_segments.append(segment)
                continue

            dominant_slot = max(
                substantial_slots,
                key=lambda slot: self._engine_slot_durations.get(slot, 0.0),
            )
            self._engine_window_phantom_merges += 1
            self.quality_stats.record_phantom_speaker_merges(1)
            logger.info(
                "nemo_session.phantom_speaker_merged session_id=%s window_speaker_id=%s canonical_speaker_id=%s",
                self.session_id,
                segment.speaker_id,
                dominant_slot,
                extra={
                    "session_id": self.session_id,
                    "window_speaker_id": segment.speaker_id,
                    "canonical_speaker_id": dominant_slot,
                    "engine": "streaming",
                },
            )
            folded_word_spans.append(
                {
                    "origin_speaker_slot": segment.speaker_id,
                    "visible_speaker_slot": dominant_slot,
                    "start_seconds": round(segment.start, 3),
                    "end_seconds": round(segment.end, 3),
                    "word_count": len(segment.text.split()),
                }
            )
            capped_segments.append(replace(segment, speaker_id=dominant_slot))

        self._capture_streaming_slot_evidence(
            fold_threshold_seconds=marginal_below,
            substantial_speaker_slots=substantial_slots,
            sustained_voice_speaker_slots=sustained_voice_speaker_slots,
            folded_word_spans=folded_word_spans,
        )
        return capped_segments

    def _sustained_voice_speaker_slots(self) -> set[str]:
        """Return cache slots whose acoustic history proves a persistent consultation voice.

        Returns:
            Protected slot IDs; empty means the guard is off or no slot met both thresholds.
        """
        # Flag-off visits keep the release fold policy byte-identical.
        if not self._should_guard_streaming_crosstalk:
            return set()

        engine = self._streaming_engine
        # The windowed path has no session-long cache evidence to protect.
        if engine is None:
            return set()

        voiced_frames_by_speaker_slot = dict(
            getattr(engine, "speaker_slot_voiced_frame_counts", {}) or {}
        )
        total_voiced_frames = sum(voiced_frames_by_speaker_slot.values())
        # Silence or a missing diarizer snapshot cannot prove a real voice.
        if total_voiced_frames <= 0:
            return set()

        # Each protected slot must clear both the absolute and visit-relative voice floors.
        return {
            speaker_slot
            for speaker_slot, cumulative_voiced_frames in voiced_frames_by_speaker_slot.items()
            if cumulative_voiced_frames >= _SUSTAINED_VOICE_MIN_FRAMES
            and cumulative_voiced_frames / total_voiced_frames
            >= _SUSTAINED_VOICE_MIN_SHARE
        }

    def _capture_streaming_slot_evidence(
        self,
        *,
        fold_threshold_seconds: float | None,
        substantial_speaker_slots: list[str],
        sustained_voice_speaker_slots: set[str],
        folded_word_spans: list[dict],
    ) -> None:
        """Capture count-only speaker evidence after one browser audio window.

        Args:
            fold_threshold_seconds: Duration below which a slot may fold; None means the user
                configured no speaker cap, so no fold threshold applied.
            substantial_speaker_slots: Slots eligible to stay visible; empty means no dominant
                voice existed yet or the cap was disabled.
            sustained_voice_speaker_slots: Acoustically protected slot IDs; empty means the
                guard is off or no slot yet proves a persistent consultation voice.
            folded_word_spans: PHI-safe timing/count records; empty means no visible row folded.
        """
        # Normal clinician visits do not need detailed operator evidence in their logs.
        if not self._should_log_streaming_slot_evidence:
            return

        engine = self._streaming_engine
        # A missing engine means the legacy windowed path has no cache slots to compare.
        if engine is None:
            return

        # An engine with no voiced activity yields an empty frame map, not fabricated shares.
        current_voiced_frames = dict(
            getattr(engine, "speaker_slot_voiced_frame_counts", {}) or {}
        )
        total_voiced_frames = sum(current_voiced_frames.values())
        total_emitted_seconds = sum(self._engine_slot_durations.values())
        evidence_speaker_slots = sorted(
            set(current_voiced_frames) | set(self._engine_slot_durations)
        )

        # One row per heard cache slot lets the operator align shares with a visible fold span.
        self._latest_slot_share_evidence = [
            self._speaker_slot_share_evidence(
                speaker_slot=speaker_slot,
                current_voiced_frames=current_voiced_frames,
                total_voiced_frames=total_voiced_frames,
                total_emitted_seconds=total_emitted_seconds,
                fold_threshold_seconds=fold_threshold_seconds,
                substantial_speaker_slots=substantial_speaker_slots,
                sustained_voice_speaker_slots=sustained_voice_speaker_slots,
            )
            for speaker_slot in evidence_speaker_slots
        ]
        self._latest_folded_word_spans = list(folded_word_spans)
        # Pairwise deltas read the prior per-slot snapshot, so they compute first.
        self._latest_slot_pair_evidence = self._pairwise_slot_evidence(
            engine, current_voiced_frames
        )
        self._previous_speaker_slot_voiced_frames = current_voiced_frames

    def _pairwise_slot_evidence(
        self,
        engine,
        current_voiced_frames: dict[str, int],
    ) -> dict | None:
        """Build count-only pairwise co-activity deltas for one browser audio window.

        Args:
            engine: Streaming engine under evidence; the windowed path never reaches here.
            current_voiced_frames: Cumulative per-slot voiced frames; empty means silence so far.

        Returns:
            Window and cumulative pair counts, or None when the engine cannot report
            pairwise counters - evidence stays honestly absent instead of fabricated zeros.
        """
        # An engine without pairwise counters yields no evidence rather than zeros.
        if not hasattr(engine, "speaker_slot_pair_co_active_frame_counts"):
            return None

        current_pair_frames = dict(
            engine.speaker_slot_pair_co_active_frame_counts or {}
        )
        cumulative_sample_frames = max(
            0, int(getattr(engine, "diar_sample_frame_total", 0) or 0)
        )
        window_sample_frames = max(
            0, cumulative_sample_frames - self._previous_diar_sample_frames
        )

        # Per-slot window deltas reuse the same prior snapshot the share rows read.
        window_voiced_frames = {
            speaker_slot: max(
                0,
                int(current_voiced_frames.get(speaker_slot, 0))
                - int(self._previous_speaker_slot_voiced_frames.get(speaker_slot, 0)),
            )
            for speaker_slot in current_voiced_frames
        }

        known_speaker_slots = sorted(current_voiced_frames)
        pair_rows: list[dict] = []
        # Every heard voice is paired with every other so a folded row's origin
        # and target always have a joinable evidence row for this window.
        for position, first_slot in enumerate(known_speaker_slots):
            # Each later voice completes one normalized pair with the first.
            for second_slot in known_speaker_slots[position + 1 :]:
                pair_key = f"{first_slot}|{second_slot}"
                cumulative_co_active = int(current_pair_frames.get(pair_key, 0))
                window_co_active = max(
                    0,
                    cumulative_co_active
                    - int(self._previous_speaker_slot_pair_frames.get(pair_key, 0)),
                )
                # A pair with no voiced member this window carries no new evidence.
                if (
                    window_voiced_frames.get(first_slot, 0) == 0
                    and window_voiced_frames.get(second_slot, 0) == 0
                    and window_co_active == 0
                ):
                    continue
                pair_rows.append(
                    {
                        "speaker_slot_pair": pair_key,
                        "window_co_active_frames": window_co_active,
                        "cumulative_co_active_frames": cumulative_co_active,
                        "window_exclusive_frames": {
                            first_slot: max(
                                0,
                                window_voiced_frames.get(first_slot, 0)
                                - window_co_active,
                            ),
                            second_slot: max(
                                0,
                                window_voiced_frames.get(second_slot, 0)
                                - window_co_active,
                            ),
                        },
                    }
                )

        self._previous_speaker_slot_pair_frames = current_pair_frames
        self._previous_diar_sample_frames = cumulative_sample_frames
        return {
            "window_sample_frames": window_sample_frames,
            "cumulative_sample_frames": cumulative_sample_frames,
            "pairs": pair_rows,
        }

    def _speaker_slot_share_evidence(
        self,
        *,
        speaker_slot: str,
        current_voiced_frames: dict[str, int],
        total_voiced_frames: int,
        total_emitted_seconds: float,
        fold_threshold_seconds: float | None,
        substantial_speaker_slots: list[str],
        sustained_voice_speaker_slots: set[str],
    ) -> dict:
        """Build the count-only evidence row an operator compares with transcript timing.

        Args:
            speaker_slot: Cache identity under review; never empty for an emitted NeMo slot.
            current_voiced_frames: Current slot totals; empty means the diarizer heard no voice.
            total_voiced_frames: Visit-wide frame total; zero means the replay is still silent.
            total_emitted_seconds: Stable transcript time; zero means no wording is visible yet.
            fold_threshold_seconds: Active duration threshold; None means the cap is disabled.
            substantial_speaker_slots: Independently visible voices; empty means none qualified.
            sustained_voice_speaker_slots: Acoustically protected voices; empty means none.

        Returns:
            PHI-safe shares and decision; zero values mean no measurable evidence yet.
        """
        cumulative_voiced_frames = max(
            0,
            int(current_voiced_frames.get(speaker_slot, 0)),
        )
        prior_voiced_frames = self._previous_speaker_slot_voiced_frames.get(
            speaker_slot,
            0,
        )
        window_voiced_frames = max(
            0,
            cumulative_voiced_frames - prior_voiced_frames,
        )
        cumulative_emitted_seconds = max(
            0.0,
            self._engine_slot_durations.get(speaker_slot, 0.0),
        )
        voiced_share = 0.0
        emitted_share = 0.0

        # A non-silent visit gets a real acoustic share; silence remains an honest zero.
        if total_voiced_frames > 0:
            voiced_share = round(cumulative_voiced_frames / total_voiced_frames, 4)

        # Stable emitted time is the exact share that currently drives the fold decision.
        if total_emitted_seconds > 0:
            emitted_share = round(
                cumulative_emitted_seconds / total_emitted_seconds,
                4,
            )

        logged_fold_threshold = None
        # A configured cap gives the operator the exact threshold used for this window.
        if fold_threshold_seconds is not None:
            logged_fold_threshold = round(fold_threshold_seconds, 3)

        evidence_row = {
            "speaker_slot": speaker_slot,
            "window_voiced_frames": window_voiced_frames,
            "cumulative_voiced_frames": cumulative_voiced_frames,
            "voiced_share": voiced_share,
            "cumulative_emitted_seconds": round(cumulative_emitted_seconds, 3),
            "emitted_share": emitted_share,
            "fold_threshold_seconds": logged_fold_threshold,
            "fold_decision": self._fold_decision_for_speaker_slot(
                speaker_slot=speaker_slot,
                fold_threshold_seconds=fold_threshold_seconds,
                cumulative_emitted_seconds=cumulative_emitted_seconds,
                substantial_speaker_slots=substantial_speaker_slots,
                sustained_voice_speaker_slots=sustained_voice_speaker_slots,
            ),
        }
        # Guard-enabled evidence records the exact policy an operator is trialling.
        if self._should_guard_streaming_crosstalk:
            evidence_row["sustained_voice_min_frames"] = _SUSTAINED_VOICE_MIN_FRAMES
            evidence_row["sustained_voice_min_share"] = _SUSTAINED_VOICE_MIN_SHARE

        return evidence_row

    @staticmethod
    def _fold_decision_for_speaker_slot(
        *,
        speaker_slot: str,
        fold_threshold_seconds: float | None,
        cumulative_emitted_seconds: float,
        substantial_speaker_slots: list[str],
        sustained_voice_speaker_slots: set[str],
    ) -> str:
        """Name why one cache slot stayed visible or folded for the operator artifact.

        Args:
            speaker_slot: Cache identity under review; never empty for an emitted NeMo slot.
            fold_threshold_seconds: Active duration threshold; None means the cap is disabled.
            cumulative_emitted_seconds: Stable wording time; zero means nothing visible yet.
            substantial_speaker_slots: Voices eligible to stay separate; empty means none yet.
            sustained_voice_speaker_slots: Acoustically protected voices; empty means none.

        Returns:
            Decision label; `no_stable_words` means the user has not seen wording from it yet.
        """
        # Frames without stable wording have not produced a visible fold decision yet.
        if cumulative_emitted_seconds <= 0:
            return "no_stable_words"

        # No configured cap means every detected voice stays available to the user.
        if fold_threshold_seconds is None:
            return "cap_disabled"

        # A substantial slot remains independently visible in the transcript.
        if cumulative_emitted_seconds >= fold_threshold_seconds:
            return "keep_substantial"

        # Sustained acoustic evidence keeps an otherwise marginal turn under its own source chip.
        if speaker_slot in sustained_voice_speaker_slots:
            return "keep_sustained_voice"

        # A marginal slot folds only after another substantial voice exists.
        if substantial_speaker_slots:
            return "fold_marginal"

        return "no_stable_words"

    def _continue_anchor_speakers(self, segments: list[Segment]) -> list[Segment]:
        """Map each window-local speaker ID to a stable session speaker ID.

        Each NeMo pass labels speakers independently. The overlap vote keeps
        the UI's visible speaker identities stable, and the cap merges stray
        `speaker_2+` IDs back into the nearest established consultation voice.

        Args:
            segments: Window segments with absolute times; empty passes through.

        Returns:
            Segments with speaker IDs aligned to the session's existing labels.
        """
        # Empty windows mean NeMo found no speaker evidence in this chunk.
        if segments == []:
            return segments

        known_speaker_ids = self._known_speaker_ids()
        window_speaker_ids = self._window_speaker_ids(segments)
        speaker_id_map = self._overlap_speaker_map(segments, known_speaker_ids)
        overlap_mapped_ids = set(speaker_id_map)
        speaker_id_map = self._complete_two_speaker_swap(
            window_speaker_ids,
            known_speaker_ids,
            speaker_id_map,
        )
        swap_completed_ids = set(speaker_id_map) - overlap_mapped_ids
        speaker_id_map, phantom_merge_count = self._complete_speaker_cap_map(
            segments,
            window_speaker_ids,
            known_speaker_ids,
            speaker_id_map,
        )
        remap_count = sum(
            1
            for window_speaker_id, canonical_speaker_id in speaker_id_map.items()
            if window_speaker_id != canonical_speaker_id
        )

        # Only changed IDs matter to the final quality record.
        if remap_count > 0:
            self.quality_stats.record_speaker_anchor_remaps(remap_count)

        # Phantom merges are the M16 signal that extra visible speakers were contained.
        if phantom_merge_count > 0:
            self.quality_stats.record_phantom_speaker_merges(phantom_merge_count)

        self._last_window_continuity = self._window_continuity_evidence(
            window_speaker_ids=window_speaker_ids,
            known_speaker_ids=known_speaker_ids,
            speaker_id_map=speaker_id_map,
            overlap_mapped_ids=overlap_mapped_ids,
            swap_completed_ids=swap_completed_ids,
            remap_count=remap_count,
            phantom_merge_count=phantom_merge_count,
        )

        return [
            replace(
                segment,
                speaker_id=speaker_id_map.get(segment.speaker_id, segment.speaker_id),
            )
            for segment in segments
        ]

    def _assign_row_identity(self, segments: list[Segment]) -> list[Segment]:
        """Mint a stable per-session row ID for each about-to-emit segment.

        Every transcript row the clinician sees gets `seg-<n>` exactly once,
        at emission. The ID travels through Mercure, storage, finalize
        replacement, and summary round-trips, so a per-row role correction can
        follow one visible line for the whole visit.

        Args:
            segments: Post-merge fresh segments; empty windows pass through.

        Returns:
            The same rows with `segment_id` set; held rows are identified later,
            when they actually emit.
        """
        identified_segments: list[Segment] = []
        # Emission order is unique within a session, making IDs collision-free.
        for segment in segments:
            self._emitted_row_count += 1
            identified_segments.append(
                replace(segment, segment_id=f"seg-{self._emitted_row_count:04d}")
            )

        return identified_segments

    def _window_continuity_evidence(
        self,
        *,
        window_speaker_ids: list[str],
        known_speaker_ids: list[str],
        speaker_id_map: dict[str, str],
        overlap_mapped_ids: set[str],
        swap_completed_ids: set[str],
        remap_count: int,
        phantom_merge_count: int,
    ) -> dict:
        """Build the speaker-continuity evidence for one emission window.

        The record explains why each raw window speaker ID became the visible
        canonical ID, so seam-level identity drift can be diagnosed offline.
        It carries only IDs, votes, and counts - no transcript text.

        Returns:
            Continuity evidence consumed by the per-window diagnostics log.
        """
        mapping_reasons: dict[str, str] = {}
        # Each raw window ID gets the mechanism that chose its visible identity:
        # overlap_vote = anchored by shared audio; two_speaker_swap = paired by
        # elimination; phantom_merge = extra ID folded into a visible voice;
        # kept_known = no evidence, same label kept; new_visible = new speaker.
        for window_speaker_id, canonical_speaker_id in speaker_id_map.items():
            if window_speaker_id in overlap_mapped_ids:
                mapping_reasons[window_speaker_id] = "overlap_vote"
            elif window_speaker_id in swap_completed_ids:
                mapping_reasons[window_speaker_id] = "two_speaker_swap"
            elif window_speaker_id != canonical_speaker_id:
                mapping_reasons[window_speaker_id] = "phantom_merge"
            elif window_speaker_id in known_speaker_ids:
                mapping_reasons[window_speaker_id] = "kept_known"
            else:
                mapping_reasons[window_speaker_id] = "new_visible"

        return {
            "raw_speaker_ids": list(window_speaker_ids),
            "known_speaker_ids": list(known_speaker_ids),
            "speaker_id_map": dict(speaker_id_map),
            "canonical_speaker_ids": list(dict.fromkeys(speaker_id_map.values())),
            "overlap_votes": list(self._window_overlap_votes),
            "mapping_reasons": mapping_reasons,
            "window_remaps": remap_count,
            "window_phantom_merges": phantom_merge_count,
        }

    def _log_window_continuity(
        self,
        *,
        window_start_seconds: float,
        emitted_from_seconds: float,
        emitted_rows: int,
        held_rows: int,
        is_finalize: bool,
    ) -> None:
        """Log one per-window speaker-continuity record for eval diagnostics.

        Eval runs turn these rows into `window-continuity.jsonl` so a wrong
        Doctor/Patient row can be traced to the emission window and mapping
        decision that produced it. The payload never includes transcript text.
        """
        # One row lands here for every ~5s chunk the clinician's browser sent,
        # plus one final row when they pressed Stop and the tail drained.
        continuity = self._last_window_continuity
        continuity_log_fields = {
            "session_id": self.session_id,
            "window_index": self.chunk_count,
            "phase": "finalize" if is_finalize else "chunk",
            "window_start_seconds": round(window_start_seconds, 3),
            "buffer_end_seconds": round(self.buffer.end_seconds, 3),
            "emitted_from_seconds": round(emitted_from_seconds, 3),
            "emitted_until_seconds": round(self._emitted_until_seconds, 3),
            "raw_speaker_ids": continuity.get("raw_speaker_ids", []),
            "known_speaker_ids": continuity.get("known_speaker_ids", []),
            "canonical_speaker_ids": continuity.get("canonical_speaker_ids", []),
            "speaker_id_map": continuity.get("speaker_id_map", {}),
            "overlap_votes": continuity.get("overlap_votes", []),
            "mapping_reasons": continuity.get("mapping_reasons", {}),
            "window_remaps": continuity.get("window_remaps", 0),
            "window_phantom_merges": continuity.get("window_phantom_merges", 0),
            "cumulative_anchor_remaps": (self.quality_stats.speaker_anchor_remap_count),
            "cumulative_phantom_merges": (
                self.quality_stats.phantom_speaker_merge_count
            ),
            "emitted_rows": emitted_rows,
            "held_rows": held_rows,
        }
        # A named replay adds count-only fold evidence; normal visit logs keep the old shape.
        if self._should_log_streaming_slot_evidence:
            continuity_log_fields["slot_share_evidence"] = continuity.get(
                "slot_share_evidence",
                [],
            )
            continuity_log_fields["folded_word_spans"] = continuity.get(
                "folded_word_spans",
                [],
            )
            # Absent pairwise evidence stays absent instead of a fabricated zero shape.
            if "pairwise_slot_evidence" in continuity:
                continuity_log_fields["pairwise_slot_evidence"] = continuity.get(
                    "pairwise_slot_evidence",
                    {},
                )
            continuity_log_fields["emission_decision_evidence"] = continuity.get(
                "emission_decision_evidence",
                {},
            )

        logger.info(
            "nemo_session.window_continuity session_id=%s window_index=%s phase=%s emitted_rows=%s",
            self.session_id,
            self.chunk_count,
            "finalize" if is_finalize else "chunk",
            emitted_rows,
            extra=continuity_log_fields,
        )

    def _known_speaker_ids(self) -> list[str]:
        """Return speaker IDs already visible in this browser session.

        Returns:
            Stable speaker IDs in first-seen order; empty means no transcript is visible yet.
        """
        return list(
            dict.fromkeys(segment.speaker_id for segment in self.accumulated_transcript)
        )

    def _window_speaker_ids(self, segments: list[Segment]) -> list[str]:
        """Return window-local speaker IDs in first-seen order.

        Args:
            segments: Window transcript rows; empty returns no IDs.

        Returns:
            Unique IDs in the order the clinician would see them.
        """
        return list(dict.fromkeys(segment.speaker_id for segment in segments))

    def _overlap_speaker_map(
        self,
        segments: list[Segment],
        known_speaker_ids: list[str],
    ) -> dict[str, str]:
        """Map window IDs to known IDs by strongest overlap vote.

        Returns:
            Window-to-session speaker map; empty means there was no overlap evidence.
        """
        overlap_votes: list[tuple[float, str, str]] = []

        # Each window/known pair gets all overlap seconds as its vote weight.
        for window_speaker_id in self._window_speaker_ids(segments):
            for known_speaker_id in known_speaker_ids:
                total_overlap = sum(
                    _segment_overlap_seconds(window_segment, known_segment)
                    for window_segment in segments
                    for known_segment in self.accumulated_transcript
                    if window_segment.speaker_id == window_speaker_id
                    and known_segment.speaker_id == known_speaker_id
                )

                # Zero overlap means this pair cannot prove speaker continuity.
                if total_overlap <= 0:
                    continue

                overlap_votes.append(
                    (total_overlap, window_speaker_id, known_speaker_id)
                )

        # Vote evidence feeds the per-window continuity log for seam diagnostics.
        self._window_overlap_votes = [
            {
                "window_speaker_id": window_speaker_id,
                "known_speaker_id": known_speaker_id,
                "overlap_seconds": round(seconds, 3),
            }
            for seconds, window_speaker_id, known_speaker_id in sorted(
                overlap_votes, reverse=True
            )
        ]

        speaker_id_map: dict[str, str] = {}
        used_window_ids: set[str] = set()
        used_known_ids: set[str] = set()

        # Strongest non-conflicting overlap wins so one window ID maps to one voice.
        for _seconds, window_speaker_id, known_speaker_id in sorted(
            overlap_votes,
            reverse=True,
        ):
            # Already-used IDs would create one-to-many visible speaker mappings.
            if (
                window_speaker_id in used_window_ids
                or known_speaker_id in used_known_ids
            ):
                continue

            speaker_id_map[window_speaker_id] = known_speaker_id
            used_window_ids.add(window_speaker_id)
            used_known_ids.add(known_speaker_id)

        return speaker_id_map

    def _complete_two_speaker_swap(
        self,
        window_speaker_ids: list[str],
        known_speaker_ids: list[str],
        speaker_id_map: dict[str, str],
    ) -> dict[str, str]:
        """Complete the classic two-speaker swap when one overlap anchor is known.

        Returns:
            Speaker map with the unanchored existing ID paired to the other visible voice.
        """
        completed_map = dict(speaker_id_map)

        # This shortcut only applies when both window IDs are already known speakers.
        if (
            self._speaker_cap == 2
            and len(known_speaker_ids) == 2
            and len(window_speaker_ids) == 2
            and set(window_speaker_ids).issubset(set(known_speaker_ids))
            and len(completed_map) == 1
        ):
            mapped_window_id = next(iter(completed_map))
            mapped_known_id = completed_map[mapped_window_id]
            other_window_id = next(
                speaker_id
                for speaker_id in window_speaker_ids
                if speaker_id != mapped_window_id
            )
            other_known_id = next(
                speaker_id
                for speaker_id in known_speaker_ids
                if speaker_id != mapped_known_id
            )
            completed_map[other_window_id] = other_known_id

        return completed_map

    def _complete_speaker_cap_map(
        self,
        segments: list[Segment],
        window_speaker_ids: list[str],
        known_speaker_ids: list[str],
        speaker_id_map: dict[str, str],
    ) -> tuple[dict[str, str], int]:
        """Fill missing window IDs and merge extras beyond the configured cap.

        Returns:
            Completed speaker map and the number of phantom IDs merged.
        """
        completed_map = dict(speaker_id_map)
        canonical_speaker_ids = list(known_speaker_ids)
        phantom_merge_count = 0

        # Mapped known IDs must count toward the cap before new IDs are considered.
        for canonical_speaker_id in completed_map.values():
            # Duplicate IDs are already visible to the browser and should count once.
            if canonical_speaker_id not in canonical_speaker_ids:
                canonical_speaker_ids.append(canonical_speaker_id)

        # Every window speaker needs an explicit visible-ID decision.
        for window_speaker_id in window_speaker_ids:
            # Overlap or swap logic already identified this speaker.
            if window_speaker_id in completed_map:
                continue

            # A known ID with no overlap is still stable enough to keep visible.
            if window_speaker_id in known_speaker_ids:
                completed_map[window_speaker_id] = window_speaker_id
                continue

            # Cap disabled means the UI should show the new participant as-is.
            if self._speaker_cap is None:
                completed_map[window_speaker_id] = window_speaker_id
                canonical_speaker_ids.append(window_speaker_id)
                continue

            # Until the cap is full, a new ID can become a visible consultation voice.
            if len(canonical_speaker_ids) < self._speaker_cap:
                completed_map[window_speaker_id] = window_speaker_id
                canonical_speaker_ids.append(window_speaker_id)
                continue

            canonical_speaker_id = self._nearest_canonical_speaker_id(
                window_speaker_id,
                segments,
                canonical_speaker_ids,
                completed_map,
            )
            completed_map[window_speaker_id] = canonical_speaker_id
            phantom_merge_count += 1
            logger.info(
                "nemo_session.phantom_speaker_merged session_id=%s window_speaker_id=%s canonical_speaker_id=%s",
                self.session_id,
                window_speaker_id,
                canonical_speaker_id,
                extra={
                    "session_id": self.session_id,
                    "window_speaker_id": window_speaker_id,
                    "canonical_speaker_id": canonical_speaker_id,
                    "speaker_cap": self._speaker_cap,
                },
            )

        return completed_map, phantom_merge_count

    def _nearest_canonical_speaker_id(
        self,
        window_speaker_id: str,
        segments: list[Segment],
        canonical_speaker_ids: list[str],
        speaker_id_map: dict[str, str],
    ) -> str:
        """Choose the nearest established voice for an extra window speaker.

        Returns:
            Canonical ID with the smallest timeline gap to the extra speaker.
        """
        extra_segments = [
            segment for segment in segments if segment.speaker_id == window_speaker_id
        ]
        best_canonical_id = canonical_speaker_ids[0]
        best_gap = float("inf")

        # Compare the extra ID to every already visible speaker track.
        for canonical_speaker_id in canonical_speaker_ids:
            candidate_segments = [
                segment
                for segment in self.accumulated_transcript
                if segment.speaker_id == canonical_speaker_id
            ]
            candidate_segments.extend(
                replace(segment, speaker_id=speaker_id_map[segment.speaker_id])
                for segment in segments
                if segment.speaker_id in speaker_id_map
                and speaker_id_map[segment.speaker_id] == canonical_speaker_id
            )

            # No candidate timing means this ID cannot be nearest yet.
            if candidate_segments == []:
                continue

            nearest_gap = min(
                _segment_gap_seconds(extra_segment, candidate_segment)
                for extra_segment in extra_segments
                for candidate_segment in candidate_segments
            )

            # The closest timeline neighbor is the least disruptive UI merge target.
            if nearest_gap < best_gap:
                best_gap = nearest_gap
                best_canonical_id = canonical_speaker_id

        return best_canonical_id

    def _validate_audio_format(self, raw_audio: bytes) -> None:
        """Validate that the first audio chunk matches the configured input format."""
        _WEBM_MAGIC = b"\x1a\x45\xdf\xa3"
        _WAV_MAGIC = b"RIFF"

        if self.input_format == "pcm":
            if raw_audio[:4] == _WEBM_MAGIC:
                raise ValueError(
                    "Audio format mismatch: configured for PCM but received WebM data. "
                    "Set NEMO_STREAM_INPUT_FORMAT=webm or fix the browser audio encoding."
                )
            if raw_audio[:4] == _WAV_MAGIC:
                raise ValueError(
                    "Audio format mismatch: configured for raw PCM but received WAV "
                    "(container with headers). Send headerless 16kHz mono s16le PCM."
                )
        elif self.input_format == "webm":
            if len(raw_audio) >= 4 and raw_audio[:4] != _WEBM_MAGIC:
                logger.warning(
                    "audio_format.webm_magic_missing session_id=%s first_bytes=%s",
                    self.session_id,
                    raw_audio[:4].hex(),
                    extra={
                        "session_id": self.session_id,
                        "first_bytes": raw_audio[:4].hex(),
                    },
                )

    def _decode_audio(self, raw_audio: bytes) -> bytes:
        """Decode the incoming browser chunk into 16kHz mono PCM."""
        if raw_audio == b"":
            return b""

        if self.input_format == "pcm":
            return raw_audio

        if self.input_format == "webm":
            return self._decode_webm_chunk(raw_audio)

        raise ValueError(f"Unsupported transcription input format: {self.input_format}")

    def _decode_webm_chunk(self, raw_audio: bytes) -> bytes:
        """Decode a MediaRecorder WebM/Opus chunk into raw PCM bytes."""
        input_path = None
        output_path = None
        try:
            with tempfile.NamedTemporaryFile(
                suffix=".webm", delete=False
            ) as input_file:
                input_file.write(raw_audio)
                input_path = input_file.name

            with tempfile.NamedTemporaryFile(
                suffix=".raw", delete=False
            ) as output_file:
                output_path = output_file.name

            subprocess.run(
                [
                    "ffmpeg",
                    "-y",
                    "-i",
                    input_path,
                    "-ar",
                    "16000",
                    "-ac",
                    "1",
                    "-f",
                    "s16le",
                    output_path,
                ],
                capture_output=True,
                check=True,
                timeout=30,
            )
            return Path(output_path).read_bytes()
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
            logger.error(
                "transcription_session.webm_decode.failed session_id=%s %s: %s",
                self.session_id,
                type(e).__name__,
                str(e)[:300],
                exc_info=e,
                extra={
                    "session_id": self.session_id,
                    "error_type": type(e).__name__,
                    "error": str(e)[:300],
                },
            )
            return b""
        finally:
            if input_path:
                Path(input_path).unlink(missing_ok=True)
            if output_path:
                Path(output_path).unlink(missing_ok=True)

    @staticmethod
    def _convert_webm_to_wav(webm_bytes: bytes) -> str | None:
        """Convert accumulated WebM audio into a WAV path for older replay tests.

        Args:
            webm_bytes: Browser WebM bytes; empty or invalid bytes return `None`.

        Returns:
            Path to a WAV file the caller must delete, or `None` when decoding fails.
        """
        webm_path = None
        wav_path = None
        try:
            with tempfile.NamedTemporaryFile(
                suffix=".webm", delete=False
            ) as input_file:
                webm_path = input_file.name
                input_file.write(webm_bytes)

            with tempfile.NamedTemporaryFile(
                suffix=".wav", delete=False
            ) as output_file:
                wav_path = output_file.name

            result = subprocess.run(
                [
                    "ffmpeg",
                    "-y",
                    "-i",
                    webm_path,
                    "-ar",
                    "16000",
                    "-ac",
                    "1",
                    "-acodec",
                    "pcm_s16le",
                    wav_path,
                ],
                capture_output=True,
                check=True,
                timeout=30,
            )
            logger.debug(
                "transcription_session.ffmpeg.completed",
                extra={
                    "input_bytes": len(webm_bytes),
                    "stderr": result.stderr.decode("utf-8", errors="replace")[-200:],
                },
            )
            return wav_path
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as error:
            logger.error(
                "transcription_session.ffmpeg.failed %s: %s",
                type(error).__name__,
                str(error)[:300],
                exc_info=error,
                extra={
                    "error_type": type(error).__name__,
                    "error": str(error)[:300],
                    "stderr": (getattr(error, "stderr", None) or b"").decode(
                        "utf-8", errors="replace"
                    )[-200:],
                },
            )
            # A failed conversion leaves the browser without usable WebM transcript text.
            if wav_path:
                Path(wav_path).unlink(missing_ok=True)
            return None
        finally:
            # The uploaded WebM scratch file is never needed after conversion.
            if webm_path:
                Path(webm_path).unlink(missing_ok=True)
