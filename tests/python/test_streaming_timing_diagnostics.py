"""Tests for independent streaming timing and speaker-ownership diagnostics."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
DIAGNOSTICS_PATH = REPO_ROOT / "scripts/streaming-timing-diagnostics.py"


def load_diagnostics() -> ModuleType:
    """Load the CPU-only evaluator without changing import paths."""
    specification = importlib.util.spec_from_file_location(
        "streaming_timing_diagnostics_test_module",
        DIAGNOSTICS_PATH,
    )
    assert specification is not None
    assert specification.loader is not None
    module = importlib.util.module_from_spec(specification)
    sys.modules[specification.name] = module
    specification.loader.exec_module(module)
    return module


def write_textgrid(
    path: Path,
    intervals: list[tuple[float, float, str]],
) -> None:
    """Write the interval subset parsed by both quality evaluators."""
    path.write_text(
        "\n".join(
            f'xmin = {start}\nxmax = {end}\ntext = "{text}"'
            for start, end, text in intervals
        )
        + "\n",
        encoding="utf-8",
    )


@pytest.fixture
def reference_paths(tmp_path: Path) -> tuple[Path, Path]:
    """Create non-overlapping Doctor and Patient reference channels."""
    doctor_path = tmp_path / "visit.doctor.TextGrid"
    patient_path = tmp_path / "visit.patient.TextGrid"
    write_textgrid(
        doctor_path,
        [
            (0.0, 2.0, "alpha bravo"),
            (4.0, 6.0, "echo foxtrot"),
        ],
    )
    write_textgrid(
        patient_path,
        [
            (2.0, 4.0, "charlie delta"),
            (6.0, 8.0, "golf hotel"),
        ],
    )
    return doctor_path, patient_path


def sample_history() -> dict:
    """Return one fully aligned dyadic history."""
    return {
        "duration_seconds": 8.0,
        "segments": [
            {
                "speaker_id": "speaker_0",
                "role": "DOCTOR",
                "text": "alpha bravo",
                "start": 0.5,
                "end": 1.5,
            },
            {
                "speaker_id": "speaker_1",
                "role": "PATIENT",
                "text": "charlie delta",
                "start": 2.5,
                "end": 3.5,
            },
            {
                "speaker_id": "speaker_0",
                "role": "DOCTOR",
                "text": "echo foxtrot",
                "start": 4.5,
                "end": 5.5,
            },
            {
                "speaker_id": "speaker_1",
                "role": "PATIENT",
                "text": "golf hotel",
                "start": 6.5,
                "end": 7.5,
            },
        ],
    }


def evaluate(
    diagnostics: ModuleType,
    history: dict,
    reference_paths: tuple[Path, Path],
) -> dict:
    """Evaluate one tiny history through the production function."""
    doctor_path, patient_path = reference_paths
    return diagnostics.evaluate_history(
        history,
        run_id="test-run",
        doctor_path=doctor_path,
        patient_path=patient_path,
        cutoff_seconds=8.0,
        boundary_milliseconds=(150, 250, 400),
    )


def test_timestamp_mutation_leaves_ownership_section_byte_identical(
    reference_paths: tuple[Path, Path],
) -> None:
    """Speaker ownership must not improve merely because row times change."""
    diagnostics = load_diagnostics()
    original = sample_history()
    mutated = json.loads(json.dumps(original))
    for index, row in enumerate(mutated["segments"]):
        row["start"] = 20.0 + index * 2.0
        row["end"] = row["start"] + 0.25

    original_result = evaluate(diagnostics, original, reference_paths)
    mutated_result = evaluate(diagnostics, mutated, reference_paths)

    assert json.dumps(
        original_result["text_aligned_speaker_ownership"],
        sort_keys=True,
    ) == json.dumps(
        mutated_result["text_aligned_speaker_ownership"],
        sort_keys=True,
    )
    assert original_result["timing_placement"] != mutated_result["timing_placement"]


def test_ownership_reports_confusion_dyadic_oracle_and_visible_roles(
    reference_paths: tuple[Path, Path],
) -> None:
    """The three label questions retain independent denominators and mappings."""
    diagnostics = load_diagnostics()
    history = sample_history()
    history["segments"][1]["role"] = "DOCTOR"

    result = evaluate(
        diagnostics,
        history,
        reference_paths,
    )
    ownership = result["text_aligned_speaker_ownership"]

    assert ownership["unambiguous_alignment_coverage_percent"] == 100.0
    assert ownership["reference_role_counts"] == {
        "DOCTOR": 4,
        "PATIENT": 4,
    }
    assert ownership["reference_role_confusion_by_speaker"] == {
        "speaker_0": {"DOCTOR": 4, "PATIENT": 0},
        "speaker_1": {"DOCTOR": 0, "PATIENT": 4},
    }
    assert ownership["unconstrained_oracle"]["accuracy_percent"] == 100.0
    assert ownership["valid_dyadic_slot_purity"]["accuracy_percent"] == 100.0
    assert ownership["visible_role_correctness"]["accuracy_percent"] == 75.0
    upper_bound = result["timing_placement"]["healthy_class_upper_bound"]
    assert upper_bound["projected_timing_placement_accuracy_percent"] == 75.0
    assert "not a speaker-slot or visible-role forecast" in upper_bound["contract"]


def test_structural_nulls_distinguish_real_rows_and_preserve_ownership(
    tmp_path: Path,
) -> None:
    """All-group removal includes a real row that floor-only removal retains."""
    diagnostics = load_diagnostics()
    doctor_path = tmp_path / "visit.doctor.TextGrid"
    patient_path = tmp_path / "visit.patient.TextGrid"
    write_textgrid(doctor_path, [(0.0, 3.0, "alpha bravo charlie")])
    write_textgrid(patient_path, [(4.0, 5.0, "patient")])
    history = {
        "segments": [
            {
                "speaker_id": "speaker_0",
                "role": "DOCTOR",
                "text": "alpha",
                "start": 1.0,
                "end": 1.05,
            },
            {
                "speaker_id": "speaker_0",
                "role": "DOCTOR",
                "text": "bravo",
                "start": 1.0,
                "end": 1.05,
            },
            {
                "speaker_id": "speaker_0",
                "role": "DOCTOR",
                "text": "charlie",
                "start": 1.0,
                "end": 2.0,
            },
        ]
    }

    result = diagnostics.evaluate_history(
        history,
        run_id="structural-test",
        doctor_path=doctor_path,
        patient_path=patient_path,
        cutoff_seconds=5.0,
        boundary_milliseconds=(250,),
    )

    assert result["row_accounting"] == {
        "total": 3,
        "floor": 2,
        "non_floor": 1,
    }
    structural_nulls = result["structural_nulls"]
    assert structural_nulls["same_slot_start_multi_groups"] == 1
    assert structural_nulls["rows_in_groups"] == 3
    assert structural_nulls["floor_rows_in_groups"] == 2
    assert structural_nulls["real_span_rows_in_groups"] == 1
    assert structural_nulls["all_group"] == {
        "rows_removed": 2,
        "word_stream_invariant": True,
        "ownership_cells_invariant": True,
    }
    assert structural_nulls["floor_only"] == {
        "rows_removed": 1,
        "word_stream_invariant": True,
        "ownership_cells_invariant": True,
    }


def test_repeated_and_overlap_words_do_not_gain_scored_ownership(
    tmp_path: Path,
) -> None:
    """Ambiguous lexical and cross-channel order stays outside role accuracy."""
    diagnostics = load_diagnostics()
    doctor_path = tmp_path / "visit.doctor.TextGrid"
    patient_path = tmp_path / "visit.patient.TextGrid"
    write_textgrid(
        doctor_path,
        [
            (0.0, 2.0, "overlap doctor"),
            (4.0, 5.0, "yes yes"),
        ],
    )
    write_textgrid(
        patient_path,
        [
            (1.0, 3.0, "overlap patient"),
            (6.0, 7.0, "clear patient"),
        ],
    )
    history = {
        "segments": [
            {
                "speaker_id": "speaker_0",
                "role": "DOCTOR",
                "text": "overlap doctor overlap patient yes",
                "start": 0.0,
                "end": 5.0,
            },
            {
                "speaker_id": "speaker_1",
                "role": "PATIENT",
                "text": "clear patient",
                "start": 6.0,
                "end": 7.0,
            },
        ]
    }

    original_result = diagnostics.evaluate_history(
        history,
        run_id="ambiguity-test",
        doctor_path=doctor_path,
        patient_path=patient_path,
        cutoff_seconds=7.0,
        boundary_milliseconds=(250,),
    )
    mutated_history = json.loads(json.dumps(history))
    for index, row in enumerate(mutated_history["segments"]):
        row["start"] = 20.0 + index
        row["end"] = row["start"] + 0.05
    mutated_result = diagnostics.evaluate_history(
        mutated_history,
        run_id="ambiguity-test",
        doctor_path=doctor_path,
        patient_path=patient_path,
        cutoff_seconds=7.0,
        boundary_milliseconds=(250,),
    )
    ownership = original_result["text_aligned_speaker_ownership"]

    assert ownership["reference_overlap_order_words"] == 4
    assert (
        ownership["alignment"]["role_alignments"]["DOCTOR"]["ambiguous_rank_count"] >= 1
    )
    assert ownership["reference_role_counts"]["PATIENT"] >= 2
    assert json.dumps(ownership, sort_keys=True) == json.dumps(
        mutated_result["text_aligned_speaker_ownership"],
        sort_keys=True,
    )


def test_cli_output_is_byte_deterministic(
    tmp_path: Path,
    reference_paths: tuple[Path, Path],
) -> None:
    """Two executions on one sealed input produce identical JSON bytes."""
    doctor_path, patient_path = reference_paths
    history_path = tmp_path / "run" / "live-history.json"
    history_path.parent.mkdir()
    history_path.write_text(json.dumps(sample_history()), encoding="utf-8")
    first_output = tmp_path / "first.json"
    second_output = tmp_path / "second.json"
    base_command = [
        sys.executable,
        str(DIAGNOSTICS_PATH),
        "--history",
        str(history_path),
        "--doctor",
        str(doctor_path),
        "--patient",
        str(patient_path),
        "--cutoff-seconds",
        "8",
    ]

    subprocess.run(
        [*base_command, "--json", str(first_output)],
        cwd=REPO_ROOT,
        check=True,
    )
    subprocess.run(
        [*base_command, "--json", str(second_output)],
        cwd=REPO_ROOT,
        check=True,
    )

    assert first_output.read_bytes() == second_output.read_bytes()


def test_every_row_and_word_lands_in_one_accounting_bucket(
    reference_paths: tuple[Path, Path],
) -> None:
    """Floor and non-floor totals close at both row and display-word levels."""
    diagnostics = load_diagnostics()
    history = sample_history()
    history["segments"][0]["end"] = history["segments"][0]["start"] + 0.05

    result = evaluate(diagnostics, history, reference_paths)

    assert result["row_accounting"]["total"] == (
        result["row_accounting"]["floor"] + result["row_accounting"]["non_floor"]
    )
    assert result["display_word_accounting"]["total"] == (
        result["display_word_accounting"]["floor"]
        + result["display_word_accounting"]["non_floor"]
    )
    reference_accounting_by_role = result["text_aligned_speaker_ownership"][
        "alignment"
    ]["reference_accounting_by_role"]
    hypothesis_accounting = result["text_aligned_speaker_ownership"]["alignment"][
        "hypothesis_accounting"
    ]
    assert (
        sum(
            count
            for role_accounting in reference_accounting_by_role.values()
            for count in role_accounting.values()
        )
        == result["text_aligned_speaker_ownership"]["reference_words"]
    )
    assert (
        sum(hypothesis_accounting.values())
        == result["display_word_accounting"]["total"]
    )
