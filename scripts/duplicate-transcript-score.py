#!/usr/bin/env python3
"""Score repeated transcript spans shown under different speaker identities.

Operators run this after a replay to measure whether the same normalized words
appeared twice at overlapping spoken times or as a grounded decoder variant.
Clinical wording is compared only in memory; JSON evidence contains safe row/
session IDs, roles, timing, counts, and metadata-derived pair IDs so M05 can
diagnose identity behavior safely.
"""

from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import dataclass
from functools import cache
import hashlib
import json
from operator import attrgetter
from pathlib import Path
import re
import sys
from typing import Any


SUPPORTED_REFERENCE_ROLES = {"DOCTOR", "PATIENT"}
GROUNDED_REPEAT_MAX_START_GAP_SECONDS = 0.5
GROUNDED_REPEAT_SHARED_PHRASE_WORDS = 4
GROUNDED_REPEAT_MIN_SEQUENCE_SIMILARITY = 0.8


@dataclass(frozen=True)
class VisibleHistoryRow:
    """Hold one browser-visible row while the operator report is assembled.

    Normalized wording stays process-local and is never serialized. Safe row
    metadata becomes the duplicate evidence a developer can inspect after a replay.

    Attributes:
        row_index: Zero-based position used to join safe TextGrid diagnostics.
        segment_id: Stable UI row ID, or a safe ordinal for legacy history.
        speaker_id: Visible source identity; never empty after validation.
        role: Visible role, or UNKNOWN when the user saw no confident role.
        start_seconds: Spoken row start; zero means speech began immediately.
        end_seconds: Spoken row end; always later than start after validation.
        normalized_text: Process-local comparison wording; never serialized.
        word_count: Positive comparable-word total; empty rows are excluded.
    """

    row_index: int
    segment_id: str
    speaker_id: str
    role: str
    start_seconds: float
    end_seconds: float
    normalized_text: str
    word_count: int


@dataclass(frozen=True)
class GroundedRowDiagnostic:
    """Hold TextGrid ownership for one visible row during offline acceptance.

    Operators use this safe timing/role record to distinguish a decoder repeat
    from genuine Doctor/Patient cross-talk without retaining reference wording.
    """

    expected_role: str | None
    in_overlap: bool


def parse_arguments() -> argparse.Namespace:
    """Read the artifacts and optional report destination selected by the operator.

    Returns:
        Parsed CLI values; a missing output path means JSON is printed to stdout.
    """
    argument_parser = argparse.ArgumentParser(
        description="Score exact and grounded duplicate transcript rows without outputting wording.",
    )
    argument_parser.add_argument(
        "paths",
        nargs="+",
        type=Path,
        help="Live-history JSON files or run directories containing live-history.json.",
    )
    argument_parser.add_argument(
        "--output",
        type=Path,
        help="Write JSON to this path; omit to print the full report to stdout.",
    )
    return argument_parser.parse_args()


def discover_live_history_paths(selected_paths: list[Path]) -> list[Path]:
    """Resolve direct histories and run directories into unique artifacts.

    Args:
        selected_paths: Operator selections; an empty list yields no evidence.

    Returns:
        Sorted artifact paths; empty means no live-history JSON was found.

    Raises:
        ValueError: A selected path is missing or no live histories are available.
    """
    discovered_paths: set[Path] = set()

    # Each selection may be one renamed browser artifact or a complete fixture run.
    for selected_path in selected_paths:
        # A direct JSON selection supports retained browser artifacts such as M08's capture.
        if selected_path.is_file():
            discovered_paths.add(selected_path.resolve())
            continue

        # A run directory contributes only live histories, never corrected-note artifacts.
        if selected_path.is_dir():
            # Every fixture folder owns at most one final browser-visible history.
            for history_path in selected_path.rglob("live-history.json"):
                discovered_paths.add(history_path.resolve())
            continue

        raise ValueError(f"selected path does not exist: {selected_path}")

    # Empty discovery usually means the operator selected the wrong run directory.
    if discovered_paths == set():
        raise ValueError("no live-history artifacts found")

    return sorted(discovered_paths)


def normalize_visible_text(visible_text: str) -> str:
    """Normalize wording only for an in-memory equality comparison.

    Args:
        visible_text: Browser row wording; empty or punctuation-only input normalizes empty.

    Returns:
        Lowercase ASCII words separated by one space; empty means no comparable wording.
    """
    lowercase_text = visible_text.casefold()
    alphanumeric_words = re.sub(r"[^a-z0-9]+", " ", lowercase_text)
    return " ".join(alphanumeric_words.split())


def safe_session_id(raw_session_id: Any) -> str:
    """Return a safe session label for an artifact missing normal metadata.

    Args:
        raw_session_id: Parsed session value; null/empty/non-text means unknown session.

    Returns:
        Existing non-empty session ID, or `unknown-session` for legacy artifacts.
    """
    # Legacy browser captures may omit the session field but are still scoreable.
    if not isinstance(raw_session_id, str) or raw_session_id.strip() == "":
        return "unknown-session"

    return raw_session_id.strip()


def numeric_row_time(
    raw_time: Any,
    *,
    artifact_path: Path,
    row_number: int,
    field_name: str,
) -> float:
    """Read one safe row timestamp used to align what the clinician saw.

    Args:
        raw_time: Parsed JSON value; null/non-numeric values are invalid evidence.
        artifact_path: Selected history path reported on safe validation failure.
        row_number: One-based row location for operator repair.
        field_name: `start` or `end`; never empty for a caller-owned field.

    Returns:
        Timestamp seconds as a float; zero means speech began with the recording.

    Raises:
        ValueError: The row lacks a usable numeric timestamp.
    """
    # Booleans are Python numbers but cannot describe a spoken-time position.
    if isinstance(raw_time, bool) or not isinstance(raw_time, (int, float)):
        raise ValueError(
            f"{artifact_path}: row {row_number} {field_name} must be numeric"
        )

    return float(raw_time)


def visible_history_row(
    raw_row: Any,
    *,
    artifact_path: Path,
    row_number: int,
) -> VisibleHistoryRow | None:
    """Validate one visible row and retain only comparable wording in memory.

    Args:
        raw_row: Parsed row object; null/list/scalar values are invalid evidence.
        artifact_path: Selected history path used only in safe error messages.
        row_number: One-based row position; used when segment ID is absent.

    Returns:
        Comparable row, or None when the user-visible wording is empty.

    Raises:
        ValueError: Required speaker, text, or timing fields cannot be scored safely.
    """
    # A scalar row cannot represent one browser-visible transcript line.
    if not isinstance(raw_row, dict):
        raise ValueError(f"{artifact_path}: row {row_number} must be an object")

    raw_text = raw_row.get("text")
    # Missing or non-text wording makes the artifact contract malformed.
    if not isinstance(raw_text, str):
        raise ValueError(f"{artifact_path}: row {row_number} text must be a string")

    normalized_text = normalize_visible_text(raw_text)
    # An empty row has no wording to duplicate and stays outside candidate groups.
    if normalized_text == "":
        return None

    raw_speaker_id = raw_row.get("speaker_id")
    # Without a speaker identity, the scorer cannot prove a cross-identity duplicate.
    if not isinstance(raw_speaker_id, str) or raw_speaker_id.strip() == "":
        raise ValueError(
            f"{artifact_path}: row {row_number} speaker_id must be non-empty"
        )

    start_seconds = numeric_row_time(
        raw_row.get("start"),
        artifact_path=artifact_path,
        row_number=row_number,
        field_name="start",
    )
    end_seconds = numeric_row_time(
        raw_row.get("end"),
        artifact_path=artifact_path,
        row_number=row_number,
        field_name="end",
    )
    # A zero/negative span cannot overlap wording the clinician heard.
    if end_seconds <= start_seconds:
        raise ValueError(f"{artifact_path}: row {row_number} end must follow start")

    raw_segment_id = raw_row.get("segment_id")
    segment_id = f"row-{row_number:04d}"
    # Modern rows keep their server ID; legacy rows receive a safe deterministic ordinal.
    if isinstance(raw_segment_id, str) and raw_segment_id.strip() != "":
        segment_id = raw_segment_id.strip()

    raw_role = raw_row.get("role")
    role = "UNKNOWN"
    # Missing/empty role means the user saw no confident Doctor/Patient ownership.
    if isinstance(raw_role, str) and raw_role.strip() != "":
        role = raw_role.strip()

    return VisibleHistoryRow(
        row_index=row_number - 1,
        segment_id=segment_id,
        speaker_id=raw_speaker_id.strip(),
        role=role,
        start_seconds=start_seconds,
        end_seconds=end_seconds,
        normalized_text=normalized_text,
        word_count=len(normalized_text.split()),
    )


def discover_row_diagnostics_path(
    history_path: Path,
) -> Path | None:
    """Find TextGrid-derived ownership for one retained browser history.

    Args:
        history_path: Visible history being scored; never absent after discovery.

    Returns:
        Resolved diagnostics path, or None when this artifact has no grounding evidence.
    """
    sibling_candidates = [
        history_path.with_name("live-row-diagnostics.json"),
        history_path.with_name("row-diagnostics.json"),
    ]
    # Renamed manual captures keep their prefix while replacing the history suffix.
    if "live-history" in history_path.name:
        sibling_candidates.insert(
            0,
            history_path.with_name(
                history_path.name.replace("live-history", "row-diagnostics")
            ),
        )
    # The first existing corpus/browser convention grounds this history deterministically.
    for candidate_path in sibling_candidates:
        # A missing sibling means this older artifact remains exact-scoreable but ungrounded.
        if not candidate_path.is_file():
            continue
        return candidate_path.resolve()

    return None


def grounded_diagnostic_for_visible_row(
    diagnostics_path: Path,
    raw_diagnostic_row: Any,
    comparable_row: VisibleHistoryRow,
) -> GroundedRowDiagnostic:
    """Validate TextGrid ownership for one transcript row the clinician can read.

    Args:
        diagnostics_path: Safe evidence path used in errors; never absent during grounding.
        raw_diagnostic_row: Parsed row object; null/scalar input is stale evidence.
        comparable_row: Matching visible row; its wording stays process-local.

    Returns:
        TextGrid role/overlap evidence; a null role means ownership remains unresolved.

    Raises:
        ValueError: The diagnostic row does not align with the selected browser history.
    """
    row_index = comparable_row.row_index
    row_number = row_index + 1
    # A stale row shape or ordinal cannot ground the wording the clinician read.
    if (
        not isinstance(raw_diagnostic_row, dict)
        or raw_diagnostic_row.get("row_index") != row_index
    ):
        raise ValueError(f"{diagnostics_path}: row {row_number} is misaligned")
    # A different source ID proves this evidence belongs to another history revision.
    if raw_diagnostic_row.get("speaker_id") != comparable_row.speaker_id:
        raise ValueError(
            f"{diagnostics_path}: row {row_number} speaker_id does not match history"
        )
    # Different timing would join wording to the wrong TextGrid speaker span.
    if (
        raw_diagnostic_row.get("start") != comparable_row.start_seconds
        or raw_diagnostic_row.get("end") != comparable_row.end_seconds
    ):
        raise ValueError(
            f"{diagnostics_path}: row {row_number} timing does not match history"
        )

    raw_expected_role = raw_diagnostic_row.get("expected_role")
    expected_role = (
        raw_expected_role.strip().upper()
        if isinstance(raw_expected_role, str) and raw_expected_role.strip()
        else None
    )
    # Unknown ownership stays unscored; other labels cannot enter a Doctor/Patient gate.
    if expected_role is not None and expected_role not in SUPPORTED_REFERENCE_ROLES:
        raise ValueError(
            f"{diagnostics_path}: row {row_number} expected_role is unsupported"
        )
    raw_in_overlap = raw_diagnostic_row.get("in_overlap")
    # A real boolean is required to preserve both speakers during genuine cross-talk.
    if not isinstance(raw_in_overlap, bool):
        raise ValueError(
            f"{diagnostics_path}: row {row_number} in_overlap must be boolean"
        )
    return GroundedRowDiagnostic(expected_role, raw_in_overlap)


def load_grounded_row_diagnostics(
    diagnostics_path: Path,
    visible_row_count: int,
    comparable_rows: list[VisibleHistoryRow],
) -> dict[int, GroundedRowDiagnostic]:
    """Validate TextGrid ownership against the exact visible rows being scored.

    Use before prevalence scoring so stale replay diagnostics cannot approve a user-visible fix.

    Args:
        diagnostics_path: Safe row-diagnostics JSON; empty files are invalid evidence.
        visible_row_count: Full history row total, including empty wording rows.
        comparable_rows: Non-empty history rows; empty means only shape validation is possible.

    Returns:
        Diagnostics keyed by visible row index; empty means the visit had no visible rows.

    Raises:
        ValueError: Diagnostics are malformed, stale, or misaligned with the selected history.
    """
    try:
        parsed_diagnostics = json.loads(diagnostics_path.read_text(encoding="utf-8"))
    # A copied acceptance artifact can be truncated before the operator runs the scorer.
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(
            f"cannot read row diagnostics JSON: {diagnostics_path}"
        ) from error

    raw_diagnostic_rows = (
        parsed_diagnostics.get("rows") if isinstance(parsed_diagnostics, dict) else None
    )
    # Only an ordered row list can ground the clinician's visible transcript.
    if not isinstance(raw_diagnostic_rows, list):
        raise ValueError(f"{diagnostics_path}: rows must be a list")
    # Different totals prove the diagnostics came from another replay or cutoff.
    if len(raw_diagnostic_rows) != visible_row_count:
        raise ValueError(f"{diagnostics_path}: row count does not match history")

    diagnostics_by_row_index: dict[int, GroundedRowDiagnostic] = {}
    # Empty wording stays aligned by total count; only comparable rows need TextGrid ownership.
    for comparable_row in comparable_rows:
        row_index = comparable_row.row_index
        diagnostics_by_row_index[row_index] = grounded_diagnostic_for_visible_row(
            diagnostics_path,
            raw_diagnostic_rows[row_index],
            comparable_row,
        )

    return diagnostics_by_row_index


def load_visible_history(
    artifact_path: Path,
) -> tuple[str, int, list[VisibleHistoryRow]]:
    """Load one retained browser history without returning its raw wording.

    Args:
        artifact_path: History JSON selected by the operator; empty files are invalid.

    Returns:
        Session ID, original visible-row count, and comparable non-empty rows.

    Raises:
        ValueError: The file is unreadable, malformed JSON, or has an invalid row shape.
    """
    try:
        parsed_artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
    # A copied replay can be truncated or disappear before the operator scores it.
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"cannot read history JSON: {artifact_path}") from error

    # A history object must carry session metadata and its visible segment list.
    if not isinstance(parsed_artifact, dict):
        raise ValueError(f"{artifact_path}: history must be an object")

    raw_visible_rows = parsed_artifact.get("segments")
    # A dict/scalar segments field cannot preserve the user's ordered transcript rows.
    if not isinstance(raw_visible_rows, list):
        raise ValueError(f"{artifact_path}: segments must be a list")

    comparable_rows: list[VisibleHistoryRow] = []
    # Validate every visible row so a malformed line cannot silently lower duplicate counts.
    for row_index, raw_row in enumerate(raw_visible_rows):
        comparable_row = visible_history_row(
            raw_row,
            artifact_path=artifact_path,
            row_number=row_index + 1,
        )
        # Empty wording remains part of the visible-row total but cannot form a duplicate.
        if comparable_row is None:
            continue

        comparable_rows.append(comparable_row)

    return (
        safe_session_id(parsed_artifact.get("session_id")),
        len(raw_visible_rows),
        comparable_rows,
    )


def spoken_overlap_seconds(
    left_row: VisibleHistoryRow,
    right_row: VisibleHistoryRow,
) -> float:
    """Return positive spoken-time overlap for two rows the user saw.

    Args:
        left_row: Earlier sorted row; never absent after artifact validation.
        right_row: Later comparison row; never absent after artifact validation.

    Returns:
        Shared seconds, or zero when the rows were spoken at separate times.
    """
    return max(
        0.0,
        min(left_row.end_seconds, right_row.end_seconds)
        - max(left_row.start_seconds, right_row.start_seconds),
    )


def longest_common_word_subsequence_count(
    left_words: tuple[str, ...],
    right_words: tuple[str, ...],
) -> int:
    """Count ordered words shared by two candidate rows without exposing them.
    Args:
        left_words: First row's normalized words; empty means no comparable wording.
        right_words: Second row's normalized words; empty means no comparable wording.
    Returns:
        Longest shared ordered-word count; zero means the rows do not restate each other.
    """

    @cache
    def shared_words_from(left_index: int, right_index: int) -> int:
        """Count the best ordered match from two process-local word positions.
        Args:
            left_index: First-row position; its end means no wording remains to compare.
            right_index: Later-row position; its end means no wording remains to compare.
        Returns:
            Remaining shared-word count; zero means the user would not reread another word.
        """
        # Reaching either row's end leaves no wording for the clinician to reread.
        if left_index == len(left_words) or right_index == len(right_words):
            return 0
        # Matching words extend the user-visible sequence before both rows advance.
        if left_words[left_index] == right_words[right_index]:
            return 1 + shared_words_from(left_index + 1, right_index + 1)
        return max(
            shared_words_from(left_index + 1, right_index),
            shared_words_from(left_index, right_index + 1),
        )

    return shared_words_from(0, 0)


def decoder_variant_word_match(
    left_words: tuple[str, ...],
    right_words: tuple[str, ...],
) -> tuple[float, str] | None:
    """Classify safely repeated wording before a decoder row can be hidden from the user.
    Args:
        left_words: First normalized row; empty means no phrase can be repeated.
        right_words: Later normalized row; empty means no phrase can be repeated.
    Returns:
        Score and safe rule name, or None when both rows must remain visible.
    """
    left_word_counts = Counter(left_words)
    right_word_counts = Counter(right_words)
    left_only_words = left_word_counts - right_word_counts
    right_only_words = right_word_counts - left_word_counts
    vocabulary_match = "equal_multiset"
    # A mismatch is safe only for the retained decoder's one-for-one and/but substitution.
    if left_only_words or right_only_words:
        is_and_but_substitution = (
            left_only_words == Counter({"and": 1})
            and right_only_words == Counter({"but": 1})
        ) or (
            left_only_words == Counter({"but": 1})
            and right_only_words == Counter({"and": 1})
        )
        # Any other word could change the symptom, denial, medicine, or qualifier shown to the user.
        if not is_and_but_substitution:
            return None
        vocabulary_match = "and_but_substitution"
    # Short rows cannot prove one cache slot decoded another slot's phrase.
    if (
        len(left_words) < GROUNDED_REPEAT_SHARED_PHRASE_WORDS
        or len(right_words) < GROUNDED_REPEAT_SHARED_PHRASE_WORDS
    ):
        return None
    # Candidate phrases stay process-local so consultation wording never enters the report.
    left_phrases = {
        left_words[start_index : start_index + GROUNDED_REPEAT_SHARED_PHRASE_WORDS]
        for start_index in range(
            len(left_words) - GROUNDED_REPEAT_SHARED_PHRASE_WORDS + 1
        )
    }
    # Four consecutive words distinguish a decoder re-read from common clinical vocabulary.
    if not any(
        right_words[start_index : start_index + GROUNDED_REPEAT_SHARED_PHRASE_WORDS]
        in left_phrases
        for start_index in range(
            len(right_words) - GROUNDED_REPEAT_SHARED_PHRASE_WORDS + 1
        )
    ):
        return None
    shared_word_count = longest_common_word_subsequence_count(left_words, right_words)
    sequence_similarity = (2.0 * shared_word_count) / (
        len(left_words) + len(right_words)
    )
    # Weakly ordered rows can carry different meaning despite safe vocabulary.
    if sequence_similarity + 1e-9 < GROUNDED_REPEAT_MIN_SEQUENCE_SIMILARITY:
        return None
    return sequence_similarity, vocabulary_match


def grounded_decoder_repeat_match(
    left_row: VisibleHistoryRow,
    right_row: VisibleHistoryRow,
    left_diagnostic: GroundedRowDiagnostic,
    right_diagnostic: GroundedRowDiagnostic,
) -> tuple[float, str] | None:
    """Return the safe match when TextGrid proves one person's audio was decoded twice.
    Args:
        left_row: Earlier visible row; never absent after history validation.
        right_row: Later visible row; never absent after history validation.
        left_diagnostic: First row's TextGrid owner; unknown ownership returns None.
        right_diagnostic: Second row's TextGrid owner; unknown ownership returns None.
    Returns:
        Score and safe rule name, or None when both rows must stay visible for the clinician.
    """
    # One source identity repeating itself is ordinary conversation, not dual identity.
    if left_row.speaker_id == right_row.speaker_id:
        return None
    # Unknown or different reference owners cannot prove one person's audio was decoded twice.
    if (
        left_diagnostic.expected_role is None
        or left_diagnostic.expected_role != right_diagnostic.expected_role
    ):
        return None
    # Genuine Doctor/Patient cross-talk keeps both voices even when their wording sounds similar.
    if left_diagnostic.in_overlap or right_diagnostic.in_overlap:
        return None
    start_offset_seconds = abs(right_row.start_seconds - left_row.start_seconds)
    # A later start belongs to another user utterance rather than the same decoder instant.
    if start_offset_seconds > GROUNDED_REPEAT_MAX_START_GAP_SECONDS + 1e-9:
        return None
    left_words = tuple(left_row.normalized_text.split())
    right_words = tuple(right_row.normalized_text.split())
    return decoder_variant_word_match(left_words, right_words)


def safe_pair_identifier(
    identifier_prefix: str,
    comparison_kind: str | None,
    session_id: str,
    left_row: VisibleHistoryRow,
    right_row: VisibleHistoryRow,
) -> str:
    """Create an opaque pair label from safe row metadata only.
    Args:
        identifier_prefix: Safe report label; empty would produce an invalid leading dash.
        comparison_kind: Version marker, or None to preserve the original exact-pair ID.
        session_id: Replay identifier; `unknown-session` represents missing metadata.
        left_row: First row in deterministic spoken-time order.
        right_row: Second row in deterministic spoken-time order.
    Returns:
        Opaque stable ID; it cannot be tested against consultation wording.
    """
    safe_pair_metadata: list[Any] = [
        session_id,
        left_row.segment_id,
        left_row.speaker_id,
        round(left_row.start_seconds, 3),
        round(left_row.end_seconds, 3),
        right_row.segment_id,
        right_row.speaker_id,
        round(right_row.start_seconds, 3),
        round(right_row.end_seconds, 3),
    ]
    # A grounded marker versions the new metric without changing committed exact-pair IDs.
    if comparison_kind is not None:
        safe_pair_metadata.insert(0, comparison_kind)
    metadata_bytes = json.dumps(
        safe_pair_metadata,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"{identifier_prefix}-{hashlib.sha256(metadata_bytes).hexdigest()[:16]}"


def duplicate_pair_report(
    session_id: str,
    left_row: VisibleHistoryRow,
    right_row: VisibleHistoryRow,
    overlap_seconds: float,
) -> dict[str, Any]:
    """Build the PHI-safe evidence one operator reviews for a repeated span.
    Args:
        session_id: Replay identifier; never empty after safe fallback handling.
        left_row: First duplicate row in spoken-time order.
        right_row: Second duplicate row in spoken-time order.
        overlap_seconds: Positive shared time; zero is filtered before this function.
    Returns:
        Safe pair evidence; arrival offset is null because final history cannot measure it.
    """
    return {
        "arrival_offset_seconds": None,
        "duplicate_pair_id": safe_pair_identifier(
            "duplicate",
            None,
            session_id,
            left_row,
            right_row,
        ),
        "left_end_seconds": round(left_row.end_seconds, 3),
        "left_role": left_row.role,
        "left_segment_id": left_row.segment_id,
        "left_speaker_id": left_row.speaker_id,
        "left_start_seconds": round(left_row.start_seconds, 3),
        "overlap_seconds": round(overlap_seconds, 3),
        "right_end_seconds": round(right_row.end_seconds, 3),
        "right_role": right_row.role,
        "right_segment_id": right_row.segment_id,
        "right_speaker_id": right_row.speaker_id,
        "right_start_seconds": round(right_row.start_seconds, 3),
        "spoken_start_offset_seconds": round(
            abs(right_row.start_seconds - left_row.start_seconds),
            3,
        ),
        "word_count": left_row.word_count,
    }


def find_duplicate_pairs(
    session_id: str,
    comparable_rows: list[VisibleHistoryRow],
) -> list[dict[str, Any]]:
    """Find same-word overlapping rows assigned to different visible identities.
    Args:
        session_id: Replay identifier used only in safe pair IDs.
        comparable_rows: Non-empty validated rows; empty means no candidate exists.
    Returns:
        Deterministically ordered safe reports; empty means no duplicate pair was measured.
    """
    rows_by_normalized_text: dict[str, list[VisibleHistoryRow]] = {}
    # Group equal wording in memory without allowing it into the final report.
    for comparable_row in comparable_rows:
        rows_by_normalized_text.setdefault(comparable_row.normalized_text, []).append(
            comparable_row
        )

    duplicate_pairs: list[dict[str, Any]] = []
    # Sorted groups and rows keep pair IDs/report ordering reproducible across runs.
    for normalized_text in sorted(rows_by_normalized_text):
        grouped_rows = sorted(
            rows_by_normalized_text[normalized_text],
            key=attrgetter("start_seconds", "end_seconds", "speaker_id", "segment_id"),
        )
        # Each pair is considered once so decoder revisions cannot inflate the report twice.
        for left_index, left_row in enumerate(grouped_rows):
            # Only later rows remain after the current left side.
            for right_row in grouped_rows[left_index + 1 :]:
                # Repeated wording under one speaker is normal continuation, not dual identity.
                if left_row.speaker_id == right_row.speaker_id:
                    continue

                overlap_seconds = spoken_overlap_seconds(left_row, right_row)
                # Matching words spoken at separate times are ordinary repeated conversation.
                if overlap_seconds <= 0.0:
                    continue

                duplicate_pairs.append(
                    duplicate_pair_report(
                        session_id,
                        left_row,
                        right_row,
                        overlap_seconds,
                    )
                )

    return duplicate_pairs


def grounded_decoder_repeat_pair_report(
    session_id: str,
    left_row: VisibleHistoryRow,
    right_row: VisibleHistoryRow,
    expected_role: str,
    sequence_similarity: float,
    vocabulary_match: str,
) -> dict[str, Any]:
    """Build PHI-safe evidence for one TextGrid-confirmed decoder re-read.
    Args:
        session_id: Replay identifier; never empty after safe fallback handling.
        left_row: First visible candidate in spoken-time order.
        right_row: Second visible candidate in spoken-time order.
        expected_role: TextGrid Doctor/Patient owner shared by both rows.
        sequence_similarity: Accepted 0-1 word-order score; never below the threshold.
        vocabulary_match: Safe equal-multiset or one-for-one connector rule used.
    Returns:
        Safe timing/identity evidence; no wording or reversible wording hash is present.
    """
    return {
        "decoder_repeat_pair_id": safe_pair_identifier(
            "decoder-repeat",
            "grounded-decoder-repeat-v2",
            session_id,
            left_row,
            right_row,
        ),
        "expected_role": expected_role,
        "left_segment_id": left_row.segment_id,
        "left_speaker_id": left_row.speaker_id,
        "left_start_seconds": round(left_row.start_seconds, 3),
        "left_word_count": left_row.word_count,
        "right_segment_id": right_row.segment_id,
        "right_speaker_id": right_row.speaker_id,
        "right_start_seconds": round(right_row.start_seconds, 3),
        "right_word_count": right_row.word_count,
        "sequence_similarity": round(sequence_similarity, 3),
        "shared_phrase_word_count": GROUNDED_REPEAT_SHARED_PHRASE_WORDS,
        "spoken_start_offset_seconds": round(
            abs(right_row.start_seconds - left_row.start_seconds),
            3,
        ),
        "vocabulary_match": vocabulary_match,
    }


def find_grounded_decoder_repeats(
    session_id: str,
    comparable_rows: list[VisibleHistoryRow],
    diagnostics_by_row_index: dict[int, GroundedRowDiagnostic],
) -> list[dict[str, Any]]:
    """Find variant wording emitted twice for one TextGrid-owned audio moment.
    Args:
        session_id: Replay identifier used only in safe pair IDs.
        comparable_rows: Non-empty visible rows; empty means no repeat can exist.
        diagnostics_by_row_index: Aligned TextGrid ownership; empty means no rows were visible.

    Returns:
        Deterministically ordered safe pair reports; empty means no grounded repeat was measured.
    """
    chronological_rows = sorted(
        comparable_rows,
        key=attrgetter("start_seconds", "end_seconds", "speaker_id", "segment_id"),
    )
    grounded_pairs: list[dict[str, Any]] = []
    # Each earlier row is compared only with later starts inside the 0.5-second decoder window.
    for left_index, left_row in enumerate(chronological_rows):
        left_diagnostic = diagnostics_by_row_index[left_row.row_index]
        # Later rows are considered once, preserving deterministic pair counts.
        for right_row in chronological_rows[left_index + 1 :]:
            # Sorted starts let the scorer stop before unrelated later conversation.
            if (
                right_row.start_seconds - left_row.start_seconds
                > GROUNDED_REPEAT_MAX_START_GAP_SECONDS + 1e-9
            ):
                break

            right_diagnostic = diagnostics_by_row_index[right_row.row_index]
            decoder_repeat_match = grounded_decoder_repeat_match(
                left_row,
                right_row,
                left_diagnostic,
                right_diagnostic,
            )
            # A rejected pair stays visible and contributes no acceptance evidence.
            if decoder_repeat_match is None:
                continue

            sequence_similarity, vocabulary_match = decoder_repeat_match
            grounded_pairs.append(
                grounded_decoder_repeat_pair_report(
                    session_id,
                    left_row,
                    right_row,
                    left_diagnostic.expected_role or "UNKNOWN",
                    sequence_similarity,
                    vocabulary_match,
                )
            )

    return grounded_pairs


def score_history_artifact(artifact_path: Path) -> dict[str, Any]:
    """Score one final browser history for cross-identity duplicate spans.

    Args:
        artifact_path: Retained history selected by the operator; never absent after discovery.

    Returns:
        PHI-safe artifact summary; duplicate pairs are empty when none were measured.
    """
    session_id, visible_row_count, comparable_rows = load_visible_history(artifact_path)
    duplicate_pairs = find_duplicate_pairs(session_id, comparable_rows)
    diagnostics_path = discover_row_diagnostics_path(artifact_path)
    grounded_decoder_repeats: dict[str, Any] = {
        "diagnostics_path": None,
        "pair_count": None,
        "pairs": [],
        "status": "unavailable",
    }
    # Grounded evidence is optional so legacy histories retain their exact-score outcome.
    if diagnostics_path is not None:
        diagnostics_by_row_index = load_grounded_row_diagnostics(
            diagnostics_path,
            visible_row_count,
            comparable_rows,
        )
        grounded_pairs = find_grounded_decoder_repeats(
            session_id,
            comparable_rows,
            diagnostics_by_row_index,
        )
        grounded_decoder_repeats = {
            "diagnostics_path": str(diagnostics_path),
            "pair_count": len(grounded_pairs),
            "pairs": grounded_pairs,
            "status": "scored",
        }

    return {
        "artifact_path": str(artifact_path),
        "duplicate_pair_count": len(duplicate_pairs),
        "duplicate_pairs": duplicate_pairs,
        "grounded_decoder_repeats": grounded_decoder_repeats,
        "session_id": session_id,
        "visible_row_count": visible_row_count,
    }


def build_duplicate_report(artifact_paths: list[Path]) -> dict[str, Any]:
    """Combine selected history scores into one Phase 0 prevalence report.

    Args:
        artifact_paths: Discovered histories; empty input produces an explicit zero report.

    Returns:
        Artifact details and aggregate counts with no consultation wording.
    """
    # One safe report per fixture lets operators locate affected retained evidence.
    artifact_reports = [score_history_artifact(path) for path in artifact_paths]

    # Only grounded artifacts contribute counts; missing diagnostics remain unavailable, not zero.
    grounded_reports = [
        artifact_report["grounded_decoder_repeats"]
        for artifact_report in artifact_reports
        if artifact_report["grounded_decoder_repeats"]["status"] == "scored"
    ]
    grounded_pair_count = sum(int(item["pair_count"]) for item in grounded_reports)
    grounded_affected_count = sum(
        int(item["pair_count"]) > 0 for item in grounded_reports
    )
    exact_pair_count = sum(
        int(item["duplicate_pair_count"]) for item in artifact_reports
    )
    exact_affected_count = sum(
        int(item["duplicate_pair_count"]) > 0 for item in artifact_reports
    )
    visible_row_count = sum(int(item["visible_row_count"]) for item in artifact_reports)

    return {
        "artifacts": artifact_reports,
        "grounded_decoder_repeats": {
            "affected_artifact_count": grounded_affected_count,
            "pair_count": grounded_pair_count,
            "scored_artifact_count": len(grounded_reports),
            "unavailable_artifact_count": len(artifact_reports) - len(grounded_reports),
        },
        "schema_version": 2,
        "scorer": "duplicate-transcript-score",
        "summary": {
            "affected_artifacts": exact_affected_count,
            "artifact_count": len(artifact_reports),
            "duplicate_pair_count": exact_pair_count,
            "visible_row_count": visible_row_count,
        },
    }


def write_or_print_report(
    report: dict[str, Any],
    output_path: Path | None,
) -> None:
    """Deliver JSON to stdout or the operator's selected evidence path.

    Args:
        report: PHI-safe scorer output; empty artifact lists remain valid JSON.
        output_path: Destination path, or None to print the complete report.
    """
    serialized_report = json.dumps(report, indent=2, sort_keys=True)
    # A selected path retains evidence while stdout stays a compact safe sentinel.
    if output_path is not None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(f"{serialized_report}\n", encoding="utf-8")
        summary = report["summary"]
        print(
            "duplicate-transcript-score "
            f"artifacts={summary['artifact_count']} "
            f"affected={summary['affected_artifacts']} "
            f"pairs={summary['duplicate_pair_count']} "
            f"grounded_scored={report['grounded_decoder_repeats']['scored_artifact_count']} "
            f"grounded_pairs={report['grounded_decoder_repeats']['pair_count']}"
        )
        return

    print(serialized_report)


def main() -> int:
    """Score the selected replay evidence without changing transcript behavior.

    Returns:
        Process code; 0 means a report was produced, 2 means evidence was invalid.
    """
    arguments = parse_arguments()
    try:
        artifact_paths = discover_live_history_paths(arguments.paths)
        report = build_duplicate_report(artifact_paths)
        write_or_print_report(report, arguments.output)
    # An operator may select a stale/malformed replay or an unwritable evidence folder.
    except (OSError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2

    return 0


# A direct operator run reads retained evidence and emits only the safe report.
if __name__ == "__main__":
    raise SystemExit(main())
