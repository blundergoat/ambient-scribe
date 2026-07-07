"""Tests for corrected source-chip role review scoring."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from corrected_source_chip_score import score_corrected_segments

REPO_ROOT = Path(__file__).resolve().parents[2]


def _row(
    role: str,
    text: str,
    segment_id: str,
    start: float = 1.0,
    end: float = 2.0,
) -> dict:
    """Build one corrected transcript row as a clinician would see it."""
    return {
        "role": role,
        "text": text,
        "segment_id": segment_id,
        "speaker_id": "speaker_0",
        "start": start,
        "end": end,
    }


def test_score_flags_patient_row_with_doctor_question() -> None:
    """A patient-owned source chip should not contain the doctor's question."""
    score = score_corrected_segments(
        [
            _row("PATIENT", "you move your neck? Well, like if I", "corrected-0052"),
        ],
        artifact_path="consult-03.json",
    )

    assert score.finding_count == 1
    assert score.findings[0].segment_id == "corrected-0052"
    assert "doctor_question_in_patient_row" in score.findings[0].codes
    assert "mixed_role_cues" in score.findings[0].codes


def test_score_flags_doctor_row_with_patient_ack_after_question() -> None:
    """A tiny patient answer after a doctor question should be reviewable."""
    score = score_corrected_segments(
        [
            _row("DOCTOR", "Have you vomited at all?", "corrected-0098", 157.0, 159.0),
            _row("DOCTOR", "Yeah.", "corrected-0099", 160.0, 161.0),
        ],
        artifact_path="consult-03.json",
    )

    assert score.finding_count == 1
    assert score.findings[0].segment_id == "corrected-0099"
    assert score.findings[0].severity == "warning"
    assert "patient_ack_after_doctor_question" in score.findings[0].codes


def test_score_accepts_clean_doctor_and_patient_rows() -> None:
    """Clean corrected rows should not create review work for the user."""
    score = score_corrected_segments(
        [
            _row("DOCTOR", "Can you tell me more about the headache?", "corrected-0011"),
            _row("PATIENT", "I have a throbbing headache.", "corrected-0012"),
        ],
        artifact_path="consult-03.json",
    )

    assert score.finding_count == 0


def test_cli_scores_corrected_transcript_directory(tmp_path: Path) -> None:
    """The fixture CLI should report artifact and row details without the app."""
    fixture_dir = tmp_path / "run" / "fixture"
    fixture_dir.mkdir(parents=True)
    corrected_path = fixture_dir / "corrected-transcript.json"
    corrected_path.write_text(
        json.dumps(
            {
                "session_id": "session-1",
                "source": "corrected_segments",
                "segments": [
                    _row("DOCTOR", "Have you vomited at all?", "corrected-0098"),
                    _row("DOCTOR", "Yeah.", "corrected-0099"),
                ],
            }
        ),
        encoding="utf-8",
    )

    result = subprocess.run(
        [
            sys.executable,
            str(REPO_ROOT / "scripts/corrected-source-chip-score.py"),
            str(tmp_path / "run"),
        ],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=True,
    )

    assert "Corrected source-chip scorer" in result.stdout
    assert "corrected-0099" in result.stdout
    assert "patient_ack_after_doctor_question" in result.stdout
