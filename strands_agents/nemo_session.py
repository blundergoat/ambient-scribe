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
        self._seen_segment_keys: set[tuple[str, int, int, str]] = set()
        self._format_validated: bool = False

        if self.input_format not in {"pcm", "webm"}:
            raise ValueError(f"Unsupported transcription input format: {self.input_format}")

        logger.info("transcription_session.created", extra={
            "session_id": session_id,
            "input_format": self.input_format,
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

        if not self._format_validated and raw_audio != b"":
            self._validate_audio_format(raw_audio)
            self._format_validated = True

        pcm_audio = self._decode_audio(raw_audio)
        if pcm_audio == b"":
            logger.info("transcription_session.chunk_skipped", extra={
                "session_id": self.session_id,
                "reason": "empty_after_decode",
            })
            return []

        self.buffer.append(pcm_audio)

        result = self.pipeline.transcribe_buffer(self.buffer.current_window())
        new_segments = self._filter_new_segments(result.segments)
        self.accumulated_transcript.extend(new_segments)

        duration_ms = int((time.time() - chunk_started_at) * 1000)
        logger.info("transcription_session.chunk_processed", extra={
            "session_id": self.session_id,
            "chunk_number": self.chunk_count,
            "audio_seconds": round(self.buffer.duration_seconds, 1),
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
        logger.info("transcription_session.finalizing", extra={
            "session_id": self.session_id,
            "total_chunks": self.chunk_count,
            "audio_seconds": round(self.buffer.duration_seconds, 1),
            "duration_seconds": round(time.time() - self.started_at, 1),
        })

        if self.buffer.total_bytes == 0:
            return self.accumulated_transcript

        # Final pass on complete audio
        result = self.pipeline.transcribe_buffer(self.buffer.full_audio())
        new_segments = self._filter_new_segments(result.segments)
        self.accumulated_transcript.extend(new_segments)

        return list(self.accumulated_transcript)

    def _filter_new_segments(self, segments: list[Segment]) -> list[Segment]:
        """Return only segments not already accumulated (deduplication by key).

        Uses (speaker_id, start_int, end_int, text) as a dedup key so that
        re-processing the same audio window on reconnect does not emit duplicates.
        """
        new_segments = []
        for seg in segments:
            key = (seg.speaker_id, int(seg.start * 100), int(seg.end * 100), seg.text)
            if key not in self._seen_segment_keys:
                self._seen_segment_keys.add(key)
                new_segments.append(seg)
        return new_segments

    def _validate_audio_format(self, raw_audio: bytes) -> None:
        """Validate that the first audio chunk matches the configured input format."""
        _WEBM_MAGIC = b"\x1a\x45\xdf\xa3"
        _WAV_MAGIC = b"RIFF"

        if self.input_format == "pcm":
            if raw_audio[:4] == _WEBM_MAGIC:
                raise ValueError(
                    f"Audio format mismatch: configured for PCM but received WebM data. "
                    f"Set NEMO_STREAM_INPUT_FORMAT=webm or fix the browser audio encoding."
                )
            if raw_audio[:4] == _WAV_MAGIC:
                raise ValueError(
                    f"Audio format mismatch: configured for raw PCM but received WAV "
                    f"(container with headers). Send headerless 16kHz mono s16le PCM."
                )
        elif self.input_format == "webm":
            if len(raw_audio) >= 4 and raw_audio[:4] != _WEBM_MAGIC:
                logger.warning("audio_format.webm_magic_missing", extra={
                    "session_id": self.session_id,
                    "first_bytes": raw_audio[:4].hex(),
                })

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
            with tempfile.NamedTemporaryFile(suffix=".webm", delete=False) as input_file:
                input_file.write(raw_audio)
                input_path = input_file.name

            with tempfile.NamedTemporaryFile(suffix=".raw", delete=False) as output_file:
                output_path = output_file.name

            subprocess.run(
                [
                    "ffmpeg", "-y", "-i", input_path,
                    "-ar", "16000", "-ac", "1",
                    "-f", "s16le",
                    output_path,
                ],
                capture_output=True,
                check=True,
                timeout=30,
            )
            return Path(output_path).read_bytes()
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
            logger.error("transcription_session.webm_decode.failed", extra={
                "error": str(e),
            })
            return b""
        finally:
            if input_path:
                Path(input_path).unlink(missing_ok=True)
            if output_path:
                Path(output_path).unlink(missing_ok=True)

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
