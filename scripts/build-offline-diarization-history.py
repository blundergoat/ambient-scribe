#!/usr/bin/env python3
"""Build a scoreable history from offline diarization and ASR words.

Use this after an offline diarizer has produced speaker-time turns and the
post-visit ASR probe has produced timed words. It assigns words to diarization
turns, optionally applies the scorer's best valid dyadic oracle mapping, and
writes a `history.json` candidate for `scripts/transcript-quality.py`.
The output is a QA artifact only and does not change runtime correction.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class OfflineSpeakerTurn:
    """One offline diarization turn used as a speaker scaffold.

    These turns come from the post-stop diarization candidate, not from live
    preview rows. Empty speaker IDs become Unknown rows because the user would
    have no reliable Doctor/Patient speaker scaffold.

    Attributes:
        turn_index: One-based turn order used in QA diagnostics.
        segment_id: Diarization segment ID; empty becomes a generated ID.
        speaker_id: Candidate speaker label; empty means no speaker can be shown.
        start: Turn start in seconds; zero means weak timing evidence.
        end: Turn end in seconds; clamped to start if the artifact goes backwards.
    """

    turn_index: int
    segment_id: str
    speaker_id: str
    start: float
    end: float


@dataclass(frozen=True)
class TimedAsrWord:
    """One post-visit ASR word used for offline speaker alignment.

    The word is the corrected text a clinician might read after Stop. Empty
    words are dropped so the scorer does not see blank transcript rows.

    Attributes:
        word_index: ASR word order used for diagnostics; fallback order preserves reading order.
        word: Corrected word text; empty words are not emitted.
        start: Estimated word start in seconds; zero means weak timing evidence.
        end: Estimated word end in seconds; clamped to start if the probe goes backwards.
    """

    word_index: int
    word: str
    start: float
    end: float


@dataclass(frozen=True)
class OfflineWordAssignment:
    """One corrected word assigned to an offline speaker turn.

    This is the candidate's word-speaker decision before transcript-quality
    scoring. Unassigned words remain visible but cannot get a confident role.

    Attributes:
        timed_word: Corrected ASR word being placed into a scoreable row.
        speaker_turn: Offline speaker turn that owns the word; null means Unknown speaker.
        match_type: Assignment evidence label; empty is not emitted by this builder.
    """

    timed_word: TimedAsrWord
    speaker_turn: OfflineSpeakerTurn | None
    match_type: str


def load_json_object(artifact_path: Path) -> dict[str, Any]:
    """Read one JSON artifact for offline diarization scoring.

    Args:
        artifact_path: Developer-selected artifact; missing or empty means no score input exists.

    Returns:
        Parsed JSON object; empty objects are allowed for defensive empty candidates.

    Raises:
        FileNotFoundError: When the selected artifact path is unavailable.
        ValueError: When the artifact is not a JSON object.
    """
    # Missing artifacts mean the earlier probe did not produce evidence to score.
    if not artifact_path.exists():
        raise FileNotFoundError(f"artifact not found: {artifact_path}")

    parsed_artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
    # Non-object JSON cannot carry diarization turns or word timings.
    if not isinstance(parsed_artifact, dict):
        raise ValueError(f"artifact must be a JSON object: {artifact_path}")

    return parsed_artifact


def artifact_float(value: Any, default_value: float = 0.0) -> float:
    """Convert an artifact value into seconds.

    Args:
        value: Artifact number or string; null/empty means the score has no precise time.
        default_value: Fallback seconds shown to the scorer when the field is missing.

    Returns:
        Parsed float seconds; fallback means weaker alignment evidence.
    """
    # Empty timing values should not crash QA; they become the scoring fallback.
    if value is None or value == "":
        return default_value

    try:
        return float(value)
    except (TypeError, ValueError):
        return default_value


def offline_turn_from_artifact(raw_turn: dict[str, Any], turn_index: int) -> OfflineSpeakerTurn:
    """Normalize one diarization segment into an offline speaker turn.

    Args:
        raw_turn: Diarization segment from a candidate artifact; empty fields weaken scoring.
        turn_index: One-based segment order; zero or negative values are not expected.

    Returns:
        Offline speaker turn with clamped timing for word assignment.
    """
    turn_start = artifact_float(raw_turn.get("start"))
    turn_end = artifact_float(raw_turn.get("end"), turn_start)
    # Backwards turns would invert the transcript row span on screen.
    if turn_end < turn_start:
        turn_end = turn_start

    segment_id = str(raw_turn.get("segment_id", ""))
    # Missing segment IDs still need a stable row ID for the QA report.
    if segment_id == "":
        segment_id = f"diar-{turn_index:04d}"

    return OfflineSpeakerTurn(
        turn_index=turn_index,
        segment_id=segment_id,
        speaker_id=str(raw_turn.get("speaker_id", "")),
        start=turn_start,
        end=turn_end,
    )


def timed_word_from_artifact(raw_word: dict[str, Any], fallback_index: int) -> TimedAsrWord:
    """Normalize one ASR word timing row.

    Args:
        raw_word: Word timing from the ASR probe; empty word text means no visible word.
        fallback_index: Word order when the probe omitted an index; negative values are not expected.

    Returns:
        Timed ASR word with clamped timing for scorer-compatible rows.
    """
    word_start = artifact_float(raw_word.get("start"))
    word_end = artifact_float(raw_word.get("end"), word_start)
    # Backwards word timing would make the candidate row impossible to score fairly.
    if word_end < word_start:
        word_end = word_start

    raw_word_index = raw_word.get("index", fallback_index)
    # Missing word indexes keep the ASR reading order visible in diagnostics.
    if raw_word_index is None or raw_word_index == "":
        raw_word_index = fallback_index

    return TimedAsrWord(
        word_index=int(raw_word_index),
        word=str(raw_word.get("word", "")),
        start=word_start,
        end=word_end,
    )


def offline_turns_from_artifact(diarization_artifact: dict[str, Any]) -> list[OfflineSpeakerTurn]:
    """Read candidate speaker turns from an offline diarization artifact.

    Args:
        diarization_artifact: Raw offline diarization JSON; missing segments means no scaffold.

    Returns:
        Offline turns in time order; empty means all ASR words become Unknown speaker rows.
    """
    turns: list[OfflineSpeakerTurn] = []
    raw_turns = diarization_artifact.get("diarization_segments", [])
    # Each raw segment is one offline speaker turn the post-stop transcript may use.
    for turn_index, raw_turn in enumerate(raw_turns, start=1):
        # Malformed turns cannot identify who spoke for the user.
        if not isinstance(raw_turn, dict):
            continue

        turns.append(offline_turn_from_artifact(raw_turn, turn_index))

    return sorted(turns, key=lambda turn: (turn.start, turn.end, turn.speaker_id))


def timed_words_from_report(timestamp_report: dict[str, Any]) -> list[TimedAsrWord]:
    """Read timed post-visit ASR words.

    Args:
        timestamp_report: ASR timing report; missing word timings means no corrected text.

    Returns:
        Timed words in ASR order; empty means the scorer sees a blank transcript.
    """
    timed_words: list[TimedAsrWord] = []
    raw_word_timings = timestamp_report.get("word_timings", [])
    # Every timed ASR word can become part of the post-stop transcript.
    for fallback_index, raw_word in enumerate(raw_word_timings):
        # Malformed word rows cannot be safely placed into a speaker turn.
        if not isinstance(raw_word, dict):
            continue

        timed_word = timed_word_from_artifact(raw_word, fallback_index)
        # Blank ASR tokens would create empty words in the transcript.
        if timed_word.word.strip() == "":
            continue

        timed_words.append(timed_word)

    return timed_words


def word_center_seconds(timed_word: TimedAsrWord) -> float:
    """Return the center time used to place one corrected word.

    Args:
        timed_word: Corrected ASR word; equal start/end means the center is that instant.

    Returns:
        Word center in seconds; zero means weak timing evidence.
    """
    return (timed_word.start + timed_word.end) / 2


def choose_turn_for_word(
    timed_word: TimedAsrWord,
    speaker_turns: list[OfflineSpeakerTurn],
) -> OfflineWordAssignment:
    """Assign one ASR word to a containing offline speaker turn.

    Args:
        timed_word: Corrected word the final transcript may show; empty timing weakens assignment.
        speaker_turns: Offline speaker turns; empty means the word remains Unknown speaker.

    Returns:
        Word assignment; unassigned means no offline speaker owned the word center.
    """
    # Without offline turns, corrected words cannot inherit any speaker identity.
    if speaker_turns == []:
        return OfflineWordAssignment(timed_word, None, "unassigned")

    word_center = word_center_seconds(timed_word)
    containing_turns: list[OfflineSpeakerTurn] = []
    # The word inherits a speaker only when its center falls inside that speaker's turn.
    for speaker_turn in speaker_turns:
        # Words outside this turn remain candidates for a later turn or stay Unknown.
        if not speaker_turn.start <= word_center <= speaker_turn.end:
            continue

        containing_turns.append(speaker_turn)

    # A gap between diarization turns leaves the word visible but speaker-unknown.
    if containing_turns == []:
        return OfflineWordAssignment(timed_word, None, "gap_unassigned")

    # Overlapping turns are resolved to the shortest turn so broad spans do not swallow precise ones.
    if len(containing_turns) > 1:
        chosen_turn = min(
            containing_turns,
            key=lambda turn: (turn.end - turn.start, turn.turn_index),
        )
        return OfflineWordAssignment(timed_word, chosen_turn, "overlap_choose_shortest")

    return OfflineWordAssignment(timed_word, containing_turns[0], "contains")


def assign_words_to_offline_turns(
    timed_words: list[TimedAsrWord],
    speaker_turns: list[OfflineSpeakerTurn],
) -> list[OfflineWordAssignment]:
    """Attach every corrected word to an offline speaker turn.

    Args:
        timed_words: Timed ASR words; empty means no scoreable text.
        speaker_turns: Offline diarization turns; empty means every word is Unknown speaker.

    Returns:
        Word assignments in spoken order; empty means the transcript is blank.
    """
    assignments: list[OfflineWordAssignment] = []
    # Every corrected word is assigned in order so the transcript remains readable.
    for timed_word in timed_words:
        assignments.append(choose_turn_for_word(timed_word, speaker_turns))

    return assignments


def assignment_row_key(assignment: OfflineWordAssignment) -> tuple[str, str, str]:
    """Return the grouping key for consecutive offline-aligned words.

    Args:
        assignment: Word assignment; null turn means an Unknown speaker row.

    Returns:
        Stable grouping key; changes create a new transcript row.
    """
    # Unassigned words should not borrow the previous offline speaker.
    if assignment.speaker_turn is None:
        return ("unassigned", "UNKNOWN", assignment.match_type)

    return (
        assignment.speaker_turn.segment_id,
        assignment.speaker_turn.speaker_id,
        assignment.match_type,
    )


def row_from_assignments(
    row_number: int,
    row_assignments: list[OfflineWordAssignment],
    speaker_role_mapping: dict[str, str],
) -> dict[str, Any]:
    """Create one scoreable transcript row from offline word assignments.

    Args:
        row_number: One-based row number; zero would make diagnostics hard to read.
        row_assignments: Consecutive words with one speaker assignment; empty is defensive only.
        speaker_role_mapping: Speaker-to-role mapping; empty means rows stay Unknown.

    Returns:
        `history.json` segment row for transcript-quality scoring.
    """
    # Empty groups are defensive only; normal candidates never show them to the scorer.
    if row_assignments == []:
        return {
            "segment_id": f"offline-align-{row_number:04d}",
            "speaker_id": "UNKNOWN",
            "role": "UNKNOWN",
            "text": "",
            "start": 0.0,
            "end": 0.0,
            "is_interim": False,
            "revision": 1,
        }

    first_assignment = row_assignments[0]
    last_assignment = row_assignments[-1]
    speaker_turn = first_assignment.speaker_turn
    speaker_id = "UNKNOWN"
    role = "UNKNOWN"
    source_segment_id = None

    # Assigned words inherit the offline speaker turn for score-time attribution.
    if speaker_turn is not None:
        speaker_id = speaker_turn.speaker_id
        # Empty speaker IDs mean the diarizer gave no user-visible owner for this row.
        if speaker_id == "":
            speaker_id = "UNKNOWN"

        # Unmapped speaker IDs stay Unknown so scoring penalizes missing Doctor/Patient labels.
        role = speaker_role_mapping.get(speaker_id, "UNKNOWN")
        source_segment_id = speaker_turn.segment_id

    row_words: list[str] = []
    # Row text is the corrected ASR word sequence assigned to this speaker turn.
    for assignment in row_assignments:
        row_words.append(assignment.timed_word.word)

    return {
        "segment_id": f"offline-align-{row_number:04d}",
        "speaker_id": speaker_id,
        "role": role,
        "text": " ".join(row_words),
        "start": round(first_assignment.timed_word.start, 3),
        "end": round(last_assignment.timed_word.end, 3),
        "is_interim": False,
        "revision": 1,
        "alignment_source": "offline_diarization_word_timing_candidate",
        "alignment_match": first_assignment.match_type,
        "source_segment_id": source_segment_id,
        "word_start_index": first_assignment.timed_word.word_index,
        "word_end_index": last_assignment.timed_word.word_index,
    }


def rows_from_assignments(
    assignments: list[OfflineWordAssignment],
    speaker_role_mapping: dict[str, str],
) -> list[dict[str, Any]]:
    """Group offline word assignments into scoreable transcript rows.

    Args:
        assignments: Word assignments in spoken order; empty means no transcript rows.
        speaker_role_mapping: Speaker-to-role mapping; empty keeps roles Unknown.

    Returns:
        History-compatible rows; empty means the transcript is blank.
    """
    rows: list[dict[str, Any]] = []
    current_group: list[OfflineWordAssignment] = []
    current_key: tuple[str, str, str] | None = None

    # Consecutive words owned by the same offline turn stay in one row.
    for assignment in assignments:
        assignment_key = assignment_row_key(assignment)
        # A changed offline turn starts the next row the scorer will inspect.
        if current_key is not None and assignment_key != current_key:
            rows.append(
                row_from_assignments(
                    len(rows) + 1,
                    current_group,
                    speaker_role_mapping,
                )
            )
            current_group = []

        current_group.append(assignment)
        current_key = assignment_key

    # The final group still needs to be written after the loop ends.
    if current_group != []:
        rows.append(row_from_assignments(len(rows) + 1, current_group, speaker_role_mapping))

    return rows


def load_transcript_quality_module(module_path: Path) -> Any:
    """Load transcript-quality helpers without renaming the existing script.

    Args:
        module_path: Path to `scripts/transcript-quality.py`; missing files fail the builder.

    Returns:
        Imported module object with TextGrid parsing and dyadic mapping helpers.

    Raises:
        ImportError: When Python cannot load the scorer helpers.
    """
    module_spec = importlib.util.spec_from_file_location("transcript_quality", module_path)
    # A missing loader means the scorer script cannot provide the shared TextGrid semantics.
    if module_spec is None or module_spec.loader is None:
        raise ImportError(f"cannot load transcript-quality helpers from {module_path}")

    module = importlib.util.module_from_spec(module_spec)
    # Dataclass helpers need the module registered during import to resolve their annotations.
    sys.modules[module_spec.name] = module
    module_spec.loader.exec_module(module)
    return module


def reference_intervals_from_textgrids(
    scorer_module: Any,
    textgrid_paths: list[Path],
    cutoff_seconds: float,
) -> list[Any]:
    """Read scorer-compatible reference intervals from TextGrid files.

    Args:
        scorer_module: Loaded transcript-quality module; missing helpers fail the builder.
        textgrid_paths: Doctor/patient TextGrid paths; empty means no oracle mapping exists.
        cutoff_seconds: Session cutoff; zero means no reference speech can be scored.

    Returns:
        Reference intervals in file order; empty means oracle mapping is unavailable.
    """
    reference_intervals: list[Any] = []
    # Each TextGrid channel contributes role-labelled speech for oracle mapping.
    for textgrid_path in textgrid_paths:
        reference_intervals.extend(
            scorer_module.textgrid_intervals(textgrid_path, cutoff_seconds)
        )

    return reference_intervals


def best_dyadic_mapping_for_rows(
    scorer_module: Any,
    candidate_rows: list[dict[str, Any]],
    reference_intervals: list[Any],
) -> dict[str, str]:
    """Return the scorer's best valid Doctor/Patient speaker mapping.

    Args:
        scorer_module: Loaded transcript-quality module; missing helpers fail the builder.
        candidate_rows: Offline-aligned rows; empty means no mapping exists.
        reference_intervals: Doctor/patient references; empty means no mapping exists.

    Returns:
        Speaker-to-role map; empty means the candidate did not expose exactly two scoreable speakers.
    """
    scorer_segments: list[Any] = []
    # Each offline row becomes the same hypothesis shape used by the live transcript scorer.
    for candidate_row in candidate_rows:
        scorer_segments.append(
            scorer_module.HypothesisSegment(
                start=artifact_float(candidate_row.get("start")),
                end=artifact_float(candidate_row.get("end")),
                speaker_id=str(candidate_row.get("speaker_id", "")),
                role=str(candidate_row.get("role", "")),
                text=str(candidate_row.get("text", "")),
            )
        )

    dyadic_ceiling = scorer_module.score_best_dyadic_mapping(
        scorer_segments,
        reference_intervals,
    )
    return dict(dyadic_ceiling.best_mapping)


def assignment_counts(assignments: list[OfflineWordAssignment]) -> dict[str, int]:
    """Count word assignment outcomes for QA metadata.

    Args:
        assignments: Word assignments; empty means all counts are zero.

    Returns:
        Counts by match type; missing keys mean that match type did not occur.
    """
    counts: dict[str, int] = {}
    # Match counts show whether words landed inside turns or fell into gaps.
    for assignment in assignments:
        counts[assignment.match_type] = counts.get(assignment.match_type, 0) + 1

    return counts


def build_offline_history(
    diarization_artifact: dict[str, Any],
    timestamp_report: dict[str, Any],
    scorer_module: Any,
    textgrid_paths: list[Path],
    cutoff_seconds: float,
    role_mode: str,
) -> dict[str, Any]:
    """Build a history candidate from offline diarization and timed ASR words.

    Args:
        diarization_artifact: Raw offline diarization JSON; empty turns make Unknown rows.
        timestamp_report: ASR word timing JSON; empty words make a blank transcript.
        scorer_module: Loaded transcript-quality module for oracle mapping.
        textgrid_paths: Doctor/patient TextGrid paths; empty disables oracle role mapping.
        cutoff_seconds: Session cutoff used for fair scoring.
        role_mode: `oracle` maps two speakers with references; `unknown` leaves roles unset.

    Returns:
        Scoreable history JSON plus candidate metadata.
    """
    speaker_turns = offline_turns_from_artifact(diarization_artifact)
    timed_words = timed_words_from_report(timestamp_report)
    assignments = assign_words_to_offline_turns(timed_words, speaker_turns)
    unknown_rows = rows_from_assignments(assignments, {})
    speaker_role_mapping: dict[str, str] = {}

    # Oracle mode proves diarization ceiling only; it is not a shippable role path.
    if role_mode == "oracle":
        reference_intervals = reference_intervals_from_textgrids(
            scorer_module,
            textgrid_paths,
            cutoff_seconds,
        )
        speaker_role_mapping = best_dyadic_mapping_for_rows(
            scorer_module,
            unknown_rows,
            reference_intervals,
        )

    speaker_ids: set[str] = set()
    # Unique non-empty speaker IDs show how many diarizer voices the user would need labeled.
    for speaker_turn in speaker_turns:
        # Empty speaker IDs cannot be labeled as Doctor or Patient.
        if speaker_turn.speaker_id == "":
            continue

        speaker_ids.add(speaker_turn.speaker_id)

    return {
        "source": "offline_diarization_history_candidate",
        "segments": rows_from_assignments(assignments, speaker_role_mapping),
        "candidate_metadata": {
            "candidate": diarization_artifact.get("candidate", {}),
            "audio": diarization_artifact.get("audio", {}),
            "word_source": {
                "model": timestamp_report.get("model"),
                "artifact_seconds": timestamp_report.get("seconds"),
                "word_timing_strategy": timestamp_report.get("word_timing_strategy"),
            },
            "role_mode": role_mode,
            "speaker_role_mapping": speaker_role_mapping,
            "speaker_turn_count": len(speaker_turns),
            "speaker_count": len(speaker_ids),
            "word_count": len(timed_words),
            "assignment_counts": assignment_counts(assignments),
        },
    }


def parse_args() -> argparse.Namespace:
    """Parse offline history builder inputs.

    Returns:
        Parsed CLI options; missing artifact paths exit through argparse.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--diarization", type=Path, required=True)
    parser.add_argument("--timestamp-report", type=Path, required=True)
    parser.add_argument(
        "--transcript-quality",
        type=Path,
        default=Path("scripts/transcript-quality.py"),
    )
    parser.add_argument("--textgrid", action="append", type=Path, default=[])
    parser.add_argument("--cutoff-seconds", type=float, required=True)
    parser.add_argument("--role-mode", choices=["oracle", "unknown"], default="oracle")
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    """Write one offline diarization history candidate.

    Returns:
        Process exit code; zero means the scoreable history artifact was written.
    """
    args = parse_args()
    scorer_module = load_transcript_quality_module(args.transcript_quality)
    candidate_history = build_offline_history(
        diarization_artifact=load_json_object(args.diarization),
        timestamp_report=load_json_object(args.timestamp_report),
        scorer_module=scorer_module,
        textgrid_paths=args.textgrid,
        cutoff_seconds=args.cutoff_seconds,
        role_mode=args.role_mode,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(candidate_history, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    metadata = candidate_history["candidate_metadata"]
    print(
        "offline-diarization-history "
        f"mode={metadata['role_mode']} "
        f"speakers={metadata['speaker_count']} "
        f"words={metadata['word_count']} "
        f"assignments={metadata['assignment_counts']} "
        f"mapping={metadata['speaker_role_mapping']} "
        f"output={args.output}"
    )
    return 0


# Command-line use writes the scoreable fixture artifact.
if __name__ == "__main__":
    raise SystemExit(main())
