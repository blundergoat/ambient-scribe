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
_IDENTITY_ECHO_ACK_WORDS = frozenset({"alright", "ok", "okay", "right"})
_IDENTITY_CUE_PATTERN = re.compile(r"\bmy names?\b|\bim \d+\b")
# Duplicated fillers and bare agreements ("Yeah. Yeah, okay.") are conversation
# noise, not a clinician echoing the patient's answer back.
_ECHO_DUPLICATE_EXCLUDED_WORDS = (
    CORRECTED_ROLE_FILLER_WORDS
    | _IDENTITY_ECHO_ACK_WORDS
    | frozenset({"yeah", "yes", "no", "mm", "hmm", "mhm"})
)
# M12: cues too generic to overrule a live scaffold on their own. On strong
# scaffolds these were the ONLY source of cleanup damage ("Okay. Oh, I can do"
# flipped to Doctor by "okay"; the doctor's "and I hope..." flipped to Patient
# by "and i"), while on weak scaffolds they still help - so they form a weak
# tier that only acts when the visit shows a weak-scaffold signal.
_WEAK_DIRECT_CUES = frozenset({"okay", "and i"})
# Strong direct cues never contradicted a good scaffold across four gated
# sessions (0 strong-cue flips on every strong scaffold, 10 on the weak one),
# so the count of strong-cue flips IS the weak-scaffold signal.
WEAK_TIER_MIN_STRONG_FLIPS = 3

_DOCTOR_CUES = (
    "hello there its dr",
    "dr steed",
    "dr steve",
    "how can i help",
    "can i confirm your name",
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
    "i vomited",
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

def prepare_corrected_source_segments(
    segments: list[dict[str, Any]],
    *,
    corrected_words: list[str] | None = None,
    word_timings: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Return corrected rows ready for summary source chips.

    Args:
        segments: Corrected rows from the post-stop ASR pass; empty means the user has no corrected artifact.
        corrected_words: Second-pass ASR words the rows were built from; None disables timing splits.
        word_timings: Per-word timing rows aligned to `corrected_words`; None disables timing splits.

    Returns:
        Split and role-cleaned rows; empty stays empty for live-preview fallback.
    """
    echo_split_segments = split_identity_echo_segments(segments, corrected_words, word_timings)
    return apply_role_cue_cleanup(split_mixed_corrected_segments(echo_split_segments))


def split_identity_echo_segments(
    segments: list[dict[str, Any]],
    corrected_words: list[str] | None,
    word_timings: list[dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    """Split identity rows whose echoed age belongs to the clinician's next chip.

    Args:
        segments: Corrected rows in visible order; empty means nothing can be split.
        corrected_words: Second-pass ASR words; None or empty means no timing evidence exists.
        word_timings: Timing rows aligned index-by-index to `corrected_words`; misaligned input is ignored.

    Returns:
        Rows in the same order, with the age echo split off only when word timing proves the gap.
    """
    # Timing evidence must exist and describe exactly the second-pass words, or rows stay whole.
    if not corrected_words or not word_timings or len(word_timings) != len(corrected_words):
        return segments

    split_segments: list[dict[str, Any]] = []
    # Each row is inspected in source-chip order so inserted chips stay where the user expects.
    for row_index, segment in enumerate(segments):
        next_row_start: float | None = None
        # The following chip's start time bounds how far the echo may extend without colliding.
        if row_index + 1 < len(segments):
            next_row_start = safe_source_time(segments[row_index + 1].get("start"))

        split_segments.extend(
            split_one_identity_echo_segment(segment, next_row_start, corrected_words, word_timings)
        )

    return split_segments


def split_one_identity_echo_segment(
    segment: dict[str, Any],
    next_row_start: float | None,
    corrected_words: list[str],
    word_timings: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Split one identity/echo row when word timing shows a real speaker gap.

    Args:
        segment: Corrected row shown under the summary; rows without the echo shape pass through.
        next_row_start: Start of the next visible chip; None means this is the final row.
        corrected_words: Second-pass ASR words the row text came from.
        word_timings: Timing rows aligned to `corrected_words`; missing values keep the row whole.

    Returns:
        One unchanged row, or the patient identity chip plus the clinician echo chip.
    """
    row_words = split_source_row_words(segment)
    normalized_row_words = [normalize_corrected_role_word(word) for word in row_words]
    echo_index = identity_echo_boundary_index(normalized_row_words)
    # Rows without the identity-plus-echoed-age shape are never timing-split.
    if echo_index is None:
        return [dict(segment)]

    boundary_times = identity_echo_boundary_times(
        segment,
        next_row_start,
        normalized_row_words,
        echo_index,
        corrected_words,
        word_timings,
    )
    # Untrustworthy or drifted timing keeps the row whole for the safer text-only cleanup.
    if boundary_times is None:
        return [dict(segment)]

    return build_identity_echo_parts(segment, row_words, echo_index, boundary_times)


def identity_echo_boundary_times(
    segment: dict[str, Any],
    next_row_start: float | None,
    normalized_row_words: list[str],
    echo_index: int,
    corrected_words: list[str],
    word_timings: list[dict[str, Any]],
) -> tuple[float, float, float] | None:
    """Validate the timed identity/echo boundary for one corrected row.

    Args:
        segment: Corrected row being split; its live start/end sanity-check the estimate.
        next_row_start: Start of the next visible chip; None means this is the final row.
        normalized_row_words: Normalized row words used to find the row in the ASR stream.
        echo_index: Index of the patient's own age word inside the row.
        corrected_words: Raw second-pass ASR words.
        word_timings: Timing rows aligned to `corrected_words`.

    Returns:
        `(identity_end, echo_start, echo_end)` seconds, or None when timing cannot be trusted.
    """
    stream_start = locate_row_word_span(normalized_row_words, corrected_words)
    # Live-fallback or reshuffled rows have no matching ASR words, so no timing exists for them.
    if stream_start is None:
        return None

    identity_end = word_timing_value(word_timings, stream_start + echo_index, "end")
    echo_start = word_timing_value(word_timings, stream_start + echo_index + 1, "start")
    echo_end = word_timing_value(
        word_timings,
        stream_start + len(normalized_row_words) - 1,
        "end",
    )
    # Missing timing values mean the split boundary cannot be trusted.
    if identity_end is None or echo_start is None or echo_end is None:
        return None

    # A voice change needs an audible gap; continuous timing means one speaker kept talking.
    if echo_start <= identity_end or echo_end <= echo_start:
        return None

    # A boundary before the row even starts means the timing joined the wrong words.
    if identity_end <= safe_source_time(segment.get("start")):
        return None

    # The echo is the row's trailing tail, so its timed end must reach or pass the live row
    # end; an earlier end means the full-clip timing estimate drifted and cannot be trusted
    # (drifted estimates placed a consult-08 echo 1.6s early, inside patient-only speech).
    if echo_end < safe_source_time(segment.get("end")):
        return None

    # The echo may extend past the live row end, but never into the next visible chip.
    if next_row_start is not None and echo_end > next_row_start:
        return None

    return identity_end, echo_start, echo_end


def build_identity_echo_parts(
    segment: dict[str, Any],
    row_words: list[str],
    echo_index: int,
    boundary_times: tuple[float, float, float],
) -> list[dict[str, Any]]:
    """Build the patient identity chip and the clinician echo chip from one row.

    Args:
        segment: Original corrected row; both parts inherit its provenance fields.
        row_words: Display words in visible order.
        echo_index: Index of the patient's own age word; the echo starts after it.
        boundary_times: Validated `(identity_end, echo_start, echo_end)` seconds.

    Returns:
        Two derivative rows in visible order, using the splitter's `-01`/`-02` ID convention.
    """
    identity_end, echo_start, echo_end = boundary_times
    base_segment_id = str(segment.get("segment_id", "")).strip() or "corrected-split"

    identity_part = dict(segment)
    identity_part["segment_id"] = f"{base_segment_id}-01"
    identity_part["role"] = "PATIENT"
    identity_part["role_source"] = ROLE_CUE_SOURCE
    identity_part["text"] = " ".join(row_words[: echo_index + 1])
    identity_part["end"] = identity_end

    echo_part = dict(segment)
    echo_part["segment_id"] = f"{base_segment_id}-02"
    echo_part["role"] = "DOCTOR"
    echo_part["role_source"] = ROLE_CUE_SOURCE
    echo_part["text"] = " ".join(row_words[echo_index + 1 :])
    echo_part["start"] = echo_start
    echo_part["end"] = echo_end

    return [identity_part, echo_part]


def split_source_row_words(segment: dict[str, Any]) -> list[str]:
    """Return one corrected row's display words in visible order.

    Args:
        segment: Corrected row; missing or blank text means the chip shows no words.

    Returns:
        Whitespace-split words with punctuation kept; empty means nothing is visible.
    """
    return str(segment.get("text", "")).strip().split()


def identity_echo_boundary_index(normalized_row_words: list[str]) -> int | None:
    """Find the last patient word before a clinician's echoed acknowledgement.

    Covers both gated echo shapes: the identity/age digit echo
    ("...I'm 26. 26, okay.", M07) and the patient-cued word echo
    ("...I vomited twice. Twice, okay.", M10).

    Args:
        normalized_row_words: Lowercase alphanumeric row words; empty means no echo shape.

    Returns:
        Index of the patient's own echoed word, or None when this row must stay whole.
    """
    # The shape needs at least a patient statement, the duplicate, and the acknowledgement.
    if len(normalized_row_words) < 4:
        return None

    last_index = len(normalized_row_words) - 1
    # The clinician's chip ends in an acknowledgement word like "okay".
    if normalized_row_words[last_index] not in _IDENTITY_ECHO_ACK_WORDS:
        return None

    duplicate_index = last_index - 2
    echoed_word = normalized_row_words[duplicate_index]
    # Duplicated fillers or bare agreements are noise, not an echo boundary.
    if echoed_word == "" or echoed_word in _ECHO_DUPLICATE_EXCLUDED_WORDS:
        return None

    # The echo must repeat the word immediately, or the duplicate is unrelated speech.
    if normalized_row_words[duplicate_index + 1] != echoed_word:
        return None

    patient_phrase = " ".join(
        word for word in normalized_row_words[: duplicate_index + 1] if word
    )
    # The patient part must prove itself: an identity statement, or a phrase the
    # cue lexicon decisively owns as patient speech. Splitting never guesses.
    if _IDENTITY_CUE_PATTERN.search(patient_phrase) is not None:
        return duplicate_index
    if role_from_corrected_phrase(patient_phrase) == "PATIENT":
        return duplicate_index

    return None


def locate_row_word_span(
    normalized_row_words: list[str],
    corrected_words: list[str],
) -> int | None:
    """Find where one corrected row's words sit inside the second-pass word stream.

    Args:
        normalized_row_words: Normalized row words in visible order; empty cannot be located.
        corrected_words: Raw second-pass ASR words; empty means there is no stream to search.

    Returns:
        Stream index of the row's first word, or None when the row text is not ASR-owned.
    """
    # Empty rows have no words for timing evidence to describe.
    if normalized_row_words == []:
        return None

    normalized_stream = [normalize_corrected_role_word(word) for word in corrected_words]
    span_length = len(normalized_row_words)

    # The row text was joined verbatim from stream words, so an exact window match finds it.
    for start_index in range(0, len(normalized_stream) - span_length + 1):
        # The first matching window is the span the allocator consumed for this row.
        if normalized_stream[start_index : start_index + span_length] == normalized_row_words:
            return start_index

    return None


def word_timing_value(
    word_timings: list[dict[str, Any]],
    timing_index: int,
    key: str,
) -> float | None:
    """Read one timing boundary from the second-pass word timing rows.

    Args:
        word_timings: Timing rows aligned to the ASR word stream.
        timing_index: Stream index of the word whose boundary is needed.
        key: `start` or `end` boundary of that word.

    Returns:
        Boundary seconds, or None when the timing row is missing or malformed.
    """
    # Out-of-range lookups mean the row span and timing rows disagree.
    if timing_index < 0 or timing_index >= len(word_timings):
        return None

    timing = word_timings[timing_index]
    # Malformed timing rows must never place a split boundary.
    if not isinstance(timing, dict):
        return None

    try:
        return float(timing[key])
    except (KeyError, TypeError, ValueError):
        return None


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

    M12 makes the cleanup scaffold-aware: strong cues always apply, but the
    weak tier (bare "okay"/"and I" and short-row neighbor inheritance) only
    acts when the visit shows the weak-scaffold signal - enough strong-cue
    flips that the live labels clearly disagree with what was said.

    Args:
        segments: Corrected rows in chronological order; empty means the user has no corrected artifact.

    Returns:
        Corrected rows with cue-based role fixes marked by `role_source`; empty stays empty.
    """
    # Pass 1 replays today's full cleanup on scratch rows purely to count
    # strong-cue flips - the self-referential weak-scaffold signal.
    strong_flip_count = _sequential_cue_pass(
        [dict(segment) for segment in segments],
        weak_tier_enabled=True,
    )
    weak_tier_enabled = strong_flip_count >= WEAK_TIER_MIN_STRONG_FLIPS

    cleaned_segments = [dict(segment) for segment in segments]
    _sequential_cue_pass(cleaned_segments, weak_tier_enabled=weak_tier_enabled)
    return cleaned_segments


def _sequential_cue_pass(
    cleaned_segments: list[dict[str, Any]],
    *,
    weak_tier_enabled: bool,
) -> int:
    """Run one sequential cue-cleanup pass over rows, honoring the weak tier.

    Args:
        cleaned_segments: Rows mutated in place, in chronological order.
        weak_tier_enabled: False suppresses weak-tier flips so a strong live
            scaffold keeps its own labels; True is the historical behavior.

    Returns:
        Number of flips backed by a strong direct cue - the weak-scaffold signal.
    """
    strong_flip_count = 0
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

        row_text = str(segment.get("text", ""))
        inferred_role = infer_role_from_corrected_text(
            row_text,
            previous_text=previous_text,
            next_text=next_text,
            previous_role=previous_role,
            next_role=next_role,
        )
        # No cue means the live scaffold remains the best available role.
        if inferred_role is None or segment.get("role") == inferred_role:
            continue

        is_strong_flip = has_strong_direct_cue(row_text, inferred_role)
        # Strong evidence counts toward the weak-scaffold signal.
        if is_strong_flip:
            strong_flip_count += 1

        # Historical semantics survive for: strong direct cues, the tiny-answer rule,
        # and phrase completions whose JOINED wording carries a strong cue ("How can I
        # help" + "this afternoon"). Only the weak tier - generic cues and borrowed
        # neighbor evidence - defers to a strong scaffold.
        keeps_historical_semantics = (
            is_strong_flip
            or is_short_patient_answer_after_doctor_question(
                normalize_corrected_role_phrase(row_text),
                previous_text=previous_text,
                previous_role=previous_role,
            )
            or _has_strong_cue_in_joined_phrase(
                row_text, previous_text, next_text, inferred_role
            )
        )
        # A weak-tier flip against a strong scaffold does net damage - skip it.
        if not keeps_historical_semantics and not weak_tier_enabled:
            continue

        segment["role"] = inferred_role
        segment["role_source"] = ROLE_CUE_SOURCE

    return strong_flip_count


def _has_strong_cue_in_joined_phrase(
    row_text: str,
    previous_text: str,
    next_text: str,
    role: str,
) -> bool:
    """Report whether a neighbor-joined phrase backs this role with a strong cue.

    Args:
        row_text: Current row text; short rows may complete a neighbor's phrase.
        previous_text: Earlier row text; empty means no backward join exists.
        next_text: Later row text; empty means no forward join exists.
        role: Candidate role a cleanup flip wants to apply.

    Returns:
        True when either join carries a specific non-generic cue for that role,
        so a phrase completion like "How can I help" + "this afternoon" still counts.
    """
    role_cues = _DOCTOR_CUES if role == "DOCTOR" else _PATIENT_CUES
    # Each join direction can complete a cue phrase split across two chips.
    for joined_text in (f"{previous_text} {row_text}", f"{row_text} {next_text}"):
        padded_phrase = f" {normalize_corrected_role_phrase(joined_text)} "
        matched_cues = {cue for cue in role_cues if f" {cue} " in padded_phrase}
        # A surviving non-weak cue in the joined wording is real speaker evidence.
        if matched_cues - _WEAK_DIRECT_CUES:
            return True

    return False


def has_strong_direct_cue(text: str, role: str) -> bool:
    """Report whether a non-generic direct cue backs this role for the row text.

    Args:
        text: Corrected row text the summary may cite.
        role: Candidate role a cleanup flip wants to apply.

    Returns:
        True when the row itself carries a specific cue for that role beyond
        the generic weak tier; False means only weak or contextual evidence.
    """
    normalized_phrase = normalize_corrected_role_phrase(text)
    # The direct verdict must agree; neighbor-context inferences are never strong.
    if role_from_corrected_phrase(normalized_phrase) != role:
        return False

    padded_phrase = f" {normalized_phrase} "
    role_cues = _DOCTOR_CUES if role == "DOCTOR" else _PATIENT_CUES
    matched_cues = {cue for cue in role_cues if f" {cue} " in padded_phrase}
    return bool(matched_cues - _WEAK_DIRECT_CUES)


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
