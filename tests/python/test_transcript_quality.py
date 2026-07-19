"""Tests for the PriMock transcript-quality scorer.

The scorer is the acceptance tool a developer runs after a replay or browser visit.
These tests use tiny TextGrid/history fixtures so attribution math, overlap filtering,
phantom speaker counts, and role-flip reporting stay verifiable without GPU audio.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from types import ModuleType


REPO_ROOT = Path(__file__).resolve().parents[2]
TRANSCRIPT_QUALITY_SCORER_PATH = REPO_ROOT / "scripts/transcript-quality.py"


def load_transcript_quality_scorer() -> ModuleType:
    """Load the CPU-only scorer a developer uses to review transcript quality.
    Use when a focused test needs the scoring functions without starting runtime services.
    """
    scorer_spec = importlib.util.spec_from_file_location(
        "ambient_scribe_transcript_quality_test_module",
        TRANSCRIPT_QUALITY_SCORER_PATH,
    )
    # No import specification means the clinician-facing quality tool cannot be tested.
    assert scorer_spec is not None
    # No loader means the test cannot execute the exact standalone scorer file.
    assert scorer_spec.loader is not None

    transcript_quality_scorer = importlib.util.module_from_spec(scorer_spec)
    sys.modules[scorer_spec.name] = transcript_quality_scorer
    scorer_spec.loader.exec_module(transcript_quality_scorer)
    return transcript_quality_scorer


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
    assert (
        "word error rate (non-overlap): 40.0% (S/I/D=2/0/0, ref=5, hyp=5)"
        in result.stdout
    )
    assert (
        "word error rate (overlap): 100.0% (S/I/D=0/0/1, ref=1, hyp=0)" in result.stdout
    )
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
                    {
                        "start": 0.1,
                        "end": 1.9,
                        "speaker_id": "speaker_0",
                        "role": "PATIENT",
                        "text": "a",
                    },
                    {
                        "start": 4.1,
                        "end": 5.9,
                        "speaker_id": "speaker_0",
                        "role": "PATIENT",
                        "text": "b",
                    },
                    {
                        "start": 2.1,
                        "end": 3.9,
                        "speaker_id": "speaker_0",
                        "role": "PATIENT",
                        "text": "c",
                    },
                    # speaker_1 is mostly the patient's time but shown as DOCTOR.
                    {
                        "start": 6.1,
                        "end": 7.9,
                        "speaker_id": "speaker_1",
                        "role": "DOCTOR",
                        "text": "d",
                    },
                    {
                        "start": 10.1,
                        "end": 11.9,
                        "speaker_id": "speaker_1",
                        "role": "DOCTOR",
                        "text": "e",
                    },
                    {
                        "start": 8.1,
                        "end": 9.9,
                        "speaker_id": "speaker_1",
                        "role": "DOCTOR",
                        "text": "f",
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
                    {
                        "start": 0.1,
                        "end": 1.9,
                        "speaker_id": "speaker_0",
                        "role": "DOCTOR",
                        "text": "a",
                    },
                    {
                        "start": 2.1,
                        "end": 3.9,
                        "speaker_id": "speaker_1",
                        "role": "PATIENT",
                        "text": "b",
                    },
                    {
                        "start": 4.1,
                        "end": 5.9,
                        "speaker_id": "speaker_2",
                        "role": "DOCTOR",
                        "text": "c",
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
    write_textgrid(
        patient_grid, [(14.0, 15.0, "patient overlap"), (13.5, 14.5, "cross")]
    )
    history_path.write_text(
        json.dumps(
            {
                "segments": [
                    # Two clean rows the UI labeled correctly.
                    {
                        "start": 0.1,
                        "end": 1.9,
                        "speaker_id": "speaker_0",
                        "role": "DOCTOR",
                        "text": "a",
                    },
                    {
                        "start": 4.1,
                        "end": 5.9,
                        "speaker_id": "speaker_0",
                        "role": "DOCTOR",
                        "text": "b",
                    },
                    # One clean row rendered as explicit uncertainty.
                    {
                        "start": 8.1,
                        "end": 9.9,
                        "speaker_id": "speaker_0",
                        "role": "UNKNOWN",
                        "text": "c",
                    },
                    # One clean row still on raw speaker labels (no role at all).
                    {
                        "start": 12.1,
                        "end": 13.3,
                        "speaker_id": "speaker_0",
                        "text": "d",
                    },
                    # One wrong row inside cross-talk stays out of strict metrics.
                    {
                        "start": 13.6,
                        "end": 14.4,
                        "speaker_id": "speaker_1",
                        "role": "DOCTOR",
                        "text": "e",
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
                    {
                        "start": 0.1,
                        "end": 1.9,
                        "speaker_id": "speaker_0",
                        "role": "DOCTOR",
                        "text": "a",
                    },
                    # Identity drifts: speaker_0 now carries the patient's voice
                    # but keeps the confident DOCTOR label - the M20 failure mode.
                    {
                        "start": 4.1,
                        "end": 5.9,
                        "speaker_id": "speaker_0",
                        "role": "DOCTOR",
                        "text": "b",
                    },
                    {
                        "start": 8.1,
                        "end": 9.9,
                        "speaker_id": "speaker_0",
                        "role": "DOCTOR",
                        "text": "c",
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
    lines = [
        json.dumps({"event": "nemo_session.window_continuity", **window})
        # Every saved window becomes one clinician-traceable continuity record.
        for window in windows
    ]
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
                    {
                        "start": 0.1,
                        "end": 1.9,
                        "speaker_id": "speaker_0",
                        "role": "PATIENT",
                        "text": "a",
                    },
                    # Emitted by window 2 but starting inside window 1's emitted
                    # audio: a seam row, and it also touches the cross-talk span.
                    {
                        "start": 1.8,
                        "end": 4.0,
                        "speaker_id": "speaker_1",
                        "role": "DOCTOR",
                        "text": "b",
                    },
                    # Clean correctly-labeled patient row emitted by window 2.
                    {
                        "start": 2.6,
                        "end": 3.4,
                        "speaker_id": "speaker_1",
                        "role": "PATIENT",
                        "text": "c",
                    },
                    # Uncertain clean row emitted by window 2.
                    {
                        "start": 6.1,
                        "end": 7.9,
                        "speaker_id": "speaker_0",
                        "role": "UNKNOWN",
                        "text": "d",
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    write_window_artifact(
        window_path,
        [
            {
                "window_index": 1,
                "emitted_from_seconds": 0.0,
                "emitted_until_seconds": 2.0,
            },
            {
                "window_index": 2,
                "emitted_from_seconds": 2.0,
                "emitted_until_seconds": 8.0,
            },
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
                    {
                        "start": 0.1,
                        "end": 1.9,
                        "speaker_id": "speaker_0",
                        "role": "DOCTOR",
                        "text": "a",
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

    assert (
        f"row diagnostics: 1 rows -> {rows_path} (no window artifact)" in result.stdout
    )

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


def test_live_and_corrected_lane_scores_keep_word_defects_independent() -> None:
    """A developer sees omissions, insertions, and duplicates in the lane the clinician saw."""
    transcript_quality_scorer = load_transcript_quality_scorer()

    lane_scores = transcript_quality_scorer.score_transcript_lanes(
        speech_truth_words=["metformin"],
        lanes=[
            {
                "lane": "live",
                "artifact_sha256": "a" * 64,
                "hypothesis_words": ["metformin", "metformin", "aspirin"],
            },
            {
                "lane": "corrected",
                "artifact_sha256": "b" * 64,
                "hypothesis_words": ["meltformin"],
            },
        ],
    )

    assert lane_scores["live"]["omissions"] == []
    assert lane_scores["live"]["false_insertions"] == ["aspirin"]
    assert lane_scores["live"]["duplicates"] == ["metformin"]
    assert lane_scores["live"]["false_insertion_rate"] == 1.0
    assert lane_scores["live"]["omission_rate"] == 0.0
    assert lane_scores["live"]["artifact_sha256"] == "a" * 64
    assert lane_scores["corrected"]["omissions"] == ["metformin"]
    assert lane_scores["corrected"]["false_insertions"] == []
    assert lane_scores["corrected"]["duplicates"] == []
    assert lane_scores["corrected"]["wrong_or_garbled_words"] == ["meltformin"]
    assert lane_scores["corrected"]["false_insertion_rate"] == 0.0
    assert lane_scores["corrected"]["omission_rate"] == 1.0
    assert lane_scores["corrected"]["artifact_sha256"] == "b" * 64


def test_consult_29_probe_scores_ignore_expected_labels() -> None:
    """Each medication/allergy defect is computed before a reviewer trusts its label."""
    transcript_quality_scorer = load_transcript_quality_scorer()
    expectation_path = (
        REPO_ROOT / "tests/fixtures/scribe/consult-2.9-cross-lane-expectations-v1.json"
    )
    expectation_document = json.loads(expectation_path.read_text(encoding="utf-8"))

    # T02.6 owns only the five transcript probes; note review remains visibly red for T02.7.
    for scoring_probe in expectation_document["independent_scoring_probes"][:5]:
        misleading_expected_label = dict(scoring_probe)
        misleading_expected_label["outcome_class"] = "ignored_expected_label"
        scored_probe = transcript_quality_scorer.score_consult_29_probe(
            misleading_expected_label
        )
        assert scored_probe == {
            "probe_id": scoring_probe["probe_id"],
            "outcome_class": scoring_probe["outcome_class"],
            **scoring_probe["expected_score"],
        }


def test_critical_span_keeps_boundary_evidence_and_wrong_speaker_separate() -> None:
    """A boundary allergy stays visible while Doctor-owned medication text still fails."""
    transcript_quality_scorer = load_transcript_quality_scorer()
    hypothesis_segments = [
        transcript_quality_scorer.HypothesisSegment(
            start=1.0,
            end=2.0,
            speaker_id="speaker_1",
            role="PATIENT",
            text="metformin",
        ),
        transcript_quality_scorer.HypothesisSegment(
            start=2.0,
            end=3.0,
            speaker_id="speaker_0",
            role="DOCTOR",
            text="amlodipine",
        ),
        transcript_quality_scorer.HypothesisSegment(
            start=9.5,
            end=10.5,
            speaker_id="speaker_1",
            role="PATIENT",
            text="penicillin",
        ),
    ]
    failed_span = {
        "span_id": "medication-and-allergy",
        "start_seconds": 0.0,
        "end_seconds": 10.0,
        "membership_rule": "time_overlap",
        "required_terms": ["metformin", "amlodipine", "penicillin"],
        "required_role": "PATIENT",
    }
    passing_span = {
        "span_id": "boundary-allergy",
        "start_seconds": 9.0,
        "end_seconds": 10.0,
        "membership_rule": "time_overlap",
        "required_terms": ["penicillin"],
        "required_role": "PATIENT",
    }

    critical_span_score = transcript_quality_scorer.score_critical_spans(
        [failed_span, passing_span], hypothesis_segments
    )

    assert critical_span_score["passed_spans"] == 1
    assert critical_span_score["required_spans"] == 2
    assert critical_span_score["critical_term_recall"] == 0.5
    assert critical_span_score["failed_span_ids"] == ["medication-and-allergy"]
    assert critical_span_score["spans"][0]["supported_terms"] == [
        "metformin",
        "penicillin",
    ]
    assert critical_span_score["spans"][0]["omissions"] == []
    assert critical_span_score["spans"][0]["wrong_speaker_terms"] == ["amlodipine"]
    assert critical_span_score["spans"][1]["overlapping_rows"] == 1


def test_turn_coherence_reports_identity_chronology_rewrite_and_duplicates() -> None:
    """A reviewer gets every assembly defect without losing the unaffected transitions."""
    transcript_quality_scorer = load_transcript_quality_scorer()
    source_units = [
        {
            "source_unit_id": "unit-01",
            "start": 0.0,
            "role": "DOCTOR",
            "text": "Which medication?",
            "source_text": "Which medication?",
        },
        {
            "source_unit_id": "unit-02",
            "start": 2.0,
            "role": "PATIENT",
            "text": "metformin",
            "source_text": "metformin",
        },
        {
            "source_unit_id": "unit-02",
            "start": 1.0,
            "role": "DOCTOR",
            "text": "warfarin",
            "source_text": "metformin",
            "source_role": "PATIENT",
        },
        {
            "source_unit_id": "unit-04",
            "start": 3.0,
            "role": "PATIENT",
            "text": "metformin",
            "source_text": "metformin",
        },
    ]
    reference_turns = [
        {"role": "DOCTOR", "text": "Which medication?"},
        {"role": "PATIENT", "text": "metformin"},
    ]

    coherence_score = transcript_quality_scorer.score_turn_coherence(
        source_units,
        reference_turns=reference_turns,
    )

    assert coherence_score["coherent_transitions"] == 2
    assert coherence_score["eligible_transitions"] == 3
    assert coherence_score["turn_coherence"] == 2 / 3
    assert coherence_score["assembly_defects"] == 1
    assert coherence_score["assembly_defect_rate"] == 1 / 3
    assert coherence_score["duplicate_turns"] == 1
    assert coherence_score["transition_defects"] == [
        {
            "transition_index": 1,
            "from_source_unit_id": "unit-02",
            "to_source_unit_id": "unit-02",
            "reasons": [
                "duplicate_source_identity",
                "chronology_regression",
                "lexical_rewrite",
                "speaker_role_changed",
            ],
        }
    ]


def test_turn_coherence_scores_first_unit_preservation_defects() -> None:
    """A corrupted opening unit makes its first adjacent transition defective."""
    transcript_quality_scorer = load_transcript_quality_scorer()
    coherence_score = transcript_quality_scorer.score_turn_coherence(
        [
            {
                "source_unit_id": "unit-01",
                "start": 0.0,
                "role": "DOCTOR",
                "text": "Take warfarin",
                "source_text": "Take metformin",
            },
            {
                "source_unit_id": "unit-02",
                "start": 1.0,
                "role": "PATIENT",
                "text": "Understood",
                "source_text": "Understood",
            },
        ]
    )

    assert coherence_score["coherent_transitions"] == 0
    assert coherence_score["eligible_transitions"] == 1
    assert coherence_score["turn_coherence"] == 0.0
    assert coherence_score["assembly_defects"] == 1
    assert coherence_score["transition_defects"] == [
        {
            "transition_index": 0,
            "from_source_unit_id": "unit-01",
            "to_source_unit_id": "unit-02",
            "reasons": ["lexical_rewrite"],
        }
    ]


def test_empty_turn_sequence_reports_unavailable_instead_of_a_favorable_pass() -> None:
    """An empty transcript gives the reviewer unavailable coherence, never a clean score."""
    transcript_quality_scorer = load_transcript_quality_scorer()

    coherence_score = transcript_quality_scorer.score_turn_coherence([])

    assert coherence_score == {
        "coherent_transitions": 0,
        "eligible_transitions": 0,
        "turn_coherence": None,
        "assembly_defects": 0,
        "assembly_defect_rate": None,
        "duplicate_turns": None,
        "transition_defects": [],
    }


def test_transcript_scorer_import_stays_cpu_only() -> None:
    """A developer can import the scorer without loading API, provider, NeMo, or GPU code."""
    dependency_probe = subprocess.run(
        [
            sys.executable,
            "-c",
            """
import importlib.util, json, sys
from pathlib import Path
path = Path(sys.argv[1])
spec = importlib.util.spec_from_file_location("cpu_only_transcript_probe", path)
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)
roots = {name.partition(".")[0] for name in sys.modules}
forbidden = {"fastapi", "nemo", "strands", "strands_agents", "torch"}
print(json.dumps(sorted(forbidden & roots)))
""",
            str(TRANSCRIPT_QUALITY_SCORER_PATH),
        ],
        check=True,
        text=True,
        capture_output=True,
    )

    assert json.loads(dependency_probe.stdout) == []
