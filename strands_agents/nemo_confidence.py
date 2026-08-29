"""
Word-confidence extraction shared by every transcript lane.

The transcript UI styles low-confidence rows, so live streaming rows, windowed rows, and post-visit corrected rows all
need one consistent per-row confidence value however they were produced.

This module owns two things: the probe-proven NeMo decoding settings, and the small joins that turn per-word confidence
into a single number per visible row. Absent confidence always stays absent, so a row without trustworthy values renders
exactly as an unstyled row rather than being guessed at.
"""

from __future__ import annotations

from typing import Any

# Probe-proven settings: word and token preservation with max_prob scoring and min aggregation.
#
# These gave non-degenerate values on both runtime models with zero CUDA instability, offline and streaming alike.
CONFIDENCE_DECODING_SETTINGS: dict[str, Any] = {
    "preserve_frame_confidence": False,
    "preserve_token_confidence": True,
    "preserve_word_confidence": True,
    "exclude_blank": True,
    "aggregation": "min",
    "method_cfg": {"name": "max_prob"},
}

# Stored and published confidence precision; more digits add payload noise without changing how a row would be styled.
_CONFIDENCE_DECIMALS = 4


def enable_word_confidence_decoding(asr_model: Any) -> None:
    """Turn on word and token confidence in one ASR model's decoding config.

    Use at model load for the live pipeline, or per correction pass for the post-visit model, so every hypothesis carries
    the per-word confidence the transcript rows are styled from.

    Callers that patch decoder internals, such as the multitalker CUDA-graph workaround, must re-apply those patches
    AFTER this call, because changing the strategy rebuilds the decoder and discards them.

    Args:
        asr_model: Loaded NeMo ASR model about to transcribe user audio.
    """
    from omegaconf import OmegaConf, open_dict

    decoding_config = OmegaConf.create(
        OmegaConf.to_container(asr_model.cfg.decoding, resolve=True)
    )
    with open_dict(decoding_config):
        # NeMo reads exactly this key; a different name is silently ignored, so every row would render unstyled.
        decoding_config.confidence_cfg = CONFIDENCE_DECODING_SETTINGS
    asr_model.change_decoding_strategy(decoding_config)


def disable_word_confidence_decoding(asr_model: Any) -> None:
    """Disable only word aggregation while preserving token confidence.

    Use after NeMo's word-confidence aggregation mismatch has already aborted one stopped-visit decode. The same loaded
    model can then make a single timestamped recovery call without changing its token confidence, phrase, beam, or other
    decoder settings, so the clinician still gets a reviewed transcript instead of falling back to the live one.

    Args:
        asr_model: Loaded post-visit NeMo model whose current decoder preserves word confidence.

    Raises:
        ValueError: When the model is not in the expected word-confidence-on state, so there is nothing to recover from.
        RuntimeError: When NeMo applies any decoder shape other than the requested one-field change.
    """
    from omegaconf import OmegaConf, open_dict

    current_config = OmegaConf.to_container(
        asr_model.cfg.decoding,
        resolve=True,
    )
    # A non-mapping config cannot be inspected field by field, so the recovery is refused rather than attempted blind.
    if not isinstance(current_config, dict):
        raise ValueError("NeMo decoding config is not a mapping.")

    confidence_config = current_config.get("confidence_cfg")
    # Word confidence being off already means this is not the failure this recovery exists for, so nothing is changed.
    if (
        not isinstance(confidence_config, dict)
        or confidence_config.get("preserve_word_confidence") is not True
    ):
        raise ValueError("NeMo word confidence is not enabled.")

    decoding_config = OmegaConf.create(current_config)
    with open_dict(decoding_config.confidence_cfg):
        decoding_config.confidence_cfg.preserve_word_confidence = False

    requested_config = OmegaConf.to_container(decoding_config, resolve=True)
    asr_model.change_decoding_strategy(decoding_config)
    applied_config = OmegaConf.to_container(
        asr_model.cfg.decoding,
        resolve=True,
    )
    # The recovery is approved only when NeMo applies exactly the requested one-field change.
    # Any implicit decoder drift would change the wording itself, so it keeps the live-row fallback instead.
    if applied_config != requested_config:
        raise RuntimeError(
            "NeMo changed decoder settings outside the recovery request."
        )


def word_confidences_for_display_words(
    hypothesis: Any,
    display_words: list[str],
) -> list[float] | None:
    """Return one confidence per display word, or None when the join is unsafe.

    Use right after decoding, while the hypothesis text and the words shown to the clinician still tokenize identically.
    Persisting misaligned values would style the wrong rows, which is worse than styling none.

    Args:
        hypothesis: NeMo hypothesis; a missing `word_confidence` field means this decode ran without confidence and rows stay unstyled.
        display_words: Whitespace-split words the user's rows are built from; empty means there is no visible text to describe.

    Returns:
        Float list aligned index-by-index with `display_words`, or None when confidence is absent or does not match the
        words one-to-one. A null leaves every row in this decode unstyled rather than styled from a different token stream.
    """
    values = _plain_float_list(getattr(hypothesis, "word_confidence", None))
    # A count mismatch means the values describe a different token stream, so no row may claim them.
    if values == [] or len(values) != len(display_words):
        return None

    return values


def transcript_row_confidence(
    word_confidences: Any,
) -> float | None:
    """Collapse one row's word confidences into the row's displayed confidence.

    Use when a transcript row is assembled from words, whether by streaming emission, fragment merges, or corrected-row spans.
    The minimum is kept, so one weakly heard word still marks the whole row as worth the clinician's attention.

    Args:
        word_confidences: Iterable of per-word values; None entries mean those words carry no evidence and are skipped.

    Returns:
        Rounded minimum confidence, or None when no word in the row has a value and the row should render without any styling.
    """
    known_values = [value for value in word_confidences if value is not None]
    # A row with no measured words keeps its unstyled rendering rather than claiming a confidence it never had.
    if known_values == []:
        return None

    return round(min(known_values), _CONFIDENCE_DECIMALS)


def row_confidence_for_word_share(
    word_confidences: list[float] | None,
    share_start_index: int,
    share_end_index: int,
    total_display_words: int,
) -> float | None:
    """Return confidence for a row holding a proportional share of the words.

    Use on the windowed live path, where medical-term correction can change the visible word count. The row's fractional
    span of the visible words is mapped back onto the raw confidence list, so each row still reflects its own stretch of audio.

    Args:
        word_confidences: Per-word values for the raw ASR words; None means confidence was unavailable for this window.
        share_start_index: First visible word index allocated to the row.
        share_end_index: One-past-last visible word index for the row; equal to the start means the row shows no words.
        total_display_words: Visible word total; zero or lower cannot map shares.

    Returns:
        Rounded minimum confidence over the row's mapped span, or None when no trustworthy value exists for that stretch.
    """
    # Without confidence values or visible words there is nothing to map, so the row renders unstyled.
    if not word_confidences or total_display_words <= 0:
        return None

    # An empty allocation means the row shows no words to style.
    if share_end_index <= share_start_index:
        return None

    scale = len(word_confidences) / total_display_words
    span_start = int(share_start_index * scale)
    span_end = max(span_start + 1, int(share_end_index * scale))
    return transcript_row_confidence(
        word_confidences[span_start : min(span_end, len(word_confidences))]
    )


def _plain_float_list(value: Any) -> list[float]:
    """Convert a tensor or list confidence field into plain floats.

    Every lane downstream expects one shape, so this is where GPU tensors and Python lists stop being different things.

    Args:
        value: NeMo hypothesis field; None means the model omitted confidence.

    Returns:
        Float list; empty means no usable confidence values exist, so the caller leaves the rows unstyled.
    """
    # A missing field is the no-confidence decode state, which is a normal outcome rather than an error.
    if value is None:
        return []

    # Torch tensors have to move off the GPU before they can become plain numbers.
    if hasattr(value, "detach"):
        value = value.detach().cpu()
    # Tensor-like values become plain lists so every lane downstream handles exactly one shape.
    if hasattr(value, "tolist"):
        value = value.tolist()

    try:
        return [float(item) for item in value]
    # Example: a model build returns confidence as nested objects rather than numbers, so the join cannot be trusted.
    # Values that cannot become numbers leave the row honestly unmeasured instead of styled from a guess.
    except (TypeError, ValueError):
        return []
