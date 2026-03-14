"""
Transcription session — per-WebSocket stateful wrapper around the NeMo pipeline.

=============================================================================
WHAT THIS FILE DOES
=============================================================================

Each WebSocket connection creates one TranscriptionSession. The session:

  1. Holds per-session state (audio buffer, accumulated transcript, timing)
  2. References the shared NemoPipeline singleton (does NOT load models)
  3. Handles audio format conversion (WebM/Opus → PCM)
  4. Manages the audio buffer strategy (growing/sliding/hybrid per M1 spike)

=============================================================================
ARCHITECTURE
=============================================================================

  NemoPipeline (singleton, loaded at startup)
       ↑
  TranscriptionSession (per-WebSocket, holds state)
       ↑
  WebSocket handler (FastAPI, calls process_chunk)

The session's process_chunk() is SYNCHRONOUS and GPU-bound. The WebSocket
handler must call it via run_in_executor() to avoid blocking the event loop.
"""

from __future__ import annotations

import logging
import os
import subprocess
import tempfile
import time

from nemo_pipeline import NemoPipeline, Segment

logger = logging.getLogger(__name__)


class AudioBuffer:
    """Manages the audio accumulation strategy for a session.

    The buffer strategy (growing vs sliding window vs hybrid) is determined
    by the Milestone 1 buffer spike. This implementation starts with a simple
    growing buffer — replace with the chosen strategy after benchmarking.
    """

    def __init__(self, max_duration_seconds: float = 900.0) -> None:
        """Initialize the audio buffer.

        Args:
            max_duration_seconds: Maximum audio duration to retain (default: 15 minutes).
                                  Safety cap to prevent unbounded memory growth.
        """
        self._chunks: list[bytes] = []
        self._total_bytes: int = 0
        self._max_bytes: int = int(max_duration_seconds * 16000 * 2)  # 16kHz, 16-bit = 32KB/s

    def append(self, pcm_audio: bytes) -> None:
        """Append PCM audio to the buffer.

        Args:
            pcm_audio: Raw PCM audio bytes (16kHz mono, 16-bit signed int)
        """
        self._chunks.append(pcm_audio)
        self._total_bytes += len(pcm_audio)

        # Safety cap: if buffer exceeds max, trim from the beginning
        while self._total_bytes > self._max_bytes and len(self._chunks) > 1:
            removed = self._chunks.pop(0)
            self._total_bytes -= len(removed)

    def current_window(self) -> bytes:
        """Return the current audio window for NeMo processing.

        For a growing buffer strategy, this returns all accumulated audio.
        For a sliding window strategy, this would return the last N seconds.

        Returns:
            Concatenated PCM bytes for the current processing window.
        """
        return b"".join(self._chunks)

    def full_audio(self) -> bytes:
        """Return all accumulated audio (for final processing on session end).

        Returns:
            All PCM bytes accumulated during the session.
        """
        return b"".join(self._chunks)

    @property
    def duration_seconds(self) -> float:
        """Estimated duration of buffered audio in seconds."""
        return self._total_bytes / (16000 * 2)  # 16kHz, 16-bit

    @property
    def total_bytes(self) -> int:
        """Total bytes in the buffer."""
        return self._total_bytes


class TranscriptionSession:
    """Manages audio buffer and NeMo processing across WebSocket chunks.

    One instance per WebSocket connection. References the shared NemoPipeline
    singleton — does NOT load models.

    All processing methods are SYNCHRONOUS (GPU-bound). Callers must use
    asyncio.run_in_executor() to avoid blocking the event loop.
    """

    def __init__(
        self,
        session_id: str,
        pipeline: NemoPipeline,
        input_format: str = "pcm",
    ) -> None:
        """Create a new transcription session.

        Args:
            session_id: Unique session identifier (UUID)
            pipeline: Shared NemoPipeline singleton (loaded at startup)
        """
        self.session_id = session_id
        self.pipeline = pipeline
        self.buffer = AudioBuffer()
        self.accumulated_transcript: list[Segment] = []
        self.chunk_count: int = 0
        self.input_format = input_format
        self.started_at: float = time.time()
        self._seen_segment_keys: set[tuple[str, int, int, str]] = set()

        logger.info("transcription_session.created", extra={
            "session_id": session_id,
            "input_format": input_format,
        })

    def process_chunk(self, raw_audio: bytes) -> list[Segment]:
        """Process a single audio chunk through the NeMo pipeline.

        SYNCHRONOUS — must be called via run_in_executor().

        Flow:
          1. Decode raw audio (WebM/Opus → PCM) if needed
          2. Append to audio buffer
          3. Run NeMo inference on the current buffer window
          4. Return new/updated segments

        Args:
            raw_audio: Raw audio bytes from the WebSocket (format depends on
                       Milestone 1 audio format spike decision)

        Returns:
            List of transcript segments from this processing pass
        """
        self.chunk_count += 1
        chunk_started_at = time.time()

        pcm_audio = self._decode_audio(raw_audio)
        if pcm_audio == b"":
            logger.info("transcription_session.chunk_skipped", extra={
                "session_id": self.session_id,
                "reason": "empty_after_decode",
            })
            return []

        self.buffer.append(pcm_audio)

        # Run NeMo on current buffer window
        result = self.pipeline.transcribe_buffer(self.buffer.current_window())
        new_segments = self._filter_new_segments(result.segments)

        # Accumulate transcript
        self.accumulated_transcript.extend(new_segments)

        duration_ms = int((time.time() - chunk_started_at) * 1000)
        logger.info("transcription_session.chunk_processed", extra={
            "session_id": self.session_id,
            "chunk_number": self.chunk_count,
            "buffer_seconds": round(self.buffer.duration_seconds, 1),
            "segments_returned": len(new_segments),
            "duration_ms": duration_ms,
        })

        return new_segments

    def finalize(self) -> list[Segment]:
        """Final processing on session end.

        Called when the WebSocket disconnects. Runs NeMo on the full audio
        buffer for a final high-quality transcription pass.

        Returns:
            Complete transcript segments for the entire session.
        """
        logger.info("transcription_session.finalizing", extra={
            "session_id": self.session_id,
            "total_chunks": self.chunk_count,
            "total_seconds": round(self.buffer.duration_seconds, 1),
            "duration_seconds": round(time.time() - self.started_at, 1),
        })

        if self.buffer.total_bytes == 0:
            return self.accumulated_transcript

        # Final pass on complete audio
        result = self.pipeline.transcribe_buffer(self.buffer.full_audio())
        new_segments = self._filter_new_segments(result.segments)
        self.accumulated_transcript.extend(new_segments)

        return list(self.accumulated_transcript)

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
        with tempfile.NamedTemporaryFile(suffix=".webm", delete=False) as input_file:
            input_file.write(raw_audio)
            input_path = input_file.name

        try:
            result = subprocess.run(
                [
                    os.environ.get("FFMPEG_BINARY", "ffmpeg"),
                    "-loglevel",
                    "error",
                    "-i",
                    input_path,
                    "-f",
                    "s16le",
                    "-acodec",
                    "pcm_s16le",
                    "-ac",
                    "1",
                    "-ar",
                    "16000",
                    "pipe:1",
                ],
                capture_output=True,
                check=True,
            )
        except FileNotFoundError as exc:
            raise RuntimeError("ffmpeg is required to decode WebM audio chunks") from exc
        except subprocess.CalledProcessError as exc:
            error_output = exc.stderr.decode("utf-8", errors="ignore").strip()
            raise RuntimeError(f"ffmpeg decode failed: {error_output}") from exc
        finally:
            try:
                os.unlink(input_path)
            except FileNotFoundError:
                pass

        return result.stdout

    def _filter_new_segments(self, segments: list[Segment]) -> list[Segment]:
        """Return only transcript segments not seen in earlier buffer passes."""
        new_segments: list[Segment] = []
        for segment in segments:
            key = (
                segment.speaker_id,
                int(round(segment.start * 100)),
                int(round(segment.end * 100)),
                segment.text.strip().lower(),
            )
            if key in self._seen_segment_keys:
                continue

            self._seen_segment_keys.add(key)
            new_segments.append(segment)

        return new_segments
