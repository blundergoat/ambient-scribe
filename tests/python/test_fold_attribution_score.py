"""Test the TextGrid-grounded speaker-fold acceptance scorer.

Developers run this scorer after a named replay to learn whether a cache-slot
alias placed words under the correct visible role, the wrong role, or no
confident role. Tiny retained-artifact shapes keep that release gate
verifiable without replaying audio or loading the clinician-facing GPU.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
SCORER_PATH = REPO_ROOT / "scripts/fold-attribution-score.py"


def write_textgrid(
    path: Path,
    speech_intervals: list[tuple[float, float, str]],
) -> None:
    """Write the minimal Doctor/Patient TextGrid shape used by the shared scorer.

    Args:
        path: Role-qualified TextGrid path; an empty file means that role never spoke.
        speech_intervals: Timed wording; an empty list means no reference speech.
    """
    # Each interval represents wording the user heard from this ground-truth speaker.
    interval_blocks = "\n".join(
        f'xmin = {start}\nxmax = {end}\ntext = "{wording}"'
        for start, end, wording in speech_intervals
    )
    path.write_text(interval_blocks, encoding="utf-8")


def run_fold_attribution_score(
    continuity_path: Path,
    history_path: Path,
    doctor_textgrid_path: Path,
    patient_textgrid_path: Path,
    baseline_history_path: Path | None = None,
) -> tuple[subprocess.CompletedProcess[str], dict]:
    """Run the operator scorer and return its process plus parsed report.

    Args:
        continuity_path: Fold evidence; empty content means no windows were captured.
        history_path: Candidate UI history; empty segments mean no visible transcript.
        doctor_textgrid_path: Doctor truth; an empty file means the doctor did not speak.
        patient_textgrid_path: Patient truth; an empty file means the patient did not speak.
        baseline_history_path: Phase 0 UI history, or None to omit delta comparison.

    Returns:
        Completed process and JSON report; a failed process returns an empty report.
    """
    command_arguments = [
        sys.executable,
        str(SCORER_PATH),
        str(continuity_path),
        str(history_path),
        str(doctor_textgrid_path),
        str(patient_textgrid_path),
    ]

    # Operators add Phase 0 history only when judging whether attribution improved.
    if baseline_history_path is not None:
        command_arguments.extend(["--baseline-history", str(baseline_history_path)])

    completed_process = subprocess.run(
        command_arguments,
        cwd=REPO_ROOT,
        check=False,
        text=True,
        capture_output=True,
    )
    parsed_report = {}

    # Successful scoring always returns one JSON object for the replay artifact.
    if completed_process.returncode == 0:
        parsed_report = json.loads(completed_process.stdout)

    return completed_process, parsed_report


def test_fold_scorer_classifies_known_roles_and_deduplicates_revisions(
    tmp_path: Path,
) -> None:
    """Count harmful, benign, and unresolved aliases without exposing visit wording."""
    continuity_path = tmp_path / "window-continuity.jsonl"
    history_path = tmp_path / "history.json"
    doctor_textgrid_path = tmp_path / "visit.doctor.TextGrid"
    patient_textgrid_path = tmp_path / "visit.patient.TextGrid"

    write_textgrid(
        doctor_textgrid_path,
        [(0.0, 3.0, "private doctor reference")],
    )
    write_textgrid(
        patient_textgrid_path,
        [(3.0, 6.0, "private patient reference")],
    )
    history_path.write_text(
        json.dumps(
            {
                "session_id": "grounded-score-session",
                "segments": [
                    {
                        "start": 0.0,
                        "end": 2.0,
                        "speaker_id": "speaker_0",
                        "role": "DOCTOR",
                        "text": "private benign visible wording",
                    },
                    {
                        "start": 3.0,
                        "end": 5.0,
                        "speaker_id": "speaker_0",
                        "role": "DOCTOR",
                        "text": "private harmful visible wording",
                    },
                    {
                        "start": 6.0,
                        "end": 7.0,
                        "speaker_id": "speaker_1",
                        "role": "UNKNOWN",
                        "text": "private unresolved visible wording",
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    fold_spans = [
        {
            "origin_speaker_slot": "speaker_2",
            "visible_speaker_slot": "speaker_0",
            "start_seconds": 0.5,
            "end_seconds": 1.0,
            "word_count": 2,
        },
        {
            "origin_speaker_slot": "speaker_2",
            "visible_speaker_slot": "speaker_0",
            "start_seconds": 3.5,
            "end_seconds": 4.0,
            "word_count": 2,
        },
        {
            "origin_speaker_slot": "speaker_3",
            "visible_speaker_slot": "speaker_1",
            "start_seconds": 6.5,
            "end_seconds": 6.8,
            "word_count": 1,
        },
    ]
    continuity_rows = [
        {
            "event": "nemo_session.window_continuity",
            "window_index": 1,
            "folded_word_spans": fold_spans,
        },
        {
            "event": "nemo_session.window_continuity",
            "window_index": 2,
            "folded_word_spans": [fold_spans[1]],
        },
        {
            "event": "window_continuity.summary",
            "window_count": 2,
        },
    ]
    continuity_path.write_text(
        "\n".join(json.dumps(row) for row in continuity_rows),
        encoding="utf-8",
    )

    completed_process, report = run_fold_attribution_score(
        continuity_path,
        history_path,
        doctor_textgrid_path,
        patient_textgrid_path,
    )

    assert completed_process.returncode == 0, completed_process.stderr
    assert report["session_id"] == "grounded-score-session"
    assert report["summary"] == {
        "fold_events": 4,
        "unique_fold_spans": 3,
        "unique_harmful_cross_speaker_folds": 1,
        "unique_benign_same_role_folds": 1,
        "unique_unresolved_folds": 1,
    }
    assert [event["classification"] for event in report["fold_events"]] == [
        "benign_same_role_fold",
        "harmful_cross_speaker_fold",
        "unresolved_fold",
        "harmful_cross_speaker_fold",
    ]
    assert report["fold_events"][1]["expected_role"] == "PATIENT"
    assert report["fold_events"][1]["visible_roles"] == ["DOCTOR"]
    assert "private" not in completed_process.stdout


def test_fold_scorer_reports_an_empty_replay_without_inventing_evidence(
    tmp_path: Path,
) -> None:
    """A replay with no fold spans remains a valid zero-count operator artifact."""
    continuity_path = tmp_path / "window-continuity.jsonl"
    history_path = tmp_path / "history.json"
    doctor_textgrid_path = tmp_path / "visit.doctor.TextGrid"
    patient_textgrid_path = tmp_path / "visit.patient.TextGrid"

    continuity_path.write_text(
        json.dumps({"event": "window_continuity.summary", "window_count": 0}),
        encoding="utf-8",
    )
    history_path.write_text(
        json.dumps({"session_id": "no-fold-session", "segments": []}),
        encoding="utf-8",
    )
    write_textgrid(doctor_textgrid_path, [])
    write_textgrid(patient_textgrid_path, [])

    completed_process, report = run_fold_attribution_score(
        continuity_path,
        history_path,
        doctor_textgrid_path,
        patient_textgrid_path,
    )

    assert completed_process.returncode == 0, completed_process.stderr
    assert report["summary"] == {
        "fold_events": 0,
        "unique_fold_spans": 0,
        "unique_harmful_cross_speaker_folds": 0,
        "unique_benign_same_role_folds": 0,
        "unique_unresolved_folds": 0,
    }
    assert report["fold_events"] == []


def test_fold_scorer_compares_candidate_attribution_with_phase_zero_history(
    tmp_path: Path,
) -> None:
    """Rank same-span UI attribution changes without relying on cache-slot identity."""
    continuity_path = tmp_path / "window-continuity.jsonl"
    candidate_history_path = tmp_path / "candidate-history.json"
    baseline_history_path = tmp_path / "phase-zero-history.json"
    doctor_textgrid_path = tmp_path / "visit.doctor.TextGrid"
    patient_textgrid_path = tmp_path / "visit.patient.TextGrid"

    write_textgrid(
        doctor_textgrid_path,
        [(0.0, 6.0, "private doctor reference")],
    )
    write_textgrid(patient_textgrid_path, [])
    candidate_roles = ["DOCTOR", "DOCTOR", "PATIENT", "PATIENT", "UNKNOWN", "PATIENT"]
    baseline_roles = ["PATIENT", "DOCTOR", "PATIENT", "DOCTOR", "DOCTOR", "UNKNOWN"]
    candidate_history_path.write_text(
        json.dumps(
            {
                "session_id": "candidate-session",
                "segments": [
                    {
                        "start": float(span_index),
                        "end": float(span_index + 1),
                        "speaker_id": "speaker_0",
                        "role": candidate_role,
                        "text": "private candidate wording",
                    }
                    for span_index, candidate_role in enumerate(candidate_roles)
                ],
            }
        ),
        encoding="utf-8",
    )
    baseline_history_path.write_text(
        json.dumps(
            {
                "session_id": "baseline-session",
                "segments": [
                    {
                        "start": float(span_index),
                        "end": float(span_index + 1),
                        "speaker_id": f"phase_zero_speaker_{span_index}",
                        "role": baseline_role,
                        "text": "private baseline wording",
                    }
                    for span_index, baseline_role in enumerate(baseline_roles)
                ],
            }
        ),
        encoding="utf-8",
    )
    continuity_path.write_text(
        json.dumps(
            {
                "event": "nemo_session.window_continuity",
                "window_index": 1,
                "folded_word_spans": [
                    {
                        "origin_speaker_slot": f"speaker_{span_index + 2}",
                        "visible_speaker_slot": "speaker_0",
                        "start_seconds": span_index + 0.1,
                        "end_seconds": span_index + 0.9,
                        "word_count": 1,
                    }
                    for span_index in range(6)
                ],
            }
        ),
        encoding="utf-8",
    )

    completed_process, report = run_fold_attribution_score(
        continuity_path,
        candidate_history_path,
        doctor_textgrid_path,
        patient_textgrid_path,
        baseline_history_path,
    )

    assert completed_process.returncode == 0, completed_process.stderr
    assert report["attribution_delta"] == {
        "improved": 1,
        "newly_confident_wrong": 2,
        "unchanged_correct": 1,
        "unchanged_unresolved": 0,
        "unchanged_wrong": 1,
        "unique_compared_spans": 6,
        "worsened": 3,
    }
    assert report["fold_events"][0]["baseline_visible_roles"] == ["PATIENT"]
    assert report["fold_events"][0]["attribution_delta"] == "improved"
    assert report["fold_events"][3]["candidate_attribution"] == "wrong"
    assert report["fold_events"][3]["attribution_delta"] == "worsened"
    assert "private" not in completed_process.stdout
