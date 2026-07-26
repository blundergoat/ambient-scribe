"""Tests for timestamp-independent corrected insertion attribution."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
DIAGNOSTICS_PATH = REPO_ROOT / "scripts/corrected-insertion-diagnostics.py"


def load_diagnostics() -> ModuleType:
    """Load the CPU-only script without adding the scripts directory to imports."""
    specification = importlib.util.spec_from_file_location(
        "corrected_insertion_diagnostics_test_module",
        DIAGNOSTICS_PATH,
    )
    assert specification is not None
    assert specification.loader is not None
    module = importlib.util.module_from_spec(specification)
    sys.modules[specification.name] = module
    specification.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def diagnostics() -> ModuleType:
    """Return one classifier module shared by deterministic CPU tests."""
    return load_diagnostics()


def write_textgrid(
    path: Path,
    intervals: list[tuple[float, float, str]],
) -> None:
    """Write the minimal interval syntax consumed by transcript_alignment.py."""
    lines = []
    for start, end, text in intervals:
        lines.extend(
            [
                f"xmin = {start}",
                f"xmax = {end}",
                f'text = "{text}"',
            ]
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def transcript(text: str, *, start: float = 0.0, end: float = 1.0) -> dict:
    """Return one persisted transcript row with mutable, non-primary timing."""
    return {
        "segments": [
            {
                "speaker_id": "speaker_0",
                "role": "DOCTOR",
                "text": text,
                "start": start,
                "end": end,
            }
        ]
    }


def allocation_artifact(
    sources: list[tuple[str, int]],
    *,
    corrected_asr_words: int,
    chunk_ranges: list[tuple[int, int]] | None = None,
) -> dict[str, Any]:
    """Build one closed, text-free allocation artifact for display words."""
    source_runs = []
    source_counts = [0] * corrected_asr_words
    retained_words = 0
    for output_index, (source, source_index) in enumerate(sources):
        source_run = {
            "source": source,
            "source_start_index": source_index,
            "source_end_index": source_index + 1,
            "word_count": 1,
            "source_run_index": output_index,
            "output_start_index": output_index,
            "output_end_index": output_index + 1,
        }
        if source == "corrected_asr":
            source_counts[source_index] += 1
        else:
            source_run["source_row_index"] = 0
            retained_words += 1
        source_runs.append(source_run)

    duplicate_indices = [
        index for index, count in enumerate(source_counts) if count > 1
    ]
    unallocated_indices = [
        index for index, count in enumerate(source_counts) if count == 0
    ]
    ranges = [
        {
            "chunk_index": chunk_index,
            "start_index": start,
            "end_index": end,
        }
        for chunk_index, (start, end) in enumerate(chunk_ranges or [], start=1)
    ]
    seams = [item["end_index"] for item in ranges[:-1]]
    return {
        "schema_version": 1,
        "session_id": "00000000-0000-4000-8000-000000000501",
        "allocation": {
            "schema_version": 1,
            "allocation_mode": (
                "anchored"
                if any(source == "retained_live" for source, _ in sources)
                else "global_proportional"
            ),
            "corrected_asr_words": corrected_asr_words,
            "allocated_asr_words": sum(source_counts),
            "retained_live_words": retained_words,
            "final_display_words": len(sources),
            "rows": [
                {
                    "row_index": 0,
                    "segment_id": "corrected-0001",
                    "output_start_index": 0,
                    "output_end_index": len(sources),
                    "output_word_count": len(sources),
                    "anchor_outcome": "not_observed",
                    "anchor_score_class": "not_observed",
                    "anchor_score": None,
                    "anchor_clamped": False,
                    "source_runs": source_runs,
                }
            ],
            "accounting": {
                "source_run_words": len(sources),
                "allocation_output_words": len(sources),
                "output_word_coverage_complete": True,
                "final_output_matches_allocation": True,
                "duplicate_corrected_asr_source_indices": duplicate_indices,
                "unallocated_corrected_asr_source_indices": unallocated_indices,
            },
            "chunk_provenance": {
                "status": "observed" if ranges else "not_observed",
                "ranges": ranges,
                "seam_indices": seams,
            },
        },
    }


def references(
    tmp_path: Path,
    *,
    doctor: list[tuple[float, float, str]],
    patient: list[tuple[float, float, str]] | None = None,
) -> tuple[Path, Path]:
    """Create one role-separated TextGrid pair."""
    doctor_path = tmp_path / "visit.doctor.TextGrid"
    patient_path = tmp_path / "visit.patient.TextGrid"
    write_textgrid(doctor_path, doctor)
    write_textgrid(patient_path, patient or [])
    return doctor_path, patient_path


def evaluate(
    diagnostics: ModuleType,
    *,
    live_text: str,
    corrected_text: str,
    allocation: dict[str, Any],
    reference_paths: tuple[Path, Path],
) -> dict[str, Any]:
    """Evaluate one compact fixture through the production classifier."""
    doctor_path, patient_path = reference_paths
    return diagnostics.evaluate_artifacts(
        transcript(live_text),
        transcript(corrected_text),
        allocation,
        doctor_path=doctor_path,
        patient_path=patient_path,
        cutoff_seconds=10.0,
    )


def classifications(report: dict[str, Any]) -> list[str]:
    """Return insertion classes in deterministic hypothesis order."""
    return [
        insertion["classification"]
        for insertion in report["primary"]["corrected_insertions"]
    ]


def test_retained_live_fallback_class(
    diagnostics: ModuleType,
    tmp_path: Path,
) -> None:
    """A word copied from a missing live anchor remains explicitly live-owned."""
    reference_paths = references(
        tmp_path,
        doctor=[(0.0, 1.0, "alpha omega")],
    )
    report = evaluate(
        diagnostics,
        live_text="alpha omega",
        corrected_text="alpha retained omega",
        allocation=allocation_artifact(
            [
                ("corrected_asr", 0),
                ("retained_live", 0),
                ("corrected_asr", 1),
            ],
            corrected_asr_words=2,
        ),
        reference_paths=reference_paths,
    )

    assert classifications(report) == ["retained_live_fallback"]


def test_duplicate_allocation_source_class(
    diagnostics: ModuleType,
    tmp_path: Path,
) -> None:
    """Repeated emission of one ASR source index outranks wording ambiguity."""
    reference_paths = references(
        tmp_path,
        doctor=[(0.0, 1.0, "alpha novel omega")],
    )
    report = evaluate(
        diagnostics,
        live_text="alpha novel omega",
        corrected_text="alpha novel novel omega",
        allocation=allocation_artifact(
            [
                ("corrected_asr", 0),
                ("corrected_asr", 1),
                ("corrected_asr", 1),
                ("corrected_asr", 2),
            ],
            corrected_asr_words=3,
        ),
        reference_paths=reference_paths,
    )

    assert classifications(report) == ["duplicate_allocation_source"]
    assert report["allocation_summary"]["duplicate_corrected_asr_source_indices"] == [1]


def test_observed_chunk_seam_duplicate_class(
    diagnostics: ModuleType,
    tmp_path: Path,
) -> None:
    """An exact adjacent two-word repeat is a seam cause only with producer ranges."""
    reference_paths = references(
        tmp_path,
        doctor=[(0.0, 1.0, "start alpha beta finish")],
    )
    report = evaluate(
        diagnostics,
        live_text="start alpha beta finish",
        corrected_text="start alpha beta alpha beta finish",
        allocation=allocation_artifact(
            [("corrected_asr", index) for index in range(6)],
            corrected_asr_words=6,
            chunk_ranges=[(0, 3), (3, 6)],
        ),
        reference_paths=reference_paths,
    )

    assert classifications(report) == [
        "chunk_seam_duplicate",
        "chunk_seam_duplicate",
    ]
    assert report["allocation_summary"]["proven_chunk_seam_repeats"] == [
        {
            "seam_index": 3,
            "phrase_word_count": 2,
            "source_start_index": 1,
            "source_end_index": 5,
        }
    ]


def test_repeat_away_from_observed_seam_is_other_corrected_asr(
    diagnostics: ModuleType,
    tmp_path: Path,
) -> None:
    """Repeated model wording is not relabelled as a guessed chunk seam."""
    reference_paths = references(
        tmp_path,
        doctor=[(0.0, 1.0, "start alpha beta finish")],
    )
    report = evaluate(
        diagnostics,
        live_text="start alpha beta finish",
        corrected_text="start alpha beta alpha beta finish",
        allocation=allocation_artifact(
            [("corrected_asr", index) for index in range(6)],
            corrected_asr_words=6,
        ),
        reference_paths=reference_paths,
    )

    assert classifications(report) == [
        "other_corrected_asr",
        "other_corrected_asr",
    ]
    assert report["allocation_summary"]["chunk_provenance_status"] == "not_observed"


def test_overlap_order_ambiguity_has_its_own_class(
    diagnostics: ModuleType,
    tmp_path: Path,
) -> None:
    """A selected insertion affected by cross-channel order stays inconclusive."""
    reference_paths = references(
        tmp_path,
        doctor=[(0.0, 2.0, "alpha")],
        patient=[(1.0, 3.0, "beta")],
    )
    report = evaluate(
        diagnostics,
        live_text="alpha beta",
        corrected_text="alpha beta beta",
        allocation=allocation_artifact(
            [("corrected_asr", index) for index in range(3)],
            corrected_asr_words=3,
        ),
        reference_paths=reference_paths,
    )

    assert classifications(report) == ["ambiguous_unclassified"]
    assert (
        report["primary"]["corrected_insertions"][0]["ambiguity_reason"]
        == "reference_overlap_order"
    )


def test_timestamp_mutation_leaves_primary_report_byte_identical(
    diagnostics: ModuleType,
    tmp_path: Path,
) -> None:
    """Changing only emitted row times cannot change S/I/D or insertion classes."""
    doctor_path, patient_path = references(
        tmp_path,
        doctor=[(0.0, 1.0, "alpha omega")],
    )
    allocation = allocation_artifact(
        [
            ("corrected_asr", 0),
            ("retained_live", 0),
            ("corrected_asr", 1),
        ],
        corrected_asr_words=2,
    )
    original = diagnostics.evaluate_artifacts(
        transcript("alpha omega", start=0.0, end=1.0),
        transcript("alpha retained omega", start=0.0, end=1.0),
        allocation,
        doctor_path=doctor_path,
        patient_path=patient_path,
        cutoff_seconds=10.0,
    )
    mutated = diagnostics.evaluate_artifacts(
        transcript("alpha omega", start=40.0, end=41.0),
        transcript("alpha retained omega", start=90.0, end=90.05),
        allocation,
        doctor_path=doctor_path,
        patient_path=patient_path,
        cutoff_seconds=10.0,
    )

    assert json.dumps(original["primary"], sort_keys=True) == json.dumps(
        mutated["primary"],
        sort_keys=True,
    )


def test_all_five_classes_are_frozen_and_exhaustive(
    diagnostics: ModuleType,
) -> None:
    """The report schema always exposes every mutually exclusive class bucket."""
    assert diagnostics.CLASS_NAMES == (
        "retained_live_fallback",
        "chunk_seam_duplicate",
        "duplicate_allocation_source",
        "other_corrected_asr",
        "ambiguous_unclassified",
    )
    assert set(diagnostics.CLASS_PRIORITY) == set(diagnostics.CLASS_NAMES)


def test_invalid_source_accounting_is_rejected(
    diagnostics: ModuleType,
) -> None:
    """A diagnostic cannot classify words when its final count is inconsistent."""
    allocation = allocation_artifact(
        [("corrected_asr", 0)],
        corrected_asr_words=1,
    )
    allocation["allocation"]["accounting"]["source_run_words"] = 2

    with pytest.raises(ValueError, match="accounting did not close"):
        diagnostics.validate_allocation_artifact(allocation)


def test_path_report_contains_hashes_but_no_transcript_wording(
    diagnostics: ModuleType,
    tmp_path: Path,
) -> None:
    """The persisted classifier report contains evidence identities, not wording."""
    doctor_path, patient_path = references(
        tmp_path,
        doctor=[(0.0, 1.0, "alpha omega")],
    )
    live_path = tmp_path / "live.json"
    corrected_path = tmp_path / "corrected.json"
    allocation_path = tmp_path / "allocation.json"
    live_path.write_text(
        json.dumps(transcript("alpha omega")),
        encoding="utf-8",
    )
    corrected_path.write_text(
        json.dumps(transcript("alpha quasarword omega")),
        encoding="utf-8",
    )
    allocation_path.write_text(
        json.dumps(
            allocation_artifact(
                [("corrected_asr", index) for index in range(3)],
                corrected_asr_words=3,
            )
        ),
        encoding="utf-8",
    )

    report = diagnostics.build_report_from_paths(
        live_path=live_path,
        corrected_path=corrected_path,
        allocation_path=allocation_path,
        doctor_path=doctor_path,
        patient_path=patient_path,
        cutoff_seconds=10.0,
    )
    serialized = json.dumps(report, sort_keys=True)

    assert set(report["input_sha256"]) == {
        "live",
        "corrected",
        "allocation",
        "doctor_reference",
        "patient_reference",
        "alignment_helper",
    }
    assert "quasarword" not in serialized
    assert '"token"' not in serialized
    assert '"text"' not in serialized


def test_production_allocation_shape_joins_to_corrected_rows(
    diagnostics: ModuleType,
    tmp_path: Path,
) -> None:
    """The real allocator's diagnostic schema feeds the classifier unchanged."""
    from post_visit_correction import build_corrected_segments_with_diagnostics

    live_segments = [
        {
            "speaker_id": "speaker_0",
            "role": "DOCTOR",
            "text": "alpha omega",
            "start": 0.0,
            "end": 1.0,
        }
    ]
    corrected_segments, allocation = build_corrected_segments_with_diagnostics(
        corrected_words=["alpha", "novel", "omega"],
        live_segments=live_segments,
        model_name="test-model",
    )
    doctor_path, patient_path = references(
        tmp_path,
        doctor=[(0.0, 1.0, "alpha omega")],
    )

    report = diagnostics.evaluate_artifacts(
        {"segments": live_segments},
        {"segments": corrected_segments},
        {"allocation": allocation},
        doctor_path=doctor_path,
        patient_path=patient_path,
        cutoff_seconds=10.0,
    )

    assert classifications(report) == ["other_corrected_asr"]
    assert report["primary"]["classification_coverage"]["complete"] is True
