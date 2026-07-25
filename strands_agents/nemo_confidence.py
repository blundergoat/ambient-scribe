"""
Word-confidence extraction shared by every transcript lane.

The transcript UI will style low-confidence rows (summary UX M7), so live
streaming rows, windowed rows, and post-visit corrected rows all need one
consistent per-row confidence value. This module owns the probe-proven NeMo
decoding settings and the small joins that turn per-word confidence into one
number per visible transcript row. Absent confidence always stays absent:
rows without trustworthy values render exactly as they do today.
"""

from __future__ import annotations

from typing import Any

# Probe-proven settings (0.4.0 M06): word+token preservation with max_prob
# scoring and min aggregation gave non-degenerate values on both runtime
# models with zero CUDA instability, offline AND streaming.
CONFIDENCE_DECODING_SETTINGS: dict[str, Any] = {
    "preserve_frame_confidence": False,
    "preserve_token_confidence": True,
    "preserve_word_confidence": True,
    "exclude_blank": True,
    "aggregation": "min",
    "method_cfg": {"name": "max_prob"},
}

# Stored/published confidence precision; more digits add payload noise without
# changing how a row would be styled.
_CONFIDENCE_DECIMALS = 4


def enable_word_confidence_decoding(asr_model: Any) -> None:
    """Turn on word/token confidence in one ASR model's decoding config.

    Use at model load (live pipeline) or per correction pass (post-visit model)
    so every hypothesis carries per-word confidence for transcript rows.
    Callers that patch decoder internals (the multitalker CUDA-graph
    workaround) must re-apply those patches AFTER this call, because the
    strategy change rebuilds the decoder.

    Args:
        asr_model: Loaded NeMo ASR model about to transcribe user audio.
    """
    from omegaconf import OmegaConf, open_dict

    decoding_config = OmegaConf.create(
        OmegaConf.to_container(asr_model.cfg.decoding, resolve=True)
    )
    with open_dict(decoding_config):
        # NeMo reads exactly this key; a different name is silently ignored.
        decoding_config.confidence_cfg = CONFIDENCE_DECODING_SETTINGS
    asr_model.change_decoding_strategy(decoding_config)


def disable_word_confidence_decoding(asr_model: Any) -> None:
    """Disable only word aggregation while preserving token confidence.

    Use after the exact NeMo word-confidence aggregation mismatch has already
    aborted one stopped-visit decode. The same loaded model can then make one
    timestamped recovery call without changing its token confidence, phrase,
    beam, or other decoder settings.

    Args:
        asr_model: Loaded post-visit NeMo model whose current decoder preserves
            word confidence.

    Raises:
        ValueError: When the model is not in the expected word-confidence-on state.
        RuntimeError: When NeMo applies any decoder shape other than the requested
            one-field change.
    """
    from omegaconf import OmegaConf, open_dict

    current_config = OmegaConf.to_container(
        asr_model.cfg.decoding,
        resolve=True,
    )
    if not isinstance(current_config, dict):
        raise ValueError("NeMo decoding config is not a mapping.")

    confidence_config = current_config.get("confidence_cfg")
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
    # The recovery is approved only when NeMo applies exactly the requested
    # one-field change; any implicit decoder drift keeps the live-row fallback.
    if applied_config != requested_config:
        raise RuntimeError(
            "NeMo changed decoder settings outside the recovery request."
        )


def word_confidences_for_display_words(
    hypothesis: Any,
    display_words: list[str],
) -> list[float] | None:
    """Return one confidence per display word, or None when the join is unsafe.

    Use right after decoding, while the hypothesis text and the words shown to
    the user still tokenize identically; persisting misaligned values would
    style the wrong transcript rows.

    Args:
        hypothesis: NeMo hypothesis; a missing `word_confidence` field means
            this decode ran without confidence and rows stay unstyled.
        display_words: Whitespace-split words the user's rows are built from;
            empty means there is no visible text to describe.

    Returns:
        Float list aligned index-by-index with `display_words`, or None when
        confidence is absent or does not match the words one-to-one.
    """
    values = _plain_float_list(getattr(hypothesis, "word_confidence", None))
    # A count mismatch means the values describe a different token stream, so
    # no row may claim them.
    if values == [] or len(values) != len(display_words):
        return None

    return values


def transcript_row_confidence(
    word_confidences: Any,
) -> float | None:
    """Collapse one row's word confidences into the row's displayed confidence.

    Use when a transcript row is assembled from words (streaming emission,
    fragment merges, corrected-row spans). The minimum is kept so one weakly
    heard word still marks the whole row as worth the clinician's attention.

    Args:
        word_confidences: Iterable of per-word values; None entries mean those
            words carry no evidence and are skipped.

    Returns:
        Rounded minimum confidence, or None when no word in the row has a value
        and the row should render without any confidence styling.
    """
    known_values = [value for value in word_confidences if value is not None]
    # A row with no measured words keeps today's unstyled rendering.
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

    Use on the windowed live path, where medical-term correction can change the
    visible word count: the row's fractional span of the visible words is
    mapped onto the raw confidence list so each row still reflects its own
    stretch of audio.

    Args:
        word_confidences: Per-word values for the raw ASR words; None means
            confidence was unavailable for this window.
        share_start_index: First visible word index allocated to the row.
        share_end_index: One-past-last visible word index for the row; equal to
            the start means the row shows no words.
        total_display_words: Visible word total; zero or lower cannot map shares.

    Returns:
        Rounded minimum confidence over the row's mapped span, or None when no
        trustworthy value exists for that stretch.
    """
    # Without confidence values or visible words there is nothing to map.
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
    """Convert a tensor/list confidence field into plain floats.

    Args:
        value: NeMo hypothesis field; None means the model omitted confidence.

    Returns:
        Float list; empty means no usable confidence values exist.
    """
    # A missing field is the no-confidence decode state.
    if value is None:
        return []

    # Torch tensors need to move off GPU before plain-number conversion.
    if hasattr(value, "detach"):
        value = value.detach().cpu()
    # Tensor-like values become plain lists so every lane handles one shape.
    if hasattr(value, "tolist"):
        value = value.tolist()

    # Values that cannot become numbers leave the row honestly unmeasured.
    try:
        return [float(item) for item in value]
    except (TypeError, ValueError):
        return []
