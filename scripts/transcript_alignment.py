#!/usr/bin/env python3
"""Deterministic, timestamp-independent transcript word alignment.

This module deliberately treats emitted timestamps as metadata. Reference
ownership comes only from the Doctor and Patient TextGrid text streams.
"""

from __future__ import annotations

import re
from array import array
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

TOKEN_PATTERN = re.compile(r"[a-z']+")
SUPPORTED_ROLES = ("DOCTOR", "PATIENT")
_MAX_COMPACT_SEQUENCE_LENGTH = 65_535
_EDIT_MATCH = 1
_EDIT_SUBSTITUTION = 2
_EDIT_DELETION = 3
_EDIT_INSERTION = 4


@dataclass(frozen=True)
class ReferenceInterval:
    """Represent one non-empty role-owned TextGrid interval.
    Timing reports use it to decide which role owns a displayed span.
    An empty TextGrid produces no instance, so no placeholder reaches operator metrics.
    """

    role: str
    interval_ordinal: int
    start: float
    end: float
    text: str


@dataclass(frozen=True)
class ReferenceWord:
    """Represent one normalized reference word with a stable source identity.
    Ownership reports use it to explain a matched display word without trusting emitted time.
    An interval with no comparable text produces no instance.
    """

    token: str
    role: str
    interval_ordinal: int
    word_ordinal: int
    interval_start: float
    interval_end: float

    @property
    def identity(self) -> tuple[str, int, int]:
        """Return the stable role/interval/word identity used in operator evidence.

        :returns: Three non-null identity fields; the tuple is never empty for a parsed reference word.
        """
        return self.role, self.interval_ordinal, self.word_ordinal

    def to_dict(self) -> dict[str, Any]:
        """Return the reference fields attached to an explainable ownership match.

        :returns: A JSON-ready record with identity and interval bounds; the mapping is never empty.
        """
        return {
            "token": self.token,
            "role": self.role,
            "reference_id": list(self.identity),
            "interval_start": self.interval_start,
            "interval_end": self.interval_end,
        }


@dataclass(frozen=True)
class HypothesisWord:
    """Represent one user-visible word with its timestamp-independent row identity.
    Quality reports use it to preserve display order while comparing role ownership.
    A saved row with empty text produces no instance.
    """

    token: str
    speaker_id: str
    role: str
    row_index: int
    word_index: int
    row_start: float
    row_end: float
    floor_span: bool

    @property
    def identity(self) -> tuple[int, int]:
        """Return the display row/word identity used to trace a visible match.

        :returns: Zero-based row and word indices; the tuple is never empty for a flattened display word.
        """
        return self.row_index, self.word_index

    def to_dict(self) -> dict[str, Any]:
        """Return the display identity and role shown beside an ownership match.

        :returns: A JSON-ready record with no emitted timestamp dependency; the mapping is never empty.
        """
        return {
            "token": self.token,
            "speaker_id": self.speaker_id,
            "visible_role": self.role,
            "hypothesis_id": list(self.identity),
        }


@dataclass(frozen=True)
class ReferenceCorpus:
    """Group reference words by whether cross-channel order is knowable.
    Ownership reports use its role streams without fabricating Doctor/Patient ordering.
    Every view may be empty when the selected TextGrids contain no usable speech.
    """

    alignable_words: tuple[ReferenceWord, ...]
    overlap_ambiguous_words: tuple[ReferenceWord, ...]
    doctor_words: tuple[ReferenceWord, ...]
    patient_words: tuple[ReferenceWord, ...]
    linearized_words: tuple[ReferenceWord, ...]

    @property
    def words_by_role(self) -> dict[str, tuple[ReferenceWord, ...]]:
        """Return independent role streams for the operator's ownership report.

        :returns: Doctor and Patient keys are always present; either word tuple may be empty when its TextGrid has no usable speech.
        """
        return {
            "DOCTOR": self.doctor_words,
            "PATIENT": self.patient_words,
        }


def normalize_words(text: str) -> list[str]:
    """Normalize displayed or reference text for the shared quality reports.

    :param text: Transcript wording to compare; empty text or text without word characters produces no tokens.
    :returns: Lowercase word and apostrophe tokens in display order; the list is empty when the caller supplied no comparable wording.
    """
    return TOKEN_PATTERN.findall(text.lower())


def word_error_alignment(
    reference_tokens: Sequence[str],
    hypothesis_tokens: Sequence[str],
) -> dict[str, Any]:
    """Compare reference and displayed words with the scorer's deterministic S/I/D tie-break.

    Use this content-error diagnostic beside ownership evidence; empty streams remain valid zero, insertion-only, or deletion-only cases.

    :param reference_tokens: Expected visit words in reference order; an empty sequence means no reference wording was available.
    :param hypothesis_tokens: User-visible transcript words in display order; an empty sequence means no comparable transcript wording was saved.

    :returns: Standard error counts and indexed operations; operations are empty only when both input streams are empty.
    :raises ValueError: If either stream exceeds the compact 65,535-word alignment limit.
    :raises RuntimeError: If internal backtracking cannot close a complete word-error alignment.
    """
    if (
        len(reference_tokens) > _MAX_COMPACT_SEQUENCE_LENGTH
        or len(hypothesis_tokens) > _MAX_COMPACT_SEQUENCE_LENGTH
    ):
        raise ValueError("word stream exceeds compact alignment limit")

    width = len(hypothesis_tokens) + 1
    choices = [array("B", [0]) * width for _ in range(len(reference_tokens) + 1)]
    for hypothesis_index in range(1, width):
        choices[0][hypothesis_index] = _EDIT_INSERTION
    for reference_index in range(1, len(reference_tokens) + 1):
        choices[reference_index][0] = _EDIT_DELETION

    previous_distance = array("H", range(width))
    previous_substitutions = array("H", [0]) * width
    previous_insertions = array("H", range(width))
    previous_deletions = array("H", [0]) * width

    for reference_index, reference_token in enumerate(reference_tokens, start=1):
        current_distance = array("H", [reference_index]) + array("H", [0]) * (width - 1)
        current_substitutions = array("H", [0]) * width
        current_insertions = array("H", [0]) * width
        current_deletions = array("H", [reference_index]) + array("H", [0]) * (
            width - 1
        )

        for hypothesis_index, hypothesis_token in enumerate(
            hypothesis_tokens,
            start=1,
        ):
            is_match = reference_token == hypothesis_token
            diagonal_candidate = (
                int(previous_distance[hypothesis_index - 1]) + int(not is_match),
                int(previous_substitutions[hypothesis_index - 1]) + int(not is_match),
                int(previous_insertions[hypothesis_index - 1]),
                int(previous_deletions[hypothesis_index - 1]),
            )
            deletion_candidate = (
                int(previous_distance[hypothesis_index]) + 1,
                int(previous_substitutions[hypothesis_index]),
                int(previous_insertions[hypothesis_index]),
                int(previous_deletions[hypothesis_index]) + 1,
            )
            insertion_candidate = (
                int(current_distance[hypothesis_index - 1]) + 1,
                int(current_substitutions[hypothesis_index - 1]),
                int(current_insertions[hypothesis_index - 1]) + 1,
                int(current_deletions[hypothesis_index - 1]),
            )
            selected, selected_code = min(
                (
                    (
                        diagonal_candidate,
                        _EDIT_MATCH if is_match else _EDIT_SUBSTITUTION,
                    ),
                    (deletion_candidate, _EDIT_DELETION),
                    (insertion_candidate, _EDIT_INSERTION),
                ),
                key=lambda candidate: (
                    candidate[0][0],
                    candidate[0][3],
                    candidate[0][1],
                    candidate[0][2],
                ),
            )
            (
                current_distance[hypothesis_index],
                current_substitutions[hypothesis_index],
                current_insertions[hypothesis_index],
                current_deletions[hypothesis_index],
            ) = selected
            choices[reference_index][hypothesis_index] = selected_code

        previous_distance = current_distance
        previous_substitutions = current_substitutions
        previous_insertions = current_insertions
        previous_deletions = current_deletions

    reference_index = len(reference_tokens)
    hypothesis_index = len(hypothesis_tokens)
    operations = []
    while reference_index > 0 or hypothesis_index > 0:
        operation_code = int(choices[reference_index][hypothesis_index])
        if operation_code in {_EDIT_MATCH, _EDIT_SUBSTITUTION}:
            operation = "match" if operation_code == _EDIT_MATCH else "substitution"
            operations.append(
                {
                    "operation": operation,
                    "reference_index": reference_index - 1,
                    "hypothesis_index": hypothesis_index - 1,
                    "reference_token": reference_tokens[reference_index - 1],
                    "hypothesis_token": hypothesis_tokens[hypothesis_index - 1],
                }
            )
            reference_index -= 1
            hypothesis_index -= 1
            continue
        if operation_code == _EDIT_DELETION:
            operations.append(
                {
                    "operation": "deletion",
                    "reference_index": reference_index - 1,
                    "hypothesis_index": None,
                    "reference_token": reference_tokens[reference_index - 1],
                    "hypothesis_token": None,
                }
            )
            reference_index -= 1
            continue
        if operation_code == _EDIT_INSERTION:
            operations.append(
                {
                    "operation": "insertion",
                    "reference_index": None,
                    "hypothesis_index": hypothesis_index - 1,
                    "reference_token": None,
                    "hypothesis_token": hypothesis_tokens[hypothesis_index - 1],
                }
            )
            hypothesis_index -= 1
            continue
        raise RuntimeError("word-error backtrace did not close")
    operations.reverse()

    return {
        "algorithm": {
            "name": "standard-word-error-alignment",
            "substitution_cost": 1,
            "insertion_cost": 1,
            "deletion_cost": 1,
            "tie_break": [
                "minimum_distance",
                "fewer_deletions",
                "fewer_substitutions",
                "fewer_insertions",
            ],
        },
        "reference_words": len(reference_tokens),
        "hypothesis_words": len(hypothesis_tokens),
        "substitutions": int(previous_substitutions[-1]),
        "insertions": int(previous_insertions[-1]),
        "deletions": int(previous_deletions[-1]),
        "total_errors": int(previous_distance[-1]),
        "operations": operations,
    }


def parse_textgrid_intervals(
    path: Path,
    role: str,
    *,
    cutoff_seconds: float,
) -> list[ReferenceInterval]:
    """Read one role's TextGrid intervals for timing and ownership reports.

    :param path: Selected reference TextGrid; a missing or unreadable file prevents the report.
    :param role: Doctor or Patient channel label; null, empty, or another role is invalid.
    :param cutoff_seconds: Visit boundary applied to interval ends; no accepted intervals produces an empty list.

    :returns: Non-empty intervals in file order; empty means the channel had no usable speech before the cutoff.
    :raises ValueError: If the role is unsupported or a matched interval contains an invalid numeric boundary.
    :raises OSError: If the selected TextGrid cannot be read.
    """
    normalized_role = role.upper()
    if normalized_role not in SUPPORTED_ROLES:
        raise ValueError(f"unsupported reference role: {role}")

    content = path.read_text(encoding="utf-8", errors="replace")
    intervals: list[ReferenceInterval] = []
    matches = re.finditer(
        r"xmin\s*=\s*([\d.]+)\s*\n\s*xmax\s*=\s*([\d.]+)"
        r'\s*\n\s*text\s*=\s*"(.*?)"',
        content,
        re.S,
    )
    for interval_ordinal, match in enumerate(matches, start=1):
        start = float(match.group(1))
        end = min(float(match.group(2)), cutoff_seconds)
        text = match.group(3).strip()
        if text == "" or start > cutoff_seconds or end <= start:
            continue
        intervals.append(
            ReferenceInterval(
                role=normalized_role,
                interval_ordinal=interval_ordinal,
                start=start,
                end=end,
                text=text,
            )
        )
    return intervals


def _intervals_overlap(
    first: ReferenceInterval,
    second: ReferenceInterval,
) -> bool:
    """Return whether two role intervals share positive-duration speech."""
    return max(first.start, second.start) < min(first.end, second.end)


def _words_for_interval(interval: ReferenceInterval) -> list[ReferenceWord]:
    """Expand one TextGrid interval into stable normalized words."""
    return [
        ReferenceWord(
            token=token,
            role=interval.role,
            interval_ordinal=interval.interval_ordinal,
            word_ordinal=word_ordinal,
            interval_start=interval.start,
            interval_end=interval.end,
        )
        for word_ordinal, token in enumerate(normalize_words(interval.text), start=1)
    ]


def build_reference_corpus(
    doctor_path: Path,
    patient_path: Path,
    *,
    cutoff_seconds: float,
) -> ReferenceCorpus:
    """Build the visit reference while isolating overlapping speaker channels.

    :param doctor_path: Doctor TextGrid selected for the visit; an empty file contributes no Doctor words.
    :param patient_path: Patient TextGrid selected for the visit; an empty file contributes no Patient words.
    :param cutoff_seconds: Visit boundary shared by both channels; no accepted intervals yields an empty corpus.

    :returns: Separate role streams plus alignable, overlap-ambiguous, and time-linearized views; each view may be empty.
    :raises ValueError: If a matched TextGrid interval contains an invalid numeric boundary.
    :raises OSError: If either selected TextGrid cannot be read.
    """
    doctor_intervals = parse_textgrid_intervals(
        doctor_path,
        "DOCTOR",
        cutoff_seconds=cutoff_seconds,
    )
    patient_intervals = parse_textgrid_intervals(
        patient_path,
        "PATIENT",
        cutoff_seconds=cutoff_seconds,
    )

    overlap_identities: set[tuple[str, int]] = set()
    for doctor_interval in doctor_intervals:
        for patient_interval in patient_intervals:
            if not _intervals_overlap(doctor_interval, patient_interval):
                continue
            overlap_identities.add(
                (doctor_interval.role, doctor_interval.interval_ordinal)
            )
            overlap_identities.add(
                (patient_interval.role, patient_interval.interval_ordinal)
            )

    alignable_words: list[ReferenceWord] = []
    overlap_ambiguous_words: list[ReferenceWord] = []
    linearized_words: list[ReferenceWord] = []
    intervals = sorted(
        [*doctor_intervals, *patient_intervals],
        key=lambda interval: (
            interval.start,
            interval.end,
            interval.role,
            interval.interval_ordinal,
        ),
    )
    for interval in intervals:
        interval_words = _words_for_interval(interval)
        linearized_words.extend(interval_words)
        target = (
            overlap_ambiguous_words
            if (interval.role, interval.interval_ordinal) in overlap_identities
            else alignable_words
        )
        target.extend(interval_words)

    return ReferenceCorpus(
        alignable_words=tuple(alignable_words),
        overlap_ambiguous_words=tuple(overlap_ambiguous_words),
        doctor_words=tuple(
            word
            for interval in doctor_intervals
            for word in _words_for_interval(interval)
        ),
        patient_words=tuple(
            word
            for interval in patient_intervals
            for word in _words_for_interval(interval)
        ),
        linearized_words=tuple(linearized_words),
    )


def flatten_hypothesis_rows(
    rows: Sequence[dict[str, Any]],
    *,
    floor_seconds: float = 0.05,
    epsilon: float = 1e-6,
) -> list[HypothesisWord]:
    """Flatten saved transcript rows into the word order the user saw.

    :param rows: Display rows to compare; empty rows or rows with empty text contribute no words.
    :param floor_seconds: Synthetic row duration used to mark timing-floor words; it does not reorder or remove them.
    :param epsilon: Numeric tolerance for recognizing the timing floor.

    :returns: Display words with row identity, role, and timing context; empty means no row contained comparable text.
    :raises ValueError: If a saved start or end value cannot be converted to a number.
    :raises TypeError: If a row or timing value has an unsupported shape.
    """
    words: list[HypothesisWord] = []
    for row_index, row in enumerate(rows):
        start = float(row.get("start", 0.0) or 0.0)
        end = float(row.get("end", 0.0) or 0.0)
        floor_span = abs((end - start) - floor_seconds) < epsilon
        for word_index, token in enumerate(normalize_words(str(row.get("text", "")))):
            words.append(
                HypothesisWord(
                    token=token,
                    speaker_id=str(row.get("speaker_id", "")),
                    role=str(row.get("role", "")).upper(),
                    row_index=row_index,
                    word_index=word_index,
                    row_start=start,
                    row_end=end,
                    floor_span=floor_span,
                )
            )
    return words


def _lcs_tables(
    reference_tokens: Sequence[str],
    hypothesis_tokens: Sequence[str],
) -> tuple[list[array[int]], list[array[int]]]:
    """Return compact forward and suffix LCS-length tables."""
    if (
        len(reference_tokens) > _MAX_COMPACT_SEQUENCE_LENGTH
        or len(hypothesis_tokens) > _MAX_COMPACT_SEQUENCE_LENGTH
    ):
        raise ValueError("word stream exceeds compact alignment limit")

    width = len(hypothesis_tokens) + 1
    forward = [array("H", [0]) * width for _ in range(len(reference_tokens) + 1)]
    for reference_index, reference_token in enumerate(reference_tokens, start=1):
        current_row = forward[reference_index]
        previous_row = forward[reference_index - 1]
        for hypothesis_index, hypothesis_token in enumerate(
            hypothesis_tokens,
            start=1,
        ):
            if reference_token == hypothesis_token:
                current_row[hypothesis_index] = previous_row[hypothesis_index - 1] + 1
            else:
                current_row[hypothesis_index] = max(
                    previous_row[hypothesis_index],
                    current_row[hypothesis_index - 1],
                )

    suffix = [array("H", [0]) * width for _ in range(len(reference_tokens) + 1)]
    for reference_index in range(len(reference_tokens) - 1, -1, -1):
        current_row = suffix[reference_index]
        next_row = suffix[reference_index + 1]
        for hypothesis_index in range(len(hypothesis_tokens) - 1, -1, -1):
            if reference_tokens[reference_index] == hypothesis_tokens[hypothesis_index]:
                current_row[hypothesis_index] = next_row[hypothesis_index + 1] + 1
            else:
                current_row[hypothesis_index] = max(
                    next_row[hypothesis_index],
                    current_row[hypothesis_index + 1],
                )
    return forward, suffix


def _optimal_match_candidates(
    reference_tokens: Sequence[str],
    hypothesis_tokens: Sequence[str],
    forward: Sequence[array[int]],
    suffix: Sequence[array[int]],
) -> list[list[tuple[int, int]]]:
    """Group every optimal exact-match pair by its LCS rank."""
    lcs_length = int(forward[-1][-1])
    candidates: list[list[tuple[int, int]]] = [[] for _ in range(lcs_length + 1)]
    hypothesis_positions: dict[str, list[int]] = {}
    for hypothesis_index, token in enumerate(hypothesis_tokens):
        hypothesis_positions.setdefault(token, []).append(hypothesis_index)

    for reference_index, token in enumerate(reference_tokens):
        for hypothesis_index in hypothesis_positions.get(token, []):
            prefix_length = int(forward[reference_index][hypothesis_index])
            suffix_length = int(suffix[reference_index + 1][hypothesis_index + 1])
            if prefix_length + 1 + suffix_length != lcs_length:
                continue
            candidates[prefix_length + 1].append((reference_index, hypothesis_index))
    return candidates


def _alignment_cost(
    reference_length: int,
    hypothesis_length: int,
    forward: Sequence[array[int]],
) -> int:
    """Return insertion/deletion cost with substitutions valued at two."""
    return (
        reference_length
        + hypothesis_length
        - 2 * int(forward[reference_length][hypothesis_length])
    )


def _selected_operations(
    reference_tokens: Sequence[str],
    hypothesis_tokens: Sequence[str],
    forward: Sequence[array[int]],
) -> list[tuple[str, int | None, int | None]]:
    """Choose one deterministic optimal edit path for explicit accounting."""
    reference_index = len(reference_tokens)
    hypothesis_index = len(hypothesis_tokens)
    operations: list[tuple[str, int | None, int | None]] = []

    while reference_index > 0 or hypothesis_index > 0:
        current_cost = _alignment_cost(
            reference_index,
            hypothesis_index,
            forward,
        )
        if (
            reference_index > 0
            and hypothesis_index > 0
            and reference_tokens[reference_index - 1]
            == hypothesis_tokens[hypothesis_index - 1]
            and int(forward[reference_index][hypothesis_index])
            == int(forward[reference_index - 1][hypothesis_index - 1]) + 1
        ):
            operations.append(("match", reference_index - 1, hypothesis_index - 1))
            reference_index -= 1
            hypothesis_index -= 1
            continue

        if reference_index > 0 and hypothesis_index > 0:
            diagonal_cost = (
                _alignment_cost(
                    reference_index - 1,
                    hypothesis_index - 1,
                    forward,
                )
                + 2
            )
            if diagonal_cost == current_cost:
                operations.append(
                    ("substitution", reference_index - 1, hypothesis_index - 1)
                )
                reference_index -= 1
                hypothesis_index -= 1
                continue

        if reference_index > 0:
            deletion_cost = (
                _alignment_cost(
                    reference_index - 1,
                    hypothesis_index,
                    forward,
                )
                + 1
            )
            if deletion_cost == current_cost:
                operations.append(("deletion", reference_index - 1, None))
                reference_index -= 1
                continue

        if hypothesis_index > 0:
            operations.append(("insertion", None, hypothesis_index - 1))
            hypothesis_index -= 1

    operations.reverse()
    return operations


def _classification_counts(values: Sequence[str]) -> dict[str, int]:
    """Count deterministic word-accounting classes."""
    counts: dict[str, int] = {}
    for value in values:
        counts[value] = counts.get(value, 0) + 1
    return dict(sorted(counts.items()))


def align_transcript_words(
    reference_words: Sequence[ReferenceWord],
    hypothesis_words: Sequence[HypothesisWord],
) -> dict[str, Any]:
    """Align one reference stream to displayed words without guessing ambiguous ownership.

    :param reference_words: Expected words for one comparison stream; empty means no reference wording can own a displayed word.
    :param hypothesis_words: User-visible words in display order; empty means the saved transcript contained no comparable wording.

    :returns: Exact forced matches plus complete word-accounting classes; empty inputs produce zero counts and empty match lists.
    :raises ValueError: If either word stream exceeds the compact 65,535-word alignment limit.
    :raises RuntimeError: If the selected alignment cannot account for every reference and displayed word.
    """
    reference_tokens = [word.token for word in reference_words]
    hypothesis_tokens = [word.token for word in hypothesis_words]
    forward, suffix = _lcs_tables(reference_tokens, hypothesis_tokens)
    candidates_by_rank = _optimal_match_candidates(
        reference_tokens,
        hypothesis_tokens,
        forward,
        suffix,
    )

    forced_pairs: list[tuple[int, int, int]] = []
    ambiguous_reference_indices: set[int] = set()
    ambiguous_hypothesis_indices: set[int] = set()
    ambiguous_rank_count = 0
    for rank, candidates in enumerate(candidates_by_rank[1:], start=1):
        if len(candidates) == 1:
            reference_index, hypothesis_index = candidates[0]
            forced_pairs.append((rank, reference_index, hypothesis_index))
            continue
        ambiguous_rank_count += 1
        ambiguous_reference_indices.update(
            reference_index for reference_index, _ in candidates
        )
        ambiguous_hypothesis_indices.update(
            hypothesis_index for _, hypothesis_index in candidates
        )

    reference_classes = ["" for _ in reference_words]
    hypothesis_classes = ["" for _ in hypothesis_words]
    for _, reference_index, hypothesis_index in forced_pairs:
        reference_classes[reference_index] = "exact_unambiguous"
        hypothesis_classes[hypothesis_index] = "exact_unambiguous"
    for reference_index in ambiguous_reference_indices:
        if reference_classes[reference_index] == "":
            reference_classes[reference_index] = "ambiguous_exact"
    for hypothesis_index in ambiguous_hypothesis_indices:
        if hypothesis_classes[hypothesis_index] == "":
            hypothesis_classes[hypothesis_index] = "ambiguous_exact"

    selected_operations = _selected_operations(
        reference_tokens,
        hypothesis_tokens,
        forward,
    )
    selected_operation_counts = {
        "match": 0,
        "substitution": 0,
        "insertion": 0,
        "deletion": 0,
    }
    for operation, reference_index, hypothesis_index in selected_operations:
        selected_operation_counts[operation] += 1
        if reference_index is not None and reference_classes[reference_index] == "":
            reference_classes[reference_index] = (
                "substituted" if operation == "substitution" else "deleted"
            )
        if hypothesis_index is not None and hypothesis_classes[hypothesis_index] == "":
            hypothesis_classes[hypothesis_index] = (
                "substituted" if operation == "substitution" else "inserted"
            )

    if "" in reference_classes or "" in hypothesis_classes:
        raise RuntimeError("word accounting did not close")

    matches = []
    for rank, reference_index, hypothesis_index in forced_pairs:
        reference_word = reference_words[reference_index]
        hypothesis_word = hypothesis_words[hypothesis_index]
        matches.append(
            {
                "rank": rank,
                "reference_index": reference_index,
                "hypothesis_index": hypothesis_index,
                "reference": reference_word.to_dict(),
                "hypothesis": hypothesis_word.to_dict(),
            }
        )

    return {
        "algorithm": {
            "name": "forced-optimal-lcs",
            "substitution_cost": 2,
            "insertion_cost": 1,
            "deletion_cost": 1,
            "tie_break": ["match", "substitution", "deletion", "insertion"],
        },
        "reference_words": len(reference_words),
        "hypothesis_words": len(hypothesis_words),
        "lcs_length": int(forward[-1][-1]),
        "unambiguous_match_count": len(matches),
        "ambiguous_rank_count": ambiguous_rank_count,
        "reference_accounting": _classification_counts(reference_classes),
        "hypothesis_accounting": _classification_counts(hypothesis_classes),
        "selected_operation_counts": selected_operation_counts,
        "ambiguous_reference_indices": sorted(ambiguous_reference_indices),
        "ambiguous_hypothesis_indices": sorted(ambiguous_hypothesis_indices),
        "matches": matches,
    }


def align_role_channels(
    reference_words_by_role: Mapping[str, Sequence[ReferenceWord]],
    hypothesis_words: Sequence[HypothesisWord],
) -> dict[str, Any]:
    """Align each role independently so overlapping speakers remain unscored.

    Use this for ownership reports where separate TextGrid channels cannot prove one shared spoken order.

    :param reference_words_by_role: Expected words grouped by role; an empty mapping means no role has reference ownership evidence.
    :param hypothesis_words: User-visible words in display order; empty means there is no transcript wording to attribute.

    :returns: Per-role alignments and conflict-safe matches; empty inputs produce zero counts and empty ownership lists.
    :raises ValueError: If any role or displayed word stream exceeds the compact alignment limit.
    :raises RuntimeError: If an underlying alignment cannot close its word accounting.
    """
    role_alignments = {
        role: align_transcript_words(reference_words, hypothesis_words)
        for role, reference_words in sorted(reference_words_by_role.items())
    }
    forced_by_hypothesis: dict[int, list[tuple[str, dict[str, Any]]]] = {}
    ambiguous_hypothesis_by_role: dict[str, set[int]] = {}
    for role, role_alignment in role_alignments.items():
        ambiguous_hypothesis_by_role[role] = set(
            role_alignment["ambiguous_hypothesis_indices"]
        )
        for match in role_alignment["matches"]:
            forced_by_hypothesis.setdefault(
                int(match["hypothesis_index"]),
                [],
            ).append((role, match))

    accepted_matches: list[dict[str, Any]] = []
    rejected_forced_by_role: dict[str, set[int]] = {
        role: set() for role in role_alignments
    }
    cross_role_ambiguous_indices: set[int] = set()
    for hypothesis_index, candidates in forced_by_hypothesis.items():
        candidate_roles = {role for role, _ in candidates}
        ambiguous_other_roles = {
            role
            for role, indices in ambiguous_hypothesis_by_role.items()
            if hypothesis_index in indices and role not in candidate_roles
        }
        if len(candidate_roles) != 1 or ambiguous_other_roles:
            cross_role_ambiguous_indices.add(hypothesis_index)
            for role, match in candidates:
                rejected_forced_by_role[role].add(int(match["reference_index"]))
            continue

        role, match = candidates[0]
        accepted_match = dict(match)
        accepted_match["role_rank"] = accepted_match.pop("rank")
        accepted_match["alignment_role"] = role
        accepted_matches.append(accepted_match)

    accepted_matches.sort(
        key=lambda match: (
            int(match["hypothesis_index"]),
            str(match["alignment_role"]),
            int(match["reference_index"]),
        )
    )
    for rank, match in enumerate(accepted_matches, start=1):
        match["rank"] = rank

    accepted_hypothesis_indices = {
        int(match["hypothesis_index"]) for match in accepted_matches
    }
    cross_role_ambiguous_indices.update(
        hypothesis_index
        for hypothesis_index in set().union(*ambiguous_hypothesis_by_role.values())
        if sum(
            hypothesis_index in indices
            for indices in ambiguous_hypothesis_by_role.values()
        )
        > 1
    )
    within_role_ambiguous_indices = (
        set().union(*ambiguous_hypothesis_by_role.values())
        - accepted_hypothesis_indices
        - cross_role_ambiguous_indices
    )
    ambiguous_hypothesis_words = []
    for hypothesis_index in sorted(
        cross_role_ambiguous_indices | within_role_ambiguous_indices
    ):
        possible_roles = sorted(
            {role for role, _ in forced_by_hypothesis.get(hypothesis_index, [])}
            | {
                role
                for role, indices in ambiguous_hypothesis_by_role.items()
                if hypothesis_index in indices
            }
        )
        ambiguous_hypothesis_words.append(
            {
                "classification": (
                    "cross_role_ambiguous"
                    if hypothesis_index in cross_role_ambiguous_indices
                    else "within_role_ambiguous"
                ),
                "hypothesis_index": hypothesis_index,
                "possible_roles": possible_roles,
                "hypothesis": hypothesis_words[hypothesis_index].to_dict(),
            }
        )
    hypothesis_classes = []
    for hypothesis_index in range(len(hypothesis_words)):
        if hypothesis_index in accepted_hypothesis_indices:
            hypothesis_classes.append("exact_unambiguous")
        elif hypothesis_index in cross_role_ambiguous_indices:
            hypothesis_classes.append("cross_role_ambiguous")
        elif hypothesis_index in within_role_ambiguous_indices:
            hypothesis_classes.append("within_role_ambiguous")
        else:
            hypothesis_classes.append("unmatched")

    reference_accounting_by_role: dict[str, dict[str, int]] = {}
    role_summaries: dict[str, dict[str, Any]] = {}
    for role, role_alignment in role_alignments.items():
        reference_accounting = dict(role_alignment["reference_accounting"])
        rejected_count = len(rejected_forced_by_role[role])
        if rejected_count:
            reference_accounting["exact_unambiguous"] -= rejected_count
            if reference_accounting["exact_unambiguous"] == 0:
                reference_accounting.pop("exact_unambiguous")
            reference_accounting["cross_role_ambiguous"] = rejected_count
        reference_accounting_by_role[role] = dict(sorted(reference_accounting.items()))
        role_summaries[role] = {
            "reference_words": role_alignment["reference_words"],
            "lcs_length": role_alignment["lcs_length"],
            "unambiguous_match_count_before_cross_role_filter": (
                role_alignment["unambiguous_match_count"]
            ),
            "ambiguous_rank_count": role_alignment["ambiguous_rank_count"],
            "reference_accounting": reference_accounting_by_role[role],
            "selected_operation_counts": role_alignment["selected_operation_counts"],
        }

    return {
        "algorithm": {
            "name": "independent-forced-optimal-lcs-by-role",
            "cross_role_rule": (
                "reject a hypothesis word if another role has a forced or "
                "ambiguous optimal exact match for it"
            ),
        },
        "reference_words": sum(
            len(reference_words) for reference_words in reference_words_by_role.values()
        ),
        "hypothesis_words": len(hypothesis_words),
        "unambiguous_match_count": len(accepted_matches),
        "cross_role_ambiguous_count": len(cross_role_ambiguous_indices),
        "within_role_ambiguous_count": len(within_role_ambiguous_indices),
        "hypothesis_accounting": _classification_counts(hypothesis_classes),
        "reference_accounting_by_role": reference_accounting_by_role,
        "cross_role_ambiguous_hypothesis_indices": sorted(cross_role_ambiguous_indices),
        "within_role_ambiguous_hypothesis_indices": sorted(
            within_role_ambiguous_indices
        ),
        "ambiguous_hypothesis_words": ambiguous_hypothesis_words,
        "role_alignments": role_summaries,
        "matches": accepted_matches,
    }
