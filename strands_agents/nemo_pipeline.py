"""
NeMo Multitalker Pipeline — shared GPU model wrapper.

=============================================================================
WHAT THIS FILE DOES
=============================================================================

This module wraps the NeMo multitalker Parakeet pipeline (Sortformer diarization
+ multi-speaker ASR) in a singleton class that is loaded once at process startup
and shared across all WebSocket sessions.

IMPORTANT: The actual NeMo API surface must be discovered during Milestone 1's
"NeMo API Discovery Spike" (task 1.4). The code below uses PLACEHOLDER calls
that need to be replaced with the real NeMo API once documented.

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
from dataclasses import dataclass, field

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

    PLACEHOLDER IMPLEMENTATION: The actual NeMo API calls need to be filled in
    after the Milestone 1 API discovery spike. See docs/nemo-api-notes.md.
    """

    def __init__(self) -> None:
        """Load NeMo models into GPU memory.

        This is called once at FastAPI startup. It may take 30-60 seconds
        to load all models depending on disk speed and GPU.
        """
        self._model_provider = os.environ.get("NEMO_MODEL_PROVIDER", "local")

        logger.info("nemo_pipeline.loading_models", extra={
            "provider": self._model_provider,
        })

        # =====================================================================
        # PLACEHOLDER: Replace with actual NeMo model loading
        # =====================================================================
        # After Milestone 1 API discovery, this should look something like:
        #
        #   from nemo.collections.asr.models import SortformerDiarModel
        #   self.diar_model = SortformerDiarModel.restore_from("path/to/sortformer.nemo")
        #   self.asr_model = MultiTalkerParakeetModel.restore_from("path/to/parakeet.nemo")
        #
        # The exact classes and methods depend on NeMo's actual API.
        # See: docs/nemo-api-notes.md
        # =====================================================================
        self._models_loaded = False

        logger.info("nemo_pipeline.models_loaded")

    @property
    def is_loaded(self) -> bool:
        """Whether models are loaded and ready for inference."""
        return self._models_loaded

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
        logger.info("nemo_pipeline.transcribe_file.started", extra={
            "audio_path": audio_path,
        })

        # =====================================================================
        # PLACEHOLDER: Replace with actual NeMo inference
        # =====================================================================
        # After Milestone 1, this should call the composite pipeline entrypoint.
        # Example (actual API TBD):
        #
        #   raw_output = self.asr_model.transcribe([audio_path])
        #   segments = self._parse_nemo_output(raw_output)
        #   return TranscriptionResult(segments=segments, raw_output=raw_output)
        # =====================================================================
        return TranscriptionResult(segments=[], raw_output={})

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

        # =====================================================================
        # PLACEHOLDER: Replace with actual NeMo buffer inference
        # =====================================================================
        return TranscriptionResult(segments=[], raw_output={})

    def _parse_nemo_output(self, raw_output: dict) -> list[Segment]:
        """Parse NeMo's raw output into our Segment format.

        PLACEHOLDER: The actual parsing logic depends on NeMo's output format,
        which will be documented in docs/nemo-api-notes.md after the API spike.

        Args:
            raw_output: Raw output from NeMo's inference entrypoint

        Returns:
            List of Segment objects
        """
        # =====================================================================
        # PLACEHOLDER: Parse actual NeMo output format
        # =====================================================================
        return []
