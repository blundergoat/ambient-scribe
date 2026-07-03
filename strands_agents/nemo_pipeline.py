"""
NeMo Multitalker Pipeline — shared GPU model wrapper.

=============================================================================
WHAT THIS FILE DOES
=============================================================================

This module wraps the NeMo multitalker Parakeet pipeline (Sortformer diarization
+ multi-speaker ASR) in a singleton class that is loaded once at process startup
and shared across all WebSocket sessions.

Approach: Independent diarize (Sortformer) + ASR (multitalker Parakeet), aligned
by proportional word distribution across diarization segments. Simpler than
SpeakerTaggedASR and sufficient for GP consultations with minimal speaker overlap.

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
import re
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import soundfile

logger = logging.getLogger(__name__)

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
    segment_id: str = ""     # Server-assigned unique ID
    revision: int = 1        # Incremented when segment is updated
    supersedes: str = ""     # segment_id this replaces (for reconciliation)

    def dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        d = {
            "speaker_id": self.speaker_id,
            "text": self.text,
            "start": self.start,
            "end": self.end,
            "is_interim": self.is_interim,
        }
        if self.segment_id:
            d["segment_id"] = self.segment_id
            d["revision"] = self.revision
        if self.supersedes:
            d["supersedes"] = self.supersedes
        return d


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

    PLACEHOLDER IMPLEMENTATION: The actual NeMo API calls need to be filled in
    after the Milestone 1 API discovery spike. See docs/nemo-api-notes.md.
    """

    def __init__(self) -> None:
        """Load NeMo models into GPU memory.

        This is called once at FastAPI startup. It may take 30-60 seconds
        to load all models depending on disk speed and GPU.

        Set NEMO_MODEL_PROVIDER=mock to skip model loading (for tests).
        """
        self._model_provider = os.environ.get("NEMO_MODEL_PROVIDER", "local")
        self._diar_model: Any = None
        self._asr_model: Any = None
        self._device: Any = None
        self._models_loaded = False
        self._load_error: str | None = None

        logger.info("nemo_pipeline.loading_models", extra={
            "provider": self._model_provider,
        })

        if self._model_provider == "mock":
            logger.info("nemo_pipeline.mock_mode")
            return

        try:
            import torch
            from nemo.collections.asr.models import SortformerEncLabelModel
            from nemo.collections.asr.models.multitalker_asr_models import EncDecMultiTalkerRNNTBPEModel

            device = torch.device("cuda" if torch.cuda.is_available() else "cpu")

            self._diar_model = SortformerEncLabelModel.from_pretrained(
                "nvidia/diar_streaming_sortformer_4spk-v2.1"
            ).eval().to(device)

            self._asr_model = EncDecMultiTalkerRNNTBPEModel.from_pretrained(
                "nvidia/multitalker-parakeet-streaming-0.6b-v1"
            ).eval().to(device)

            # CUDA graph workaround (required for PyTorch 2.8 compat)
            self._asr_model.decoding.decoding.use_cuda_graph_decoder = False
            self._asr_model.decoding.decoding.decoding_computer.disable_cuda_graphs()

            self._device = device
            self._models_loaded = True

            logger.info("nemo_pipeline.models_loaded", extra={"device": str(device)})
        except Exception as e:
            self._load_error = str(e)
            logger.exception("nemo_pipeline.models_failed", extra={
                "provider": self._model_provider,
                "error": self._load_error,
            })

    @property
    def is_loaded(self) -> bool:
        """Whether models are loaded and ready for inference."""
        return self._models_loaded

    @property
    def load_error(self) -> str | None:
        """Model load failure, if startup fell back to degraded mode."""
        return self._load_error

    def transcribe_file(self, audio_path: str) -> TranscriptionResult:
        """Process a complete audio file. Returns speaker-attributed segments.

        Runs independent diarization (Sortformer) + ASR (multitalker Parakeet),
        then aligns words to speaker segments proportionally.

        Args:
            audio_path: Path to a WAV file (16kHz mono PCM expected)

        Returns:
            TranscriptionResult with speaker-attributed segments.
        """
        logger.info("nemo_pipeline.transcribe_file.started", extra={
            "audio_path": audio_path,
        })

        if not self._models_loaded:
            return TranscriptionResult(segments=[], raw_output={})

        import torch

        with torch.inference_mode():
            diar_output = self._diar_model.diarize(
                audio=audio_path, batch_size=1, verbose=False,
            )
            asr_hyps = self._asr_model.transcribe(
                [audio_path], return_hypotheses=True, verbose=False,
            )

        parsed = self._parse_nemo_output(diar_output, asr_hyps)

        logger.info("nemo_pipeline.transcribe_file.completed", extra={
            "audio_path": audio_path,
            "segments": len(parsed),
        })

        return TranscriptionResult(
            segments=parsed,
            raw_output={
                "diar": str(diar_output),
                "asr_text": asr_hyps[0].text if asr_hyps else "",
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
        logger.info("nemo_pipeline.transcribe_buffer.started", extra={
            "buffer_bytes": len(audio_buffer),
        })

        if not self._models_loaded:
            return TranscriptionResult(segments=[], raw_output={})

        pcm_array = np.frombuffer(audio_buffer, dtype=np.int16).astype(np.float32) / 32768.0

        tmp_path = None
        try:
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
                tmp_path = f.name
                soundfile.write(tmp_path, pcm_array, 16000)
            return self.transcribe_file(tmp_path)
        finally:
            if tmp_path:
                Path(tmp_path).unlink(missing_ok=True)

    def _parse_nemo_output(
        self, diar_segments: Any, asr_hyps: Any,
    ) -> list[Segment]:
        """Align ASR words to diarization speaker segments proportionally.

        Approach: Independent diarize + ASR. Words are distributed across
        diarization segments proportional to each segment's duration share.

        Diarization output format (list of strings per file):
            ["0.560 3.120 speaker_0", "3.200 5.800 speaker_1", ...]

        ASR output: list of Hypothesis objects with .text attribute.

        Args:
            diar_segments: Raw output from SortformerEncLabelModel.diarize()
            asr_hyps: Raw output from EncDecMultiTalkerRNNTBPEModel.transcribe()

        Returns:
            List of Segment objects with speaker attribution.
        """
        # Parse diarization segments: "start end speaker_id"
        parsed_diar = self._parse_diar_strings(diar_segments)
        if not parsed_diar:
            return []

        # Filter out hallucinated speakers (< 5% of total frame activity)
        parsed_diar = self._filter_hallucinated_speakers(parsed_diar)
        if not parsed_diar:
            return []

        # Get ASR text
        asr_text = ""
        if asr_hyps:
            hyp = asr_hyps[0]
            asr_text = hyp.text if hasattr(hyp, "text") else str(hyp)
        if not asr_text.strip():
            return [
                Segment(speaker_id=spk, text="", start=start, end=end)
                for start, end, spk in parsed_diar
            ]

        words = asr_text.split()
        if not words:
            return []

        # Distribute words proportionally across diarization segments
        total_duration = sum(end - start for start, end, _ in parsed_diar)
        if total_duration <= 0:
            return []

        segments: list[Segment] = []
        word_idx = 0
        for i, (start, end, speaker_id) in enumerate(parsed_diar):
            seg_duration = end - start
            share = seg_duration / total_duration

            if i == len(parsed_diar) - 1:
                # Last segment gets all remaining words
                seg_words = words[word_idx:]
            else:
                word_count = max(1, round(len(words) * share))
                seg_words = words[word_idx:word_idx + word_count]
                word_idx += len(seg_words)

            text = " ".join(seg_words)
            if text:
                segments.append(Segment(
                    speaker_id=speaker_id,
                    text=text,
                    start=start,
                    end=end,
                ))

        return segments

    @staticmethod
    def _filter_hallucinated_speakers(
        parsed_diar: list[tuple[float, float, str]],
        min_share: float = 0.05,
        min_absolute_duration: float = 1.0,
        min_segment_count: int = 2,
    ) -> list[tuple[float, float, str]]:
        """Remove brief one-off speakers with less than ``min_share`` of activity.

        NeMo's Sortformer occasionally hallucinates a brief speaker segment
        (e.g., <= 1 second in a 100-second recording). These ghost speakers
        cause downstream role-inference noise. The absolute-duration and segment
        count floors keep legitimate short speakers from being dropped solely
        because they have less than 5% of a long recording.

        Args:
            parsed_diar: List of ``(start, end, speaker_id)`` tuples from
                :meth:`_parse_diar_strings`.
            min_share: Minimum fraction of total duration a speaker must
                occupy to be kept (default 5 %).
            min_absolute_duration: Always keep speakers above this cumulative
                duration even if their share is small.
            min_segment_count: Always keep speakers that appear in at least this
                many diarization segments.

        Returns:
            Filtered list with the same tuple structure.
        """
        if not parsed_diar:
            return parsed_diar

        total_duration = sum(end - start for start, end, _ in parsed_diar)
        if total_duration <= 0:
            return parsed_diar

        # Accumulate per-speaker duration
        speaker_durations: dict[str, float] = {}
        speaker_segment_counts: dict[str, int] = {}
        for start, end, speaker_id in parsed_diar:
            speaker_durations[speaker_id] = speaker_durations.get(speaker_id, 0.0) + (end - start)
            speaker_segment_counts[speaker_id] = speaker_segment_counts.get(speaker_id, 0) + 1

        # Suppress only tiny, one-off speakers below the proportional threshold.
        suppressed = {
            spk
            for spk, dur in speaker_durations.items()
            if (
                dur / total_duration < min_share
                and dur <= min_absolute_duration
                and speaker_segment_counts[spk] < min_segment_count
            )
        }

        if suppressed:
            for spk in suppressed:
                logger.warning(
                    "nemo_pipeline.hallucinated_speaker_suppressed",
                    extra={
                        "speaker_id": spk,
                        "duration": speaker_durations[spk],
                        "total_duration": total_duration,
                        "share": speaker_durations[spk] / total_duration,
                        "segments": speaker_segment_counts[spk],
                        "min_share": min_share,
                        "min_absolute_duration": min_absolute_duration,
                        "min_segment_count": min_segment_count,
                    },
                )

        return [
            (start, end, spk)
            for start, end, spk in parsed_diar
            if spk not in suppressed
        ]

    @staticmethod
    def _parse_diar_strings(diar_output: Any) -> list[tuple[float, float, str]]:
        """Parse Sortformer diarization output strings.

        Input formats handled:
          - List of lists of strings: [["0.56 3.12 speaker_0", ...]]
          - List of strings: ["0.56 3.12 speaker_0", ...]

        Returns:
            List of (start, end, speaker_id) tuples, sorted by start time.
        """
        raw_strings: list[str] = []

        if not diar_output:
            return []

        # Handle nested list (batch output: list of lists)
        items = diar_output
        if items and isinstance(items[0], list):
            items = items[0]

        for item in items:
            s = str(item).strip()
            if s:
                raw_strings.append(s)

        parsed: list[tuple[float, float, str]] = []
        for s in raw_strings:
            match = re.match(r"([\d.]+)\s+([\d.]+)\s+(\S+)", s)
            if match:
                start = float(match.group(1))
                end = float(match.group(2))
                speaker = match.group(3)
                parsed.append((start, end, speaker))

        parsed.sort(key=lambda x: x[0])
        return parsed
