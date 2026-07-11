"""
Add wording-review flags to generated note sentences backed by uncertain rows.

Clinicians use the marker after a corrected transcript built the note, so a
plausible ASR substitution does not read as certain clinical detail. Generated
prose stays unchanged; this module only adds exact sentence strings for the
browser to mark beside the existing fidelity review surface.
"""

from __future__ import annotations

from copy import deepcopy
import math
import re
from typing import Any

from api.summary_fidelity import _sentences as clinician_visible_sentences

# Empirical threshold: `<0.78` catches both corrected calf/carp rows without a warning wall.
CORRECTED_NOTE_REVIEW_THRESHOLD = 0.78
LOW_CONFIDENCE_NOTE_FIELD = "low_confidence"

# Generic prose must not connect an unrelated citation to a sentence merely because both say
# "patient reports". Clinical/detail words remain available for the local evidence match.
_NOTE_LINK_STOP_WORDS = frozenset(
    {
        "about",
        "after",
        "also",
        "and",
        "are",
        "because",
        "been",
        "before",
        "clinician",
        "doctor",
        "for",
        "from",
        "had",
        "has",
        "have",
        "his",
        "into",
        "noted",
        "patient",
        "report",
        "reported",
        "reports",
        "said",
        "says",
        "she",
        "that",
        "the",
        "their",
        "them",
        "they",
        "this",
        "was",
        "were",
        "with",
    }
)


def add_low_confidence_note_flags(
    summary_payload: dict[str, Any],
    citation_rows: list[dict[str, Any]],
    review_threshold: float = CORRECTED_NOTE_REVIEW_THRESHOLD,
) -> dict[str, Any]:
    """Return the note with exact sentences that need a transcription review marker.

    Use after citation validation. Empty sections, citations, or measured confidence leave the
    note unchanged on screen; the input object is never mutated.

    Args:
        summary_payload: Generated note shown in the UI; empty means no sections can be flagged.
        citation_rows: Corrected source rows allowed for citations; empty means no safe linkage.
        review_threshold: Strict confidence boundary; invalid values leave all wording unmarked.

    Returns:
        Copied note with `sections[].low_confidence` where needed; an empty result mirrors input.
    """
    note_for_review = deepcopy(summary_payload)

    # An invalid threshold cannot support an honest clinician-facing confidence claim.
    if not isinstance(review_threshold, (int, float)) or isinstance(
        review_threshold, bool
    ):
        return note_for_review
    # Non-finite thresholds would mark everything or nothing for a meaningless reason.
    if not math.isfinite(float(review_threshold)):
        return note_for_review

    source_rows_by_id = _citation_rows_by_id(citation_rows)
    note_sections = note_for_review.get("sections", [])

    # A malformed or empty section list renders through the existing no-content path.
    if not isinstance(note_sections, list) or not note_sections:
        return note_for_review

    # Each SOAP section owns its validated citations and exact browser marker strings.
    for note_section in note_sections:
        # Non-object sections cannot expose content or citations, so the browser leaves them alone.
        if not isinstance(note_section, dict):
            continue

        note_section.pop(LOW_CONFIDENCE_NOTE_FIELD, None)
        section_content = str(note_section.get("content", "")).strip()
        # Empty prose gives the clinician no sentence to review.
        if section_content == "":
            continue

        section_citation_rows = _section_citation_rows(note_section, source_rows_by_id)
        # No validated corrected-row evidence means no sentence-level confidence claim is safe.
        if not section_citation_rows:
            continue

        sentences_to_review: list[str] = []
        # Exact sentence strings let the browser mark prose without rewriting model output.
        for note_sentence in clinician_visible_sentences(section_content):
            # Only sentences dominated by relevant sub-threshold rows receive the visible cue.
            if _sentence_needs_wording_review(
                note_sentence,
                section_citation_rows,
                float(review_threshold),
            ):
                sentences_to_review.append(note_sentence)

        # A fully supported section keeps the pre-M03 payload shape.
        if sentences_to_review:
            note_section[LOW_CONFIDENCE_NOTE_FIELD] = sentences_to_review

    return note_for_review


def _citation_rows_by_id(
    citation_rows: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    """Index corrected rows so validated section citations can find stored confidence.

    Empty or unidentified rows stay unavailable to note markers instead of being guessed.

    Args:
        citation_rows: Corrected rows selected for the note; empty produces an empty index.

    Returns:
        Rows keyed by stable segment ID; empty means no section can be linked safely.
    """
    source_rows_by_id: dict[str, dict[str, Any]] = {}
    # Every identified corrected row remains eligible for a section's validated citation.
    for citation_row in citation_rows:
        # Malformed rows cannot provide stable confidence evidence to the UI.
        if not isinstance(citation_row, dict):
            continue

        segment_id = str(citation_row.get("segment_id", "")).strip()
        # Missing identity prevents a note citation from tracing back to this wording.
        if segment_id == "":
            continue

        source_rows_by_id[segment_id] = citation_row

    return source_rows_by_id


def _section_citation_rows(
    note_section: dict[str, Any],
    source_rows_by_id: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    """Resolve one visible section's citation chips to trusted corrected rows.

    Empty, duplicate, or unresolved chips contribute no confidence evidence.

    Args:
        note_section: SOAP section the clinician reads; missing citations mean no linkage.
        source_rows_by_id: Trusted corrected rows; empty leaves every citation unresolved.

    Returns:
        Unique source rows in citation order; empty means the section stays unmarked.
    """
    resolved_rows: list[dict[str, Any]] = []
    resolved_segment_ids: set[str] = set()
    section_citations = note_section.get("citations", [])

    # Missing or malformed citations are equivalent to an uncited section in the UI.
    if not isinstance(section_citations, list):
        return resolved_rows

    # Each validated citation may contribute one source row to sentence review.
    for section_citation in section_citations:
        # A malformed chip cannot point to stored transcript evidence.
        if not isinstance(section_citation, dict):
            continue

        segment_id = str(section_citation.get("segment_id", "")).strip()
        # Duplicate or unresolved IDs must not weight the confidence decision twice.
        if segment_id in resolved_segment_ids or segment_id not in source_rows_by_id:
            continue

        resolved_segment_ids.add(segment_id)
        resolved_rows.append(source_rows_by_id[segment_id])

    return resolved_rows


def _sentence_needs_wording_review(
    note_sentence: str,
    section_citation_rows: list[dict[str, Any]],
    review_threshold: float,
) -> bool:
    """Decide whether relevant cited rows predominantly carry uncertain wording.

    No lexical link, no measured rows, and a low/high tie all leave the sentence plain.

    Args:
        note_sentence: Exact sentence rendered in the note; empty matches no source wording.
        section_citation_rows: Validated section sources; empty provides no confidence evidence.
        review_threshold: Strict corrected-row boundary used by the transcript UI.

    Returns:
        True when more than half of relevant measured rows are below threshold.
    """
    relevant_rows = _rows_relevant_to_sentence(note_sentence, section_citation_rows)
    measured_confidences: list[float] = []

    # Only finite stored values can influence what the clinician sees flagged.
    for relevant_row in relevant_rows:
        row_confidence = _finite_row_confidence(relevant_row.get("confidence"))
        # An unmeasured row remains absent rather than counting as high or low confidence.
        if row_confidence is not None:
            measured_confidences.append(row_confidence)

    # No measured relevant evidence means the sentence keeps its normal presentation.
    if not measured_confidences:
        return False

    low_confidence_count = sum(
        row_confidence < review_threshold for row_confidence in measured_confidences
    )
    return low_confidence_count * 2 > len(measured_confidences)


def _rows_relevant_to_sentence(
    note_sentence: str,
    section_citation_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Link a sentence to section citations that share meaningful clinical wording.

    Use because the current contract cites sections, not individual sentences; no overlap leaves
    the sentence unflagged rather than borrowing unrelated confidence.

    Args:
        note_sentence: Exact note sentence; empty yields no meaningful words.
        section_citation_rows: Section-level source rows; empty yields no links.

    Returns:
        Citation rows with conservative word overlap; empty means no sentence-level linkage.
    """
    sentence_words = _meaningful_note_words(note_sentence)
    relevant_rows: list[dict[str, Any]] = []

    # Empty prose has no honest lexical path to a transcript row.
    if not sentence_words:
        return relevant_rows

    # Each cited row is considered locally instead of lending confidence across the whole section.
    for citation_row in section_citation_rows:
        row_words = _meaningful_note_words(str(citation_row.get("text", "")))
        shared_words = sentence_words.intersection(row_words)
        minimum_shared_words = 1 if min(len(sentence_words), len(row_words)) <= 3 else 2

        # Conservative overlap keeps generic section citations from flagging unrelated sentences.
        if len(shared_words) >= minimum_shared_words:
            relevant_rows.append(citation_row)

    return relevant_rows


def _meaningful_note_words(note_text: str) -> set[str]:
    """Return words suitable for linking visible note prose to cited transcript text.

    Empty or generic-only text yields no words, so the UI does not invent a link.

    Args:
        note_text: Note sentence or corrected row; empty yields an empty set.

    Returns:
        Lowercase clinical/detail words; empty means no conservative match is possible.
    """
    # Short grammar words and generic note framing do not identify the clinical claim.
    return {
        word
        for word in re.findall(r"[a-z0-9]+", note_text.lower())
        if len(word) >= 3 and word not in _NOTE_LINK_STOP_WORDS
    }


def _finite_row_confidence(row_confidence: Any) -> float | None:
    """Return stored acoustic confidence, or no value when the row was unmeasured.

    Invalid, boolean, null, and non-finite values leave the note sentence unweighted.

    Args:
        row_confidence: Stored row value; null or invalid means no UI confidence evidence.

    Returns:
        Finite float, or null when the row must not influence the marker.
    """
    # Booleans are numeric in Python but are not acoustic confidence measurements.
    if isinstance(row_confidence, bool) or not isinstance(row_confidence, (int, float)):
        return None

    confidence_value = float(row_confidence)
    # NaN or infinity cannot support a threshold comparison shown to a clinician.
    if not math.isfinite(confidence_value):
        return None

    return confidence_value
