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
from api.summary_fidelity import (
    note_coverage_review_reasons,
    risk_pair_review_reasons,
    temporal_action_review_reasons,
    unsupported_demographic_review_reasons,
)
from medical_lexicon import default_medical_lexicon_path, load_medical_lexicon

# Empirical threshold: `<0.78` catches both corrected calf/carp rows without a warning wall.
CORRECTED_NOTE_REVIEW_THRESHOLD = 0.78
LOW_CONFIDENCE_NOTE_FIELD = "low_confidence"
# Machine-readable reason consumed by later review milestones; the browser
# payload itself never changes shape for this lane.
SOURCE_LOW_CONFIDENCE_REASON = "source_low_confidence"

# Clinical link tokens are cached per process; the lexicon file is static.
_clinical_link_tokens_cache: dict[str, frozenset[str]] | None = None

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


def _clinical_link_tokens() -> dict[str, frozenset[str]]:
    """Return single-word clinical terms the lexicon knows, split by kind.

    Use for sentence-to-row linkage: a drug or condition name is specific
    enough that sharing it alone ties a note sentence to its source row -
    consult 1.2's "Fexaphenidine" Plan item shared only that one word with its
    row and was never linked, so it printed as a confident prescription.

    Returns:
        {"canonical": ..., "variant": ...} casefolded single-word tokens;
        both empty when no lexicon is available, which disables the rule.
    """
    global _clinical_link_tokens_cache
    # The lexicon file is static per process, so one load serves every note.
    if _clinical_link_tokens_cache is not None:
        return _clinical_link_tokens_cache

    canonical_tokens: set[str] = set()
    variant_tokens: set[str] = set()
    # Every reviewed lexicon row can contribute link tokens of both kinds.
    for phrase in load_medical_lexicon(default_medical_lexicon_path()):
        # Multiword entries (e.g. "metro pro lol") contain ordinary words that
        # must never link on their own; only whole single-word terms qualify.
        if " " not in phrase.canonical and len(phrase.canonical) >= 4:
            canonical_tokens.add(phrase.canonical.casefold())
        # Known misspellings become the tokens that mark non-canonical wording.
        for variant in phrase.variants:
            # The same single-word/length bar keeps ordinary words out.
            if " " not in variant and len(variant) >= 4:
                variant_tokens.add(variant.casefold())

    # Canonical spellings are not "non-canonical wording"; keep the sets disjoint.
    variant_tokens -= canonical_tokens
    _clinical_link_tokens_cache = {
        "canonical": frozenset(canonical_tokens),
        "variant": frozenset(variant_tokens),
    }
    return _clinical_link_tokens_cache


def low_confidence_review_reasons(
    summary_payload: dict[str, Any],
    citation_rows: list[dict[str, Any]],
    review_threshold: float = CORRECTED_NOTE_REVIEW_THRESHOLD,
) -> list[dict[str, Any]]:
    """Return machine-readable reasons for flagged non-canonical clinical wording.

    Use downstream (M05/M06 review surfaces) when a flagged sentence needs a
    reason code, the offending terms, and the exact source rows - the browser
    payload stays unchanged; this is an internal reason lane only.

    Args:
        summary_payload: Generated note; empty produces no reasons.
        citation_rows: Corrected source rows; empty means nothing can be traced.
        review_threshold: Same strict boundary the visible markers use.

    Returns:
        One entry per flagged sentence containing a known non-canonical term:
        {section, sentence, reason, terms, segment_ids}; empty means every
        flagged sentence used ordinary or canonical wording.
    """
    reviewed_note = add_low_confidence_note_flags(
        summary_payload, citation_rows, review_threshold
    )
    variant_tokens = _clinical_link_tokens()["variant"]
    source_rows_by_id = _citation_rows_by_id(citation_rows)
    review_reasons: list[dict[str, Any]] = []

    # Each SOAP section is walked the same way the panel renders it.
    for note_section in reviewed_note.get("sections", []):
        # Malformed sections carry no flagged sentences to explain downstream.
        if not isinstance(note_section, dict):
            continue

        section_citation_rows = _section_citation_rows(note_section, source_rows_by_id)
        # Every visibly flagged sentence is checked for a known misspelled term.
        for flagged_sentence in note_section.get(LOW_CONFIDENCE_NOTE_FIELD, []):
            sentence_terms = sorted(
                _meaningful_note_words(flagged_sentence) & variant_tokens
            )
            # Ordinary low-confidence wording is already visibly marked; only
            # known non-canonical clinical terms need the machine reason.
            if not sentence_terms:
                continue

            # The reason names the exact sub-threshold rows behind the sentence
            # so a reviewer can jump straight to the uncertain audio moments.
            supporting_segment_ids = [
                str(citation_row.get("segment_id", ""))
                for citation_row in _rows_relevant_to_sentence(
                    flagged_sentence, section_citation_rows
                )
                if (
                    _finite_row_confidence(citation_row.get("confidence")) is not None
                    and _finite_row_confidence(citation_row.get("confidence"))
                    < float(review_threshold)
                )
            ]
            review_reasons.append(
                {
                    "section": str(note_section.get("heading", "")),
                    "sentence": flagged_sentence,
                    "reason": SOURCE_LOW_CONFIDENCE_REASON,
                    "terms": sentence_terms,
                    "segment_ids": supporting_segment_ids,
                }
            )

    return review_reasons


def note_review_reasons(
    summary_payload: dict[str, Any],
    citation_rows: list[dict[str, Any]],
    review_threshold: float = CORRECTED_NOTE_REVIEW_THRESHOLD,
) -> list[dict[str, Any]]:
    """Every machine-readable review reason for one generated note.

    The M05 aggregation point M06 will consume: clinical-term confidence
    reasons plus the temporal/action-state family, in note reading order per
    family. The browser payload stays unchanged; human wording is owned by
    M03/M06.

    Args:
        summary_payload: Generated note; empty produces no reasons.
        citation_rows: The note's selected corrected source rows; empty
            disables every source comparison.
        review_threshold: Strict confidence boundary for the term lane.

    Returns:
        Reason entries ({section, sentence, reason, ...}); empty means no
        automated review reason exists for this note.
    """
    review_reasons = low_confidence_review_reasons(
        summary_payload, citation_rows, review_threshold
    )

    note_sections = summary_payload.get("sections", [])
    # Malformed payloads reach the detectors as empty, never as a crash.
    if not isinstance(note_sections, list):
        note_sections = []
    note_key_points = summary_payload.get("key_points", [])
    if not isinstance(note_key_points, list):
        note_key_points = []

    review_reasons.extend(
        temporal_action_review_reasons(note_sections, note_key_points, citation_rows)
    )
    review_reasons.extend(
        risk_pair_review_reasons(note_sections, note_key_points, citation_rows)
    )
    review_reasons.extend(
        unsupported_demographic_review_reasons(
            note_sections, note_key_points, citation_rows
        )
    )
    review_reasons.extend(
        note_coverage_review_reasons(note_sections, note_key_points, citation_rows)
    )
    return review_reasons


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

    clinical_tokens = _clinical_link_tokens()
    known_clinical_words = clinical_tokens["canonical"] | clinical_tokens["variant"]

    # Each cited row is considered locally instead of lending confidence across the whole section.
    for citation_row in section_citation_rows:
        row_words = _meaningful_note_words(str(citation_row.get("text", "")))
        shared_words = sentence_words.intersection(row_words)
        # Very short texts can only ever share one word, so one match suffices.
        minimum_shared_words = 1 if min(len(sentence_words), len(row_words)) <= 3 else 2

        # Conservative overlap keeps generic section citations from flagging unrelated sentences.
        if len(shared_words) >= minimum_shared_words:
            relevant_rows.append(citation_row)
            continue

        # A shared clinical term links on its own: a drug name is specific
        # enough that one word ties the Plan item to the row that said it -
        # the consult-1.2 "Fexaphenidine" gap this rule closes.
        if shared_words & known_clinical_words:
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
