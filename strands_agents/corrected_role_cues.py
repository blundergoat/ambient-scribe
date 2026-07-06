"""
Doctor/Patient role cues for corrected source chips.

The live transcript keeps the preview labels, but the stopped-visit summary
cites corrected rows. This module applies conservative text cues to those
corrected rows so the source chips shown under the summary do not confidently
point at the wrong speaker after alignment drift.
"""

from __future__ import annotations

import re
from typing import Any

CORRECTED_ROLE_FILLER_WORDS = frozenset({"ah", "oh", "um", "uh", "erm", "well"})
DOCTOR_CONNECTOR_STARTS = frozenset({"and", "are", "can", "did", "is", "okay", "was"})
ROLE_CUE_SOURCE = "post_visit_alignment"

_DOCTOR_CUES = (
    "hello there its dr",
    "dr steed",
    "dr steve",
    "how can i help",
    "im sorry to hear",
    "i can understand",
    "can you tell",
    "tell me",
    "age please",
    "whereabouts in your skin",
    "have you",
    "vomited at all",
    "bright lights",
    "is it affected",
    "it affected",
    "okay",
    "lets try",
    "you mentioned",
    "did the pain",
    "are you able",
)
_PATIENT_CUES = (
    "and i",
    "ive",
    "ive had",
    "ive just",
    "i have",
    "i had",
    "i noticed",
    "i dont know",
    "my vision",
    "well you know",
    "just want you",
    "want you to do something",
    "need to vomit",
    "headache since",
    "my chest",
    "my hands",
    "my arms",
    "my name",
    "my names",
    "my friends mum",
    "im wearing",
    "im worried",
    "im concerned",
    "im 26",
    "like if i",
    "when i",
    "if i",
)
_DOCTOR_QUESTION_CUES = (
    "can you",
    "could you",
    "did you",
    "do you",
    "does it",
    "have you",
    "has it",
    "are you",
    "is that",
    "tell me",
    "describe",
    "whereabouts in your skin",
    "your neck",
    "bright lights",
    "vomited at all",
    "is it affected",
    "it affected",
)
_SHORT_PATIENT_ANSWERS = {
    "yeah",
    "yeah yeah",
    "yeah well",
    "yeah um",
    "yes",
    "yes well",
    "yep",
    "no",
    "nope",
    "um no",
    "uh no",
}
_DOCTOR_PROMPT_PATIENT_ANSWER_PATTERN = re.compile(
    r"^(?P<doctor>.*?(?:please|whereabouts in your skin|have you vomited at all|"
    r"is it affected|it affected)\?)\s+"
    r"(?P<patient>(?:my|i|i'm|ive|i've|all over|mostly|yeah|yes|no)\b.*)$",
    re.IGNORECASE,
)

def prepare_corrected_source_segments(segments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return corrected rows ready for summary source chips.

    Args:
        segments: Corrected rows from the post-stop ASR pass; empty means the user has no corrected artifact.

    Returns:
        Split and role-cleaned rows; empty stays empty for live-preview fallback.
    """
    return apply_role_cue_cleanup(split_mixed_corrected_segments(segments))


def split_mixed_corrected_segments(segments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Split corrected rows that visibly contain both consultation speakers.

    Args:
        segments: Corrected source rows in visible order; empty means nothing can be split.

    Returns:
        Rows in the same order, with derivative IDs for split chips.
    """
    split_segments: list[dict[str, Any]] = []

    # Each row is inspected in source-chip order so inserted chips stay where the user expects.
    for segment in segments:
        split_segments.extend(split_one_mixed_corrected_segment(segment))

    return split_segments


def split_one_mixed_corrected_segment(segment: dict[str, Any]) -> list[dict[str, Any]]:
    """Split one source row when a conservative mixed-speaker pattern matches.

    Args:
        segment: Corrected row shown under the summary; empty text returns the row unchanged.

    Returns:
        One unchanged row or multiple derivative rows for separate source chips.
    """
    text = str(segment.get("text", "")).strip()
    # Blank source chips are left unchanged so later fallback logic can decide visibility.
    if text == "":
        return [dict(segment)]

    split_parts = mixed_source_text_parts(text)
    # Single-speaker rows keep their original ID and timing.
    if split_parts == []:
        return [dict(segment)]

    return build_split_source_segments(segment, split_parts)


def mixed_source_text_parts(text: str) -> list[tuple[str, str]]:
    """Return role/text parts for one mixed corrected source row.

    Args:
        text: Source-chip text the summary may cite; empty means no split parts.

    Returns:
        Ordered `(role, text)` parts, or empty when the row should stay intact.
    """
    doctor_prompt_match = _DOCTOR_PROMPT_PATIENT_ANSWER_PATTERN.match(text)
    # A prompt followed by an answer should become separate Doctor and Patient chips.
    if doctor_prompt_match is not None:
        return [
            ("DOCTOR", doctor_prompt_match.group("doctor").strip()),
            ("PATIENT", doctor_prompt_match.group("patient").strip()),
        ]

    return []


def build_split_source_segments(
    source_segment: dict[str, Any],
    split_parts: list[tuple[str, str]],
) -> list[dict[str, Any]]:
    """Build derivative source rows from one mixed corrected row.

    Args:
        source_segment: Original corrected row; empty IDs get a generic split ID prefix.
        split_parts: Role/text parts to show as separate source chips; empty is not passed here.

    Returns:
        Split rows with source metadata preserved for summary citation.
    """
    start_time = safe_source_time(source_segment.get("start"))
    end_time = safe_source_time(source_segment.get("end"))
    duration = max(0.0, end_time - start_time)
    base_segment_id = str(source_segment.get("segment_id", "")).strip() or "corrected-split"
    total_words = sum(max(1, len(text.split())) for _role, text in split_parts)
    consumed_words = 0
    split_segments: list[dict[str, Any]] = []

    # Each split part keeps provenance but gets its own role and source ID.
    for part_index, (role, text) in enumerate(split_parts, start=1):
        part_words = max(1, len(text.split()))
        part_start = start_time + (duration * consumed_words / total_words)
        consumed_words += part_words
        # The final chip receives the original end so source timing does not drift.
        if part_index == len(split_parts):
            part_end = end_time
        else:
            part_end = start_time + (duration * consumed_words / total_words)

        split_segment = dict(source_segment)
        split_segment["segment_id"] = f"{base_segment_id}-{part_index:02d}"
        split_segment["role"] = role
        split_segment["role_source"] = ROLE_CUE_SOURCE
        split_segment["text"] = text
        split_segment["start"] = part_start
        split_segment["end"] = part_end
        split_segments.append(split_segment)

    return split_segments


def safe_source_time(value: Any) -> float:
    """Return a numeric source-chip time.

    Args:
        value: Stored row time; null or invalid values mean the source chip has no clock precision.

    Returns:
        Float seconds, or `0.0` when timing is unavailable.
    """
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def apply_role_cue_cleanup(segments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Adjust corrected-row roles when text carries obvious speaker cues.

    Args:
        segments: Corrected rows in chronological order; empty means the user has no corrected artifact.

    Returns:
        Corrected rows with cue-based role fixes marked by `role_source`; empty stays empty.
    """
    cleaned_segments = [dict(segment) for segment in segments]
    # Each corrected row can carry its own cue or borrow context from a short neighbor.
    for row_index, segment in enumerate(cleaned_segments):
        # Rows created by the mixed-chip splitter already have a deliberate visible owner.
        if segment.get("role_source") == ROLE_CUE_SOURCE:
            continue

        previous_text = ""
        previous_role = ""
        # First source chips have no earlier row to explain a short answer.
        if row_index > 0:
            previous_segment = cleaned_segments[row_index - 1]
            previous_text = str(previous_segment.get("text", ""))
            previous_role = str(previous_segment.get("role", ""))

        next_text = ""
        next_role = ""
        # Final source chips have no later row to complete a short lead-in.
        if row_index + 1 < len(cleaned_segments):
            next_segment = cleaned_segments[row_index + 1]
            next_text = str(next_segment.get("text", ""))
            next_role = str(next_segment.get("role", ""))

        inferred_role = infer_role_from_corrected_text(
            str(segment.get("text", "")),
            previous_text=previous_text,
            next_text=next_text,
            previous_role=previous_role,
            next_role=next_role,
        )
        # No cue means the live scaffold remains the best available role.
        if inferred_role is None or segment.get("role") == inferred_role:
            continue

        segment["role"] = inferred_role
        segment["role_source"] = ROLE_CUE_SOURCE

    return cleaned_segments


def infer_role_from_corrected_text(
    text: str,
    *,
    previous_text: str = "",
    next_text: str = "",
    previous_role: str = "",
    next_role: str = "",
) -> str | None:
    """Infer an obvious Doctor/Patient role from corrected source-chip text.

    Args:
        text: Corrected row text the summary will cite; empty means no cue.
        previous_text: Previous row text for short connector cues; empty means no context.
        next_text: Next row text for short connector cues; empty means no context.
        previous_role: Previous corrected role; empty means no trusted previous speaker.
        next_role: Next corrected role; empty means no trusted next speaker.

    Returns:
        `DOCTOR`, `PATIENT`, or None when the source chip should keep its scaffold role.
    """
    normalized_text = normalize_corrected_role_phrase(text)
    # Empty text carries no user-visible cue.
    if normalized_text == "":
        return None

    direct_role = role_from_corrected_phrase(normalized_text)
    # Direct phrases like "I'm sorry to hear" can safely label this corrected row.
    if direct_role is not None:
        return direct_role

    # A brief answer after the clinician's question is visible patient speech.
    if is_short_patient_answer_after_doctor_question(
        normalized_text,
        previous_text=previous_text,
        previous_role=previous_role,
    ):
        return "PATIENT"

    word_count = len(normalized_text.split())
    # Longer rows should not inherit a role from a neighbor's phrase.
    if word_count > 3:
        return None

    previous_phrase = normalize_corrected_role_phrase(f"{previous_text} {text}")
    # A short continuation can complete the previous doctor's prompt.
    if previous_role == "DOCTOR" and role_from_corrected_phrase(previous_phrase) == "DOCTOR":
        return "DOCTOR"

    next_phrase = normalize_corrected_role_phrase(f"{text} {next_text}")
    # A short lead-in like "Can" can attach to the next doctor question.
    if (
        next_role == "DOCTOR"
        and first_meaningful_word(normalized_text) in DOCTOR_CONNECTOR_STARTS
        and role_from_corrected_phrase(next_phrase) == "DOCTOR"
    ):
        return "DOCTOR"

    # Short symptom continuations can attach to nearby patient statements.
    if previous_role == "PATIENT" and role_from_corrected_phrase(previous_phrase) == "PATIENT":
        return "PATIENT"

    # A short lead-in can also borrow patient ownership from the next answer row.
    if next_role == "PATIENT" and role_from_corrected_phrase(next_phrase) == "PATIENT":
        return "PATIENT"

    return None


def is_short_patient_answer_after_doctor_question(
    normalized_text: str,
    *,
    previous_text: str,
    previous_role: str,
) -> bool:
    """Detect a tiny patient answer that follows a clinician question.

    Args:
        normalized_text: Current corrected row text; empty means no answer cue.
        previous_text: Prior corrected row text; empty means no question context.
        previous_role: Prior corrected row role; empty means no trusted clinician prompt.

    Returns:
        True when the row should show as Patient in corrected source chips.
    """
    # Longer text needs lexical cues; only tiny answers use neighbor context.
    if normalized_text not in _SHORT_PATIENT_ANSWERS:
        return False

    # The previous row must be the clinician's question for the answer to be safe.
    if previous_role != "DOCTOR":
        return False

    previous_phrase = normalize_corrected_role_phrase(previous_text)
    return first_cue_index(previous_phrase, _DOCTOR_QUESTION_CUES) is not None


def first_meaningful_word(normalized_phrase: str) -> str:
    """Return the first non-filler word from a normalized source-chip phrase.

    Args:
        normalized_phrase: Lowercase cue phrase; empty means no user-visible word.

    Returns:
        First content word, or empty when the phrase contains only fillers.
    """
    # Filler-only rows cannot safely inherit a role from adjacent content.
    if normalized_phrase == "":
        return ""

    # The first non-filler word carries the row's user-visible action.
    for word in normalized_phrase.split():
        # Fillers introduce speech but do not identify the speaker by themselves.
        if word in CORRECTED_ROLE_FILLER_WORDS:
            continue
        return word

    return ""


def role_from_corrected_phrase(normalized_phrase: str) -> str | None:
    """Map normalized corrected source text to an obvious consultation role.

    Args:
        normalized_phrase: Lowercase words without punctuation; empty has no cue.

    Returns:
        Role label or None when no cue is safe enough for corrected rows.
    """
    doctor_cue_index = first_cue_index(normalized_phrase, _DOCTOR_CUES)
    patient_cue_index = first_cue_index(normalized_phrase, _PATIENT_CUES)

    # No obvious consultation phrase means the user keeps the scaffold role.
    if doctor_cue_index is None and patient_cue_index is None:
        return None

    # A patient-only cue identifies symptom text for the corrected note.
    if doctor_cue_index is None:
        return "PATIENT"

    # A doctor-only cue identifies clinician prompts or empathy for the note.
    if patient_cue_index is None:
        return "DOCTOR"

    # When a row blends both speakers, the first cue is the safest visible owner.
    if doctor_cue_index <= patient_cue_index:
        return "DOCTOR"

    return "PATIENT"


def first_cue_index(normalized_phrase: str, cues: tuple[str, ...]) -> int | None:
    """Find the first role cue in text the corrected note may cite.

    Args:
        normalized_phrase: Corrected row text after punctuation cleanup; empty means no cue.
        cues: Role phrases to search; empty means this role cannot be inferred.

    Returns:
        First cue position, or None when this row has no visible role evidence.
    """
    padded_phrase = f" {normalized_phrase} "
    cue_indexes: list[int] = []

    # Each cue is matched as whole words so "ive" does not fire inside another word.
    for cue in cues:
        cue_index = padded_phrase.find(f" {cue} ")
        # Missing cues leave the current row with its existing visible role.
        if cue_index < 0:
            continue

        cue_indexes.append(cue_index)

    # No cue means the post-stop artifact should not invent a role.
    if cue_indexes == []:
        return None

    return min(cue_indexes)


def normalize_corrected_role_phrase(text: str) -> str:
    """Normalize source-chip text for role cue matching.

    Args:
        text: User-visible corrected row text; empty produces an empty cue phrase.

    Returns:
        Space-joined lowercase words with punctuation removed; empty means no cue text.
    """
    normalized_words: list[str] = []

    # Each visible token becomes a cue word in the same order the user reads it.
    for raw_word in text.strip().split():
        normalized_word = normalize_corrected_role_word(raw_word)
        # Punctuation-only tokens have no role cue for the corrected source chip.
        if normalized_word == "":
            continue

        normalized_words.append(normalized_word)

    return " ".join(normalized_words)


def normalize_corrected_role_word(word: str) -> str:
    """Normalize one source-chip word for role cue matching.

    Args:
        word: Transcript token with punctuation; empty means no cue term.

    Returns:
        Lowercase alphanumeric token; empty means punctuation-only input.
    """
    return re.sub(r"[^a-z0-9]+", "", word.lower())
