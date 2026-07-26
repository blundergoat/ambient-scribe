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
import hashlib
import json
import re
from collections import Counter
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

SUPPORTED_REFERENCE_ROLES = {"DOCTOR", "PATIENT"}
DEFAULT_SPEAKER_CAP = 2
GARBLE_REVIEW_SIMILARITY_FLOOR = 0.70


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
            insertion_candidate = _add_error(
                current_row[hypothesis_index - 1], "insertion"
            )
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


def words_missing_from_clinician_transcript(
    reference_words: list[str],
    hypothesis_words: list[str],
) -> list[str]:
    """Return reference words absent from the clinician-visible transcript.

    Args:
        reference_words: Official words in order; empty means there is no omission denominator.
        hypothesis_words: Visible words in order; empty means every reference word is missing.

    Returns:
        Missing words in official order; empty means every reference occurrence is present.
    """
    remaining_visible_words = Counter(hypothesis_words)
    missing_reference_words: list[str] = []

    # Each official occurrence must have its own matching word in the visible transcript.
    for reference_word in reference_words:
        # A remaining visible occurrence satisfies this one official word.
        if remaining_visible_words[reference_word] > 0:
            remaining_visible_words[reference_word] -= 1
            continue
        missing_reference_words.append(reference_word)

    return missing_reference_words


def surplus_words_in_clinician_transcript(
    reference_words: list[str],
    hypothesis_words: list[str],
) -> list[str]:
    """Return visible words not licensed by the official transcript count.

    Args:
        reference_words: Official words in order; empty means every visible word is surplus.
        hypothesis_words: Visible words in order; empty means no insertion or duplicate exists.

    Returns:
        Surplus words in display order for insertion and duplicate classification.
    """
    remaining_official_words = Counter(reference_words)
    surplus_visible_words: list[str] = []

    # Each visible occurrence consumes one matching official occurrence when available.
    for visible_word in hypothesis_words:
        # A remaining official occurrence licenses this word in the clinician view.
        if remaining_official_words[visible_word] > 0:
            remaining_official_words[visible_word] -= 1
            continue
        surplus_visible_words.append(visible_word)

    return surplus_visible_words


def _normalized_word_values(word_values: list[Any]) -> list[str]:
    """Flatten supplied words or phrases into the clinician-facing token order.

    Args:
        word_values: Words/phrases; empty means the transcript lane has no readable text.

    Returns:
        Lowercase scorer tokens in supplied order.
    """
    normalized_words: list[str] = []
    # Each value may be one word or a short phrase from a stored transcript row.
    for word_value in word_values:
        normalized_words.extend(tokens(str(word_value)))
    return normalized_words


def _classify_surplus_words(
    reference_words: list[str],
    missing_words: list[str],
    surplus_words: list[str],
) -> tuple[list[str], list[str], list[str]]:
    """Separate visible surplus into insertion, duplicate, and substitution evidence.

    Args:
        reference_words: Official tokens; empty makes every visible surplus an insertion.
        missing_words: Exact official words absent from the lane; empty means no substitution gap.
        surplus_words: Extra visible occurrences; empty means no surplus defect exists.

    Returns:
        False insertions, duplicates, then wrong-or-garbled words in display order.
    """
    official_vocabulary = set(reference_words)
    false_insertions: list[str] = []
    duplicate_words: list[str] = []
    wrong_or_garbled_words: list[str] = []

    # Each surplus word keeps the most specific defect class the reviewer can prove.
    for surplus_word in surplus_words:
        # Example: a second "metformin" is a duplicate, not a new medication insertion.
        if surplus_word in official_vocabulary:
            duplicate_words.append(surplus_word)
        # Missing truth plus different wording is substitution/garble evidence.
        elif missing_words:
            wrong_or_garbled_words.append(surplus_word)
        else:
            false_insertions.append(surplus_word)
    return false_insertions, duplicate_words, wrong_or_garbled_words


def _lane_error_rates(
    lane_word_errors: WordErrorScore,
    false_insertions: list[str],
    missing_words: list[str],
    duplicate_words: list[str],
) -> tuple[float | None, float | None, float | None]:
    """Return insertion, omission, and duplicate rates with honest empty denominators.

    Args:
        lane_word_errors: Alignment counts for one clinician-visible lane.
        false_insertions: Classified unlicensed words, excluding repeats.
        missing_words: Classified official words absent from the clinician view.
        duplicate_words: Proven surplus repeats; empty means the duplicate numerator is zero.

    Returns:
        Insertion, omission, and duplicate rates; None means that denominator is empty.
    """
    false_insertion_rate: float | None = None
    omission_rate: float | None = None
    duplicate_word_rate: float | None = None
    # No official words means insertion and omission rates have no fair denominator.
    if lane_word_errors.reference_words > 0:
        false_insertion_rate = len(false_insertions) / lane_word_errors.reference_words
        omission_rate = len(missing_words) / lane_word_errors.reference_words
    # No visible words means the clinician saw no duplicate denominator.
    if lane_word_errors.hypothesis_words > 0:
        duplicate_word_rate = len(duplicate_words) / lane_word_errors.hypothesis_words
    return false_insertion_rate, omission_rate, duplicate_word_rate


def _score_transcript_lane_words(
    reference_words: list[str],
    lane_record: dict[str, Any],
) -> dict[str, Any]:
    """Build one word-error record for exactly one stored transcript lane.

    Args:
        reference_words: Normalized official words; empty keeps raw counts and unavailable rates.
        lane_record: Lane identity/hash/words; empty words represent an empty clinician view.

    Returns:
        Counts, rates, and exact defect words for the named artifact.
    """
    hypothesis_words = _normalized_word_values(
        list(lane_record.get("hypothesis_words", []))
    )
    missing_words = words_missing_from_clinician_transcript(
        reference_words, hypothesis_words
    )
    surplus_words = surplus_words_in_clinician_transcript(
        reference_words, hypothesis_words
    )
    false_insertions, duplicate_words, wrong_or_garbled_words = _classify_surplus_words(
        reference_words, missing_words, surplus_words
    )
    lane_word_errors = word_error_score(reference_words, hypothesis_words)
    false_insertion_rate, omission_rate, duplicate_word_rate = _lane_error_rates(
        lane_word_errors, false_insertions, missing_words, duplicate_words
    )
    return {
        "artifact_sha256": lane_record.get("artifact_sha256"),
        "substitutions": lane_word_errors.substitutions,
        "insertions": lane_word_errors.insertions,
        "deletions": lane_word_errors.deletions,
        "reference_words": lane_word_errors.reference_words,
        "hypothesis_words": lane_word_errors.hypothesis_words,
        "wer_percent": lane_word_errors.wer_percent,
        "omissions": missing_words,
        "false_insertions": false_insertions,
        "duplicates": duplicate_words,
        "wrong_or_garbled_words": wrong_or_garbled_words,
        "false_insertion_rate": false_insertion_rate,
        "omission_rate": omission_rate,
        "duplicate_word_rate": duplicate_word_rate,
    }


def score_transcript_lanes(
    *,
    speech_truth_words: list[str],
    lanes: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    """Score live and corrected words without merging what the clinician could see.

    Args:
        speech_truth_words: Official words; empty makes rates unavailable but keeps raw counts.
        lanes: Named lane records; empty returns no scores, and duplicate names fail closed.

    Returns:
        One independent word-error record per lane in the supplied order.

    Raises:
        ValueError: When a lane is unnamed, unsupported, or repeated.
    """
    reference_words = _normalized_word_values(list(speech_truth_words))
    lane_scores: dict[str, dict[str, Any]] = {}

    # Each lane is scored independently so correction cannot repair the live display.
    for lane_record in lanes:
        lane_name = str(lane_record.get("lane", "")).strip().lower()
        # Only the two clinician workflow lanes belong in this frozen scorecard.
        if lane_name not in {"live", "corrected"}:
            raise ValueError(f"unsupported transcript lane: {lane_name or '<empty>'}")
        # A repeated name would silently replace evidence from an earlier run.
        if lane_name in lane_scores:
            raise ValueError(f"duplicate transcript lane: {lane_name}")
        lane_scores[lane_name] = _score_transcript_lane_words(
            reference_words, lane_record
        )
    return lane_scores


def _best_term_similarity_in_rows(
    expected_term: str,
    hypothesis_rows: list[dict[str, Any]],
) -> float:
    """Return the strongest diagnostic spelling resemblance in visible rows.

    Args:
        expected_term: Official clinical term; empty means no resemblance can be measured.
        hypothesis_rows: Visible mock rows; empty means the term was fully omitted.

    Returns:
        Character similarity from zero to one for review classification only.
    """
    normalized_expected_term = "".join(tokens(expected_term))
    best_similarity = 0.0

    # Each row is compared as one collapsed phrase; this never rewrites its displayed text.
    for hypothesis_row in hypothesis_rows:
        normalized_visible_phrase = "".join(tokens(str(hypothesis_row.get("text", ""))))
        # Empty official or visible text gives the reviewer no spelling evidence.
        if normalized_expected_term == "" or normalized_visible_phrase == "":
            continue
        row_similarity = SequenceMatcher(
            None, normalized_expected_term, normalized_visible_phrase
        ).ratio()
        # The strongest row explains whether wording is garbled rather than absent.
        if row_similarity > best_similarity:
            best_similarity = row_similarity

    return best_similarity


def _terms_on_wrong_speaker(
    speech_truth_terms: list[str],
    required_role: str,
    hypothesis_rows: list[dict[str, Any]],
) -> list[str]:
    """Return exact truth terms shown under the wrong clinician/patient role.

    Args:
        speech_truth_terms: Official clinical terms; empty means no attribution requirement.
        required_role: Required visible role; empty means no wrong-speaker term is inferred.
        hypothesis_rows: Literal mock rows with roles; empty means no contamination exists.

    Returns:
        Wrong-speaker terms in official order.
    """
    wrong_speaker_terms: list[str] = []
    # Every official term keeps its own speaker-attribution check.
    for speech_truth_term in speech_truth_terms:
        # Each row retains its role so Doctor text cannot repair Patient evidence.
        for hypothesis_row in hypothesis_rows:
            row_role = str(hypothesis_row.get("role", "")).upper()
            row_words = tokens(str(hypothesis_row.get("text", "")))
            # Only exact text with a pinned different role proves contamination.
            if (
                speech_truth_term in row_words
                and required_role != ""
                and row_role != required_role
            ):
                wrong_speaker_terms.append(speech_truth_term)
    return wrong_speaker_terms


def _consult_29_transcript_outcome(
    lane_score: dict[str, Any],
    hypothesis_rows: list[dict[str, Any]],
    visible_words: list[str],
    wrong_speaker_terms: list[str],
) -> tuple[str, list[str]]:
    """Choose one independent consult-2.9 outcome from computed transcript evidence.

    Args:
        lane_score: Exact word defects for one mock lane.
        hypothesis_rows: Literal rows used only for diagnostic resemblance.
        visible_words: Normalized displayed words; empty means full omission.
        wrong_speaker_terms: Exact terms assigned to the wrong role; empty means none.

    Returns:
        Outcome class and affected terms without reading the expected fixture result.
    """
    # Wrong-speaker exact wording outranks lexical error counts.
    if wrong_speaker_terms:
        return "cross_speaker_contamination", wrong_speaker_terms
    # Extra words after all truth terms are present are false insertions.
    if not lane_score["omissions"] and lane_score["false_insertions"]:
        return "false_insertion", lane_score["false_insertions"]

    best_missing_term_similarity = 0.0
    # Every missing term gets its own non-rewriting resemblance check.
    for missing_term in lane_score["omissions"]:
        best_missing_term_similarity = max(
            best_missing_term_similarity,
            _best_term_similarity_in_rows(missing_term, hypothesis_rows),
        )
    # Strong resemblance marks garble for review but never canonical support.
    if best_missing_term_similarity >= GARBLE_REVIEW_SIMILARITY_FLOOR:
        return "garble", lane_score["omissions"]
    # One unrelated replacement word is a wrong term rather than a silent omission.
    if len(visible_words) == 1:
        return "wrong_term", lane_score["omissions"]
    # Exact text on the required speaker is supported and affects no term.
    if not lane_score["omissions"]:
        return "supported", []
    return "omission", lane_score["omissions"]


def score_consult_29_probe(scoring_probe: dict[str, Any]) -> dict[str, Any]:
    """Classify one medication/allergy transcript defect from its mock evidence.

    Args:
        scoring_probe: CPU-only probe; empty or note-scoped input fails closed.

    Returns:
        One computed clinician-facing outcome without consulting the expected result.

    Raises:
        ValueError: When the probe belongs to the saved-note scorer or lacks truth terms.
    """
    scoring_scope = str(scoring_probe.get("scoring_scope", ""))
    # Note and source-review probes stay red until the dedicated note scorer owns them.
    if scoring_scope != "transcript_lane":
        raise ValueError(
            f"unsupported transcript probe scope: {scoring_scope or '<empty>'}"
        )

    probe_input = scoring_probe.get("input", {})
    speech_truth_terms = [
        str(term).lower()
        # Each official term remains an independent clinical truth requirement.
        for term in probe_input.get("speech_truth_terms", [])
    ]
    # No official term means the scorer cannot identify what the clinician should have seen.
    if not speech_truth_terms:
        raise ValueError("consult-2.9 transcript probe has no speech truth term")

    hypothesis_rows = list(probe_input.get("hypothesis_rows", []))
    visible_row_texts = [
        hypothesis_row.get("text", "")
        # Every mock row contributes only its literal displayed wording.
        for hypothesis_row in hypothesis_rows
    ]
    visible_words = _normalized_word_values(visible_row_texts)

    lane_score = score_transcript_lanes(
        speech_truth_words=speech_truth_terms,
        lanes=[
            {
                "lane": "live",
                "artifact_sha256": None,
                "hypothesis_words": visible_words,
            }
        ],
    )["live"]
    wrong_speaker_terms = _terms_on_wrong_speaker(
        speech_truth_terms,
        str(probe_input.get("required_role", "")).upper(),
        hypothesis_rows,
    )
    outcome_class, affected_terms = _consult_29_transcript_outcome(
        lane_score, hypothesis_rows, visible_words, wrong_speaker_terms
    )

    return {
        "probe_id": scoring_probe.get("probe_id"),
        "outcome_class": outcome_class,
        "outcomes": [outcome_class],
        "affected_terms": affected_terms,
    }


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


def _word_error_sort_key(
    score_tuple: tuple[int, int, int, int],
) -> tuple[int, int, int, int]:
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


def overlap_seconds(
    start: float, end: float, interval_start: float, interval_end: float
) -> float:
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


def overlap_spans(
    reference_intervals: list[ReferenceInterval],
) -> list[tuple[float, float]]:
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
        # Every patient span may overlap this doctor span in the mixed recording.
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
        # Any positive intersection means the row touched simultaneous speech.
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


def touches_any_span(
    segment: HypothesisSegment, spans: list[tuple[float, float]]
) -> bool:
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


def score_regional_word_errors(
    reference_intervals: list[ReferenceInterval],
    hypothesis_segments: list[HypothesisSegment],
) -> dict[str, WordErrorScore]:
    """Score clean and overlap words with the same token-centre allocation.

    Args:
        reference_intervals: Official timed speech; empty makes both WER values unavailable.
        hypothesis_segments: Visible timed rows; empty means all regional reference words are omitted.

    Returns:
        Independent `clean` and `overlap` WER counts for the clinician-facing lane.
    """
    mixed_speech_spans = overlap_spans(reference_intervals)
    clean_word_errors = word_error_score(
        timed_words_for_region(
            reference_intervals, mixed_speech_spans, include_overlap=False
        ),
        timed_words_for_region(
            hypothesis_segments, mixed_speech_spans, include_overlap=False
        ),
    )
    overlap_word_errors = word_error_score(
        timed_words_for_region(
            reference_intervals, mixed_speech_spans, include_overlap=True
        ),
        timed_words_for_region(
            hypothesis_segments, mixed_speech_spans, include_overlap=True
        ),
    )
    return {"clean": clean_word_errors, "overlap": overlap_word_errors}


def is_phrase_present_in_text(text: str, required_phrase: str) -> bool:
    """Return whether a required phrase appears as consecutive visible words.

    Args:
        text: Clinician-visible row text; empty means no phrase can be supported.
        required_phrase: Official phrase; empty is never treated as evidence.

    Returns:
        True only for an exact normalized word sequence in the displayed row.
    """
    visible_words = tokens(text)
    required_words = tokens(required_phrase)
    # Empty official wording cannot become a vacuous clinical match.
    if not required_words:
        return False

    last_start_index = len(visible_words) - len(required_words)
    # Each possible start keeps multiword medication names contiguous.
    for start_index in range(last_start_index + 1):
        end_index = start_index + len(required_words)
        # Exact words support the phrase; fuzzy wording remains a transcript defect.
        if visible_words[start_index:end_index] == required_words:
            return True
    return False


def score_clinical_term_identity(
    required_terms: list[str],
    hypothesis_segments: list[HypothesisSegment],
) -> dict[str, Any]:
    """Score exact clinical phrases without consulting transcript timestamps.

    Args:
        required_terms: Canonical phrases in frozen manifest order.
        hypothesis_segments: One displayed transcript lane; empty omits every term.

    Returns:
        Text-free term-set identity, supported/missing indices, counts, and recall.

    Raises:
        ValueError: When a canonical term is empty or duplicates another normalized term.
    """
    normalized_terms = [tokens(required_term) for required_term in required_terms]
    # Empty canonical wording could become a vacuous match and must fail closed.
    if any(not normalized_term for normalized_term in normalized_terms):
        raise ValueError("clinical terms must contain normalized words")

    normalized_term_phrases = [
        " ".join(normalized_term) for normalized_term in normalized_terms
    ]
    # Duplicate normalized phrases would overweight one clinical fact.
    if len(normalized_term_phrases) != len(set(normalized_term_phrases)):
        raise ValueError("clinical terms must be unique after normalization")

    supported_term_indices = [
        term_index
        for term_index, required_term in enumerate(required_terms)
        if any(
            is_phrase_present_in_text(segment.text, required_term)
            for segment in hypothesis_segments
        )
    ]
    supported_index_set = set(supported_term_indices)
    missing_term_indices = [
        term_index
        for term_index in range(len(required_terms))
        if term_index not in supported_index_set
    ]
    recall: float | None = None
    # An empty registered term set is unmeasured, never a favorable pass.
    if required_terms:
        recall = len(supported_term_indices) / len(required_terms)

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


def _validated_critical_span_bounds(
    span_expectation: dict[str, Any],
) -> tuple[float, float]:
    """Return valid overlap-only bounds for a clinician-reviewed critical span.

    Args:
        span_expectation: Span timing/rule; empty or containment input fails closed.

    Returns:
        Positive start/end seconds for overlap membership.

    Raises:
        ValueError: When containment is requested or the time range is not positive.
    """
    span_start_seconds = float(span_expectation.get("start_seconds", 0.0) or 0.0)
    span_end_seconds = float(span_expectation.get("end_seconds", 0.0) or 0.0)
    membership_rule = str(
        span_expectation.get("membership_rule", "time_overlap")
    ).lower()
    # Containment would drop an answer that crosses the clinician's review boundary.
    if membership_rule != "time_overlap":
        raise ValueError(f"unsupported critical-span membership: {membership_rule}")
    # An empty or reversed span cannot identify clinician-visible evidence safely.
    if span_end_seconds <= span_start_seconds:
        raise ValueError("critical span must have a positive time range")
    return span_start_seconds, span_end_seconds


def _segments_overlapping_critical_span(
    hypothesis_segments: list[HypothesisSegment],
    span_start_seconds: float,
    span_end_seconds: float,
) -> list[HypothesisSegment]:
    """Return stored rows touching any part of a frozen review span.

    Args:
        hypothesis_segments: One stored lane; empty means no span evidence exists.
        span_start_seconds: Inclusive review start in seconds.
        span_end_seconds: Review end in seconds; boundary-crossing rows remain eligible.

    Returns:
        Overlapping rows in their supplied order.
    """
    span_rows: list[HypothesisSegment] = []
    # Any positive overlap keeps boundary evidence such as the allergy answer.
    for hypothesis_segment in hypothesis_segments:
        # A row can extend beyond the span and still remain clinician-visible evidence.
        if (
            overlap_seconds(
                hypothesis_segment.start,
                hypothesis_segment.end,
                span_start_seconds,
                span_end_seconds,
            )
            > 0
        ):
            span_rows.append(hypothesis_segment)
    return span_rows


def _critical_term_state(
    required_term: str,
    required_role: str,
    span_rows: list[HypothesisSegment],
) -> str:
    """Return supported, omitted, or wrong-speaker for one exact clinical term.

    Args:
        required_term: Official term; empty never becomes supported.
        required_role: Required role; empty accepts exact text from either speaker.
        span_rows: Overlapping lane rows; empty means the term is omitted.

    Returns:
        Stable term-state label used by critical-span evidence.
    """
    matching_rows: list[HypothesisSegment] = []
    # Each overlapping row is a possible exact source for this term.
    for span_row in span_rows:
        # Garbled or split wording does not become a canonical match.
        if is_phrase_present_in_text(span_row.text, required_term):
            matching_rows.append(span_row)
    # No exact row means omission even when a plausible garble is nearby.
    if not matching_rows:
        return "omitted"
    # An unpinned role accepts exact wording from either consultation speaker.
    if required_role == "":
        return "supported"
    # Any correctly attributed exact row safely supports the term.
    if any(matching_row.role == required_role for matching_row in matching_rows):
        return "supported"
    return "wrong_speaker"


def _score_required_critical_terms(
    required_terms: list[str],
    required_role: str,
    span_rows: list[HypothesisSegment],
) -> tuple[list[str], list[str], list[str]]:
    """Group exact critical terms by supported, omitted, and wrong-speaker state.

    Args:
        required_terms: Official terms; empty returns three empty result lists.
        required_role: Required visible role; empty accepts either speaker.
        span_rows: Time-overlapping lane rows; empty omits every required term.

    Returns:
        Supported, omitted, and wrong-speaker terms in official order.
    """
    supported_terms: list[str] = []
    omitted_terms: list[str] = []
    wrong_speaker_terms: list[str] = []
    # Every term stays independent so one correct medicine cannot hide another failure.
    for required_term in required_terms:
        term_state = _critical_term_state(required_term, required_role, span_rows)
        # Exact text on the required role contributes to span success.
        if term_state == "supported":
            supported_terms.append(required_term)
        # Missing exact text stays an omission rather than a reconstructed name.
        elif term_state == "omitted":
            omitted_terms.append(required_term)
        else:
            wrong_speaker_terms.append(required_term)
    return supported_terms, omitted_terms, wrong_speaker_terms


def score_critical_span(
    span_expectation: dict[str, Any],
    hypothesis_segments: list[HypothesisSegment],
) -> dict[str, Any]:
    """Score one frozen clinical span without reconstructing missing wording.

    Args:
        span_expectation: Span ID, timing, terms, and optional role; empty fields fail closed.
        hypothesis_segments: One stored transcript lane; empty means every term is omitted.

    Returns:
        Exact supported, omitted, and wrong-speaker terms for the reviewer.

    Raises:
        ValueError: When the span uses containment or has a non-positive time range.
    """
    span_start_seconds, span_end_seconds = _validated_critical_span_bounds(
        span_expectation
    )
    required_role = str(span_expectation.get("required_role", "")).upper()
    required_terms = [
        str(required_term).lower()
        # Every named term must pass; partial credit cannot hide a medication failure.
        for required_term in span_expectation.get("required_terms", [])
    ]
    span_rows = _segments_overlapping_critical_span(
        hypothesis_segments, span_start_seconds, span_end_seconds
    )
    supported_terms, omitted_terms, wrong_speaker_terms = (
        _score_required_critical_terms(required_terms, required_role, span_rows)
    )

    # Empty required terms make the span fail closed instead of becoming a vacuous pass.
    passed = not omitted_terms and not wrong_speaker_terms and bool(required_terms)
    return {
        "span_id": span_expectation.get("span_id"),
        "passed": passed,
        "required_terms": required_terms,
        "supported_terms": supported_terms,
        "omissions": omitted_terms,
        "wrong_speaker_terms": wrong_speaker_terms,
        "overlapping_rows": len(span_rows),
    }


def score_critical_spans(
    span_expectations: list[dict[str, Any]],
    hypothesis_segments: list[HypothesisSegment],
) -> dict[str, Any]:
    """Aggregate named critical spans while retaining every failed span result.

    Args:
        span_expectations: Frozen span definitions; empty makes recall unavailable.
        hypothesis_segments: One stored lane; empty scores every required span as failed.

    Returns:
        Passed/required counts, recall, failed IDs, and full per-span evidence.
    """
    span_scores: list[dict[str, Any]] = []
    # Each named span remains visible so an average cannot hide a clinical failure.
    for span_expectation in span_expectations:
        span_scores.append(score_critical_span(span_expectation, hypothesis_segments))

    passed_span_ids = [
        span_score["span_id"]
        # Every passing span has exact wording, timing membership, and required attribution.
        for span_score in span_scores
        # A failed span stays out of the numerator but remains in the evidence array.
        if span_score["passed"]
    ]
    failed_span_ids = [
        span_score["span_id"]
        # Every failed span is named for the clinician or quality reviewer.
        for span_score in span_scores
        # Only failed spans belong in the diagnostic failure list.
        if not span_score["passed"]
    ]
    critical_term_recall: float | None = None
    # No registered span means recall is unavailable, never a favorable zero or pass.
    if span_scores:
        critical_term_recall = len(passed_span_ids) / len(span_scores)

    return {
        "passed_spans": len(passed_span_ids),
        "required_spans": len(span_scores),
        "critical_term_recall": critical_term_recall,
        "passed_span_ids": passed_span_ids,
        "failed_span_ids": failed_span_ids,
        "spans": span_scores,
    }


def _source_unit_identifier(source_unit: dict[str, Any]) -> str:
    """Return the stable identity a reviewer uses to trace one transcript unit.

    Args:
        source_unit: Stored row or derived unit; empty identity means provenance is missing.

    Returns:
        Source-unit/segment ID, or an empty string when the UI evidence cannot be traced.
    """
    # A derived source-unit ID takes precedence; raw transcript rows fall back to segment ID.
    return str(
        source_unit.get("source_unit_id") or source_unit.get("segment_id") or ""
    ).strip()


def _turn_key(source_unit: dict[str, Any]) -> tuple[str, str]:
    """Return normalized role and wording for duplicate-turn comparison.

    Args:
        source_unit: Visible unit; empty role/text stays an explicit empty key.

    Returns:
        Uppercase role plus normalized words as one deterministic key.
    """
    return (
        str(source_unit.get("role", "")).upper(),
        " ".join(tokens(str(source_unit.get("text", "")))),
    )


def _surplus_duplicate_turn_count(
    source_units: list[dict[str, Any]],
    reference_turns: list[dict[str, Any]] | None,
) -> int | None:
    """Count repeated visible turns beyond the official occurrence allowance.

    Args:
        source_units: Emitted units; empty means zero duplicates when references exist.
        reference_turns: Official turns; None means duplicate-turn truth is unavailable.

    Returns:
        Surplus repeated turns, or None when no reference-turn contract was supplied.
    """
    # Without official turns, repeated-looking wording cannot be called unlicensed.
    if reference_turns is None:
        return None

    visible_turn_counts: Counter[tuple[str, str]] = Counter()
    reference_turn_counts: Counter[tuple[str, str]] = Counter()
    # Every visible unit contributes one emitted turn occurrence.
    for source_unit in source_units:
        visible_turn_counts[_turn_key(source_unit)] += 1
    # Every official turn licenses one matching occurrence.
    for reference_turn in reference_turns:
        reference_turn_counts[_turn_key(reference_turn)] += 1

    duplicate_turn_count = 0
    # A turn must repeat before any surplus occurrence can count as a duplicate.
    for turn_key, visible_count in visible_turn_counts.items():
        # One visible occurrence is never a duplicate, even if absent from references.
        if visible_count <= 1:
            continue
        licensed_occurrences = max(1, reference_turn_counts[turn_key])
        duplicate_turn_count += max(0, visible_count - licensed_occurrences)
    return duplicate_turn_count


def _source_preservation_defects(source_unit: dict[str, Any]) -> list[str]:
    """Return wording and speaker changes against supplied source-unit truth.

    Args:
        source_unit: Visible unit plus optional source text/role/speaker; empty means no checks.

    Returns:
        Exact preservation defect labels for the clinician-facing unit.
    """
    preservation_defects: list[str] = []
    source_text = source_unit.get("source_text")
    # A supplied source text is an exact no-rewrite contract for visible wording.
    if source_text is not None and tokens(str(source_text)) != tokens(
        str(source_unit.get("text", ""))
    ):
        preservation_defects.append("lexical_rewrite")

    source_role = source_unit.get("source_role")
    # A supplied source role prevents assembly from borrowing across speakers.
    if (
        source_role is not None
        and str(source_role).upper() != str(source_unit.get("role", "")).upper()
    ):
        preservation_defects.append("speaker_role_changed")

    source_speaker_id = source_unit.get("source_speaker_id")
    # A supplied speaker ID must survive assembly even when its role is unchanged.
    if source_speaker_id is not None and str(source_speaker_id) != str(
        source_unit.get("speaker_id", "")
    ):
        preservation_defects.append("speaker_identity_changed")
    return preservation_defects


def _transition_defect_reasons(
    previous_source_unit: dict[str, Any],
    current_source_unit: dict[str, Any],
    seen_source_unit_ids: set[str],
) -> list[str]:
    """Return identity, chronology, wording, and speaker defects for one transition.

    Args:
        previous_source_unit: Earlier persisted unit; empty identity fails traceability.
        current_source_unit: Next persisted unit; empty identity fails traceability.
        seen_source_unit_ids: IDs already shown; an empty set means no duplicate is possible.

    Returns:
        All independent defect reasons for this adjacent clinician-visible transition.
    """
    previous_source_unit_id = _source_unit_identifier(previous_source_unit)
    current_source_unit_id = _source_unit_identifier(current_source_unit)
    defect_reasons: list[str] = []
    # Empty identity prevents the clinician from tracing the transition to evidence.
    if previous_source_unit_id == "" or current_source_unit_id == "":
        defect_reasons.append("source_identity_missing")
    # Reusing an earlier ID makes two visible turns claim the same source unit.
    if current_source_unit_id in seen_source_unit_ids:
        defect_reasons.append("duplicate_source_identity")

    previous_start = float(previous_source_unit.get("start", 0.0) or 0.0)
    current_start = float(current_source_unit.get("start", 0.0) or 0.0)
    # Example: a late medication row must not silently appear before its question.
    if current_start < previous_start:
        defect_reasons.append("chronology_regression")
    defect_reasons.extend(_source_preservation_defects(current_source_unit))
    return defect_reasons


def _coherence_rates(
    eligible_transitions: int,
    assembly_defects: int,
) -> tuple[int, float | None, float | None]:
    """Return coherent count plus coherence and defect rates.

    Args:
        eligible_transitions: Adjacent unit pairs; zero makes both rates unavailable.
        assembly_defects: Transition pairs with one or more defects.

    Returns:
        Coherent transitions, coherence rate, then defect rate.
    """
    coherent_transitions = eligible_transitions - assembly_defects
    # Fewer than two units has no adjacent-transition denominator.
    if eligible_transitions == 0:
        return coherent_transitions, None, None
    return (
        coherent_transitions,
        coherent_transitions / eligible_transitions,
        assembly_defects / eligible_transitions,
    )


def score_turn_coherence(
    source_units: list[dict[str, Any]],
    *,
    reference_turns: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Score adjacent stored units for chronology, identity, and lexical preservation.

    Args:
        source_units: Units in persisted order; fewer than two makes coherence unavailable.
        reference_turns: Official turns for duplicate counts; None leaves that count unavailable.

    Returns:
        Coherence/defect rates, duplicate count, and every transition-level reason.
    """
    eligible_transitions = max(0, len(source_units) - 1)
    transition_defects: list[dict[str, Any]] = []
    seen_source_unit_ids: set[str] = set()
    # The first unit starts identity tracking even though it has no preceding transition.
    if source_units:
        first_source_unit_id = _source_unit_identifier(source_units[0])
        # Missing identity stays visible as a later transition defect instead of a fake ID.
        if first_source_unit_id != "":
            seen_source_unit_ids.add(first_source_unit_id)

    # Each adjacent pair represents one transition the clinician reads in sequence.
    for transition_index in range(eligible_transitions):
        previous_source_unit = source_units[transition_index]
        current_source_unit = source_units[transition_index + 1]
        previous_source_unit_id = _source_unit_identifier(previous_source_unit)
        current_source_unit_id = _source_unit_identifier(current_source_unit)
        defect_reasons = _transition_defect_reasons(
            previous_source_unit, current_source_unit, seen_source_unit_ids
        )
        # The opening unit has no earlier pair, so attach its preservation
        # evidence to the first clinician-visible transition.
        if transition_index == 0:
            defect_reasons = [
                *_source_preservation_defects(previous_source_unit),
                *defect_reasons,
            ]

        # Every defective transition keeps all reasons instead of collapsing to one favorable label.
        if defect_reasons:
            transition_defects.append(
                {
                    "transition_index": transition_index,
                    "from_source_unit_id": previous_source_unit_id,
                    "to_source_unit_id": current_source_unit_id,
                    "reasons": defect_reasons,
                }
            )
        # A non-empty current ID becomes unavailable to every later transition.
        if current_source_unit_id != "":
            seen_source_unit_ids.add(current_source_unit_id)

    coherent_transitions, turn_coherence, assembly_defect_rate = _coherence_rates(
        eligible_transitions, len(transition_defects)
    )

    return {
        "coherent_transitions": coherent_transitions,
        "eligible_transitions": eligible_transitions,
        "turn_coherence": turn_coherence,
        "assembly_defects": len(transition_defects),
        "assembly_defect_rate": assembly_defect_rate,
        "duplicate_turns": _surplus_duplicate_turn_count(source_units, reference_turns),
        "transition_defects": transition_defects,
    }


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
        # A clean short row still contributes to the clinician's readability burden.
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
                first_in_window = (
                    owning_window.window_index not in first_row_seen_by_window
                )
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

        # Missing identity or reference ownership cannot support a role mapping.
        if speaker_id == "" or expected_role is None:
            continue
        # Cross-talk rows stay out of the clean speaker-mapping ceiling.
        if touches_any_span(segment, spans):
            continue

        reference_counts = reference_counts_by_speaker.setdefault(
            speaker_id, {role: 0 for role in sorted(SUPPORTED_REFERENCE_ROLES)}
        )
        reference_counts[expected_role] += 1

    speaker_ids = sorted(reference_counts_by_speaker)
    # The dyadic ceiling only exists for exactly two visible consultation voices.
    if len(speaker_ids) != 2:
        return DyadicMappingCeiling(
            best_mapping={}, correct_segments=0, scored_segments=0
        )

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
        # The stronger valid assignment is the recoverable mapping shown to reviewers.
        if correct > best_correct:
            best_correct = correct
            best_mapping = candidate

    return DyadicMappingCeiling(
        best_mapping=best_mapping,
        correct_segments=best_correct,
        scored_segments=scored_segments,
    )


def phantom_speaker_count(
    segments: list[HypothesisSegment], cap: int = DEFAULT_SPEAKER_CAP
) -> int:
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
    regional_word_errors = score_regional_word_errors(reference_intervals, segments)
    clean_word_errors = regional_word_errors["clean"]
    overlap_word_errors = regional_word_errors["overlap"]
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
    # No window artifact means seam ownership diagnostics are unavailable for this run.
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
    # An empty official transcript has no meaningful clinician-visible length ratio.
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
    print(f"role mapping headroom (non-overlap): {points_or_na(role_mapping_headroom)}")
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


# Direct execution prints the report a reviewer uses after a saved visit.
if __name__ == "__main__":
    raise SystemExit(main())
