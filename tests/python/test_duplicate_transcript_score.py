"""Test the PHI-safe duplicate transcript scorer used after M05 replays.

Developers run the scorer against retained live-history artifacts before
changing speaker identity behavior. These tests pin the user-visible duplicate
rule, directory discovery, and safe failure output without loading NeMo or
exposing the consultation wording used by the fixtures.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
SCORER_PATH = REPO_ROOT / "scripts/duplicate-transcript-score.py"


def write_live_history(
    artifact_path: Path,
    session_id: str,
    visible_rows: list[dict],
) -> None:
    """Write one retained history artifact the operator can score.

    Args:
        artifact_path: Destination JSON path; parent folders may not exist yet.
        session_id: Safe replay identifier; an empty value models missing session metadata.
        visible_rows: Browser-visible rows; an empty list means the visit showed no transcript.
    """
    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    artifact_path.write_text(
        json.dumps({"session_id": session_id, "segments": visible_rows}),
        encoding="utf-8",
    )


def run_duplicate_scorer(*artifact_paths: Path) -> subprocess.CompletedProcess[str]:
    """Run the operator CLI against selected artifacts or run directories.

    Args:
        artifact_paths: Paths selected for scoring; empty input is invalid CLI use.

    Returns:
        Completed process; nonzero means the selected evidence could not be scored safely.
    """
    return subprocess.run(
        [sys.executable, str(SCORER_PATH), *(str(path) for path in artifact_paths)],
        cwd=REPO_ROOT,
        check=False,
        text=True,
        capture_output=True,
    )


def test_scorer_reports_only_overlapping_cross_identity_pairs(tmp_path: Path) -> None:
    """Show one repeated UI span while excluding unrelated or same-speaker rows."""
    history_path = tmp_path / "live-history.json"
    write_live_history(
        history_path,
        "duplicate-session",
        [
            {
                "segment_id": "seg-0005",
                "speaker_id": "speaker_3",
                "role": "PATIENT",
                "start": 50.0,
                "end": 52.0,
                "text": "Same private phrase!",
            },
            {
                "segment_id": "seg-0006",
                "speaker_id": "speaker_0",
                "role": "DOCTOR",
                "start": 51.0,
                "end": 51.5,
                "text": " same PRIVATE phrase ",
            },
            {
                "segment_id": "seg-0007",
                "speaker_id": "speaker_1",
                "role": "DOCTOR",
                "start": 70.0,
                "end": 71.0,
                "text": "Same private phrase",
            },
            {
                "segment_id": "seg-0008",
                "speaker_id": "speaker_2",
                "role": "UNKNOWN",
                "start": 51.1,
                "end": 51.4,
                "text": "Different private wording",
            },
            {
                "segment_id": "seg-0009",
                "speaker_id": "speaker_4",
                "role": "UNKNOWN",
                "start": 51.2,
                "end": 51.3,
                "text": "   ",
            },
        ],
    )

    first_run = run_duplicate_scorer(history_path)
    second_run = run_duplicate_scorer(history_path)

    assert first_run.returncode == 0, first_run.stderr
    assert second_run.returncode == 0, second_run.stderr
    first_report = json.loads(first_run.stdout)
    second_report = json.loads(second_run.stdout)
    assert first_report["summary"] == {
        "affected_artifacts": 1,
        "artifact_count": 1,
        "duplicate_pair_count": 1,
        "visible_row_count": 5,
    }
    duplicate_pair = first_report["artifacts"][0]["duplicate_pairs"][0]
    assert duplicate_pair == {
        "arrival_offset_seconds": None,
        "duplicate_pair_id": duplicate_pair["duplicate_pair_id"],
        "left_end_seconds": 52.0,
        "left_role": "PATIENT",
        "left_segment_id": "seg-0005",
        "left_speaker_id": "speaker_3",
        "left_start_seconds": 50.0,
        "overlap_seconds": 0.5,
        "right_end_seconds": 51.5,
        "right_role": "DOCTOR",
        "right_segment_id": "seg-0006",
        "right_speaker_id": "speaker_0",
        "right_start_seconds": 51.0,
        "spoken_start_offset_seconds": 1.0,
        "word_count": 3,
    }
    assert duplicate_pair["duplicate_pair_id"].startswith("duplicate-")
    assert (
        duplicate_pair["duplicate_pair_id"]
        == second_report["artifacts"][0]["duplicate_pairs"][0]["duplicate_pair_id"]
    )
    assert "text" not in duplicate_pair
    assert "private" not in first_run.stdout.lower()
    assert "private" not in first_run.stderr.lower()


def test_scorer_discovers_live_histories_and_reports_empty_visits(
    tmp_path: Path,
) -> None:
    """Summarize a run directory while an empty visit stays an explicit zero."""
    run_directory = tmp_path / "corpus-run"
    write_live_history(
        run_directory / "fixture-a" / "live-history.json",
        "affected-session",
        [
            {
                "segment_id": "seg-a",
                "speaker_id": "speaker_0",
                "role": "DOCTOR",
                "start": 10.0,
                "end": 12.0,
                "text": "Repeated private words",
            },
            {
                "segment_id": "seg-b",
                "speaker_id": "speaker_3",
                "role": "DOCTOR",
                "start": 10.5,
                "end": 11.5,
                "text": "repeated PRIVATE words",
            },
        ],
    )
    write_live_history(
        run_directory / "fixture-b" / "live-history.json",
        "empty-session",
        [],
    )
    write_live_history(
        run_directory / "fixture-c" / "corrected-transcript.json",
        "ignored-corrected-session",
        [
            {
                "segment_id": "corrected-a",
                "speaker_id": "speaker_0",
                "role": "DOCTOR",
                "start": 1.0,
                "end": 2.0,
                "text": "Ignored private wording",
            }
        ],
    )

    completed_process = run_duplicate_scorer(run_directory)

    assert completed_process.returncode == 0, completed_process.stderr
    report = json.loads(completed_process.stdout)
    assert report["summary"] == {
        "affected_artifacts": 1,
        "artifact_count": 2,
        "duplicate_pair_count": 1,
        "visible_row_count": 2,
    }
    assert [artifact["session_id"] for artifact in report["artifacts"]] == [
        "affected-session",
        "empty-session",
    ]
    assert report["artifacts"][1]["duplicate_pairs"] == []
    assert "ignored-corrected-session" not in completed_process.stdout
    assert "private" not in completed_process.stdout.lower()


def test_scorer_rejects_malformed_history_without_echoing_words(tmp_path: Path) -> None:
    """Fail safely when a selected artifact cannot represent visible transcript rows."""
    malformed_path = tmp_path / "live-history.json"
    malformed_path.write_text(
        json.dumps(
            {
                "session_id": "malformed-session",
                "segments": {"text": "Private malformed consultation wording"},
            }
        ),
        encoding="utf-8",
    )

    completed_process = run_duplicate_scorer(malformed_path)

    assert completed_process.returncode == 2
    assert completed_process.stdout == ""
    assert "segments must be a list" in completed_process.stderr
    assert str(malformed_path) in completed_process.stderr
    assert "private" not in completed_process.stderr.lower()
