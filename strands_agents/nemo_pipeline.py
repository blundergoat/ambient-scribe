"""
NeMo Multitalker Pipeline — shared GPU model wrapper.

=============================================================================
WHAT THIS FILE DOES
=============================================================================

This module wraps the NeMo multitalker Parakeet pipeline (Sortformer diarization
+ multi-speaker ASR) in a singleton class that is loaded once at process startup
and shared across all WebSocket sessions.

This implementation follows the API surface documented during Milestone 1's
"NeMo API Discovery Spike" (task 1.4).

The plan's original code sketches assumed independent sortformer.diarize() and
parakeet.transcribe() calls, but NeMo's multitalker pipeline is a COMPOSITE
RECIPE with its own inference entrypoint. Do NOT assume you can manually pass
speaker masks between models.

=============================================================================
DESIGN DECISIONS
=============================================================================

  - Models loaded ONCE at startup, shared across sessions (multi-GB GPU models)
  - GPU-bound: all inference runs in a ThreadPoolExecutor to avoid blocking
    the FastAPI async event loop
  - NeMo owns the GPU exclusively — the Strands role inference agent must use
    Bedrock or CPU-only Ollama (see Milestone 1 VRAM constraint)

See: docs/nemo-api-notes.md for the actual API surface documentation.
"""

from __future__ import annotations

import logging
import os
import tempfile
import wave
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_SAMPLE_RATE_HZ = 16000
DIAR_MODEL_ID = os.environ.get(
    "NEMO_DIAR_MODEL_ID",
    "nvidia/diar_streaming_sortformer_4spk-v2.1",
)
ASR_MODEL_ID = os.environ.get(
    "NEMO_ASR_MODEL_ID",
    "nvidia/multitalker-parakeet-streaming-0.6b-v1",
)

# =============================================================================
# DATA MODELS
# =============================================================================


@dataclass
class Segment:
    """A single transcript segment with speaker attribution."""

    speaker_id: str          # e.g., "spk_0", "spk_1"
    text: str                # Transcribed text
    start: float             # Start time in seconds
    end: float               # End time in seconds
    is_interim: bool = False  # True if this segment may be revised

    def dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        return {
            "speaker_id": self.speaker_id,
            "text": self.text,
            "start": self.start,
            "end": self.end,
            "is_interim": self.is_interim,
        }


@dataclass
class TranscriptionResult:
    """Result from a NeMo transcription call."""

    segments: list[Segment] = field(default_factory=list)
    raw_output: dict = field(default_factory=dict)  # Store raw NeMo output for debugging


# =============================================================================
# NEMO PIPELINE
# =============================================================================


class NemoPipeline:
    """Wraps the NeMo multitalker Parakeet pipeline.

    Loaded once at process startup. Shared across all TranscriptionSession instances.
    All methods are synchronous and GPU-bound — callers must use run_in_executor().

    This implementation uses the documented Sortformer diarizer plus
    multitalker Parakeet ASR, with per-segment WAV slicing for a pragmatic
    speaker-attributed transcript path.
    """

    def __init__(
        self,
        load_models: bool = False,
        strict_startup: bool | None = None,
    ) -> None:
        """Load NeMo models into GPU memory.

        This is called once at FastAPI startup. It may take 30-60 seconds
        to load all models depending on disk speed and GPU.
        """
        self._model_provider = os.environ.get("NEMO_MODEL_PROVIDER", "local")
        self._device: Any = None
        self._torch: Any = None
        self._diar_model: Any = None
        self._asr_model: Any = None
        self._load_error: str | None = None
        self._strict_startup = load_models if strict_startup is None else strict_startup

        logger.info("nemo_pipeline.loading_models", extra={
            "provider": self._model_provider,
            "load_models": load_models,
        })

        self._models_loaded = False
        if load_models:
            try:
                self._load_models()
            except Exception as exc:
                self._load_error = str(exc)
                logger.exception("nemo_pipeline.model_load_failed")
                if self._strict_startup:
                    raise

        logger.info("nemo_pipeline.ready", extra={
            "models_loaded": self._models_loaded,
        })

    @property
    def is_loaded(self) -> bool:
        """Whether models are loaded and ready for inference."""
        return self._models_loaded

    @property
    def load_error(self) -> str | None:
        """Model load error if startup failed in non-strict mode."""
        return self._load_error

    def transcribe_file(self, audio_path: str) -> TranscriptionResult:
        """Process a complete audio file. Returns speaker-attributed segments.

        This is the offline/batch mode entry point. Used for:
          - Testing with fixture WAV files (Milestone 1)
          - The /transcribe/file HTTP endpoint (Milestone 2)
          - Demo replay mode (Milestone 4)

        Args:
            audio_path: Path to a WAV file (16kHz mono PCM expected)

        Returns:
            TranscriptionResult with speaker-attributed segments.
        """
        if not self.is_loaded:
            logger.warning("nemo_pipeline.transcribe_file.skipped", extra={
                "reason": "models_not_loaded",
                "audio_path": audio_path,
            })
            return TranscriptionResult(raw_output={
                "warning": "NeMo models are not loaded",
                "audio_path": audio_path,
            })

        audio = Path(audio_path)
        if not audio.exists():
            raise FileNotFoundError(f"Audio file not found: {audio_path}")

        logger.info("nemo_pipeline.transcribe_file.started", extra={
            "audio_path": audio_path,
        })

        diarization_output = self._run_diarization(audio_path)
        diarized_segments = self._merge_adjacent_segments(
            self._parse_nemo_output(diarization_output)
        )

        if not diarized_segments:
            return TranscriptionResult(segments=[], raw_output={
                "audio_path": audio_path,
                "diarization_segments": [],
            })

        transcript_segments: list[Segment] = []
        raw_segments: list[dict[str, Any]] = []

        with tempfile.TemporaryDirectory(prefix="ambient-scribe-segments-") as temp_dir:
            for index, diarized_segment in enumerate(diarized_segments):
                clip_path = Path(temp_dir) / f"segment_{index}.wav"
                if not self._extract_wav_segment(
                    audio_path,
                    clip_path,
                    diarized_segment["start"],
                    diarized_segment["end"],
                ):
                    continue

                text = self._transcribe_clip(str(clip_path))
                if text == "":
                    continue

                segment = Segment(
                    speaker_id=diarized_segment["speaker_id"],
                    text=text,
                    start=diarized_segment["start"],
                    end=diarized_segment["end"],
                )
                transcript_segments.append(segment)
                raw_segments.append({
                    **diarized_segment,
                    "text": text,
                })

        return TranscriptionResult(
            segments=transcript_segments,
            raw_output={
                "audio_path": audio_path,
                "diarization_segments": raw_segments,
            },
        )

    def transcribe_buffer(self, audio_buffer: bytes) -> TranscriptionResult:
        """Process an audio buffer (PCM bytes). Returns speaker-attributed segments.

        This is the chunked/streaming mode entry point. Used by TranscriptionSession
        to process accumulated audio from WebSocket chunks.

        The buffer strategy (growing vs sliding window vs hybrid) is determined
        by the Milestone 1 buffer strategy spike. See docs/nemo-api-notes.md.

        Args:
            audio_buffer: Raw PCM audio bytes (16kHz mono, 16-bit signed int)

        Returns:
            TranscriptionResult with speaker-attributed segments.
        """
        if audio_buffer == b"":
            return TranscriptionResult()

        logger.info("nemo_pipeline.transcribe_buffer.started", extra={
            "buffer_bytes": len(audio_buffer),
        })

        with tempfile.NamedTemporaryFile(suffix=".wav") as wav_file:
            self._write_pcm_wav(wav_file.name, audio_buffer)
            return self.transcribe_file(wav_file.name)

    def _load_models(self) -> None:
        """Load the documented NeMo models into GPU memory."""
        import torch
        from nemo.collections.asr.models import SortformerEncLabelModel
        from nemo.collections.asr.models.multitalker_asr_models import EncDecMultiTalkerRNNTBPEModel

        self._torch = torch
        self._device = torch.device("cuda" if torch.cuda.is_available() else "cpu")

        self._diar_model = SortformerEncLabelModel.from_pretrained(
            DIAR_MODEL_ID,
        ).eval().to(self._device)
        self._asr_model = EncDecMultiTalkerRNNTBPEModel.from_pretrained(
            ASR_MODEL_ID,
        ).eval().to(self._device)

        if hasattr(self._asr_model, "decoding") and hasattr(self._asr_model.decoding, "decoding"):
            decoding = self._asr_model.decoding.decoding
            decoding.use_cuda_graph_decoder = False
            decoding_computer = getattr(decoding, "decoding_computer", None)
            if hasattr(decoding_computer, "disable_cuda_graphs"):
                decoding_computer.disable_cuda_graphs()

        self._models_loaded = True
        self._load_error = None

        logger.info("nemo_pipeline.models_loaded", extra={
            "device": str(self._device),
            "diar_model_id": DIAR_MODEL_ID,
            "asr_model_id": ASR_MODEL_ID,
        })

    def _run_diarization(self, audio_path: str) -> Any:
        """Run diarization with the validated Sortformer API."""
        with self._torch.inference_mode():
            return self._diar_model.diarize(
                audio=audio_path,
                batch_size=1,
                verbose=False,
            )

    def _parse_nemo_output(self, raw_output: Any) -> list[dict[str, Any]]:
        """Parse NeMo's raw output into our Segment format.

        Args:
            raw_output: Raw output from NeMo's inference entrypoint

        Returns:
            Parsed diarization segments with speaker IDs and timestamps.
        """
        diarization_lines: list[str] = []

        if isinstance(raw_output, tuple):
            first_item = raw_output[0]
        else:
            first_item = raw_output

        if isinstance(first_item, list) and len(first_item) == 1 and isinstance(first_item[0], list):
            diarization_lines = [line for line in first_item[0] if isinstance(line, str)]
        elif isinstance(first_item, list):
            diarization_lines = [line for line in first_item if isinstance(line, str)]

        parsed_segments: list[dict[str, Any]] = []
        for line in diarization_lines:
            parts = line.split()
            if len(parts) != 3:
                continue

            start, end, speaker_id = parts
            parsed_segments.append({
                "speaker_id": speaker_id.replace("speaker_", "spk_"),
                "start": float(start),
                "end": float(end),
            })

        return parsed_segments

    def _merge_adjacent_segments(
        self,
        segments: list[dict[str, Any]],
        max_gap_seconds: float = 0.35,
        min_duration_seconds: float = 0.15,
    ) -> list[dict[str, Any]]:
        """Coalesce adjacent diarization segments to reduce tiny ASR calls."""
        if segments == []:
            return []

        merged: list[dict[str, Any]] = [dict(segments[0])]
        for segment in segments[1:]:
            current = merged[-1]
            same_speaker = current["speaker_id"] == segment["speaker_id"]
            gap_seconds = segment["start"] - current["end"]

            if same_speaker and gap_seconds <= max_gap_seconds:
                current["end"] = segment["end"]
                continue

            if (segment["end"] - segment["start"]) < min_duration_seconds:
                continue

            merged.append(dict(segment))

        return merged

    def _extract_wav_segment(
        self,
        source_path: str,
        destination_path: Path,
        start_seconds: float,
        end_seconds: float,
    ) -> bool:
        """Slice a PCM WAV clip for per-segment ASR."""
        with wave.open(source_path, "rb") as source_wav:
            frame_rate = source_wav.getframerate()
            start_frame = max(int(start_seconds * frame_rate), 0)
            end_frame = max(int(end_seconds * frame_rate), start_frame)
            total_frames = source_wav.getnframes()

            if start_frame >= total_frames or end_frame <= start_frame:
                return False

            source_wav.setpos(start_frame)
            frames = source_wav.readframes(end_frame - start_frame)
            if frames == b"":
                return False

            with wave.open(str(destination_path), "wb") as clip_wav:
                clip_wav.setnchannels(source_wav.getnchannels())
                clip_wav.setsampwidth(source_wav.getsampwidth())
                clip_wav.setframerate(frame_rate)
                clip_wav.writeframes(frames)

        return True

    def _transcribe_clip(self, clip_path: str) -> str:
        """Run ASR on a single diarized clip."""
        with self._torch.inference_mode():
            hypotheses = self._asr_model.transcribe(
                [clip_path],
                return_hypotheses=True,
                verbose=False,
            )

        if hypotheses == []:
            return ""

        first_hypothesis = hypotheses[0]
        if hasattr(first_hypothesis, "text"):
            text = str(first_hypothesis.text)
        elif isinstance(first_hypothesis, str):
            text = first_hypothesis
        else:
            text = str(first_hypothesis)

        return " ".join(text.split())

    def _write_pcm_wav(
        self,
        output_path: str,
        pcm_audio: bytes,
        sample_rate_hz: int = DEFAULT_SAMPLE_RATE_HZ,
    ) -> None:
        """Write raw PCM audio to a mono 16-bit WAV file."""
        with wave.open(output_path, "wb") as wav_file:
            wav_file.setnchannels(1)
            wav_file.setsampwidth(2)
            wav_file.setframerate(sample_rate_hz)
            wav_file.writeframes(pcm_audio)
