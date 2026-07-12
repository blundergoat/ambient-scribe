#!/usr/bin/env python3
"""Score repeated transcript spans shown under different speaker identities.

Operators run this after a replay to measure whether the same normalized words
appeared twice at overlapping spoken times. Clinical wording is compared only
in memory; JSON evidence contains safe row/session IDs, roles, timing, counts,
and a metadata-derived pair ID so M05 can diagnose identity behavior safely.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
import sys
from typing import Any


@dataclass(frozen=True)
class VisibleHistoryRow:
    """Hold one browser-visible row while the operator report is assembled.

    Normalized wording stays process-local and is never serialized. Safe row
    metadata becomes the duplicate evidence a developer can inspect after a replay.

    Attributes:
        segment_id: Stable row ID, or a safe ordinal when legacy history omitted it.
        speaker_id: Visible source identity; never empty after artifact validation.
        role: Visible role, or UNKNOWN when the user saw no confident role.
        start_seconds: Spoken row start; zero means the recording began with speech.
        end_seconds: Spoken row end; always later than start after validation.
        normalized_text: Process-local comparison words; never empty or serialized.
        word_count: Comparable word total; positive because empty rows are excluded.
    """

    segment_id: str
    speaker_id: str
    role: str
    start_seconds: float
    end_seconds: float
    normalized_text: str
    word_count: int


def parse_arguments() -> argparse.Namespace:
    """Read the artifacts and optional report destination selected by the operator.

    Returns:
        Parsed CLI values; a missing output path means JSON is printed to stdout.
    """
    argument_parser = argparse.ArgumentParser(
        description="Score overlapping duplicate transcript rows without outputting wording.",
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
        segment_id=segment_id,
        speaker_id=raw_speaker_id.strip(),
        role=role,
        start_seconds=start_seconds,
        end_seconds=end_seconds,
        normalized_text=normalized_text,
        word_count=len(normalized_text.split()),
    )


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


def duplicate_pair_identifier(
    session_id: str,
    left_row: VisibleHistoryRow,
    right_row: VisibleHistoryRow,
) -> str:
    """Create a stable pair label from safe row metadata only.

    Args:
        session_id: Replay identifier; `unknown-session` represents missing metadata.
        left_row: First row in deterministic spoken-time order.
        right_row: Second row in deterministic spoken-time order.

    Returns:
        Opaque stable ID; it cannot be tested against consultation wording.
    """
    safe_pair_metadata = [
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
    metadata_bytes = json.dumps(
        safe_pair_metadata,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"duplicate-{hashlib.sha256(metadata_bytes).hexdigest()[:16]}"


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
        "duplicate_pair_id": duplicate_pair_identifier(
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
            key=lambda row: (
                row.start_seconds,
                row.end_seconds,
                row.speaker_id,
                row.segment_id,
            ),
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


def score_history_artifact(artifact_path: Path) -> dict[str, Any]:
    """Score one final browser history for cross-identity duplicate spans.

    Args:
        artifact_path: Retained history selected by the operator; never absent after discovery.

    Returns:
        PHI-safe artifact summary; duplicate pairs are empty when none were measured.
    """
    session_id, visible_row_count, comparable_rows = load_visible_history(artifact_path)
    duplicate_pairs = find_duplicate_pairs(session_id, comparable_rows)
    return {
        "artifact_path": str(artifact_path),
        "duplicate_pair_count": len(duplicate_pairs),
        "duplicate_pairs": duplicate_pairs,
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
    artifact_reports: list[dict[str, Any]] = []
    # One safe report per fixture lets operators locate affected retained evidence.
    for artifact_path in artifact_paths:
        artifact_reports.append(score_history_artifact(artifact_path))

    return {
        "artifacts": artifact_reports,
        "schema_version": 1,
        "scorer": "duplicate-transcript-score",
        "summary": {
            "affected_artifacts": sum(
                1
                for artifact_report in artifact_reports
                if artifact_report["duplicate_pair_count"] > 0
            ),
            "artifact_count": len(artifact_reports),
            "duplicate_pair_count": sum(
                int(artifact_report["duplicate_pair_count"])
                for artifact_report in artifact_reports
            ),
            "visible_row_count": sum(
                int(artifact_report["visible_row_count"])
                for artifact_report in artifact_reports
            ),
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
            f"pairs={summary['duplicate_pair_count']}"
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
