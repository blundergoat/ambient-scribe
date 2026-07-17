"""
NeMo GPU transcription pipeline for the live scribe.

The FastAPI server loads this once, then each browser recording sends audio
through it from a worker thread. It turns consultation audio into timestamped
speaker segments that the UI can stream, relabel, replay, and summarize.
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
from medical_lexicon import (
    correct_medical_terms,
    default_medical_lexicon_path,
    load_medical_lexicon,
)
from nemo_confidence import (
    enable_word_confidence_decoding,
    row_confidence_for_word_share,
    word_confidences_for_display_words,
)

logger = logging.getLogger(__name__)

# =============================================================================
# DATA MODELS
# =============================================================================


@dataclass
class Segment:
    """
    One line of transcript text shown in the browser.

    NeMo creates the raw speaker ID and timestamp, while later role inference
    may add DOCTOR/PATIENT labels. Empty IDs or text mean the UI has less
    context and should avoid overconfident role display.

    Attributes:
        speaker_id: Raw NeMo speaker label such as `spk_0`; empty means unknown.
        text: Transcript text shown to the user; empty means no speech captured.
        start: Segment start time in seconds for transcript timing.
        end: Segment end time in seconds for transcript timing.
        is_interim: True when the browser may see this line revised later.
        segment_id: Stable server ID; empty means no reconciliation ID yet.
        revision: Version number used when a visible line is updated.
        supersedes: Prior segment ID replaced by this line; empty means none.
        confidence: How clearly the row was heard (0-1); None means no
            trustworthy value exists and the row renders without styling.
    """

    speaker_id: str  # e.g., "spk_0", "spk_1"
    text: str  # Transcribed text
    start: float  # Start time in seconds
    end: float  # End time in seconds
    is_interim: bool = False  # True if this segment may be revised
    segment_id: str = ""  # Server-assigned unique ID
    revision: int = 1  # Incremented when segment is updated
    supersedes: str = ""  # segment_id this replaces (for reconciliation)
    confidence: float | None = None  # Row ASR confidence; None means unmeasured

    def dict(self) -> dict:
        """Convert the segment into the JSON shape streamed to the browser.

        Returns:
            Segment payload; missing IDs mean the browser treats it as a new raw line.
        """
        segment_payload = {
            "speaker_id": self.speaker_id,
            "text": self.text,
            "start": self.start,
            "end": self.end,
            "is_interim": self.is_interim,
        }
        # Segment IDs let the UI reconcile a revised line instead of duplicating it.
        if self.segment_id:
            segment_payload["segment_id"] = self.segment_id
            segment_payload["revision"] = self.revision
        # Superseded IDs tell the UI which earlier transcript line was replaced.
        if self.supersedes:
            segment_payload["supersedes"] = self.supersedes
        # Unmeasured rows omit the key so they render exactly as before.
        if self.confidence is not None:
            segment_payload["confidence"] = self.confidence
        return segment_payload


@dataclass
class TranscriptionResult:
    """Result from a NeMo transcription call."""

    segments: list[Segment] = field(default_factory=list)
    raw_output: dict = field(
        default_factory=dict
    )  # Store raw NeMo output for debugging


# =============================================================================
# NEMO PIPELINE
# =============================================================================


class NemoPipeline:
    """
    Wraps the NeMo multitalker Parakeet pipeline used by the browser.

    The FastAPI app loads it once and shares it across live recordings, uploads,
    and replay demos. Public methods stay synchronous because callers run them
    in the GPU worker pool. Optional medical-term correction is applied here so
    the UI never needs to know how transcript text was normalised.
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
        self._models_loaded = False
        self._load_error: str | None = None
        # Misheard drug/condition names are corrected by default; explicit
        # MEDICAL_BOOST_ENABLED=0 opts a deployment back out.
        self._medical_boost_enabled = _is_env_flag_enabled(
            "MEDICAL_BOOST_ENABLED", default=True
        )
        self._medical_lexicon_path = Path(
            os.environ.get("MEDICAL_LEXICON_PATH", default_medical_lexicon_path())
        )
        self._medical_phrases = (
            load_medical_lexicon(self._medical_lexicon_path)
            if self._medical_boost_enabled
            else ()
        )

        logger.info(
            "nemo_pipeline.loading_models",
            extra={
                "provider": self._model_provider,
            },
        )

        if self._model_provider == "mock":
            logger.info("nemo_pipeline.mock_mode")
            return

        try:
            import torch
            from nemo.collections.asr.models import SortformerEncLabelModel
            from nemo.collections.asr.models.multitalker_asr_models import (
                EncDecMultiTalkerRNNTBPEModel,
            )

            device = torch.device("cuda" if torch.cuda.is_available() else "cpu")

            self._diar_model = (
                SortformerEncLabelModel.from_pretrained(
                    "nvidia/diar_streaming_sortformer_4spk-v2.1"
                )
                .eval()
                .to(device)
            )

            self._asr_model = (
                EncDecMultiTalkerRNNTBPEModel.from_pretrained(
                    "nvidia/multitalker-parakeet-streaming-0.6b-v1"
                )
                .eval()
                .to(device)
            )

            # Word confidence feeds low-confidence row styling; it must be
            # enabled BEFORE the CUDA-graph workaround because the decoding
            # strategy rebuild replaces the patched decoder internals.
            enable_word_confidence_decoding(self._asr_model)

            # CUDA graph workaround (required for PyTorch 2.8 compat)
            self._asr_model.decoding.decoding.use_cuda_graph_decoder = False
            self._asr_model.decoding.decoding.decoding_computer.disable_cuda_graphs()

            # Decode-time phrase boosting is GPU-pending; this toggle currently enables post-ASR correction.
            if self._medical_boost_enabled:
                logger.info(
                    "nemo_pipeline.medical_lexicon.loaded",
                    extra={
                        "path": str(self._medical_lexicon_path),
                        "phrases": len(self._medical_phrases),
                    },
                )

            self._models_loaded = True

            logger.info("nemo_pipeline.models_loaded", extra={"device": str(device)})
        except Exception as e:
            self._load_error = str(e)
            logger.exception(
                "nemo_pipeline.models_failed %s: %s",
                type(e).__name__,
                str(e)[:300],
                extra={
                    "provider": self._model_provider,
                    "error_type": type(e).__name__,
                    "error": self._load_error,
                },
            )

    @property
    def is_loaded(self) -> bool:
        """Whether recordings can currently produce live transcript segments.

        Returns:
            True when NeMo models loaded; false means the browser sees degraded health.
        """
        return self._models_loaded

    @property
    def load_error(self) -> str | None:
        """Model-load failure shown by health checks when transcription is degraded.

        Returns:
            Error text, or `None` when users can record normally.
        """
        return self._load_error

    def create_streaming_engine(self, session_id: str):
        """Build one session-long streaming engine over the shared models (M22).

        The first call configures the shared Sortformer singleton for
        streaming. That mutation is safe because NEMO_SESSION_ENGINE is
        process-level: windowed sessions never run in a streaming-flagged
        process, so no windowed call observes the streaming configuration.

        Args:
            session_id: Recording UUID the engine belongs to.

        Returns:
            A `StreamingSessionEngine` holding this session's streaming state.

        Raises:
            RuntimeError: When models are not loaded (mock provider or failed
                startup) - callers must fall back to degraded health behavior.
        """
        if not self._models_loaded:
            raise RuntimeError(
                "streaming engine requires loaded NeMo models "
                f"(provider={self._model_provider})"
            )

        from nemo_streaming_engine import StreamingSessionEngine

        if not getattr(self, "_diar_streaming_configured", False):
            # Mirror the reference CLI's diar streaming setup once per process.
            self._diar_model.streaming_mode = True
            sortformer_modules = self._diar_model.sortformer_modules
            sortformer_modules.spkcache_len = 188
            sortformer_modules.fifo_len = 188
            sortformer_modules.chunk_len = 0
            sortformer_modules.chunk_left_context = 0
            sortformer_modules.chunk_right_context = 0
            sortformer_modules.spkcache_refresh_rate = 0
            sortformer_modules.log = False
            self._diar_streaming_configured = True
            logger.info("nemo_pipeline.diar_streaming_configured")

        return StreamingSessionEngine(
            session_id=session_id,
            asr_model=self._asr_model,
            diar_model=self._diar_model,
        )

    def transcribe_file(self, audio_path: str) -> TranscriptionResult:
        """Process a complete audio file. Returns speaker-attributed segments.

        Runs independent diarization (Sortformer) + ASR (multitalker Parakeet),
        then aligns words to speaker segments proportionally.

        Args:
            audio_path: Path to a WAV file (16kHz mono PCM expected)

        Returns:
            TranscriptionResult with speaker-attributed segments.
        """
        logger.info(
            "nemo_pipeline.transcribe_file.started",
            extra={
                "audio_path": audio_path,
            },
        )

        if not self._models_loaded:
            return TranscriptionResult(segments=[], raw_output={})

        import torch

        with torch.inference_mode():
            diar_output = self._diar_model.diarize(
                audio=audio_path,
                batch_size=1,
                verbose=False,
            )
            asr_hyps = self._asr_model.transcribe(
                [audio_path],
                return_hypotheses=True,
                verbose=False,
            )

        parsed = self._parse_nemo_output(diar_output, asr_hyps)

        logger.info(
            "nemo_pipeline.transcribe_file.completed",
            extra={
                "audio_path": audio_path,
                "segments": len(parsed),
            },
        )

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
        logger.info(
            "nemo_pipeline.transcribe_buffer.started",
            extra={
                "buffer_bytes": len(audio_buffer),
            },
        )

        if not self._models_loaded:
            return TranscriptionResult(segments=[], raw_output={})

        pcm_array = (
            np.frombuffer(audio_buffer, dtype=np.int16).astype(np.float32) / 32768.0
        )

        scratch_audio_path = None
        try:
            with tempfile.NamedTemporaryFile(
                suffix=".wav", delete=False
            ) as scratch_audio_file:
                scratch_audio_path = scratch_audio_file.name
                soundfile.write(scratch_audio_path, pcm_array, 16000)
            return self.transcribe_file(scratch_audio_path)
        finally:
            # The scratch WAV exists only so NeMo can process the user's current chunk.
            if scratch_audio_path:
                Path(scratch_audio_path).unlink(missing_ok=True)

    def _parse_nemo_output(
        self,
        diar_segments: Any,
        asr_hyps: Any,
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

        asr_text = self._visible_asr_text(asr_hyps)
        if not asr_text.strip():
            return [
                Segment(speaker_id=spk, text="", start=start, end=end)
                for start, end, spk in parsed_diar
            ]

        words = asr_text.split()
        if not words:
            return []

        total_duration = sum(end - start for start, end, _ in parsed_diar)
        if total_duration <= 0:
            return []

        return self._segments_from_words(
            parsed_diar,
            words,
            total_duration,
            self._raw_word_confidences(asr_hyps),
        )

    @staticmethod
    def _raw_word_confidences(asr_hyps: Any) -> list[float] | None:
        """Return per-word confidence for the window's raw ASR words.

        Confidence aligns with the words NeMo actually decoded, BEFORE medical
        correction may change the visible word count; rows later map their
        visible word share onto this raw list.

        Args:
            asr_hyps: Raw NeMo hypotheses; empty means no words were decoded.

        Returns:
            Raw-word confidence list, or None when this decode carried none and
            the window's rows should render without confidence.
        """
        # No hypotheses means there are no decoded words to describe.
        if not asr_hyps:
            return None

        first_hypothesis = asr_hyps[0]
        raw_words = str(getattr(first_hypothesis, "text", "") or "").split()
        return word_confidences_for_display_words(first_hypothesis, raw_words)

    def _visible_asr_text(self, asr_hyps: Any) -> str:
        """Return ASR text after optional medical correction for the UI.

        Args:
            asr_hyps: Raw NeMo hypotheses; empty means no transcript text reached ASR.

        Returns:
            Visible transcript text; blank means the UI shows timed empty segments only.
        """
        asr_text = ""
        # Empty hypotheses mean diarization found timing but ASR produced no words.
        if asr_hyps:
            first_hypothesis = asr_hyps[0]
            asr_text = (
                first_hypothesis.text
                if hasattr(first_hypothesis, "text")
                else str(first_hypothesis)
            )

        return self.visible_text(asr_text)

    def visible_text(self, asr_text: str) -> str:
        """Normalize clinical terms in ASR text before it becomes user-visible.

        Both emission paths (windowed and streaming engine) must route text
        through this seam so transcript, summary, and download see the same
        medical-boost corrections.

        Args:
            asr_text: Raw ASR text; empty passes through unchanged.

        Returns:
            Text with known clinical terms normalized when the boost is enabled.
        """
        # Enabled correction normalizes known clinical terms before they reach the UI.
        if self._medical_boost_enabled and self._medical_phrases:
            return correct_medical_terms(asr_text, self._medical_phrases)

        return asr_text

    def _segments_from_words(
        self,
        parsed_diar: list[tuple[float, float, str]],
        words: list[str],
        total_duration: float,
        word_confidences: list[float] | None = None,
    ) -> list[Segment]:
        """Distribute ASR words across diarized speakers for transcript cards.

        Args:
            parsed_diar: Speaker timing rows; empty would produce no transcript cards.
            words: ASR tokens; empty means the caller should avoid this helper.
            total_duration: Sum of diarized speech duration; zero would make shares invalid.
            word_confidences: Raw-word confidence values; None leaves every row unmeasured.

        Returns:
            Transcript segments shown by the browser; empty means no text survived splitting.
        """
        segments: list[Segment] = []
        word_cursor = 0
        # Each diarized span receives a proportional share of the ASR word stream.
        for diarization_index, (start, end, speaker_id) in enumerate(parsed_diar):
            segment_duration = end - start
            duration_share = segment_duration / total_duration
            row_first_word_index = word_cursor

            # The final visible card receives any leftover words after rounding.
            if diarization_index == len(parsed_diar) - 1:
                segment_words = words[word_cursor:]
                word_cursor = len(words)
            else:
                word_count = max(1, round(len(words) * duration_share))
                segment_words = words[word_cursor : word_cursor + word_count]
                word_cursor += len(segment_words)

            visible_text = " ".join(segment_words)
            # Empty proportional splits should not create blank transcript cards.
            if visible_text:
                segments.append(
                    Segment(
                        speaker_id=speaker_id,
                        text=visible_text,
                        start=start,
                        end=end,
                        confidence=row_confidence_for_word_share(
                            word_confidences,
                            row_first_word_index,
                            word_cursor,
                            len(words),
                        ),
                    )
                )

        return segments

    @staticmethod
    def _filter_hallucinated_speakers(
        parsed_diar: list[tuple[float, float, str]],
        min_share: float = 0.05,
        min_absolute_duration: float = 1.0,
        min_segment_count: int = 2,
    ) -> list[tuple[float, float, str]]:
        """Hide brief one-off speaker IDs before the browser sees them.

        Use after NeMo diarization when a tiny speaker blip would look like a
        third person in a two-person consultation. Duration and repeat floors
        keep real short turns visible for the user.

        Args:
            parsed_diar: Diarized speaker spans; empty means the transcript view
                has no speaker rows to clean up.
            min_share: Smallest session share to keep; zero keeps percentage from
                hiding any detected speaker.
            min_absolute_duration: Cumulative seconds that always keep a speaker;
                zero means only share/repeat evidence protects short turns.
            min_segment_count: Repeat count that always keeps a speaker; zero
                means a one-off blip can still be kept.

        Returns:
            Filtered speaker spans; empty means no visible speaker rows remain.
        """
        # No speaker rows reached this point, so the UI has nothing to label yet.
        if not parsed_diar:
            return parsed_diar

        total_duration = sum(end - start for start, end, _ in parsed_diar)
        # Broken or zero-length timing cannot safely remove anything the user might need.
        if total_duration <= 0:
            return parsed_diar

        speaker_durations, speaker_segment_counts = (
            NemoPipeline._speaker_activity_by_id(parsed_diar)
        )
        suppressed = NemoPipeline._suppressed_speaker_ids(
            speaker_durations,
            speaker_segment_counts,
            total_duration,
            min_share,
            min_absolute_duration,
            min_segment_count,
        )

        # Hidden speaker blips are logged so support can explain why no extra role appeared.
        if suppressed:
            NemoPipeline._log_suppressed_speakers(
                suppressed,
                speaker_durations,
                speaker_segment_counts,
                total_duration,
                min_share,
                min_absolute_duration,
                min_segment_count,
            )

        return [
            (start, end, speaker_id)
            for start, end, speaker_id in parsed_diar
            if speaker_id not in suppressed
        ]

    @staticmethod
    def _speaker_activity_by_id(
        parsed_diar: list[tuple[float, float, str]],
    ) -> tuple[dict[str, float], dict[str, int]]:
        """Count each speaker's visible time and repeats.

        Use while preparing transcript rows so a brief real turn is not hidden
        just because the whole consultation is long.

        Args:
            parsed_diar: Diarized spans; empty means both returned maps are empty.

        Returns:
            Duration and count maps keyed by speaker ID; empty maps mean no speaker evidence.
        """
        speaker_durations: dict[str, float] = {}
        speaker_segment_counts: dict[str, int] = {}
        # Each diarization row contributes evidence for whether a speaker should stay visible.
        for start, end, speaker_id in parsed_diar:
            speaker_durations[speaker_id] = speaker_durations.get(speaker_id, 0.0) + (
                end - start
            )
            speaker_segment_counts[speaker_id] = (
                speaker_segment_counts.get(speaker_id, 0) + 1
            )

        return speaker_durations, speaker_segment_counts

    @staticmethod
    def _suppressed_speaker_ids(
        speaker_durations: dict[str, float],
        speaker_segment_counts: dict[str, int],
        total_duration: float,
        min_share: float,
        min_absolute_duration: float,
        min_segment_count: int,
    ) -> set[str]:
        """Choose speaker IDs that would look like phantom participants.

        Use after activity counts exist, before transcript cards are built, so
        the browser does not show a third role for a tiny one-window blip.

        Args:
            speaker_durations: Seconds per speaker; empty means no one can be hidden.
            speaker_segment_counts: Segment count per speaker; missing speakers are treated as one-off blips.
            total_duration: Session seconds used for share checks; zero means no speaker is suppressed.
            min_share: Smallest session share to keep; zero keeps percentage from hiding any speaker.
            min_absolute_duration: Seconds that keep a speaker even with low share.
            min_segment_count: Repeated appearances that keep a speaker visible.

        Returns:
            Speaker IDs to hide; empty means all detected speakers stay visible.
        """
        # Without a valid session duration, the UI keeps all speaker evidence.
        if total_duration <= 0:
            return set()

        return {
            speaker_id
            # One activity row per speaker decides whether it looks like a phantom participant.
            for speaker_id, speaker_duration in speaker_durations.items()
            if (
                speaker_duration / total_duration < min_share
                and speaker_duration <= min_absolute_duration
                and speaker_segment_counts.get(speaker_id, 0) < min_segment_count
            )
        }

    @staticmethod
    def _log_suppressed_speakers(
        suppressed_speaker_ids: set[str],
        speaker_durations: dict[str, float],
        speaker_segment_counts: dict[str, int],
        total_duration: float,
        min_share: float,
        min_absolute_duration: float,
        min_segment_count: int,
    ) -> None:
        """Log hidden speaker blips for support and quality review.

        Use when NeMo emitted a tiny participant that the UI will not show, so
        a transcript-quality run can explain why roles stayed limited.

        Args:
            suppressed_speaker_ids: Speaker IDs hidden from the transcript; empty means nothing is logged.
            speaker_durations: Seconds per speaker; missing IDs log as zero seconds.
            speaker_segment_counts: Segment count per speaker; missing IDs log as zero repeats.
            total_duration: Session seconds for share reporting; zero logs a zero share.
            min_share: Share threshold that contributed to hiding the speaker.
            min_absolute_duration: Duration threshold that would have kept the speaker.
            min_segment_count: Repeat threshold that would have kept the speaker.
        """
        # Each hidden speaker gets a log row so a quality report can trace the missing role.
        for speaker_id in suppressed_speaker_ids:
            speaker_duration = speaker_durations.get(speaker_id, 0.0)
            speaker_count = speaker_segment_counts.get(speaker_id, 0)
            # Zero duration is possible only for malformed input, so report zero share safely.
            speaker_share = speaker_duration / total_duration if total_duration > 0 else 0.0
            logger.warning(
                (
                    "nemo_pipeline.hallucinated_speaker_suppressed "
                    "speaker_id=%s duration=%.2f share=%.4f"
                ),
                speaker_id,
                speaker_duration,
                speaker_share,
                extra={
                    "speaker_id": speaker_id,
                    "duration": speaker_duration,
                    "total_duration": total_duration,
                    "share": speaker_share,
                    "segments": speaker_count,
                    "min_share": min_share,
                    "min_absolute_duration": min_absolute_duration,
                    "min_segment_count": min_segment_count,
                },
            )

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
            diarization_line = str(item).strip()
            if diarization_line:
                raw_strings.append(diarization_line)

        parsed: list[tuple[float, float, str]] = []
        for diarization_line in raw_strings:
            match = re.match(r"([\d.]+)\s+([\d.]+)\s+(\S+)", diarization_line)
            if match:
                start = float(match.group(1))
                end = float(match.group(2))
                speaker = match.group(3)
                parsed.append((start, end, speaker))

        parsed.sort(key=lambda x: x[0])
        return parsed


def _is_env_flag_enabled(name: str, default: bool = False) -> bool:
    """Read a boolean env toggle used by the user-facing transcript pipeline.

    Args:
        name: Environment variable name; unset or blank falls back to `default`.
        default: Value an unconfigured deployment gets for this toggle.

    Returns:
        True for common enabled values; false leaves the transcript path unchanged.
    """
    raw_value = os.environ.get(name)
    # An unconfigured deployment gets the toggle's shipped default.
    if raw_value is None or raw_value.strip() == "":
        return default

    return raw_value.strip().lower() in {"1", "true", "yes", "on"}
