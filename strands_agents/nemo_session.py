"""
Transcription session — per-WebSocket stateful wrapper around the NeMo pipeline.

=============================================================================
WHAT THIS FILE DOES
=============================================================================

Each WebSocket connection creates one TranscriptionSession. The session:

  1. Holds per-session state (WebM accumulator, accumulated transcript, timing)
  2. References the shared NemoPipeline singleton (does NOT load models)
  3. Converts WebM/Opus audio → WAV via ffmpeg for NeMo ingestion
  4. Implements the growing buffer strategy (re-process full audio each chunk)

=============================================================================
ARCHITECTURE
=============================================================================

  NemoPipeline (singleton, loaded at startup)
       ↑
  TranscriptionSession (per-WebSocket, holds state)
       ↑
  WebSocket handler (FastAPI, calls process_chunk)

Audio flow per chunk:
  1. Browser sends WebM/Opus chunk via WebSocket
  2. Session accumulates raw WebM bytes (first chunk contains container header)
  3. Full accumulated WebM is converted to 16kHz mono WAV via ffmpeg
  4. WAV file is passed to pipeline.transcribe_file() (growing buffer strategy)

The session's process_chunk() is SYNCHRONOUS and GPU-bound. The WebSocket
handler must call it via run_in_executor() to avoid blocking the event loop.
"""

from __future__ import annotations

import logging
import subprocess
import tempfile
import time
from pathlib import Path

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
    """Manages WebM accumulation and NeMo processing across WebSocket chunks.

    One instance per WebSocket connection. References the shared NemoPipeline
    singleton — does NOT load models.

    Audio strategy (growing buffer):
      - Accumulates raw WebM bytes from the browser (first chunk has container header)
      - On each process_chunk call, converts the full accumulated WebM → WAV via ffmpeg
      - Passes the complete WAV to pipeline.transcribe_file() for full reprocessing
      - AudioBuffer tracks stats only (duration estimate from chunk count)

    All processing methods are SYNCHRONOUS (GPU-bound). Callers must use
    asyncio.run_in_executor() to avoid blocking the event loop.
    """

    CHUNK_INTERVAL_SECONDS = 5.0  # Browser sends chunks every 5 seconds

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
        self._webm_accumulator: bytearray = bytearray()

        logger.info("transcription_session.created", extra={
            "session_id": session_id,
        })

    def process_chunk(self, raw_audio: bytes) -> list[Segment]:
        """Process a single audio chunk through the NeMo pipeline.

        SYNCHRONOUS — must be called via run_in_executor().

        Flow:
          1. Append WebM bytes to accumulator
          2. Convert full accumulated WebM → WAV via ffmpeg
          3. Pass WAV to pipeline.transcribe_file()
          4. Return new/updated segments

        Args:
            raw_audio: Raw WebM/Opus audio bytes from the browser WebSocket

        Returns:
            List of transcript segments from this processing pass
        """
        self.chunk_count += 1
        chunk_started_at = time.time()

        # Accumulate raw WebM bytes
        self._webm_accumulator.extend(raw_audio)

        # Convert full accumulated WebM to WAV, then transcribe
        wav_path = None
        try:
            wav_path = self._convert_webm_to_wav(bytes(self._webm_accumulator))
            if wav_path is None:
                return []

            result = self.pipeline.transcribe_file(wav_path)
            new_segments = result.segments
        finally:
            if wav_path:
                Path(wav_path).unlink(missing_ok=True)

        # Replace accumulated transcript with fresh full-audio result
        self.accumulated_transcript = list(new_segments)

        duration_ms = int((time.time() - chunk_started_at) * 1000)
        estimated_seconds = self.chunk_count * self.CHUNK_INTERVAL_SECONDS
        logger.info("transcription_session.chunk_processed", extra={
            "session_id": self.session_id,
            "chunk_number": self.chunk_count,
            "estimated_audio_seconds": round(estimated_seconds, 1),
            "webm_bytes": len(self._webm_accumulator),
            "segments_returned": len(new_segments),
            "duration_ms": duration_ms,
        })

        return new_segments

    def finalize(self) -> list[Segment]:
        """Final processing on session end.

        Called when the WebSocket disconnects. Runs NeMo on the full
        accumulated audio for a final high-quality transcription pass.

        Returns:
            Complete transcript segments for the entire session.
        """
        estimated_seconds = self.chunk_count * self.CHUNK_INTERVAL_SECONDS
        logger.info("transcription_session.finalizing", extra={
            "session_id": self.session_id,
            "total_chunks": self.chunk_count,
            "estimated_audio_seconds": round(estimated_seconds, 1),
            "duration_seconds": round(time.time() - self.started_at, 1),
        })

        if not self._webm_accumulator:
            return self.accumulated_transcript

        # Final pass on complete audio
        wav_path = None
        try:
            wav_path = self._convert_webm_to_wav(bytes(self._webm_accumulator))
            if wav_path is None:
                return self.accumulated_transcript

            result = self.pipeline.transcribe_file(wav_path)
            return result.segments if result.segments else self.accumulated_transcript
        finally:
            if wav_path:
                Path(wav_path).unlink(missing_ok=True)

    @staticmethod
    def _convert_webm_to_wav(webm_bytes: bytes) -> str | None:
        """Convert WebM/Opus audio to 16kHz mono WAV via ffmpeg.

        Args:
            webm_bytes: Raw WebM container bytes (accumulated from browser chunks)

        Returns:
            Path to the temporary WAV file, or None on conversion failure.
            Caller is responsible for deleting the file.
        """
        webm_path = None
        wav_path = None
        try:
            with tempfile.NamedTemporaryFile(suffix=".webm", delete=False) as infile:
                webm_path = infile.name
                infile.write(webm_bytes)

            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as outfile:
                wav_path = outfile.name

            result = subprocess.run(
                [
                    "ffmpeg", "-y", "-i", webm_path,
                    "-ar", "16000", "-ac", "1",
                    "-acodec", "pcm_s16le",
                    wav_path,
                ],
                capture_output=True,
                check=True,
                timeout=30,
            )
            logger.debug("transcription_session.ffmpeg.completed", extra={
                "input_bytes": len(webm_bytes),
                "stderr": result.stderr.decode("utf-8", errors="replace")[-200:],
            })
            return wav_path
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
            logger.error("transcription_session.ffmpeg.failed", extra={
                "error": str(e),
                "stderr": (getattr(e, "stderr", None) or b"").decode("utf-8", errors="replace")[-200:],
            })
            if wav_path:
                Path(wav_path).unlink(missing_ok=True)
            return None
        finally:
            if webm_path:
                Path(webm_path).unlink(missing_ok=True)
