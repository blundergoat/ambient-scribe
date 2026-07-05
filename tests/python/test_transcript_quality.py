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


def test_strict_metrics_keep_uncertain_rows_in_denominator(tmp_path: Path) -> None:
    """Uncertain/UNKNOWN clean rows stay in the strict denominator as incorrect.

    This pins the M20 anti-gaming rule: a build that hides hard rows behind
    uncertainty must not raise strict attribution, only uncertainty coverage.
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
            (12.0, 14.0, "doctor four"),
        ],
    )
    write_textgrid(patient_grid, [(14.0, 15.0, "patient overlap"), (13.5, 14.5, "cross")])
    history_path.write_text(
        json.dumps(
            {
                "segments": [
                    # Two clean rows the UI labeled correctly.
                    {"start": 0.1, "end": 1.9, "speaker_id": "speaker_0", "role": "DOCTOR", "text": "a"},
                    {"start": 4.1, "end": 5.9, "speaker_id": "speaker_0", "role": "DOCTOR", "text": "b"},
                    # One clean row rendered as explicit uncertainty.
                    {"start": 8.1, "end": 9.9, "speaker_id": "speaker_0", "role": "UNKNOWN", "text": "c"},
                    # One clean row still on raw speaker labels (no role at all).
                    {"start": 12.1, "end": 13.3, "speaker_id": "speaker_0", "text": "d"},
                    # One wrong row inside cross-talk stays out of strict metrics.
                    {"start": 13.6, "end": 14.4, "speaker_id": "speaker_1", "role": "DOCTOR", "text": "e"},
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
            "15",
            str(doctor_grid),
            str(patient_grid),
        ],
        cwd=REPO_ROOT,
        check=True,
        text=True,
        capture_output=True,
    )

    # Visible attribution only sees the two confident labels and looks perfect.
    assert "speaker attribution accuracy (non-overlap): 100.0% (2/2)" in result.stdout
    # Strict attribution keeps all four clean rows; uncertain rows count as incorrect.
    assert "strict attribution (non-overlap): 50.0% (2/4)" in result.stdout
    assert "labeled-row accuracy (non-overlap): 100.0% (2/2)" in result.stdout
    assert "uncertainty coverage (non-overlap): 50.0% (2/4)" in result.stdout
    assert "incorrect-confident rate (non-overlap): 0.0% (0/4)" in result.stdout


def test_strict_metrics_count_confidently_wrong_rows_after_identity_drift(
    tmp_path: Path,
) -> None:
    """A speaker ID whose true role changes mid-session yields confidently-wrong rows."""
    doctor_grid = tmp_path / "visit.doctor.TextGrid"
    patient_grid = tmp_path / "visit.patient.TextGrid"
    history_path = tmp_path / "history.json"

    write_textgrid(doctor_grid, [(0.0, 2.0, "doctor start")])
    write_textgrid(
        patient_grid,
        [
            (4.0, 6.0, "patient middle"),
            (8.0, 10.0, "patient end"),
        ],
    )
    history_path.write_text(
        json.dumps(
            {
                "segments": [
                    # speaker_0 starts as the doctor's voice, labeled DOCTOR: correct.
                    {"start": 0.1, "end": 1.9, "speaker_id": "speaker_0", "role": "DOCTOR", "text": "a"},
                    # Identity drifts: speaker_0 now carries the patient's voice
                    # but keeps the confident DOCTOR label - the M20 failure mode.
                    {"start": 4.1, "end": 5.9, "speaker_id": "speaker_0", "role": "DOCTOR", "text": "b"},
                    {"start": 8.1, "end": 9.9, "speaker_id": "speaker_0", "role": "DOCTOR", "text": "c"},
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
            "10",
            str(doctor_grid),
            str(patient_grid),
        ],
        cwd=REPO_ROOT,
        check=True,
        text=True,
        capture_output=True,
    )

    assert "strict attribution (non-overlap): 33.3% (1/3)" in result.stdout
    assert "labeled-row accuracy (non-overlap): 33.3% (1/3)" in result.stdout
    assert "uncertainty coverage (non-overlap): 0.0% (0/3)" in result.stdout
    # Two rows showed the clinician a confident wrong label.
    assert "incorrect-confident rate (non-overlap): 66.7% (2/3)" in result.stdout


def write_window_artifact(path: Path, windows: list[dict]) -> None:
    """Write a minimal window-continuity JSONL artifact for seam-join tests.

    Args:
        path: Destination JSONL file joined by the scorer's seam flags.
        windows: Window records; empty means every seam flag stays unknown.
    """
    lines = [json.dumps({"event": "nemo_session.window_continuity", **window}) for window in windows]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_row_diagnostics_json_reports_seam_overlap_and_correctable_flags(
    tmp_path: Path,
) -> None:
    """The row artifact explains each visible row: seam, overlap, and fixability."""
    doctor_grid = tmp_path / "visit.doctor.TextGrid"
    patient_grid = tmp_path / "visit.patient.TextGrid"
    history_path = tmp_path / "history.json"
    window_path = tmp_path / "window-continuity.jsonl"
    rows_path = tmp_path / "row-diagnostics.json"

    write_textgrid(
        doctor_grid,
        [
            (0.0, 2.0, "doctor one"),
            (3.5, 4.5, "doctor interjects"),
            (6.0, 8.0, "doctor two"),
        ],
    )
    write_textgrid(patient_grid, [(2.5, 4.5, "patient one")])
    history_path.write_text(
        json.dumps(
            {
                "segments": [
                    # Emitted by window 1, labeled wrongly - a better mapping fixes it.
                    {"start": 0.1, "end": 1.9, "speaker_id": "speaker_0", "role": "PATIENT", "text": "a"},
                    # Emitted by window 2 but starting inside window 1's emitted
                    # audio: a seam row, and it also touches the cross-talk span.
                    {"start": 1.8, "end": 4.0, "speaker_id": "speaker_1", "role": "DOCTOR", "text": "b"},
                    # Clean correctly-labeled patient row emitted by window 2.
                    {"start": 2.6, "end": 3.4, "speaker_id": "speaker_1", "role": "PATIENT", "text": "c"},
                    # Uncertain clean row emitted by window 2.
                    {"start": 6.1, "end": 7.9, "speaker_id": "speaker_0", "role": "UNKNOWN", "text": "d"},
                ],
            }
        ),
        encoding="utf-8",
    )
    write_window_artifact(
        window_path,
        [
            {"window_index": 1, "emitted_from_seconds": 0.0, "emitted_until_seconds": 2.0},
            {"window_index": 2, "emitted_from_seconds": 2.0, "emitted_until_seconds": 8.0},
        ],
    )

    result = subprocess.run(
        [
            sys.executable,
            str(REPO_ROOT / "scripts/transcript-quality.py"),
            "--window-artifact",
            str(window_path),
            "--row-diagnostics-json",
            str(rows_path),
            str(history_path),
            "8",
            str(doctor_grid),
            str(patient_grid),
        ],
        cwd=REPO_ROOT,
        check=True,
        text=True,
        capture_output=True,
    )

    assert f"row diagnostics: 4 rows -> {rows_path} (window artifact)" in result.stdout

    artifact = json.loads(rows_path.read_text(encoding="utf-8"))
    first_row, seam_row, correct_row, uncertain_row = artifact["rows"]

    # The wrong-but-fixable row: best dyadic mapping would label speaker_0 DOCTOR.
    assert first_row["confidently_wrong"] is True
    assert first_row["mapping_correctable"] is True
    assert first_row["window_index"] == 1
    assert first_row["crosses_window_seam"] is False
    assert first_row["first_in_window"] is True

    # The seam row ends in window 2 but started inside window 1's emitted audio.
    assert seam_row["window_index"] == 2
    assert seam_row["crosses_window_seam"] is True
    assert seam_row["in_overlap"] is True

    # The clean patient row shows a correct confident label for contrast.
    assert correct_row["correct"] is True
    assert correct_row["crosses_window_seam"] is False

    # The uncertain row keeps its reference truth without claiming correctness.
    assert uncertain_row["labeled"] is False
    assert uncertain_row["correct"] is None
    assert uncertain_row["visible_role"] == "UNKNOWN"
    assert uncertain_row["expected_role"] == "DOCTOR"

    # The summary makes the artifact usable without re-reading the text report.
    assert artifact["summary"]["clean_reference_rows"] == 3
    assert artifact["summary"]["uncertain_rows"] == 1
    assert artifact["summary"]["incorrect_confident_rows"] == 1


def test_row_diagnostics_without_window_artifact_leaves_seams_unknown(
    tmp_path: Path,
) -> None:
    """Runs without a window artifact still get row records with unknown seams."""
    doctor_grid = tmp_path / "visit.doctor.TextGrid"
    patient_grid = tmp_path / "visit.patient.TextGrid"
    history_path = tmp_path / "history.json"
    rows_path = tmp_path / "row-diagnostics.json"

    write_textgrid(doctor_grid, [(0.0, 2.0, "doctor one")])
    write_textgrid(patient_grid, [])
    history_path.write_text(
        json.dumps(
            {
                "segments": [
                    {"start": 0.1, "end": 1.9, "speaker_id": "speaker_0", "role": "DOCTOR", "text": "a"},
                ],
            }
        ),
        encoding="utf-8",
    )

    result = subprocess.run(
        [
            sys.executable,
            str(REPO_ROOT / "scripts/transcript-quality.py"),
            "--row-diagnostics-json",
            str(rows_path),
            str(history_path),
            "2",
            str(doctor_grid),
            str(patient_grid),
        ],
        cwd=REPO_ROOT,
        check=True,
        text=True,
        capture_output=True,
    )

    assert f"row diagnostics: 1 rows -> {rows_path} (no window artifact)" in result.stdout

    artifact = json.loads(rows_path.read_text(encoding="utf-8"))
    only_row = artifact["rows"][0]
    # Seam truth needs the per-window artifact, so the flags stay honest nulls.
    assert only_row["crosses_window_seam"] is None
    assert only_row["window_index"] is None
    assert only_row["first_in_window"] is None


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
