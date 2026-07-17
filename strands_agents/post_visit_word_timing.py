"""
Second-pass word-timing extraction for post-visit correction.

After the user stops a visit, the correction pass re-runs ASR over retained
audio. This module turns that pass's NeMo hypothesis into per-word timing rows
and validates them against the display words, so the corrected lane can split
echo-boundary rows without changing storage or browser contracts. Timing here
is best-effort evidence: when anything disagrees, the answer is None and the
corrected note renders exactly as it did before word timing existed.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import soundfile

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class PostVisitTranscription:
    """
    Second-pass ASR output for one stopped visit, with optional word timing.

    Timing-aware transcribers return this so the corrected lane can split
    echo-boundary rows; plain-text test seams may still return a bare string
    and the correction simply skips timing-based splits.

    Attributes:
        text: Plain transcript text; empty means the correction pass falls back to live rows.
        word_timings: Per-word `{"word","start","end"}` rows aligned to the split display
            words; None means no trustworthy timing exists and no row is timing-split.
        word_confidences: Per-word confidence aligned to the split display words; None
            means corrected rows carry no confidence and render unstyled.
    """

    text: str
    word_timings: list[dict[str, Any]] | None = None
    word_confidences: list[float] | None = None


def validated_word_timings(
    word_timings: list[dict[str, Any]] | None,
    words: list[str],
) -> list[dict[str, Any]] | None:
    """Keep word timings only when they describe exactly the corrected display words.

    Args:
        word_timings: Timing rows from the transcriber; None means timing was unavailable.
        words: Split display words the corrected rows will show.

    Returns:
        The validated timing rows, or None when any drift makes the timings untrustworthy.
    """
    # Absent timing evidence simply disables timing-based splits.
    if not word_timings:
        return None

    # Timing rows must pair one-to-one with the words the user will see cited.
    if len(word_timings) != len(words):
        logger.warning(
            "post_visit_correction.word_timings_mismatch",
            extra={"timing_rows": len(word_timings), "words": len(words)},
        )
        return None

    # Any word drift means the timing describes a different token stream.
    for timing, word in zip(word_timings, words, strict=True):
        if not isinstance(timing, dict) or str(timing.get("word", "")) != word:
            logger.warning(
                "post_visit_correction.word_timings_mismatch",
                extra={"timing_rows": len(word_timings), "words": len(words)},
            )
            return None

    return word_timings


def validated_word_confidences(
    word_confidences: list[float] | None,
    words: list[str],
) -> list[float] | None:
    """Keep word confidences only when they pair one-to-one with display words.

    Args:
        word_confidences: Confidence values from the transcriber; None means the
            correction ran without confidence.
        words: Split display words the corrected rows will show.

    Returns:
        The validated values, or None when a count mismatch makes them
        untrustworthy and corrected rows should render unstyled.
    """
    # Absent confidence simply leaves corrected rows unmeasured.
    if not word_confidences:
        return None

    # Values must pair one-to-one with visible words or rows would style the
    # wrong lines.
    if len(word_confidences) != len(words):
        logger.warning(
            "post_visit_correction.word_confidences_mismatch",
            extra={"confidence_rows": len(word_confidences), "words": len(words)},
        )
        return None

    return word_confidences


def word_timings_from_hypothesis(
    hypothesis: Any,
    audio_duration_seconds: float,
) -> list[dict[str, Any]]:
    """Return the best available per-word timings for one second-pass hypothesis.

    NeMo's native word timestamps (from `timestamps=True`) are used when present -
    the 2026-07-07 consult-08 probes showed them within ~40ms of TextGrid truth and
    stable across clip lengths, where the proportional estimate drifted ~1.6s at 60s.
    Older tensor-shaped hypotheses fall back to the proportional estimate.

    Args:
        hypothesis: NeMo ASR hypothesis; None means no timing evidence exists.
        audio_duration_seconds: Retained-audio duration for the proportional fallback.

    Returns:
        `{"word","start","end"}` rows in spoken order; empty means timing is unavailable.
    """
    native_timings = native_word_timings_from_hypothesis(hypothesis)
    # Real model-reported word times beat any estimate whenever they exist.
    if native_timings:
        return native_timings

    return estimate_post_visit_word_timings(hypothesis, audio_duration_seconds)


def native_word_timings_from_hypothesis(hypothesis: Any) -> list[dict[str, Any]]:
    """Extract NeMo's own word-level timestamps when the model reported them.

    Args:
        hypothesis: NeMo ASR hypothesis; a tensor-shaped `timestamp` field means the
            transcribe call did not request native timestamps.

    Returns:
        `{"word","start","end"}` rows in spoken order; empty means no complete native
        timing exists and the caller should fall back to the estimate.
    """
    timestamp_field = getattr(hypothesis, "timestamp", None)
    # Without timestamps=True the field is a token tensor, not a word-entry mapping.
    if not isinstance(timestamp_field, dict):
        return []

    word_entries = timestamp_field.get("word", [])
    # A non-list word field means this NeMo version reports timing another way.
    if not isinstance(word_entries, list) or word_entries == []:
        return []

    native_timings: list[dict[str, Any]] = []
    # One incomplete entry poisons the whole list - zero-time words would fake boundaries.
    for entry in word_entries:
        # Entries must carry word text plus numeric start/end to be trustworthy.
        if not isinstance(entry, dict):
            return []

        try:
            native_timings.append(
                {
                    "word": str(entry["word"]),
                    "start": float(entry["start"]),
                    "end": float(entry["end"]),
                }
            )
        except (KeyError, TypeError, ValueError):
            return []

    return native_timings


def estimate_post_visit_word_timings(
    hypothesis: Any,
    audio_duration_seconds: float,
) -> list[dict[str, Any]]:
    """Estimate per-word timings from NeMo token timestamps.

    Uses the probe-proven token-timestamps-proportional-to-words strategy:
    tokens outnumber words, so token frames are distributed across words in
    order and converted to seconds with the clip duration.

    Args:
        hypothesis: NeMo ASR hypothesis; None means no timing evidence exists.
        audio_duration_seconds: Retained-audio duration; zero disables conversion to seconds.

    Returns:
        `{"word","start","end"}` rows in spoken order; empty means timing is unavailable.
    """
    # Without a hypothesis there are no words to time.
    if hypothesis is None or audio_duration_seconds <= 0:
        return []

    timestamp_field = getattr(hypothesis, "timestamp", None)
    # Native word/segment mappings belong to the native extractor, not this estimator.
    if isinstance(timestamp_field, dict):
        return []

    words = [str(word) for word in _hypothesis_list(getattr(hypothesis, "words", None))]
    token_timestamps = [
        float(timestamp)
        for timestamp in _hypothesis_list(timestamp_field)
    ]
    frame_count = _hypothesis_scalar(getattr(hypothesis, "length", None))

    # All three fields are needed before any corrected row may claim word timing.
    if words == [] or token_timestamps == [] or frame_count is None or frame_count <= 0:
        return []

    seconds_per_frame = audio_duration_seconds / frame_count
    word_timings: list[dict[str, Any]] = []
    # Tokens outnumber words, so distribute token timestamps across words proportionally.
    for word_index, word in enumerate(words):
        token_start_index = int(word_index * len(token_timestamps) / len(words))
        token_end_index = int((word_index + 1) * len(token_timestamps) / len(words))
        token_end_index = min(
            max(token_end_index, token_start_index + 1),
            len(token_timestamps),
        )
        token_start_frame = token_timestamps[token_start_index]
        token_end_frame = token_timestamps[token_end_index - 1] + 1.0
        word_timings.append(
            {
                "word": word,
                "start": round(token_start_frame * seconds_per_frame, 3),
                "end": round(max(token_end_frame, token_start_frame + 1.0) * seconds_per_frame, 3),
            }
        )

    return word_timings


def wav_duration_seconds(audio_path: str) -> float:
    """Return one WAV clip's duration for frame-to-seconds conversion.

    Args:
        audio_path: WAV path the correction pass transcribed.

    Returns:
        Duration in seconds; `0.0` means the clip cannot provide timing.
    """
    audio_info = soundfile.info(audio_path)
    # A zero sample rate means the file cannot describe user-visible timing.
    if audio_info.samplerate == 0:
        return 0.0

    return audio_info.frames / audio_info.samplerate


def _hypothesis_list(value: Any) -> list[Any]:
    """Convert tensor or list hypothesis fields into plain lists.

    Args:
        value: NeMo hypothesis field; None means the model omitted the field.

    Returns:
        Plain list; empty means no iterable timing or word data exists.
    """
    # Missing fields cannot drive a word-level corrected transcript.
    if value is None:
        return []

    # Torch tensors need to move off GPU before plain-list conversion.
    if hasattr(value, "detach"):
        value = value.detach().cpu()

    # Tensor-like objects expose tolist(), which preserves numeric order.
    if hasattr(value, "tolist"):
        return value.tolist()

    # Already-list fields such as hypothesis.words can be used directly.
    if isinstance(value, list):
        return value

    try:
        return list(value)
    except TypeError:
        return []


def _hypothesis_scalar(value: Any) -> float | None:
    """Convert scalar tensor or number hypothesis fields into a float.

    Args:
        value: NeMo scalar field; None means the timing denominator is unavailable.

    Returns:
        Float value, or None when the field is missing or not numeric.
    """
    # Missing scalar data means timestamp frames cannot be converted to seconds.
    if value is None:
        return None

    # Tensor scalars expose item(); detach first in case the value lives on GPU.
    if hasattr(value, "detach"):
        value = value.detach().cpu()

    # A tensor scalar's item() carries the actual numeric denominator.
    if hasattr(value, "item"):
        value = value.item()

    try:
        return float(value)
    except (TypeError, ValueError):
        return None
