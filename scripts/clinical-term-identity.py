"""Score exact clinical-term identity without reading transcript metadata.

M05 uses this evaluator beside the manifest-bound primary transcript scorer.
It accepts clinician-visible rows, reads only their text, and emits a text-free
summary suitable for baseline and candidate comparison.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from typing import Any


def _tokens(text: str) -> list[str]:
    """Return the lowercase word tokens used by the clinical-term contract."""
    return re.findall(r"[a-z']+", text.lower())


def _is_phrase_present_in_text(text: str, required_phrase: str) -> bool:
    """Return whether one normalized phrase occurs within one displayed row."""
    visible_words = _tokens(text)
    required_words = _tokens(required_phrase)
    if not required_words:
        return False

    last_start_index = len(visible_words) - len(required_words)
    for start_index in range(last_start_index + 1):
        end_index = start_index + len(required_words)
        if visible_words[start_index:end_index] == required_words:
            return True
    return False


def _displayed_text(hypothesis_row: Mapping[str, object]) -> str:
    """Return one row's visible text and reject non-text payloads."""
    displayed_text = hypothesis_row.get("text", "")
    if not isinstance(displayed_text, str):
        raise ValueError("hypothesis row text must be a string")
    return displayed_text


def score_clinical_term_identity(
    required_terms: Sequence[str],
    hypothesis_rows: Sequence[Mapping[str, object]],
) -> dict[str, Any]:
    """Score exact phrases without consulting timing, role, or speaker fields.

    Args:
        required_terms: Canonical phrases in frozen manifest order.
        hypothesis_rows: Displayed transcript rows; only each ``text`` value is read.

    Returns:
        Text-free term-set identity, supported/missing indices, counts, and recall.

    Raises:
        ValueError: A canonical term is empty, duplicates another normalized term,
            or a displayed row has a non-string text value.
    """
    normalized_terms = [_tokens(required_term) for required_term in required_terms]
    if any(not normalized_term for normalized_term in normalized_terms):
        raise ValueError("clinical terms must contain normalized words")

    normalized_term_phrases = [
        " ".join(normalized_term) for normalized_term in normalized_terms
    ]
    if len(normalized_term_phrases) != len(set(normalized_term_phrases)):
        raise ValueError("clinical terms must be unique after normalization")

    displayed_texts = [_displayed_text(row) for row in hypothesis_rows]
    supported_term_indices = [
        term_index
        for term_index, required_term in enumerate(required_terms)
        if any(
            _is_phrase_present_in_text(displayed_text, required_term)
            for displayed_text in displayed_texts
        )
    ]
    supported_index_set = set(supported_term_indices)
    missing_term_indices = [
        term_index
        for term_index in range(len(required_terms))
        if term_index not in supported_index_set
    ]
    recall = (
        len(supported_term_indices) / len(required_terms) if required_terms else None
    )
    term_set_sha256 = hashlib.sha256(
        json.dumps(
            normalized_term_phrases,
            ensure_ascii=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    return {
        "metric_contract": (
            "exact normalized phrase in any displayed row; transcript start/end "
            "and role fields are not read"
        ),
        "normalization": "[a-z']+ lowercase words",
        "term_set_sha256": term_set_sha256,
        "required_term_count": len(required_terms),
        "supported_term_count": len(supported_term_indices),
        "supported_term_indices": supported_term_indices,
        "missing_term_indices": missing_term_indices,
        "recall": recall,
    }
