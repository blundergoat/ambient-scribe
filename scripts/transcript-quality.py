"""Score transcript quality against PriMock57 TextGrid ground truth.

Use this after a browser or eval-runner session has stored `history.json`.
It compares the visible transcript against doctor/patient TextGrid channels,
reports word recall/duplication, and scores whether visible role labels match
the speaker who owns most of each segment's timestamp span.

Usage:
  python3 scripts/transcript-quality.py [--quality-json quality.json] \
      [--window-artifact window-continuity.jsonl] \
      [--row-diagnostics-json row-diagnostics.json] \
      <history.json> <cutoff_seconds> <doctor.TextGrid> <patient.TextGrid>

Attribution is reported two ways. Visible/labeled metrics score only rows that
carry a confident DOCTOR/PATIENT label; strict metrics keep uncertain or
UNKNOWN clean rows in the denominator as incorrect, so hiding hard rows behind
uncertainty can never raise the headline number. The speaker oracle is a free
per-ID assignment (both IDs may take one role) and stays diagnostic only; the
best valid dyadic mapping is the shippable ceiling.

Reference numbers from 2026-07-05 (windowed emission, pre-M16):
consultation-02 @31s -> dup 0.0%, recall 83.9%, ratio 0.83;
consultation-03 @70s -> dup 3.0%, recall 77.7%, ratio 0.77.
"""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

SUPPORTED_REFERENCE_ROLES = {"DOCTOR", "PATIENT"}
DEFAULT_SPEAKER_CAP = 2


@dataclass(frozen=True)
class ReferenceInterval:
    """One role-labeled ground-truth speech span.

    TextGrid doctor/patient channels become these intervals before scoring.
    Empty TextGrid intervals are excluded, so each instance means a real
    speaker was talking during that time range.
    """

    start: float
    end: float
    role: str
    text: str


@dataclass(frozen=True)
class HypothesisSegment:
    """One transcript row visible to the clinician.

    These rows come from `history.json` after NeMo, role inference, and any
    manual override have labeled them. Empty role or timing data means the row
    cannot contribute to speaker-attribution accuracy.
    """

    start: float
    end: float
    speaker_id: str
    role: str
    text: str


@dataclass(frozen=True)
class AttributionScore:
    """Segment-level speaker-attribution counts.

    Overall counts include cross-talk regions. Clean counts exclude any
    hypothesis row touching a TextGrid overlap span, which is the user-visible
    number M16 uses to judge diarization improvements.
    """

    scored_segments: int
    correct_segments: int
    clean_scored_segments: int
    clean_correct_segments: int
    overlap_span_seconds: float

    @property
    def overall_accuracy_percent(self) -> float | None:
        """Return overall attribution accuracy for visible role labels.

        Returns:
            Percent correct, or None when no timestamped segments overlap the reference.
        """
        # No scored rows means the session ended before attribution could be judged.
        if self.scored_segments == 0:
            return None
        return self.correct_segments / self.scored_segments * 100

    @property
    def clean_accuracy_percent(self) -> float | None:
        """Return attribution accuracy outside cross-talk spans.

        Returns:
            Percent correct outside overlap; None means all scored rows touched overlap.
        """
        # No clean rows means overlap speech consumed every scoreable segment.
        if self.clean_scored_segments == 0:
            return None
        return self.clean_correct_segments / self.clean_scored_segments * 100


@dataclass(frozen=True)
class StrictAttributionScore:
    """Uncertainty-honest attribution counts for clean (non-overlap) rows.

    The denominator is every clean row whose reference speaker is knowable,
    whether or not the UI labeled it. Rows without a confident DOCTOR/PATIENT
    label count as incorrect in strict accuracy, so marking hard rows
    uncertain lowers coverage instead of inflating the headline metric.
    """

    clean_reference_rows: int
    strict_correct_rows: int
    labeled_rows: int
    labeled_correct_rows: int
    uncertain_rows: int
    incorrect_confident_rows: int

    @property
    def strict_accuracy_percent(self) -> float | None:
        """Return clean-row accuracy where uncertain/UNKNOWN counts as incorrect.

        Returns:
            Percent correct over all clean reference rows; None means no clean rows exist.
        """
        # No clean reference rows means strict attribution cannot be judged.
        if self.clean_reference_rows == 0:
            return None
        return self.strict_correct_rows / self.clean_reference_rows * 100

    @property
    def labeled_accuracy_percent(self) -> float | None:
        """Return accuracy among rows the UI labeled confidently.

        Returns:
            Percent correct over labeled rows; None means no row carried a confident label.
        """
        # No labeled rows means every clean row rendered as uncertain or raw.
        if self.labeled_rows == 0:
            return None
        return self.labeled_correct_rows / self.labeled_rows * 100

    @property
    def uncertainty_coverage_percent(self) -> float | None:
        """Return the share of clean rows shown without a confident role.

        Returns:
            Percent uncertain over clean reference rows; None means no clean rows exist.
        """
        # No clean reference rows means coverage has no denominator.
        if self.clean_reference_rows == 0:
            return None
        return self.uncertain_rows / self.clean_reference_rows * 100

    @property
    def incorrect_confident_rate_percent(self) -> float | None:
        """Return the share of clean rows that were confidently wrong.

        Returns:
            Percent confidently-wrong rows; None means no clean rows exist.
        """
        # No clean reference rows means the misleading-label rate has no denominator.
        if self.clean_reference_rows == 0:
            return None
        return self.incorrect_confident_rows / self.clean_reference_rows * 100


@dataclass(frozen=True)
class EmissionWindowSpan:
    """One emission window's owned slice of the session timeline.

    Parsed from the per-window continuity artifact so row diagnostics can say
    whether a transcript row crossed a window seam, where speaker identity is
    known to drift. A window owns the span between the emission mark before
    and after it ran; zero-width spans emitted no rows.
    """

    window_index: int
    emitted_from_seconds: float
    emitted_until_seconds: float


@dataclass(frozen=True)
class WordErrorScore:
    """Token-level WER counts for one transcript region.

    Use this when a replay needs to show whether the browser missed words,
    added repeats, or substituted different words. Empty references have no
    fair denominator, so their WER percentage is reported as unavailable.
    """

    substitutions: int
    insertions: int
    deletions: int
    reference_words: int
    hypothesis_words: int

    @property
    def total_errors(self) -> int:
        """Return total word errors for the scored region.

        Returns:
            Sum of substitutions, insertions, and deletions; `0` means exact match.
        """
        return self.substitutions + self.insertions + self.deletions

    @property
    def wer_percent(self) -> float | None:
        """Return word error rate as a percentage.

        Returns:
            Percent errors over reference words, or None when no reference words exist.
        """
        # No reference speech means there is no denominator for WER.
        if self.reference_words == 0:
            return None
        return self.total_errors / self.reference_words * 100


@dataclass(frozen=True)
class SegmentationScore:
    """Readability counts for the transcript rows the clinician sees.

    These metrics expose whether the UI is producing many tiny transcript
    cards or short seam repeats. Overlap-clean counts let M17 judge readability
    without blaming unavoidable mixed-mono cross-talk.
    """

    segment_count: int
    fragment_count: int
    clean_segment_count: int
    clean_fragment_count: int
    seam_reread_count: int
    cutoff_seconds: float

    @property
    def segments_per_minute(self) -> float:
        """Return transcript card density for the visit.

        Returns:
            Segments per minute; `0.0` means the visit has no positive duration.
        """
        # A zero cutoff can happen only in empty/manual scorer inputs.
        if self.cutoff_seconds <= 0:
            return 0.0
        return self.segment_count / (self.cutoff_seconds / 60)

    @property
    def fragment_rate_percent(self) -> float | None:
        """Return the share of visible rows shorter than three words.

        Returns:
            Percent fragments, or None when the session produced no rows.
        """
        # No transcript rows means there is no readability denominator.
        if self.segment_count == 0:
            return None
        return self.fragment_count / self.segment_count * 100

    @property
    def clean_fragment_rate_percent(self) -> float | None:
        """Return fragment share outside overlap spans.

        Returns:
            Percent clean-region fragments, or None when every row touched overlap.
        """
        # No clean rows means overlap speech consumed every readability row.
        if self.clean_segment_count == 0:
            return None
        return self.clean_fragment_count / self.clean_segment_count * 100


@dataclass(frozen=True)
class SpeakerPurityBreakdown:
    """Oracle role breakdown for one visible speaker ID.

    Use this when a fixture run needs to explain whether a browser label is
    wrong because diarization mixed voices under one ID, or because role
    mapping chose the wrong label for an otherwise stable speaker.
    """

    speaker_id: str
    best_reference_role: str
    best_reference_count: int
    scored_segments: int
    visible_role_counts: dict[str, int]

    @property
    def purity_percent(self) -> float | None:
        """Return the oracle-best share for this speaker ID.

        Returns:
            Percent purity, or None when this speaker had no scoreable user-visible rows.
        """
        # No rows for this ID means the fixture cannot judge this speaker.
        if self.scored_segments == 0:
            return None
        return self.best_reference_count / self.scored_segments * 100

    @property
    def visible_majority_role(self) -> str:
        """Return the role label most often shown for this speaker ID.

        Returns:
            Visible role name, or `UNKNOWN` when the UI never assigned a supported role.
        """
        # Empty visible counts mean the row stayed on raw speaker labels in the UI.
        if not self.visible_role_counts:
            return "UNKNOWN"
        return max(self.visible_role_counts, key=self.visible_role_counts.get)


@dataclass(frozen=True)
class DyadicMappingCeiling:
    """Best accuracy any one-DOCTOR/one-PATIENT mapping could reach.

    The speaker-oracle metric lets both IDs take the same role, which no real
    consultation mapping can ship, so on heavily mixed audio it overstates how
    much a better role mapping could recover. This ceiling constrains the
    assignment to a valid dyad and is the honest role-mapping target.
    """

    best_mapping: dict[str, str]
    correct_segments: int
    scored_segments: int

    @property
    def accuracy_percent(self) -> float | None:
        """Return the best valid dyadic mapping accuracy.

        Returns:
            Percent correct under the best one-role-each mapping; None means the
            session did not have exactly two scoreable speaker IDs.
        """
        # Without exactly two visible speakers there is no dyadic mapping to rank.
        if self.scored_segments == 0 or not self.best_mapping:
            return None
        return self.correct_segments / self.scored_segments * 100


@dataclass(frozen=True)
class SpeakerPurityScore:
    """Best-possible role attribution if each speaker ID got its oracle role.

    This is the diagnostic split M16 needs after a run: low oracle accuracy
    points at diarization/canonicalization, while a large gap between oracle
    and visible attribution points at role mapping or flip damping.
    """

    scored_segments: int
    oracle_correct_segments: int
    per_speaker: tuple[SpeakerPurityBreakdown, ...]

    @property
    def accuracy_percent(self) -> float | None:
        """Return best-possible role accuracy for the current speaker IDs.

        Returns:
            Percent correct after oracle speaker-to-role mapping; None means no rows were scoreable.
        """
        # No scoreable speaker IDs means the browser did not emit usable speaker rows.
        if self.scored_segments == 0:
            return None
        return self.oracle_correct_segments / self.scored_segments * 100


def tokens(text: str) -> list[str]:
    """Split transcript text into lowercase words for recall/duplication.

    Args:
        text: Transcript or TextGrid text; empty means no words are returned.

    Returns:
        Word tokens used by the quality report; empty means no readable words.
    """
    return re.findall(r"[a-z']+", text.lower())


def word_error_score(
    reference_words: list[str],
    hypothesis_words: list[str],
) -> WordErrorScore:
    """Align two token streams and return WER edit counts.

    Args:
        reference_words: Ground-truth words in spoken order; empty means WER is unavailable.
        hypothesis_words: Visible transcript words in UI order; empty means all reference words were deleted.

    Returns:
        Substitution/insertion/deletion counts used by the M17 quality report.
    """
    width = len(hypothesis_words) + 1
    previous_row: list[tuple[int, int, int, int]] = [
        (column, 0, column, 0) for column in range(width)
    ]

    # Each reference token row considers delete, insert, substitute, or match.
    for reference_index, reference_word in enumerate(reference_words, start=1):
        current_row: list[tuple[int, int, int, int]] = [
            (reference_index, 0, 0, reference_index)
        ]

        # Each hypothesis token column represents the visible words up to that point.
        for hypothesis_index, hypothesis_word in enumerate(hypothesis_words, start=1):
            match_or_substitute = previous_row[hypothesis_index - 1]

            # Matching words carry the previous cost; different words add a substitution.
            if reference_word == hypothesis_word:
                substitution_candidate = match_or_substitute
            else:
                substitution_candidate = _add_error(match_or_substitute, "substitution")

            deletion_candidate = _add_error(previous_row[hypothesis_index], "deletion")
            insertion_candidate = _add_error(current_row[hypothesis_index - 1], "insertion")
            current_row.append(
                min(
                    substitution_candidate,
                    deletion_candidate,
                    insertion_candidate,
                    key=_word_error_sort_key,
                )
            )

        previous_row = current_row

    _distance, substitutions, insertions, deletions = previous_row[-1]
    return WordErrorScore(
        substitutions=substitutions,
        insertions=insertions,
        deletions=deletions,
        reference_words=len(reference_words),
        hypothesis_words=len(hypothesis_words),
    )


def _add_error(
    score_tuple: tuple[int, int, int, int],
    error_kind: str,
) -> tuple[int, int, int, int]:
    """Return a WER dynamic-programming cell with one more edit.

    Args:
        score_tuple: Current `(distance, substitutions, insertions, deletions)` cell.
        error_kind: Edit operation to add for a user-visible word mismatch.

    Returns:
        Updated cell; unsupported kinds are treated as no-op by the caller contract.
    """
    distance, substitutions, insertions, deletions = score_tuple

    # A substitution means the UI showed a different word than the reference.
    if error_kind == "substitution":
        return distance + 1, substitutions + 1, insertions, deletions

    # An insertion means the UI showed an extra word, usually a repeat or hallucination.
    if error_kind == "insertion":
        return distance + 1, substitutions, insertions + 1, deletions

    # A deletion means reference speech never reached the visible transcript.
    if error_kind == "deletion":
        return distance + 1, substitutions, insertions, deletions + 1

    return score_tuple


def _word_error_sort_key(score_tuple: tuple[int, int, int, int]) -> tuple[int, int, int, int]:
    """Rank WER candidates deterministically when edit distances tie.

    Args:
        score_tuple: `(distance, substitutions, insertions, deletions)` cell.

    Returns:
        Sort key that prefers fewer total edits, then fewer deletions in clinical text.
    """
    distance, substitutions, insertions, deletions = score_tuple
    return distance, deletions, substitutions, insertions


def role_from_textgrid_path(path: Path) -> str:
    """Infer DOCTOR or PATIENT from a PriMock57 TextGrid filename.

    Args:
        path: TextGrid path ending in `.doctor.TextGrid` or `.patient.TextGrid`.

    Returns:
        Uppercase role label used by transcript history.

    Raises:
        ValueError: When the filename cannot be mapped to a visible medical role.
    """
    lower_name = path.name.lower()
    # Doctor-channel references are the clinician side of the consultation.
    if ".doctor.textgrid" in lower_name:
        return "DOCTOR"
    # Patient-channel references are the patient side of the consultation.
    if ".patient.textgrid" in lower_name:
        return "PATIENT"
    raise ValueError(f"Cannot infer role from TextGrid filename: {path}")


def textgrid_intervals(path: Path, cutoff: float) -> list[ReferenceInterval]:
    """Parse non-empty reference intervals from one TextGrid file.

    Args:
        path: Praat TextGrid channel file; missing files fail at open time.
        cutoff: Session cutoff in seconds; intervals starting after this are ignored.

    Returns:
        Reference intervals in file order; empty means no speech before cutoff.
    """
    role = role_from_textgrid_path(path)
    content = path.read_text(encoding="utf-8", errors="replace")
    intervals: list[ReferenceInterval] = []

    # Praat interval blocks carry start, end, then quoted transcript text.
    for match in re.finditer(
        r'xmin\s*=\s*([\d.]+)\s*\n\s*xmax\s*=\s*([\d.]+)\s*\n\s*text\s*=\s*"(.*?)"',
        content,
        re.S,
    ):
        start = float(match.group(1))
        end = float(match.group(2))
        text = match.group(3).strip()

        # Empty intervals or future intervals do not describe speech the user heard.
        if text == "" or start > cutoff:
            continue

        intervals.append(
            ReferenceInterval(start=start, end=min(end, cutoff), role=role, text=text)
        )

    return intervals


def textgrid_words(path: Path, cutoff: float) -> list[str]:
    """Return reference words spoken before the session cutoff.

    Args:
        path: Doctor or patient TextGrid path; empty intervals are ignored.
        cutoff: Score only intervals that started before this session time.

    Returns:
        Reference words; empty means this channel has no scored speech.
    """
    words: list[str] = []
    # Each non-empty reference interval contributes to bag-of-words recall.
    for interval in textgrid_intervals(path, cutoff):
        words.extend(tokens(interval.text))
    return words


def history_segments(history: dict[str, Any]) -> list[HypothesisSegment]:
    """Read visible transcript rows from a history payload.

    Args:
        history: JSON object returned by `/session/{id}/history`; missing segments means no transcript.

    Returns:
        Transcript rows with usable defaults; empty means the session produced no visible rows.
    """
    segments: list[HypothesisSegment] = []
    # Each stored segment is one card or appended row the clinician reviewed.
    for raw_segment in history.get("segments", []):
        start = float(raw_segment.get("start", 0.0) or 0.0)
        end = float(raw_segment.get("end", 0.0) or 0.0)
        text = str(raw_segment.get("text", ""))

        segments.append(
            HypothesisSegment(
                start=start,
                end=end,
                speaker_id=str(raw_segment.get("speaker_id", "")),
                role=str(raw_segment.get("role", "")).upper(),
                text=text,
            )
        )

    return segments


def shingle_duplication(words: list[str], n: int = 4) -> float:
    """Return repeated n-gram share inside the hypothesis transcript.

    Args:
        words: Hypothesis words in visible order; empty means no duplication.
        n: Shingle size; `4` targets repeated phrase loops users notice.

    Returns:
        Fraction of repeated shingles; `0.0` means no repeated n-grams.
    """
    # Short transcripts cannot form a repeated shingle of this size.
    if len(words) < n:
        return 0.0
    shingles = [tuple(words[i : i + n]) for i in range(len(words) - n + 1)]
    return 1 - len(set(shingles)) / len(shingles)


def overlap_seconds(start: float, end: float, interval_start: float, interval_end: float) -> float:
    """Return time overlap between two spans.

    Args:
        start: Hypothesis or reference start time in seconds.
        end: Hypothesis or reference end time in seconds.
        interval_start: Other span start time in seconds.
        interval_end: Other span end time in seconds.

    Returns:
        Overlap seconds; `0.0` means the spans do not intersect.
    """
    return max(0.0, min(end, interval_end) - max(start, interval_start))


def reference_role_for_segment(
    segment: HypothesisSegment,
    reference_intervals: list[ReferenceInterval],
) -> str | None:
    """Choose the reference role with majority time overlap for one segment.

    Args:
        segment: Visible transcript row being scored.
        reference_intervals: Doctor/patient speech intervals before the cutoff.

    Returns:
        DOCTOR/PATIENT role, or None when the row has no clear reference speaker.
    """
    overlap_by_role = {role: 0.0 for role in SUPPORTED_REFERENCE_ROLES}

    # Every reference interval can contribute overlap to the visible row.
    for interval in reference_intervals:
        overlap_by_role[interval.role] += overlap_seconds(
            segment.start, segment.end, interval.start, interval.end
        )

    best_role = max(overlap_by_role, key=overlap_by_role.get)
    best_overlap = overlap_by_role[best_role]
    tied_roles = [
        role for role, seconds in overlap_by_role.items() if seconds == best_overlap
    ]

    # No overlap or a tie means the row cannot fairly judge attribution.
    if best_overlap <= 0 or len(tied_roles) > 1:
        return None
    return best_role


def overlap_spans(reference_intervals: list[ReferenceInterval]) -> list[tuple[float, float]]:
    """Return spans where doctor and patient channels talk simultaneously.

    Args:
        reference_intervals: Non-empty doctor/patient intervals before the cutoff.

    Returns:
        Cross-talk spans; empty means every reference span has a single speaker.
    """
    doctor_intervals = [
        interval for interval in reference_intervals if interval.role == "DOCTOR"
    ]
    patient_intervals = [
        interval for interval in reference_intervals if interval.role == "PATIENT"
    ]
    spans: list[tuple[float, float]] = []

    # Pairwise channel intersections are the ambiguous mixed-mono regions.
    for doctor_interval in doctor_intervals:
        for patient_interval in patient_intervals:
            start = max(doctor_interval.start, patient_interval.start)
            end = min(doctor_interval.end, patient_interval.end)

            # Non-positive intersections mean these two intervals do not overlap.
            if end <= start:
                continue

            spans.append((start, end))

    return merge_spans(spans)


def merge_spans(spans: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """Merge adjacent or overlapping time spans.

    Args:
        spans: Time spans in any order; empty means no overlap regions exist.

    Returns:
        Sorted merged spans; empty means there is no cross-talk to exclude.
    """
    # No spans means no overlap policy needs to be applied.
    if not spans:
        return []

    merged: list[tuple[float, float]] = []
    # Sorting lets neighboring overlap intervals collapse into one region.
    for start, end in sorted(spans):
        # First span starts the merged list.
        if not merged:
            merged.append((start, end))
            continue

        previous_start, previous_end = merged[-1]
        # Separated spans remain distinct clean/overlap regions.
        if start > previous_end:
            merged.append((start, end))
            continue

        merged[-1] = (previous_start, max(previous_end, end))

    return merged


def span_touches_any_overlap(
    start: float,
    end: float,
    spans: list[tuple[float, float]],
) -> bool:
    """Return whether a time span touches mixed-mono cross-talk.

    Args:
        start: Start time in seconds for a transcript row or reference interval.
        end: End time in seconds for the same span.
        spans: Overlap spans; empty means the whole visit is clean speech.

    Returns:
        True when the span belongs to the overlap bucket in the M17 report.
    """
    # Each overlap span can make words or segment boundaries ambiguous for users.
    for overlap_start, overlap_end in spans:
        if overlap_seconds(start, end, overlap_start, overlap_end) > 0:
            return True
    return False


def timestamp_in_overlap(timestamp: float, spans: list[tuple[float, float]]) -> bool:
    """Return whether an estimated word time sits inside cross-talk.

    Args:
        timestamp: Estimated word-center timestamp; values outside speech spans are clean.
        spans: Overlap spans; empty means no word belongs to cross-talk.

    Returns:
        True when the word is scored in the overlap WER bucket.
    """
    # Each overlap span marks a period where mixed-mono audio can hide words from users.
    for overlap_start, overlap_end in spans:
        # A token inside cross-talk belongs to the overlap WER bucket.
        if overlap_start <= timestamp < overlap_end:
            return True
    return False


def touches_any_span(segment: HypothesisSegment, spans: list[tuple[float, float]]) -> bool:
    """Return whether a transcript row touches an ambiguous overlap span.

    Args:
        segment: Visible transcript row being scored.
        spans: Cross-talk spans from doctor/patient TextGrids; empty means clean speech.

    Returns:
        True when the row should be excluded from non-overlap attribution accuracy.
    """
    return span_touches_any_overlap(segment.start, segment.end, spans)


def timed_words_for_region(
    timed_text_items: list[ReferenceInterval] | list[HypothesisSegment],
    spans: list[tuple[float, float]],
    *,
    include_overlap: bool,
) -> list[str]:
    """Return text tokens whose estimated centers fall in one WER region.

    Args:
        timed_text_items: Reference intervals or visible transcript rows; empty means no words.
        spans: Cross-talk spans used to split clean vs overlap scoring.
        include_overlap: True keeps overlap intervals; false keeps clean intervals.

    Returns:
        Tokens in spoken/UI order for the requested WER region.
    """
    region_words: list[str] = []

    # Word-level timestamps are unavailable, so centers are spread evenly across each row.
    for item in sorted(timed_text_items, key=lambda value: value.start):
        item_words = tokens(item.text)

        # Empty rows carry no spoken content into the WER alignment.
        if not item_words:
            continue

        duration_seconds = max(0.0, item.end - item.start)
        # Each token gets an estimated center because saved artifacts lack word timings.
        for word_index, word in enumerate(item_words):
            word_position = (word_index + 0.5) / len(item_words)
            estimated_word_time = item.start + (duration_seconds * word_position)

            # Region-specific WER separates clean words from mixed-mono cross-talk words.
            if timestamp_in_overlap(estimated_word_time, spans) == include_overlap:
                region_words.append(word)

    return region_words


def score_segmentation(
    segments: list[HypothesisSegment],
    spans: list[tuple[float, float]],
    cutoff_seconds: float,
) -> SegmentationScore:
    """Score transcript row density, short fragments, and nearby repeats.

    Args:
        segments: Visible transcript rows; empty means no readability rows exist.
        spans: Cross-talk spans; empty means every row is scored as clean speech.
        cutoff_seconds: Visit duration used for segment density; zero means no duration.

    Returns:
        Segmentation metrics for M17 Phase 0 baselines.
    """
    fragment_count = 0
    clean_segment_count = 0
    clean_fragment_count = 0

    # Every visible row is a card or line the clinician must read.
    for segment in segments:
        word_count = len(tokens(segment.text))
        is_fragment = word_count < 3

        # Sub-3-word rows are the opening ping-pong fragments users notice.
        if is_fragment:
            fragment_count += 1

        # Clean rows exclude cross-talk so overlap diarization does not dominate readability.
        if touches_any_span(segment, spans):
            continue

        clean_segment_count += 1
        if is_fragment:
            clean_fragment_count += 1

    return SegmentationScore(
        segment_count=len(segments),
        fragment_count=fragment_count,
        clean_segment_count=clean_segment_count,
        clean_fragment_count=clean_fragment_count,
        seam_reread_count=seam_reread_count(segments),
        cutoff_seconds=cutoff_seconds,
    )


def seam_reread_count(
    segments: list[HypothesisSegment],
    *,
    shingle_size: int = 3,
    max_start_gap_seconds: float = 2.0,
) -> int:
    """Count nearby repeated phrases that look like window-boundary re-reads.

    Args:
        segments: Visible transcript rows in UI order; empty means no seam repeats.
        shingle_size: Phrase length to compare; three words avoids counting common fillers.
        max_start_gap_seconds: Maximum start-time gap for a repeat to count as seam-local.

    Returns:
        Number of repeated shingles near the same timestamp.
    """
    previous_starts_by_shingle: dict[tuple[str, ...], list[float]] = {}
    reread_count = 0

    # Each segment contributes shingles with the row start time users see in history.
    for segment in sorted(segments, key=lambda item: item.start):
        segment_words = tokens(segment.text)

        # Short rows cannot form a seam shingle of the requested size.
        if len(segment_words) < shingle_size:
            continue

        current_segment_shingles: set[tuple[str, ...]] = set()
        # Each phrase in this row can match a phrase from an earlier visible row.
        for start_index in range(len(segment_words) - shingle_size + 1):
            shingle = tuple(segment_words[start_index : start_index + shingle_size])
            current_segment_shingles.add(shingle)
            previous_starts = previous_starts_by_shingle.get(shingle, [])

            # A nearby previous occurrence means the window likely replayed a phrase.
            if any(
                abs(segment.start - previous_start) <= max_start_gap_seconds
                for previous_start in previous_starts
            ):
                reread_count += 1

        # Same-row repetitions are regular transcript text, not a window seam re-read.
        for shingle in current_segment_shingles:
            previous_starts_by_shingle.setdefault(shingle, []).append(segment.start)

    return reread_count


def score_attribution(
    segments: list[HypothesisSegment],
    reference_intervals: list[ReferenceInterval],
) -> AttributionScore:
    """Score visible role labels against reference speaker timing.

    Args:
        segments: Visible transcript rows; empty means no attribution can be scored.
        reference_intervals: Doctor/patient intervals; empty means no reference oracle exists.

    Returns:
        Attribution counts for all rows and clean non-overlap rows.
    """
    spans = overlap_spans(reference_intervals)
    scored_segments = 0
    correct_segments = 0
    clean_scored_segments = 0
    clean_correct_segments = 0

    # Each visible row is scored by its majority-overlap reference speaker.
    for segment in segments:
        expected_role = reference_role_for_segment(segment, reference_intervals)

        # Unlabeled or unaligned rows cannot prove diarization right or wrong.
        if expected_role is None or segment.role not in SUPPORTED_REFERENCE_ROLES:
            continue

        is_correct = segment.role == expected_role
        scored_segments += 1
        correct_segments += int(is_correct)

        # Cross-talk rows are reported overall but excluded from clean accuracy.
        if touches_any_span(segment, spans):
            continue

        clean_scored_segments += 1
        clean_correct_segments += int(is_correct)

    return AttributionScore(
        scored_segments=scored_segments,
        correct_segments=correct_segments,
        clean_scored_segments=clean_scored_segments,
        clean_correct_segments=clean_correct_segments,
        overlap_span_seconds=sum(end - start for start, end in spans),
    )


def score_strict_attribution(
    segments: list[HypothesisSegment],
    reference_intervals: list[ReferenceInterval],
) -> StrictAttributionScore:
    """Score clean rows with uncertain/UNKNOWN counted as incorrect.

    Args:
        segments: Visible transcript rows; empty means nothing can be judged.
        reference_intervals: Doctor/patient intervals; empty means no oracle exists.

    Returns:
        Strict counts whose denominator keeps unlabeled clean rows, so
        uncertainty lowers coverage instead of hiding wrong labels.
    """
    spans = overlap_spans(reference_intervals)
    clean_reference_rows = 0
    strict_correct_rows = 0
    labeled_rows = 0
    labeled_correct_rows = 0
    uncertain_rows = 0
    incorrect_confident_rows = 0

    # Every clean row with a knowable reference speaker stays in the denominator.
    for segment in segments:
        expected_role = reference_role_for_segment(segment, reference_intervals)

        # Rows without a clear reference speaker cannot prove a label right or wrong.
        if expected_role is None:
            continue

        # Cross-talk rows are excluded so overlap ambiguity does not dominate strictness.
        if touches_any_span(segment, spans):
            continue

        clean_reference_rows += 1

        # Unlabeled/UNKNOWN rows are the uncertainty the clinician sees; they count as incorrect.
        if segment.role not in SUPPORTED_REFERENCE_ROLES:
            uncertain_rows += 1
            continue

        labeled_rows += 1
        # A confident label is either correct or actively misleading.
        if segment.role == expected_role:
            labeled_correct_rows += 1
            strict_correct_rows += 1
        else:
            incorrect_confident_rows += 1

    return StrictAttributionScore(
        clean_reference_rows=clean_reference_rows,
        strict_correct_rows=strict_correct_rows,
        labeled_rows=labeled_rows,
        labeled_correct_rows=labeled_correct_rows,
        uncertain_rows=uncertain_rows,
        incorrect_confident_rows=incorrect_confident_rows,
    )


def emission_window_spans(window_artifact_path: Path) -> list[EmissionWindowSpan]:
    """Parse emission-window spans from a window-continuity JSONL artifact.

    Args:
        window_artifact_path: Per-window artifact written by the eval runner;
            missing or empty files mean seam flags stay unknown.

    Returns:
        Windows that emitted at least one row, sorted by their emission span.
    """
    # A missing artifact means the run predates window diagnostics.
    if not window_artifact_path.exists():
        return []

    spans: list[EmissionWindowSpan] = []
    # Each JSONL row may be one emission window's continuity record.
    for line in window_artifact_path.read_text(encoding="utf-8").splitlines():
        # Blank lines keep hand-edited artifacts readable without breaking joins.
        if not line.strip():
            continue

        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue

        # Only window-continuity rows describe emission spans; summaries are skipped.
        if record.get("event") != "nemo_session.window_continuity":
            continue

        emitted_from = float(record.get("emitted_from_seconds", 0.0) or 0.0)
        emitted_until = float(record.get("emitted_until_seconds", 0.0) or 0.0)

        # Zero-width spans emitted no rows and cannot own a transcript row.
        if emitted_until <= emitted_from:
            continue

        spans.append(
            EmissionWindowSpan(
                window_index=int(record.get("window_index", 0) or 0),
                emitted_from_seconds=emitted_from,
                emitted_until_seconds=emitted_until,
            )
        )

    spans.sort(key=lambda span: (span.emitted_from_seconds, span.window_index))
    return spans


def window_span_for_row(
    segment: HypothesisSegment,
    window_spans: list[EmissionWindowSpan],
) -> EmissionWindowSpan | None:
    """Find the emission window that published one transcript row.

    Args:
        segment: Visible transcript row with session timing.
        window_spans: Emission spans from the window artifact; empty means unknown.

    Returns:
        Owning window, or None when the row cannot be joined by time span.
    """
    # Rows are emitted once, so the row's end time falls inside exactly one span.
    for span in window_spans:
        # A row ending inside this window's emission span was published by it.
        if span.emitted_from_seconds < segment.end <= span.emitted_until_seconds + 1e-6:
            return span
    return None


def build_row_diagnostics(
    segments: list[HypothesisSegment],
    reference_intervals: list[ReferenceInterval],
    dyadic_ceiling: DyadicMappingCeiling,
    window_spans: list[EmissionWindowSpan],
) -> list[dict[str, Any]]:
    """Build per-row doctor/patient diagnostics for one scored session.

    Args:
        segments: Visible transcript rows in history order.
        reference_intervals: Doctor/patient intervals; empty means rows are unscoreable.
        dyadic_ceiling: Best valid mapping used for the correctable flag.
        window_spans: Emission windows for seam flags; empty leaves seams unknown.

    Returns:
        One record per visible row explaining what the clinician saw versus the
        reference truth, whether it was confidently wrong, whether a better
        global mapping could have fixed it, and how it relates to window seams.
    """
    # Typical trigger: a clinician reports "the doctor's question at 01:00
    # shows as Patient" - these records answer why, row by row.
    spans = overlap_spans(reference_intervals)
    rows: list[dict[str, Any]] = []
    first_row_seen_by_window: set[int] = set()

    # History order matches the order rows became visible to the clinician.
    for row_index, segment in enumerate(segments):
        expected_role = reference_role_for_segment(segment, reference_intervals)
        is_labeled = segment.role in SUPPORTED_REFERENCE_ROLES
        is_correct: bool | None = None
        # Correctness exists only when both a confident label and a reference role exist.
        if expected_role is not None and is_labeled:
            is_correct = segment.role == expected_role

        mapping_correctable: bool | None = None
        # The correctable flag asks whether the best valid global mapping fixes this row.
        if expected_role is not None and dyadic_ceiling.best_mapping:
            mapping_correctable = (
                dyadic_ceiling.best_mapping.get(segment.speaker_id) == expected_role
            )

        owning_window = window_span_for_row(segment, window_spans)
        crosses_window_seam: bool | None = None
        first_in_window: bool | None = None
        # Seam flags require the per-window artifact; without it they stay unknown.
        if window_spans:
            # A seam-crossing row started in audio an earlier window already
            # emitted - exactly where speaker identity is known to drift.
            crosses_window_seam = (
                owning_window is not None
                and segment.start < owning_window.emitted_from_seconds - 1e-3
            )
            # The first row of a window is where a whole-window identity swap
            # would first become visible to the clinician.
            if owning_window is not None:
                first_in_window = owning_window.window_index not in first_row_seen_by_window
                first_row_seen_by_window.add(owning_window.window_index)

        rows.append(
            {
                "row_index": row_index,
                "start": segment.start,
                "end": segment.end,
                "speaker_id": segment.speaker_id,
                "visible_role": segment.role if segment.role else "UNKNOWN",
                "expected_role": expected_role,
                "in_overlap": touches_any_span(segment, spans),
                "labeled": is_labeled,
                "correct": is_correct,
                "confidently_wrong": bool(is_labeled and is_correct is False),
                "mapping_correctable": mapping_correctable,
                "window_index": (
                    owning_window.window_index if owning_window is not None else None
                ),
                "crosses_window_seam": crosses_window_seam,
                "first_in_window": first_in_window,
            }
        )

    return rows


def write_row_diagnostics_json(
    output_path: Path,
    rows: list[dict[str, Any]],
    strict: StrictAttributionScore,
    history_path: Path,
    cutoff_seconds: float,
    window_artifact_path: Path | None,
) -> None:
    """Write the compact per-row diagnostics artifact next to the text report.

    Args:
        output_path: Destination JSON path chosen by the eval runner or developer.
        rows: Per-row diagnostic records from `build_row_diagnostics`.
        strict: Strict attribution counts summarized alongside the rows.
        history_path: Scored history file recorded for later joins.
        cutoff_seconds: Session cutoff recorded for later joins.
        window_artifact_path: Window artifact used for seam flags; None means seams unknown.
    """
    artifact = {
        "schema_version": 1,
        "history_path": str(history_path),
        "cutoff_seconds": cutoff_seconds,
        "window_artifact_path": (
            str(window_artifact_path) if window_artifact_path is not None else None
        ),
        "summary": {
            "clean_reference_rows": strict.clean_reference_rows,
            "strict_correct_rows": strict.strict_correct_rows,
            "labeled_rows": strict.labeled_rows,
            "labeled_correct_rows": strict.labeled_correct_rows,
            "uncertain_rows": strict.uncertain_rows,
            "incorrect_confident_rows": strict.incorrect_confident_rows,
        },
        "rows": rows,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(artifact, sort_keys=True, indent=None) + "\n", encoding="utf-8"
    )


def score_speaker_purity(
    segments: list[HypothesisSegment],
    reference_intervals: list[ReferenceInterval],
    *,
    clean_only: bool = False,
) -> SpeakerPurityScore:
    """Score how cleanly each visible speaker ID maps to one reference role.

    Args:
        segments: Visible transcript rows; empty means no speaker IDs can be diagnosed.
        reference_intervals: Doctor/patient intervals; empty means no reference oracle exists.
        clean_only: True excludes cross-talk rows; false keeps every scoreable row.

    Returns:
        Oracle-best score and per-speaker details for deciding the next M16 fix.
    """
    spans = overlap_spans(reference_intervals)
    reference_counts_by_speaker: dict[str, dict[str, int]] = {}
    visible_counts_by_speaker: dict[str, dict[str, int]] = {}

    # Each visible row teaches which reference role this emitted speaker ID carried.
    for segment in segments:
        speaker_id = segment.speaker_id.strip()
        expected_role = reference_role_for_segment(segment, reference_intervals)

        # Missing speaker or reference role means the UI row cannot diagnose identity stability.
        if speaker_id == "" or expected_role is None:
            continue

        # Clean-only mode removes cross-talk so mixed-mono overlap does not dominate the diagnosis.
        if clean_only and touches_any_span(segment, spans):
            continue

        reference_counts = reference_counts_by_speaker.setdefault(
            speaker_id, {role: 0 for role in SUPPORTED_REFERENCE_ROLES}
        )
        reference_counts[expected_role] += 1

        # Unsupported or empty roles still count against visible attribution, not diarization purity.
        if segment.role in SUPPORTED_REFERENCE_ROLES:
            visible_counts = visible_counts_by_speaker.setdefault(speaker_id, {})
            visible_counts[segment.role] = visible_counts.get(segment.role, 0) + 1

    per_speaker: list[SpeakerPurityBreakdown] = []
    # Each speaker ID gets the reference role it carried most often as an oracle mapping.
    for speaker_id, reference_counts in sorted(reference_counts_by_speaker.items()):
        best_reference_role = max(reference_counts, key=reference_counts.get)
        best_reference_count = reference_counts[best_reference_role]
        scored_segments = sum(reference_counts.values())
        per_speaker.append(
            SpeakerPurityBreakdown(
                speaker_id=speaker_id,
                best_reference_role=best_reference_role,
                best_reference_count=best_reference_count,
                scored_segments=scored_segments,
                visible_role_counts=visible_counts_by_speaker.get(speaker_id, {}),
            )
        )

    return SpeakerPurityScore(
        scored_segments=sum(item.scored_segments for item in per_speaker),
        oracle_correct_segments=sum(item.best_reference_count for item in per_speaker),
        per_speaker=tuple(per_speaker),
    )


def score_best_dyadic_mapping(
    segments: list[HypothesisSegment],
    reference_intervals: list[ReferenceInterval],
) -> DyadicMappingCeiling:
    """Score the best valid one-DOCTOR/one-PATIENT mapping on clean rows.

    Args:
        segments: Visible transcript rows; empty means no mapping can be ranked.
        reference_intervals: Doctor/patient intervals; empty means no oracle exists.

    Returns:
        Ceiling for the two visible speaker IDs; an empty best mapping means the
        session did not have exactly two scoreable IDs outside overlap spans.
    """
    spans = overlap_spans(reference_intervals)
    reference_counts_by_speaker: dict[str, dict[str, int]] = {}

    # Only clean rows with a clear reference speaker can rank a dyadic mapping fairly.
    for segment in segments:
        speaker_id = segment.speaker_id.strip()
        expected_role = reference_role_for_segment(segment, reference_intervals)

        if speaker_id == "" or expected_role is None:
            continue
        if touches_any_span(segment, spans):
            continue

        reference_counts = reference_counts_by_speaker.setdefault(
            speaker_id, {role: 0 for role in sorted(SUPPORTED_REFERENCE_ROLES)}
        )
        reference_counts[expected_role] += 1

    speaker_ids = sorted(reference_counts_by_speaker)
    # The dyadic ceiling only exists for exactly two visible consultation voices.
    if len(speaker_ids) != 2:
        return DyadicMappingCeiling(best_mapping={}, correct_segments=0, scored_segments=0)

    scored_segments = sum(
        sum(counts.values()) for counts in reference_counts_by_speaker.values()
    )
    first_id, second_id = speaker_ids
    best_mapping: dict[str, str] = {}
    best_correct = -1

    # Two one-role-each assignments exist for a dyad; keep the stronger one.
    for candidate in (
        {first_id: "DOCTOR", second_id: "PATIENT"},
        {first_id: "PATIENT", second_id: "DOCTOR"},
    ):
        correct = sum(
            reference_counts_by_speaker[speaker_id][role]
            for speaker_id, role in candidate.items()
        )
        if correct > best_correct:
            best_correct = correct
            best_mapping = candidate

    return DyadicMappingCeiling(
        best_mapping=best_mapping,
        correct_segments=best_correct,
        scored_segments=scored_segments,
    )


def phantom_speaker_count(segments: list[HypothesisSegment], cap: int = DEFAULT_SPEAKER_CAP) -> int:
    """Count visible speaker IDs beyond the expected dyadic cap.

    Args:
        segments: Visible transcript rows; empty means no speakers were emitted.
        cap: Expected maximum visible speaker identities for this fixture set.

    Returns:
        Number of extra distinct speaker IDs; `0` means no phantom speakers reached history.
    """
    speaker_ids = {
        segment.speaker_id for segment in segments if segment.speaker_id.strip()
    }
    return max(0, len(speaker_ids) - cap)


def quality_flip_counts(quality_path: Path | None) -> tuple[int | None, int | None]:
    """Read role flip counts from an optional session quality record.

    Args:
        quality_path: JSON quality path from `eval-fixtures.sh`; None means manual scoring.

    Returns:
        `(accepted, suppressed)` counts; None values mean no quality record was provided.
    """
    # Manual history scoring may not have a saved quality record yet.
    if quality_path is None:
        return None, None

    quality = json.loads(quality_path.read_text(encoding="utf-8"))
    return (
        int(quality.get("role_flips_accepted", 0)),
        int(quality.get("role_flips_suppressed", 0)),
    )


def percent_or_na(value: float | None) -> str:
    """Format a percentage for human-readable scorer output.

    Args:
        value: Percent value; None means no fair denominator exists.

    Returns:
        Formatted percentage or `n/a`.
    """
    # Missing denominators should be visible instead of printing a misleading zero.
    if value is None:
        return "n/a"
    return f"{value:.1f}%"


def count_or_na(value: int | None) -> str:
    """Format an optional integer count.

    Args:
        value: Count value; None means the source artifact was not provided.

    Returns:
        Count text or `n/a`.
    """
    # Manual runs without quality JSON cannot know role flip counts.
    if value is None:
        return "n/a"
    return str(value)


def points_or_na(value: float | None) -> str:
    """Format an optional percentage-point diagnostic gap.

    Args:
        value: Difference in percentage points; None means one side was not scoreable.

    Returns:
        Signed point value, or `n/a` when the diagnostic gap cannot be computed.
    """
    # Missing attribution or oracle denominators make the mapping gap unknowable.
    if value is None:
        return "n/a"
    return f"{value:+.1f}pp"


def dyadic_mapping_detail(ceiling: DyadicMappingCeiling) -> str:
    """Format the best valid dyadic mapping for the score report.

    Args:
        ceiling: Best-mapping result; an empty mapping means no dyad was scoreable.

    Returns:
        Compact `speaker->ROLE` pairs, or `n/a` when no dyadic mapping exists.
    """
    # No dyad means the session had fewer or more than two scoreable voices.
    if not ceiling.best_mapping:
        return "n/a"
    return " ".join(
        f"{speaker_id}->{role}"
        for speaker_id, role in sorted(ceiling.best_mapping.items())
    )


def word_error_detail(score: WordErrorScore) -> str:
    """Format WER edit counts for the score report.

    Args:
        score: WER score; zero reference words means WER is unavailable.

    Returns:
        Compact edit-count detail used by the eval runner parser.
    """
    return (
        f"S/I/D={score.substitutions}/{score.insertions}/{score.deletions}, "
        f"ref={score.reference_words}, hyp={score.hypothesis_words}"
    )


def speaker_purity_detail(score: SpeakerPurityScore) -> str:
    """Format per-speaker oracle details for a fixture score report.

    Args:
        score: Speaker purity result; empty means no speaker IDs were scoreable.

    Returns:
        Compact speaker detail line, or `n/a` when no diagnostic rows exist.
    """
    # Empty speaker details mean the session produced no timestamped speaker rows.
    if not score.per_speaker:
        return "n/a"

    details: list[str] = []
    # Each detail shows the oracle role and the role users mostly saw for that speaker.
    for item in score.per_speaker:
        details.append(
            f"{item.speaker_id}->{item.best_reference_role} "
            f"{item.best_reference_count}/{item.scored_segments} "
            f"visible={item.visible_majority_role}"
        )
    return "; ".join(details)


def parse_args() -> argparse.Namespace:
    """Parse CLI arguments for one scored session.

    Returns:
        Namespace containing history path, cutoff, TextGrid paths, and optional quality JSON.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--quality-json", type=Path, default=None)
    parser.add_argument("--window-artifact", type=Path, default=None)
    parser.add_argument("--row-diagnostics-json", type=Path, default=None)
    parser.add_argument("history_json", type=Path)
    parser.add_argument("cutoff_seconds", type=float)
    parser.add_argument("textgrids", nargs="+", type=Path)
    return parser.parse_args()


def main() -> int:
    """Run the transcript-quality scorer and print one human-readable report.

    Returns:
        Process exit code; `0` means the report was produced.
    """
    args = parse_args()
    history = json.loads(args.history_json.read_text(encoding="utf-8"))
    segments = history_segments(history)
    hyp_words: list[str] = []

    # Every visible row contributes text to word-level quality metrics.
    for segment in segments:
        hyp_words.extend(tokens(segment.text))

    reference_intervals: list[ReferenceInterval] = []
    ref_words: list[str] = []
    # Each TextGrid channel contributes both words and speaker-time intervals.
    for grid_path in args.textgrids:
        reference_intervals.extend(textgrid_intervals(grid_path, args.cutoff_seconds))
        ref_words.extend(textgrid_words(grid_path, args.cutoff_seconds))

    hyp_set, ref_set = set(hyp_words), set(ref_words)
    recall = len(hyp_set & ref_set) / len(ref_set) if ref_set else 0.0
    attribution = score_attribution(segments, reference_intervals)
    spans = overlap_spans(reference_intervals)
    overall_word_errors = word_error_score(ref_words, hyp_words)
    clean_word_errors = word_error_score(
        timed_words_for_region(reference_intervals, spans, include_overlap=False),
        timed_words_for_region(segments, spans, include_overlap=False),
    )
    overlap_word_errors = word_error_score(
        timed_words_for_region(reference_intervals, spans, include_overlap=True),
        timed_words_for_region(segments, spans, include_overlap=True),
    )
    segmentation = score_segmentation(segments, spans, args.cutoff_seconds)
    speaker_purity = score_speaker_purity(segments, reference_intervals)
    clean_speaker_purity = score_speaker_purity(
        segments, reference_intervals, clean_only=True
    )
    accepted_flips, suppressed_flips = quality_flip_counts(args.quality_json)
    role_mapping_gap = None
    # The gap tells whether wrong visible labels remain after an oracle speaker-role assignment.
    if (
        attribution.clean_accuracy_percent is not None
        and clean_speaker_purity.accuracy_percent is not None
    ):
        role_mapping_gap = (
            clean_speaker_purity.accuracy_percent - attribution.clean_accuracy_percent
        )

    dyadic_ceiling = score_best_dyadic_mapping(segments, reference_intervals)
    role_mapping_headroom = None
    # Headroom is what a perfect valid mapping could still recover; the oracle gap
    # above can exceed it when diarization mixes one voice across both IDs.
    if (
        attribution.clean_accuracy_percent is not None
        and dyadic_ceiling.accuracy_percent is not None
    ):
        role_mapping_headroom = (
            dyadic_ceiling.accuracy_percent - attribution.clean_accuracy_percent
        )

    strict_attribution = score_strict_attribution(segments, reference_intervals)
    window_spans = (
        emission_window_spans(args.window_artifact)
        if args.window_artifact is not None
        else []
    )

    print(f"hypothesis words: {len(hyp_words)}  (unique {len(hyp_set)})")
    print(
        f"reference words (to {args.cutoff_seconds:.0f}s): "
        f"{len(ref_words)}  (unique {len(ref_set)})"
    )
    print(f"4-gram duplication in hypothesis: {shingle_duplication(hyp_words):.1%}")
    print(f"reference vocabulary recall: {recall:.1%}")
    print(
        f"length ratio hyp/ref: {len(hyp_words) / len(ref_words):.2f}"
        if ref_words
        else ""
    )
    print(
        "word error rate: "
        f"{percent_or_na(overall_word_errors.wer_percent)} "
        f"({word_error_detail(overall_word_errors)})"
    )
    print(
        "word error rate (non-overlap): "
        f"{percent_or_na(clean_word_errors.wer_percent)} "
        f"({word_error_detail(clean_word_errors)})"
    )
    print(
        "word error rate (overlap): "
        f"{percent_or_na(overlap_word_errors.wer_percent)} "
        f"({word_error_detail(overlap_word_errors)})"
    )
    print(
        "segments per minute: "
        f"{segmentation.segments_per_minute:.1f} "
        f"(segments={segmentation.segment_count})"
    )
    print(
        "fragment rate: "
        f"{percent_or_na(segmentation.fragment_rate_percent)} "
        f"({segmentation.fragment_count}/{segmentation.segment_count})"
    )
    print(
        "fragment rate (non-overlap): "
        f"{percent_or_na(segmentation.clean_fragment_rate_percent)} "
        f"({segmentation.clean_fragment_count}/{segmentation.clean_segment_count})"
    )
    print(f"seam re-read count: {segmentation.seam_reread_count}")
    print(
        "speaker attribution accuracy: "
        f"{percent_or_na(attribution.overall_accuracy_percent)} "
        f"({attribution.correct_segments}/{attribution.scored_segments})"
    )
    print(
        "speaker attribution accuracy (non-overlap): "
        f"{percent_or_na(attribution.clean_accuracy_percent)} "
        f"({attribution.clean_correct_segments}/{attribution.clean_scored_segments})"
    )
    print(
        "strict attribution (non-overlap): "
        f"{percent_or_na(strict_attribution.strict_accuracy_percent)} "
        f"({strict_attribution.strict_correct_rows}/"
        f"{strict_attribution.clean_reference_rows})"
    )
    print(
        "labeled-row accuracy (non-overlap): "
        f"{percent_or_na(strict_attribution.labeled_accuracy_percent)} "
        f"({strict_attribution.labeled_correct_rows}/{strict_attribution.labeled_rows})"
    )
    print(
        "uncertainty coverage (non-overlap): "
        f"{percent_or_na(strict_attribution.uncertainty_coverage_percent)} "
        f"({strict_attribution.uncertain_rows}/"
        f"{strict_attribution.clean_reference_rows})"
    )
    print(
        "incorrect-confident rate (non-overlap): "
        f"{percent_or_na(strict_attribution.incorrect_confident_rate_percent)} "
        f"({strict_attribution.incorrect_confident_rows}/"
        f"{strict_attribution.clean_reference_rows})"
    )
    print(
        "speaker oracle accuracy: "
        f"{percent_or_na(speaker_purity.accuracy_percent)} "
        f"({speaker_purity.oracle_correct_segments}/{speaker_purity.scored_segments}) "
        "[free oracle; diagnostic only]"
    )
    print(
        "speaker oracle accuracy (non-overlap): "
        f"{percent_or_na(clean_speaker_purity.accuracy_percent)} "
        f"({clean_speaker_purity.oracle_correct_segments}/"
        f"{clean_speaker_purity.scored_segments}) "
        "[free oracle; diagnostic only]"
    )
    print(f"role mapping gap (non-overlap): {points_or_na(role_mapping_gap)}")
    print(
        "best dyadic mapping accuracy (non-overlap): "
        f"{percent_or_na(dyadic_ceiling.accuracy_percent)} "
        f"({dyadic_ceiling.correct_segments}/{dyadic_ceiling.scored_segments})"
    )
    print(f"best dyadic mapping: {dyadic_mapping_detail(dyadic_ceiling)}")
    print(
        "role mapping headroom (non-overlap): "
        f"{points_or_na(role_mapping_headroom)}"
    )
    print(
        "speaker purity by ID (non-overlap): "
        f"{speaker_purity_detail(clean_speaker_purity)}"
    )
    print(f"overlap-span seconds: {attribution.overlap_span_seconds:.1f}")
    print(f"phantom speaker count: {phantom_speaker_count(segments)}")
    print(f"role flips accepted: {count_or_na(accepted_flips)}")
    print(f"role flips suppressed: {count_or_na(suppressed_flips)}")

    # The row artifact lets a developer trace one wrong UI row without rescoring.
    if args.row_diagnostics_json is not None:
        diagnostic_rows = build_row_diagnostics(
            segments, reference_intervals, dyadic_ceiling, window_spans
        )
        write_row_diagnostics_json(
            args.row_diagnostics_json,
            diagnostic_rows,
            strict_attribution,
            args.history_json,
            args.cutoff_seconds,
            args.window_artifact,
        )
        seam_source = "window artifact" if window_spans else "no window artifact"
        print(
            f"row diagnostics: {len(diagnostic_rows)} rows -> "
            f"{args.row_diagnostics_json} ({seam_source})"
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
