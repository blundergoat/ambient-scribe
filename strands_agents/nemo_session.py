"""
Per-recording audio state for live transcription.

Each browser WebSocket owns one `TranscriptionSession` and shares the process
wide NeMo pipeline. The session buffers PCM or WebM chunks, runs synchronous
GPU work from the API executor, and returns only transcript lines the user has
not already seen.
"""

from __future__ import annotations

import logging
import subprocess
import tempfile
import time
from collections import deque
from dataclasses import replace
from pathlib import Path

from nemo_pipeline import NemoPipeline, Segment

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
    ) -> None:
        """Create a new transcription session.

        Args:
            session_id: Unique session identifier (UUID)
            pipeline: Shared NemoPipeline singleton (loaded at startup)
            input_format: Audio input format ("pcm" or "webm")
            max_buffer_duration: Maximum audio buffer duration in seconds.
        """
        self.session_id = session_id
        self.pipeline = pipeline
        self.input_format = input_format.lower()
        self.buffer = AudioBuffer(max_duration_seconds=max_buffer_duration)
        self.accumulated_transcript: list[Segment] = []
        self.chunk_count: int = 0
        self.started_at: float = time.time()
        self._emitted_until_seconds: float = 0.0
        self._anchor_segment: Segment | None = None
        self._format_validated: bool = False

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
        # Whole samples only: an odd byte offset would split a 16-bit sample and
        # NeMo rejects buffers that are not a multiple of the element size.
        window_start_byte = int(window_start_seconds * 16000) * 2
        window_audio = self.buffer.audio_from(window_start_byte)
        if window_audio == b"":
            return []

        result = self.pipeline.transcribe_buffer(window_audio)
        window_segments = [
            replace(
                segment,
                start=segment.start + window_start_seconds,
                end=segment.end + window_start_seconds,
            )
            for segment in sorted(result.segments, key=lambda segment: segment.start)
        ]
        window_segments = self._continue_anchor_speakers(window_segments)

        # Segments fully inside already-emitted audio are the context replay.
        fresh_segments = [
            segment
            for segment in window_segments
            if segment.end > self._emitted_until_seconds + _MIN_NEW_AUDIO_SECONDS
        ]

        # The buffer edge is still being spoken; those lines firm up next pass.
        if hold_unstable_tail:
            buffer_end_seconds = self.buffer.end_seconds
            while (
                fresh_segments
                and fresh_segments[-1].end
                > buffer_end_seconds - _UNSTABLE_TAIL_SECONDS
            ):
                fresh_segments.pop()

        if fresh_segments:
            self._emitted_until_seconds = max(
                self._emitted_until_seconds, fresh_segments[-1].end
            )
            self._anchor_segment = fresh_segments[-1]

        return fresh_segments

    def _continue_anchor_speakers(self, segments: list[Segment]) -> list[Segment]:
        """Match a window's speaker IDs to the previous window via the overlap.

        Each NeMo pass labels speakers independently, so `spk_0` in one window
        can be `spk_1` in the next. The context overlap re-reads the tail of
        the last emitted segment; whichever window speaker covers that span is
        the same voice, and for two-speaker consultations the other window
        speaker maps to the other known ID.

        Args:
            segments: Window segments with absolute times; empty passes through.

        Returns:
            Segments with speaker IDs aligned to the session's existing labels.
        """
        anchor = self._anchor_segment
        if anchor is None or segments == []:
            return segments

        def anchor_overlap(segment: Segment) -> float:
            return min(segment.end, anchor.end) - max(segment.start, anchor.start)

        overlap_segment = max(segments, key=anchor_overlap)
        # No usable overlap means the window cannot be aligned; keep labels as-is.
        if anchor_overlap(overlap_segment) <= 0:
            return segments

        if overlap_segment.speaker_id == anchor.speaker_id:
            return segments

        speaker_id_map = {overlap_segment.speaker_id: anchor.speaker_id}
        known_ids = list(
            dict.fromkeys(s.speaker_id for s in self.accumulated_transcript)
        )
        window_ids = list(dict.fromkeys(s.speaker_id for s in segments))
        # Two-speaker consultations let the remaining IDs pair up unambiguously.
        if len(known_ids) == 2 and len(window_ids) == 2:
            other_window_id = next(
                i for i in window_ids if i != overlap_segment.speaker_id
            )
            other_known_id = next(i for i in known_ids if i != anchor.speaker_id)
            speaker_id_map[other_window_id] = other_known_id

        return [
            replace(
                segment,
                speaker_id=speaker_id_map.get(segment.speaker_id, segment.speaker_id),
            )
            for segment in segments
        ]

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
                    "audio_format.webm_magic_missing",
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
                "transcription_session.webm_decode.failed",
                extra={
                    "error": str(e),
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
                "transcription_session.ffmpeg.failed",
                extra={
                    "error": str(error),
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
