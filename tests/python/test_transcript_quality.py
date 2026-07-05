"""Tests for the PriMock transcript-quality scorer.

The scorer is the acceptance tool a developer runs after a replay or browser visit.
These tests use tiny TextGrid/history fixtures so attribution math, overlap filtering,
phantom speaker counts, and role-flip reporting stay verifiable without GPU audio.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]


def write_textgrid(path: Path, intervals: list[tuple[float, float, str]]) -> None:
    """Write the minimal TextGrid interval shape the scorer parses.

    Args:
        path: Destination `.doctor.TextGrid` or `.patient.TextGrid` file.
        intervals: Speech intervals; empty text means the user heard no role-owned words.
    """
    interval_blocks = "\n".join(
        f'    xmin = {start}\n    xmax = {end}\n    text = "{text}"'
        for start, end, text in intervals
    )
    path.write_text(interval_blocks, encoding="utf-8")


def test_transcript_quality_reports_attribution_overlap_and_flip_counts(
    tmp_path: Path,
) -> None:
    """A saved visit prints the attribution metrics M16 uses for baseline decisions."""
    doctor_grid = tmp_path / "visit.doctor.TextGrid"
    patient_grid = tmp_path / "visit.patient.TextGrid"
    history_path = tmp_path / "history.json"
    quality_path = tmp_path / "quality.json"

    write_textgrid(
        doctor_grid,
        [
            (0.0, 2.0, "hello doctor"),
            (3.0, 5.0, "doctor talk"),
        ],
    )
    write_textgrid(
        patient_grid,
        [
            (2.0, 3.0, "answer"),
            (3.0, 3.5, "overlap"),
        ],
    )
    history_path.write_text(
        json.dumps(
            {
                "segments": [
                    {
                        "start": 0.2,
                        "end": 1.8,
                        "speaker_id": "speaker_0",
                        "role": "DOCTOR",
                        "text": "hello doctor",
                    },
                    {
                        "start": 2.1,
                        "end": 2.9,
                        "speaker_id": "speaker_1",
                        "role": "PATIENT",
                        "text": "answer",
                    },
                    {
                        "start": 3.2,
                        "end": 4.5,
                        "speaker_id": "speaker_2",
                        "role": "PATIENT",
                        "text": "wrong owner",
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    quality_path.write_text(
        json.dumps({"role_flips_accepted": 2, "role_flips_suppressed": 1}),
        encoding="utf-8",
    )

    result = subprocess.run(
        [
            sys.executable,
            str(REPO_ROOT / "scripts/transcript-quality.py"),
            "--quality-json",
            str(quality_path),
            str(history_path),
            "5",
            str(doctor_grid),
            str(patient_grid),
        ],
        cwd=REPO_ROOT,
        check=True,
        text=True,
        capture_output=True,
    )

    assert "speaker attribution accuracy: 66.7% (2/3)" in result.stdout
    assert "speaker attribution accuracy (non-overlap): 100.0% (2/2)" in result.stdout
    assert "word error rate (non-overlap): 40.0% (S/I/D=2/0/0, ref=5, hyp=5)" in result.stdout
    assert "word error rate (overlap): 100.0% (S/I/D=0/0/1, ref=1, hyp=0)" in result.stdout
    assert "segments per minute: 36.0 (segments=3)" in result.stdout
    assert "fragment rate: 100.0% (3/3)" in result.stdout
    assert "fragment rate (non-overlap): 100.0% (2/2)" in result.stdout
    assert "seam re-read count: 0" in result.stdout
    assert "speaker oracle accuracy: 100.0% (3/3)" in result.stdout
    assert "speaker oracle accuracy (non-overlap): 100.0% (2/2)" in result.stdout
    assert "role mapping gap (non-overlap): +0.0pp" in result.stdout
    assert "best dyadic mapping accuracy (non-overlap): 100.0% (2/2)" in result.stdout
    assert "best dyadic mapping: speaker_0->DOCTOR speaker_1->PATIENT" in result.stdout
    assert "role mapping headroom (non-overlap): +0.0pp" in result.stdout
    assert (
        "speaker purity by ID (non-overlap): speaker_0->DOCTOR 1/1 visible=DOCTOR; "
        "speaker_1->PATIENT 1/1 visible=PATIENT"
    ) in result.stdout
    assert "overlap-span seconds: 0.5" in result.stdout
    assert "phantom speaker count: 1" in result.stdout
    assert "role flips accepted: 2" in result.stdout
    assert "role flips suppressed: 1" in result.stdout


def test_transcript_quality_reports_dyadic_ceiling_when_visible_mapping_is_worse(
    tmp_path: Path,
) -> None:
    """The best-valid-dyadic ceiling exposes headroom the free-role oracle overstates.

    The visible mapping here is the worse of the two one-role-each assignments,
    so the report must show real recoverable headroom instead of only the
    unconstrained oracle gap.
    """
    doctor_grid = tmp_path / "visit.doctor.TextGrid"
    patient_grid = tmp_path / "visit.patient.TextGrid"
    history_path = tmp_path / "history.json"

    write_textgrid(
        doctor_grid,
        [
            (0.0, 2.0, "doctor one"),
            (4.0, 6.0, "doctor two"),
            (8.0, 10.0, "doctor three"),
        ],
    )
    write_textgrid(
        patient_grid,
        [
            (2.0, 4.0, "patient one"),
            (6.0, 8.0, "patient two"),
            (10.0, 12.0, "patient three"),
        ],
    )
    history_path.write_text(
        json.dumps(
            {
                "segments": [
                    # speaker_0 is mostly the doctor's time but shown as PATIENT.
                    {"start": 0.1, "end": 1.9, "speaker_id": "speaker_0", "role": "PATIENT", "text": "a"},
                    {"start": 4.1, "end": 5.9, "speaker_id": "speaker_0", "role": "PATIENT", "text": "b"},
                    {"start": 2.1, "end": 3.9, "speaker_id": "speaker_0", "role": "PATIENT", "text": "c"},
                    # speaker_1 is mostly the patient's time but shown as DOCTOR.
                    {"start": 6.1, "end": 7.9, "speaker_id": "speaker_1", "role": "DOCTOR", "text": "d"},
                    {"start": 10.1, "end": 11.9, "speaker_id": "speaker_1", "role": "DOCTOR", "text": "e"},
                    {"start": 8.1, "end": 9.9, "speaker_id": "speaker_1", "role": "DOCTOR", "text": "f"},
                ],
            }
        ),
        encoding="utf-8",
    )

    result = subprocess.run(
        [
            sys.executable,
            str(REPO_ROOT / "scripts/transcript-quality.py"),
            str(history_path),
            "12",
            str(doctor_grid),
            str(patient_grid),
        ],
        cwd=REPO_ROOT,
        check=True,
        text=True,
        capture_output=True,
    )

    # Visible labels are inverted for both speakers: 2 of 6 rows correct.
    assert "speaker attribution accuracy (non-overlap): 33.3% (2/6)" in result.stdout
    assert "best dyadic mapping accuracy (non-overlap): 66.7% (4/6)" in result.stdout
    assert "best dyadic mapping: speaker_0->DOCTOR speaker_1->PATIENT" in result.stdout
    assert "role mapping headroom (non-overlap): +33.3pp" in result.stdout


def test_transcript_quality_dyadic_ceiling_is_na_without_exactly_two_speakers(
    tmp_path: Path,
) -> None:
    """Three clean speaker IDs mean no valid dyadic mapping can be ranked."""
    doctor_grid = tmp_path / "visit.doctor.TextGrid"
    patient_grid = tmp_path / "visit.patient.TextGrid"
    history_path = tmp_path / "history.json"

    write_textgrid(doctor_grid, [(0.0, 2.0, "doctor"), (4.0, 6.0, "doctor")])
    write_textgrid(patient_grid, [(2.0, 4.0, "patient")])
    history_path.write_text(
        json.dumps(
            {
                "segments": [
                    {"start": 0.1, "end": 1.9, "speaker_id": "speaker_0", "role": "DOCTOR", "text": "a"},
                    {"start": 2.1, "end": 3.9, "speaker_id": "speaker_1", "role": "PATIENT", "text": "b"},
                    {"start": 4.1, "end": 5.9, "speaker_id": "speaker_2", "role": "DOCTOR", "text": "c"},
                ],
            }
        ),
        encoding="utf-8",
    )

    result = subprocess.run(
        [
            sys.executable,
            str(REPO_ROOT / "scripts/transcript-quality.py"),
            str(history_path),
            "6",
            str(doctor_grid),
            str(patient_grid),
        ],
        cwd=REPO_ROOT,
        check=True,
        text=True,
        capture_output=True,
    )

    assert "best dyadic mapping accuracy (non-overlap): n/a (0/0)" in result.stdout
    assert "best dyadic mapping: n/a" in result.stdout
    assert "role mapping headroom (non-overlap): n/a" in result.stdout


def test_transcript_quality_reports_nearby_seam_rereads(tmp_path: Path) -> None:
    """A repeated phrase near the same timestamp is counted as a seam re-read."""
    doctor_grid = tmp_path / "visit.doctor.TextGrid"
    patient_grid = tmp_path / "visit.patient.TextGrid"
    history_path = tmp_path / "history.json"

    write_textgrid(doctor_grid, [(0.0, 3.0, "hello can you help")])
    write_textgrid(patient_grid, [])
    history_path.write_text(
        json.dumps(
            {
                "segments": [
                    {
                        "start": 0.0,
                        "end": 1.0,
                        "speaker_id": "speaker_0",
                        "role": "DOCTOR",
                        "text": "hello can you",
                    },
                    {
                        "start": 1.2,
                        "end": 2.0,
                        "speaker_id": "speaker_0",
                        "role": "DOCTOR",
                        "text": "hello can you help",
                    },
                ],
            }
        ),
        encoding="utf-8",
    )

    result = subprocess.run(
        [
            sys.executable,
            str(REPO_ROOT / "scripts/transcript-quality.py"),
            str(history_path),
            "3",
            str(doctor_grid),
            str(patient_grid),
        ],
        cwd=REPO_ROOT,
        check=True,
        text=True,
        capture_output=True,
    )

    assert "fragment rate: 0.0% (0/2)" in result.stdout
    assert "fragment rate (non-overlap): 0.0% (0/2)" in result.stdout
    assert "seam re-read count: 1" in result.stdout


def test_transcript_quality_ignores_same_row_phrase_repeats(tmp_path: Path) -> None:
    """One transcript row can repeat words without becoming a seam re-read."""
    doctor_grid = tmp_path / "visit.doctor.TextGrid"
    patient_grid = tmp_path / "visit.patient.TextGrid"
    history_path = tmp_path / "history.json"

    write_textgrid(doctor_grid, [(0.0, 4.0, "pain pain pain pain")])
    write_textgrid(patient_grid, [])
    history_path.write_text(
        json.dumps(
            {
                "segments": [
                    {
                        "start": 0.0,
                        "end": 4.0,
                        "speaker_id": "speaker_0",
                        "role": "DOCTOR",
                        "text": "pain pain pain pain",
                    },
                ],
            }
        ),
        encoding="utf-8",
    )

    result = subprocess.run(
        [
            sys.executable,
            str(REPO_ROOT / "scripts/transcript-quality.py"),
            str(history_path),
            "4",
            str(doctor_grid),
            str(patient_grid),
        ],
        cwd=REPO_ROOT,
        check=True,
        text=True,
        capture_output=True,
    )

    assert "seam re-read count: 0" in result.stdout
