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

    def __init__(self, session_id: str, pipeline: NemoPipeline) -> None:
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
        self.started_at: float = time.time()

        logger.info("transcription_session.created", extra={
            "session_id": session_id,
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

        # =====================================================================
        # PLACEHOLDER: Audio format conversion
        # =====================================================================
        # The actual conversion depends on the Milestone 1 audio format spike:
        #   - If PyAV: convert WebM/Opus → PCM in-process
        #   - If AudioWorklet: raw_audio is already PCM Float32, convert to int16
        #   - If ffmpeg: pipe through persistent ffmpeg subprocess
        # For now, assume raw PCM input.
        pcm_audio = raw_audio

        self.buffer.append(pcm_audio)

        # Run NeMo on current buffer window
        result = self.pipeline.transcribe_buffer(self.buffer.current_window())
        new_segments = result.segments

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
        return result.segments if result.segments else self.accumulated_transcript
