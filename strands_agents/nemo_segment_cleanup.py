"""
Transcript segment cleanup before rows reach the browser.

`TranscriptionSession` owns audio windows; this module owns the small text rules that make already-decoded rows readable.

It is purely cosmetic from the transcript's point of view: it adjusts server-side `Segment` objects before publishing, so
the clinician reads whole turns instead of one-word fragments, and the browser payload shape never changes.
"""

from __future__ import annotations

import re
from dataclasses import replace

from nemo_confidence import transcript_row_confidence
from nemo_pipeline import Segment

_SENTENCE_JOIN_PATTERN = re.compile(r"(?<=[a-z0-9][.!?])(?=[A-Z])")
_SPACE_BEFORE_PUNCTUATION_PATTERN = re.compile(r"\s+([,.;:!?])")
_REPEATED_SPACE_PATTERN = re.compile(r"[ \t]{2,}")

# Rows below this word count read like fragments in the transcript UI.
_FRAGMENT_WORD_LIMIT = 3
# Very short timed rows also read as fragments, even when ASR attached enough words to clear the count above.
_FRAGMENT_DURATION_SECONDS = 1.0
# Same-speaker fragments this close together were one continuous turn from the clinician's perspective.
_FRAGMENT_MERGE_GAP_SECONDS = 0.4


def shift_segment_to_session_time(
    segment: Segment,
    window_start_seconds: float,
) -> Segment:
    """Move one NeMo window row onto the full visit timeline.

    NeMo times each row from the start of its own audio window, so this is what lets a row taken twenty minutes into a
    consultation report where it actually sits in the visit.

    Args:
        segment: Window-local row from NeMo; empty text still keeps timing.
        window_start_seconds: Absolute window start in the user's session.

    Returns:
        Segment with absolute row timing for UI emission decisions.
    """
    return replace(
        segment,
        start=segment.start + window_start_seconds,
        end=segment.end + window_start_seconds,
    )


def merge_adjacent_fragments_for_display(segments: list[Segment]) -> list[Segment]:
    """Clean and merge tiny same-speaker rows before the transcript UI.

    Use on each batch of stable rows, so a sentence split across two decode windows arrives as one line the clinician can read.

    Args:
        segments: Fresh stable rows for this browser update; empty means no visible text.

    Returns:
        Rows with adjacent same-speaker fragments combined into readable turns; empty in, empty out.
    """
    merged_segments: list[Segment] = []

    # Each stable row is considered in the order the clinician will read it, so merges never reorder the conversation.
    for segment in segments:
        segment = _segment_with_readable_text(segment)

        # The first row of an update has nothing before it to merge into, so it simply starts the visible batch.
        if not merged_segments:
            merged_segments.append(segment)
            continue

        previous_segment = merged_segments[-1]
        # A same-speaker fragment pair reads as one turn, so it is folded into the previous row rather than shown twice.
        if _should_merge_visible_fragment(previous_segment, segment):
            merged_segments[-1] = _combine_visible_segments(previous_segment, segment)
            continue

        merged_segments.append(segment)

    return merged_segments


def _segment_with_readable_text(segment: Segment) -> Segment:
    """Return one transcript row with UI-readable text spacing.

    Args:
        segment: Transcript row from NeMo; empty text means no visible words to clean.

    Returns:
        Segment with display text cleaned, or the original row when no change is needed.
    """
    readable_text = _readable_transcript_text(segment.text)

    # Unchanged text keeps the original row object, which is the normal path for an already-clean transcript.
    if readable_text == segment.text:
        return segment

    return replace(segment, text=readable_text)


def _readable_transcript_text(text: str) -> str:
    """Add missing sentence spacing for one visible transcript row.

    Args:
        text: ASR text for a browser row; empty or blank means no words should be shown.

    Returns:
        Plain transcript text; empty means the UI should not show readable content for this row.
    """
    # Blank ASR output means there is no sentence text for the clinician to read, so no spacing rules apply.
    if text.strip() == "":
        return ""

    # Example: the doctor said "headache started. My other symptom" and NeMo returned it as "started.My".
    readable_text = _SENTENCE_JOIN_PATTERN.sub(" ", text)
    readable_text = _SPACE_BEFORE_PUNCTUATION_PATTERN.sub(r"\1", readable_text)
    readable_text = _REPEATED_SPACE_PATTERN.sub(" ", readable_text)

    return readable_text.strip()


def _should_merge_visible_fragment(
    previous_segment: Segment,
    current_segment: Segment,
) -> bool:
    """Return whether two neighbouring rows should become one UI turn.

    Args:
        previous_segment: Earlier visible row; empty text should stay separate.
        current_segment: Next visible row; empty text should stay separate.

    Returns:
        True when merging improves readability without crossing speakers or turns.
    """
    # Different speakers must stay separate rows, or the merged line would attribute one person's words to the other.
    if previous_segment.speaker_id != current_segment.speaker_id:
        return False

    # Empty text has no readable fragment to merge, and merging would silently drop the empty row's timing.
    if previous_segment.text.strip() == "" or current_segment.text.strip() == "":
        return False

    turn_gap_seconds = _segment_gap_seconds(previous_segment, current_segment)
    # A long pause means the clinician heard two separate turns, even when the same person spoke both.
    if turn_gap_seconds > _FRAGMENT_MERGE_GAP_SECONDS:
        return False

    return _is_visible_fragment(previous_segment) or _is_visible_fragment(
        current_segment
    )


def _is_visible_fragment(segment: Segment) -> bool:
    """Return whether one transcript row is too small to stand alone.

    Args:
        segment: Transcript row about to be shown; empty text is a fragment.

    Returns:
        True when the row reads as a short fragment in the browser rather than a turn.
    """
    word_count = len(segment.text.split())
    duration_seconds = max(0.0, segment.end - segment.start)
    return (
        word_count < _FRAGMENT_WORD_LIMIT
        or duration_seconds < _FRAGMENT_DURATION_SECONDS
    )


def _combine_visible_segments(
    previous_segment: Segment,
    current_segment: Segment,
) -> Segment:
    """Combine two same-speaker rows into one transcript row.

    Args:
        previous_segment: Earlier row that keeps the stable speaker ID.
        current_segment: Later row whose text and end time join the prior row.

    Returns:
        One row covering both spans; text stays in the order the user heard it.
    """
    merged_text = _readable_transcript_text(
        f"{previous_segment.text.strip()} {current_segment.text.strip()}".strip()
    )
    return replace(
        previous_segment,
        start=min(previous_segment.start, current_segment.start),
        end=max(previous_segment.end, current_segment.end),
        text=merged_text,
        is_interim=previous_segment.is_interim or current_segment.is_interim,
        # The combined turn is only as clearly heard as its weakest part, so the lower confidence wins.
        confidence=transcript_row_confidence(
            [previous_segment.confidence, current_segment.confidence]
        ),
    )


def _segment_gap_seconds(first_segment: Segment, second_segment: Segment) -> float:
    """Return the silent gap between two transcript rows.

    Args:
        first_segment: One transcript row in the browser timeline.
        second_segment: Neighbouring transcript row in the browser timeline.

    Returns:
        Seconds between rows; zero means they overlap or touch in the visible timeline, so no pause was heard.
    """
    # The first row ending before the second is the ordinary case: the gap is a forward pause the clinician heard.
    if first_segment.end < second_segment.start:
        return second_segment.start - first_segment.end

    # The reversed order is measured the same way, so the caller gets a real pause length whichever row came first.
    if second_segment.end < first_segment.start:
        return first_segment.start - second_segment.end

    # Overlapping or touching rows leave no audible pause, so they stay eligible to merge into one turn.
    return 0.0
