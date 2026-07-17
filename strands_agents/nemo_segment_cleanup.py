"""
Transcript segment cleanup before rows reach the browser.

`TranscriptionSession` owns audio windows, while this module owns small text
cleanup rules for already-decoded rows. The functions keep browser payloads
unchanged: they only adjust server-side `Segment` objects before publishing.
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
# Very short timed rows also read as fragments even when ASR attached enough words.
_FRAGMENT_DURATION_SECONDS = 1.0
# Same-speaker fragments this close together are one turn from the user's perspective.
_FRAGMENT_MERGE_GAP_SECONDS = 0.4


def shift_segment_to_session_time(
    segment: Segment,
    window_start_seconds: float,
) -> Segment:
    """Move one NeMo window row onto the full visit timeline.

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

    Args:
        segments: Fresh stable rows for this browser update; empty means no visible text.

    Returns:
        Rows with adjacent same-speaker fragments combined into readable turns.
    """
    merged_segments: list[Segment] = []

    # Each stable row is considered in the order the clinician will read it.
    for segment in segments:
        segment = _segment_with_readable_text(segment)

        # The first row starts the visible update.
        if not merged_segments:
            merged_segments.append(segment)
            continue

        previous_segment = merged_segments[-1]
        # A same-speaker fragment pair is one readable turn for the user.
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

    # Unchanged text keeps the original row object for the normal clean-transcript path.
    if readable_text == segment.text:
        return segment

    return replace(segment, text=readable_text)


def _readable_transcript_text(text: str) -> str:
    """Add missing sentence spacing for one visible transcript row.

    Args:
        text: ASR text for a browser row; empty/blank means no words should be shown.

    Returns:
        Plain transcript text; empty means the UI should not show readable content.
    """
    # Blank ASR output means there is no sentence text for the clinician to read.
    if text.strip() == "":
        return ""

    # e.g. the doctor said "headache started" and NeMo returned "started.My".
    readable_text = _SENTENCE_JOIN_PATTERN.sub(" ", text)
    readable_text = _SPACE_BEFORE_PUNCTUATION_PATTERN.sub(r"\1", readable_text)
    readable_text = _REPEATED_SPACE_PATTERN.sub(" ", readable_text)

    return readable_text.strip()


def _should_merge_visible_fragment(
    previous_segment: Segment,
    current_segment: Segment,
) -> bool:
    """Return whether two neighboring rows should become one UI turn.

    Args:
        previous_segment: Earlier visible row; empty text should stay separate.
        current_segment: Next visible row; empty text should stay separate.

    Returns:
        True when merging improves readability without crossing speakers or turns.
    """
    # Different speakers must stay separate so role labels remain truthful.
    if previous_segment.speaker_id != current_segment.speaker_id:
        return False

    # Empty text has no readable fragment to merge into the browser transcript.
    if previous_segment.text.strip() == "" or current_segment.text.strip() == "":
        return False

    turn_gap_seconds = _segment_gap_seconds(previous_segment, current_segment)
    # A large pause means the user heard two turns, even from the same speaker.
    if turn_gap_seconds > _FRAGMENT_MERGE_GAP_SECONDS:
        return False

    return _is_visible_fragment(previous_segment) or _is_visible_fragment(current_segment)


def _is_visible_fragment(segment: Segment) -> bool:
    """Return whether one transcript row is too small to stand alone.

    Args:
        segment: Transcript row about to be shown; empty text is a fragment.

    Returns:
        True when the row reads as a short fragment in the browser.
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
        # The combined turn is only as clearly heard as its weakest part.
        confidence=transcript_row_confidence(
            [previous_segment.confidence, current_segment.confidence]
        ),
    )


def _segment_gap_seconds(first_segment: Segment, second_segment: Segment) -> float:
    """Return the silent gap between two transcript rows.

    Args:
        first_segment: One transcript row in the browser timeline.
        second_segment: Neighboring transcript row in the browser timeline.

    Returns:
        Seconds between rows; zero means they overlap or touch in the visible timeline.
    """
    # First row ending before the second creates a forward pause for the user.
    if first_segment.end < second_segment.start:
        return second_segment.start - first_segment.end

    # Second row ending before the first creates the same pause in reverse.
    if second_segment.end < first_segment.start:
        return first_segment.start - second_segment.end

    return 0.0
