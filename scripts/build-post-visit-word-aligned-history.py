#!/usr/bin/env python3
"""Build a scoreable post-visit word-speaker candidate.

Use this developer-only script after a stopped consultation has both a post-visit
ASR word-timing probe and a live preview history artifact. It assigns each
corrected ASR word to the live row the clinician saw at that time, then writes a
`history.json`-compatible candidate for `scripts/transcript-quality.py`.
The output is QA evidence only and does not change correction storage, summaries,
FastAPI payloads, browser events, or model loading.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class LiveTranscriptRow:
    """One live preview row used as the speaker/role scaffold.

    These rows are what the clinician saw before the post-visit correction pass.
    Empty text still matters because the timing may preserve a speaker turn that
    the corrected ASR model dropped or blurred.

    Attributes:
        row_index: One-based visible row number used in QA reports.
        segment_id: Live row ID shown in history; empty becomes a generated row ID.
        speaker_id: Live speaker ID the user saw; empty means the row has no stable voice.
        role: Doctor/Patient label the user saw; empty becomes UNKNOWN for scoring.
        text: Live row text; empty means the row only contributes timing/role context.
        start: Row start in seconds; zero means no useful timing was stored.
        end: Row end in seconds; clamped to start when the artifact goes backwards.
    """

    row_index: int
    segment_id: str
    speaker_id: str
    role: str
    text: str
    start: float
    end: float


@dataclass(frozen=True)
class TimedAsrWord:
    """One corrected ASR word with estimated timing.

    These words come from the post-stop ASR candidate, not from the live preview.
    Use them when testing whether word timing can replace row-level allocation
    without hurting the transcript users read after Stop.

    Attributes:
        word_index: ASR word order used for diagnostics; fallback order keeps text readable.
        word: Corrected word the clinician may read; empty words are dropped.
        start: Estimated word start in seconds; zero means weak timing evidence.
        end: Estimated word end in seconds; clamped to start if the probe goes backwards.
    """

    word_index: int
    word: str
    start: float
    end: float


@dataclass(frozen=True)
class WordAssignment:
    """One corrected word attached to the best live preview row.

    The assignment records whether timing landed inside the row or only near it.
    Use the match type to decide whether a candidate is real alignment evidence
    or a diagnostic fallback that still needs better diarization.

    Attributes:
        timed_word: Corrected word being placed into the final candidate transcript.
        live_row: Visible row that supplies speaker/role; null means the UI would show Unknown.
        match_type: Assignment evidence label; empty is not emitted by this builder.
        gap_seconds: Distance to nearest row; zero means contained or unassigned.
    """

    timed_word: TimedAsrWord
    live_row: LiveTranscriptRow | None
    match_type: str
    gap_seconds: float


def load_json_object(artifact_path: Path) -> dict[str, Any]:
    """Read one JSON artifact used by the fixture candidate.

    Args:
        artifact_path: Developer-selected artifact; missing or empty means there is no candidate input.

    Returns:
        Parsed JSON object; empty objects are allowed when the caller wants an empty candidate.

    Raises:
        FileNotFoundError: When the selected artifact path is unavailable.
        ValueError: When the artifact is not a JSON object the scorer can inspect.
    """
    # No artifact means the developer has not produced the required replay/probe evidence yet.
    if not artifact_path.exists():
        raise FileNotFoundError(f"artifact not found: {artifact_path}")

    parsed_artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
    # Non-object JSON cannot carry the `segments` or `word_timings` fields this flow scores.
    if not isinstance(parsed_artifact, dict):
        raise ValueError(f"artifact must be a JSON object: {artifact_path}")

    return parsed_artifact


def artifact_float(value: Any, default_value: float = 0.0) -> float:
    """Convert an artifact value into seconds for scoring.

    Args:
        value: Artifact number or string; null/empty means the UI row has no usable time.
        default_value: Fallback seconds; used when the artifact omitted the field.

    Returns:
        Parsed float seconds; the fallback means the row cannot improve timing precision.
    """
    # Missing artifact values should not crash QA; they become the visible fallback time.
    if value is None or value == "":
        return default_value

    try:
        return float(value)
    except (TypeError, ValueError):
        return default_value


def live_row_from_artifact(raw_row: dict[str, Any], row_index: int) -> LiveTranscriptRow:
    """Normalize one stored live row into the scorer scaffold.

    Args:
        raw_row: Segment from live history; empty fields mean the candidate inherits weak row evidence.
        row_index: One-based visible row number; zero or negative would make row diagnostics confusing.

    Returns:
        Live row with clamped timing and uppercase role for transcript-quality scoring.
    """
    row_start = artifact_float(raw_row.get("start"))
    row_end = artifact_float(raw_row.get("end"), row_start)
    # Bad live timing should still leave a stable row rather than hiding the user's utterance.
    if row_end < row_start:
        row_end = row_start

    return LiveTranscriptRow(
        row_index=row_index,
        segment_id=str(raw_row.get("segment_id", f"live-row-{row_index:04d}")),
        speaker_id=str(raw_row.get("speaker_id", "")),
        role=str(raw_row.get("role", "UNKNOWN")).upper() or "UNKNOWN",
        text=str(raw_row.get("text", "")),
        start=row_start,
        end=row_end,
    )


def timed_word_from_artifact(raw_word: dict[str, Any], fallback_index: int) -> TimedAsrWord:
    """Normalize one post-visit ASR word into candidate timing.

    Args:
        raw_word: Word-timing row from the probe; empty word text means nothing should appear on screen.
        fallback_index: Word order when the probe omitted an index; negative values are not expected.

    Returns:
        Timed ASR word with clamped timing for scorer-compatible transcript rows.
    """
    word_start = artifact_float(raw_word.get("start"))
    word_end = artifact_float(raw_word.get("end"), word_start)
    # Backwards word timing would invert the visible row span, so clamp it for scoring.
    if word_end < word_start:
        word_end = word_start

    return TimedAsrWord(
        word_index=int(raw_word.get("index", fallback_index) or fallback_index),
        word=str(raw_word.get("word", "")),
        start=word_start,
        end=word_end,
    )


def live_rows_from_history(live_history: dict[str, Any]) -> list[LiveTranscriptRow]:
    """Read live preview rows from a replay history artifact.

    Args:
        live_history: History JSON from `/session/{id}/history`; missing segments means no live scaffold.

    Returns:
        Normalized live rows; empty means corrected words cannot inherit speaker identity.
    """
    live_rows: list[LiveTranscriptRow] = []
    raw_segments = live_history.get("segments", [])
    # Each stored segment is one transcript row the clinician could review before Stop.
    for row_index, raw_segment in enumerate(raw_segments, start=1):
        # Malformed rows are skipped because they cannot represent a visible transcript line.
        if not isinstance(raw_segment, dict):
            continue

        live_rows.append(live_row_from_artifact(raw_segment, row_index))

    return live_rows


def timed_words_from_report(timestamp_report: dict[str, Any]) -> list[TimedAsrWord]:
    """Read corrected ASR words from a timestamp probe artifact.

    Args:
        timestamp_report: Probe JSON; missing word timings means the candidate has no corrected text.

    Returns:
        Timed corrected words in ASR order; empty means there is no scoreable candidate text.
    """
    timed_words: list[TimedAsrWord] = []
    raw_word_timings = timestamp_report.get("word_timings", [])
    # Every word timing becomes a possible row in the post-visit transcript candidate.
    for fallback_index, raw_word in enumerate(raw_word_timings):
        # Malformed timing rows cannot safely be placed in the clinician's transcript.
        if not isinstance(raw_word, dict):
            continue

        timed_word = timed_word_from_artifact(raw_word, fallback_index)
        # Empty ASR tokens would create blank transcript rows for the clinician.
        if timed_word.word.strip() == "":
            continue

        timed_words.append(timed_word)

    return timed_words


def word_center_seconds(timed_word: TimedAsrWord) -> float:
    """Return the midpoint used to place one corrected word.

    Args:
        timed_word: Corrected ASR word; equal start/end means the midpoint is that instant.

    Returns:
        Center seconds for live-row assignment; zero means the word has no useful timing.
    """
    return (timed_word.start + timed_word.end) / 2


def choose_live_row_for_word(
    timed_word: TimedAsrWord,
    live_rows: list[LiveTranscriptRow],
    max_nearest_gap_seconds: float | None,
) -> WordAssignment:
    """Assign one corrected word to the best live preview row.

    Args:
        timed_word: Corrected ASR word the final transcript might show; empty timing weakens assignment.
        live_rows: Visible rows from the same replay; empty means the word must stay unassigned.
        max_nearest_gap_seconds: Optional fallback cap; null means nearest-row fallback is unlimited.

    Returns:
        Word assignment; an unassigned row means the UI would need an Unknown speaker label.
    """
    # Without a live scaffold, the corrected word cannot inherit Doctor or Patient.
    if live_rows == []:
        return WordAssignment(timed_word, None, "unassigned", 0.0)

    word_center = word_center_seconds(timed_word)
    nearest_live_row: LiveTranscriptRow | None = None
    nearest_gap_seconds = float("inf")

    # Check direct containment first because it is the clearest row ownership evidence.
    for live_row in live_rows:
        # A contained word belongs to the visible row covering that time.
        if live_row.start <= word_center <= live_row.end:
            return WordAssignment(timed_word, live_row, "contains", 0.0)

        row_gap_seconds = min(
            abs(word_center - live_row.start),
            abs(word_center - live_row.end),
        )
        # The nearest row is the fallback when estimated word timing falls between short live rows.
        if row_gap_seconds < nearest_gap_seconds:
            nearest_gap_seconds = row_gap_seconds
            nearest_live_row = live_row

    # No nearest row means every candidate row stays Unknown instead of inventing a speaker.
    if nearest_live_row is None:
        return WordAssignment(timed_word, None, "unassigned", 0.0)

    # A configured gap cap prevents very distant words from borrowing a misleading visible row.
    if (
        max_nearest_gap_seconds is not None
        and nearest_gap_seconds > max_nearest_gap_seconds
    ):
        return WordAssignment(
            timed_word,
            None,
            "gap_unassigned",
            round(nearest_gap_seconds, 3),
        )

    return WordAssignment(
        timed_word,
        nearest_live_row,
        "nearest",
        round(nearest_gap_seconds, 3),
    )


def assign_corrected_words_to_live_rows(
    timed_words: list[TimedAsrWord],
    live_rows: list[LiveTranscriptRow],
    max_nearest_gap_seconds: float | None,
) -> list[WordAssignment]:
    """Attach every corrected word to the live row that best owns it.

    Args:
        timed_words: Corrected ASR words; empty means the output candidate has no transcript rows.
        live_rows: Live preview rows; empty means all corrected words are Unknown speaker rows.
        max_nearest_gap_seconds: Optional nearest fallback cap; null allows every word to be placed.

    Returns:
        Word assignments in spoken order; empty means the scorer sees an empty transcript.
    """
    assigned_words: list[WordAssignment] = []
    # Every corrected word is placed in order so the final transcript still reads naturally.
    for timed_word in timed_words:
        assigned_words.append(
            choose_live_row_for_word(
                timed_word,
                live_rows,
                max_nearest_gap_seconds,
            )
        )

    return assigned_words


def assignment_row_key(assignment: WordAssignment) -> tuple[str, str, str, str]:
    """Return the grouping key for consecutive candidate words.

    Args:
        assignment: Word with live-row ownership; null live row means an Unknown row in the UI.

    Returns:
        Stable grouping key; changes create a new visible transcript row.
    """
    # Unassigned words should not borrow the previous speaker in the corrected transcript.
    if assignment.live_row is None:
        return ("unassigned", "UNKNOWN", "UNKNOWN", assignment.match_type)

    return (
        assignment.live_row.segment_id,
        assignment.live_row.speaker_id,
        assignment.live_row.role,
        assignment.match_type,
    )


def candidate_row_from_assignments(
    row_number: int,
    row_assignments: list[WordAssignment],
) -> dict[str, Any]:
    """Create one scorer-compatible transcript row from assigned words.

    Args:
        row_number: One-based candidate row number; zero would make diagnostics harder to read.
        row_assignments: Consecutive words with one assignment key; empty would make no visible row.

    Returns:
        History segment row; empty input becomes an Unknown blank row only for defensive callers.
    """
    # Empty assignment groups are defensive only; the user should not see them in normal output.
    if row_assignments == []:
        return {
            "segment_id": f"word-align-{row_number:04d}",
            "speaker_id": "UNKNOWN",
            "role": "UNKNOWN",
            "text": "",
            "start": 0.0,
            "end": 0.0,
            "is_interim": False,
            "revision": 1,
            "alignment_source": "post_visit_word_timing_candidate",
            "alignment_match": "empty",
        }

    first_assignment = row_assignments[0]
    last_assignment = row_assignments[-1]
    live_row = first_assignment.live_row
    speaker_id = "UNKNOWN"
    role = "UNKNOWN"
    source_segment_id = None
    source_row_index = None

    # Assigned words inherit the speaker and role the clinician saw for that live row.
    if live_row is not None:
        speaker_id = live_row.speaker_id
        role = live_row.role
        source_segment_id = live_row.segment_id
        source_row_index = live_row.row_index

    words: list[str] = []
    # The visible candidate text is the corrected ASR word sequence for this row.
    for assignment in row_assignments:
        words.append(assignment.timed_word.word)

    return {
        "segment_id": f"word-align-{row_number:04d}",
        "speaker_id": speaker_id,
        "role": role,
        "text": " ".join(words),
        "start": round(first_assignment.timed_word.start, 3),
        "end": round(last_assignment.timed_word.end, 3),
        "is_interim": False,
        "revision": 1,
        "alignment_source": "post_visit_word_timing_candidate",
        "alignment_match": first_assignment.match_type,
        "alignment_gap_seconds": first_assignment.gap_seconds,
        "source_segment_id": source_segment_id,
        "source_row_index": source_row_index,
        "word_start_index": first_assignment.timed_word.word_index,
        "word_end_index": last_assignment.timed_word.word_index,
    }


def candidate_rows_from_assignments(assignments: list[WordAssignment]) -> list[dict[str, Any]]:
    """Group assigned words into visible transcript rows.

    Args:
        assignments: Word assignments in spoken order; empty means no candidate rows.

    Returns:
        History-compatible segment rows for scoring; empty means the transcript is blank.
    """
    candidate_rows: list[dict[str, Any]] = []
    current_group: list[WordAssignment] = []
    current_key: tuple[str, str, str, str] | None = None

    # Consecutive words with the same live-row ownership stay in one visible row.
    for assignment in assignments:
        assignment_key = assignment_row_key(assignment)
        # A changed speaker/role row starts the next transcript line the clinician would review.
        if current_key is not None and assignment_key != current_key:
            candidate_rows.append(
                candidate_row_from_assignments(
                    len(candidate_rows) + 1,
                    current_group,
                )
            )
            current_group = []

        current_group.append(assignment)
        current_key = assignment_key

    # The final spoken group still needs to reach the scorer after the loop ends.
    if current_group != []:
        candidate_rows.append(
            candidate_row_from_assignments(
                len(candidate_rows) + 1,
                current_group,
            )
        )

    return candidate_rows


def assignment_summary(assignments: list[WordAssignment]) -> dict[str, int]:
    """Count assignment types for the saved QA metadata.

    Args:
        assignments: Word assignments; empty means all counts are zero.

    Returns:
        Counts by match type; missing keys mean that assignment type did not occur.
    """
    counts: dict[str, int] = {}
    # Match counts show whether the candidate used real containment or nearest-row fallback.
    for assignment in assignments:
        counts[assignment.match_type] = counts.get(assignment.match_type, 0) + 1

    return counts


def build_candidate_history(
    timestamp_report: dict[str, Any],
    live_history: dict[str, Any],
    max_nearest_gap_seconds: float | None,
) -> dict[str, Any]:
    """Build the word-aligned candidate history payload.

    Args:
        timestamp_report: M03-style ASR word timing report; empty timings mean blank candidate rows.
        live_history: Live replay history; missing rows mean Unknown speaker candidate rows.
        max_nearest_gap_seconds: Optional nearest fallback cap; null means no cap is applied.

    Returns:
        History JSON compatible with transcript-quality scoring plus QA metadata.
    """
    live_rows = live_rows_from_history(live_history)
    timed_words = timed_words_from_report(timestamp_report)
    assignments = assign_corrected_words_to_live_rows(
        timed_words,
        live_rows,
        max_nearest_gap_seconds,
    )
    candidate_segments = candidate_rows_from_assignments(assignments)

    return {
        "source": "post_visit_word_aligned_history_candidate",
        "session_id": live_history.get("session_id"),
        "duration_seconds": timestamp_report.get("seconds"),
        "segments": candidate_segments,
        "candidate_metadata": {
            "model": timestamp_report.get("model"),
            "audio": timestamp_report.get("audio"),
            "word_timing_strategy": timestamp_report.get("word_timing_strategy"),
            "assignment_strategy": "word-center-contained-then-nearest-live-row",
            "max_nearest_gap_seconds": max_nearest_gap_seconds,
            "word_count": len(timed_words),
            "live_row_count": len(live_rows),
            "candidate_segment_count": len(candidate_segments),
            "assignment_counts": assignment_summary(assignments),
        },
    }


def parse_args() -> argparse.Namespace:
    """Parse local candidate-builder inputs.

    Returns:
        Parsed CLI options; missing required artifact paths exit through argparse.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--timestamp-report", type=Path, required=True)
    parser.add_argument("--live-history", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--max-nearest-gap-seconds",
        type=float,
        default=None,
        help="Optional cap before a corrected word becomes an Unknown-speaker row.",
    )
    return parser.parse_args()


def main() -> int:
    """Write one fixture-only word-aligned candidate history file.

    Returns:
        Process exit code; zero means the candidate artifact was written.
    """
    args = parse_args()
    candidate_history = build_candidate_history(
        load_json_object(args.timestamp_report),
        load_json_object(args.live_history),
        args.max_nearest_gap_seconds,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(candidate_history, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    metadata = candidate_history["candidate_metadata"]
    print(
        "post-visit-word-aligned-history "
        f"words={metadata['word_count']} "
        f"segments={metadata['candidate_segment_count']} "
        f"assignments={metadata['assignment_counts']} "
        f"output={args.output}"
    )
    return 0


# Command-line execution starts the developer QA artifact build.
if __name__ == "__main__":
    raise SystemExit(main())
