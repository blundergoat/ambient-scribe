"""Synthetic contracts for the CPU-only M05 source-chip disposition."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from copy import deepcopy
from pathlib import Path
from types import ModuleType
from typing import Any, Callable

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
EVALUATOR_PATH = REPO_ROOT / "scripts/m05-source-chip-disposition.py"
SYNTHETIC_WORDING = (
    "Synthetic non-clinical wording that must never appear in disposition output."
)


def load_evaluator() -> ModuleType:
    """Load the exact evaluator file used by the M05-only runner."""
    evaluator_spec = importlib.util.spec_from_file_location(
        "ambient_scribe_m05_source_chip_disposition_test_module",
        EVALUATOR_PATH,
    )
    assert evaluator_spec is not None
    assert evaluator_spec.loader is not None
    evaluator = importlib.util.module_from_spec(evaluator_spec)
    sys.modules[evaluator_spec.name] = evaluator
    evaluator_spec.loader.exec_module(evaluator)
    return evaluator


def write_json(path: Path, document: object) -> None:
    """Write one deterministic synthetic input."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(document, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def synthetic_documents(
    tmp_path: Path,
) -> tuple[Path, Path, dict[str, Any], dict[str, Any], list[dict[str, Any]]]:
    """Create one fixture containing each supported disposition class."""
    fixture = "synthetic-fixture"
    corrected_root = tmp_path / "evidence"
    fixture_root = corrected_root / fixture
    corrected_path = fixture_root / "corrected-transcript.json"
    diagnostics_path = fixture_root / "corrected-row-diagnostics.json"
    score_path = corrected_root / "source-chip-score.json"

    segments = [
        {
            "end": 2.0,
            "role": "DOCTOR",
            "segment_id": "synthetic-0001",
            "start": 1.0,
            "text": f"{SYNTHETIC_WORDING} one",
        },
        {
            "end": 4.0,
            "role": "PATIENT",
            "segment_id": "synthetic-0002",
            "start": 3.0,
            "text": f"{SYNTHETIC_WORDING} two",
        },
        {
            "end": 6.0,
            "role": "DOCTOR",
            "segment_id": "synthetic-0003",
            "start": 5.0,
            "text": f"{SYNTHETIC_WORDING} three",
        },
        {
            "end": 8.0,
            "role": "UNRESOLVED",
            "segment_id": "synthetic-0004",
            "start": 7.0,
            "text": f"{SYNTHETIC_WORDING} non-finding",
        },
    ]
    diagnostic_rows = [
        {
            "confidently_wrong": False,
            "correct": True,
            "end": 2.0,
            "expected_role": "DOCTOR",
            "row_index": 0,
            "start": 1.0,
            "visible_role": "DOCTOR",
        },
        {
            "confidently_wrong": True,
            "correct": False,
            "end": 4.0,
            "expected_role": "DOCTOR",
            "row_index": 1,
            "start": 3.0,
            "visible_role": "PATIENT",
        },
        {
            "confidently_wrong": False,
            "correct": None,
            "end": 6.0,
            "expected_role": None,
            "row_index": 2,
            "start": 5.0,
            "visible_role": "DOCTOR",
        },
        {
            "confidently_wrong": False,
            "correct": None,
            "end": 8.0,
            "expected_role": None,
            "row_index": 3,
            "start": 7.0,
            "visible_role": "UNRESOLVED",
        },
    ]
    findings = [
        {
            "artifact_path": str(corrected_path),
            "codes": ["synthetic_review"],
            "end": segment["end"],
            "evidence": ["synthetic evidence"],
            "role": segment["role"],
            "row_number": index,
            "segment_id": segment["segment_id"],
            "severity": "error",
            "start": segment["start"],
            "text": segment["text"],
        }
        for index, segment in enumerate(segments[:3], start=1)
    ]
    score_document = [
        {
            "artifact_path": str(corrected_path),
            "error_count": 3,
            "finding_count": 3,
            "findings": findings,
            "row_count": 4,
            "warning_count": 0,
        }
    ]
    transcript_document = {"segments": segments}
    diagnostics_document = {"rows": diagnostic_rows}
    write_json(corrected_path, transcript_document)
    write_json(diagnostics_path, diagnostics_document)
    write_json(score_path, score_document)
    return (
        score_path,
        corrected_root,
        transcript_document,
        diagnostics_document,
        score_document,
    )


def rebuild_inputs(
    score_path: Path,
    corrected_root: Path,
    transcript_document: dict[str, Any],
    diagnostics_document: dict[str, Any],
    score_document: list[dict[str, Any]],
) -> None:
    """Persist mutated synthetic documents before one rejection check."""
    fixture_root = corrected_root / "synthetic-fixture"
    write_json(fixture_root / "corrected-transcript.json", transcript_document)
    write_json(
        fixture_root / "corrected-row-diagnostics.json",
        diagnostics_document,
    )
    write_json(score_path, score_document)


def build_synthetic_disposition(tmp_path: Path) -> tuple[ModuleType, dict[str, Any]]:
    """Build one valid result and return its loaded evaluator."""
    evaluator = load_evaluator()
    score_path, corrected_root, *_documents = synthetic_documents(tmp_path)
    result = evaluator.build_source_chip_disposition(
        score_path,
        corrected_root,
        tmp_path,
    )
    return evaluator, result


def test_classifies_all_three_supported_dispositions(tmp_path: Path) -> None:
    """Every aligned finding receives exactly one frozen classification."""
    _evaluator, result = build_synthetic_disposition(tmp_path)

    assert result["status"] == "complete"
    assert result["counts"] == {
        "alignment_mismatch_count": 0,
        "artifact_count": 1,
        "classification_counts": {
            "truth_aligned_heuristic_false_positive": 1,
            "baseline_confirmed_role_error": 1,
            "reference_gap": 1,
        },
        "classified_finding_count": 3,
        "finding_count": 3,
        "row_count": 4,
        "unclassified_finding_count": 0,
    }


def test_render_is_byte_deterministic_and_text_free(tmp_path: Path) -> None:
    """Repeated output is identical and excludes all synthetic wording."""
    evaluator, first_result = build_synthetic_disposition(tmp_path)
    score_path = tmp_path / "evidence" / "source-chip-score.json"
    second_result = evaluator.build_source_chip_disposition(
        score_path,
        tmp_path / "evidence",
        tmp_path,
    )

    first_render = evaluator.render_source_chip_disposition(first_result)
    second_render = evaluator.render_source_chip_disposition(second_result)

    assert first_render == second_render
    assert SYNTHETIC_WORDING not in first_render
    assert '"text":' not in first_render
    assert '"evidence":' not in first_render


@pytest.mark.parametrize(
    ("field_name", "replacement", "expected_error"),
    [
        ("row_number", 5, "finding row is out of range"),
        ("segment_id", "synthetic-wrong", "segment identity differs"),
        ("role", "PATIENT", "visible role differs"),
        ("start", 1.25, "timing differs"),
        ("end", 2.25, "timing differs"),
        ("text", "different synthetic wording", "wording fingerprint differs"),
    ],
)
def test_rejects_every_finding_identity_mismatch(
    tmp_path: Path,
    field_name: str,
    replacement: object,
    expected_error: str,
) -> None:
    """Row, segment, role, timing, and wording identities all fail closed."""
    evaluator = load_evaluator()
    inputs = synthetic_documents(tmp_path)
    score_path, corrected_root, transcript, diagnostics, score = inputs
    score[0]["findings"][0][field_name] = replacement
    rebuild_inputs(score_path, corrected_root, transcript, diagnostics, score)

    with pytest.raises(
        evaluator.M05SourceChipDispositionError,
        match=expected_error,
    ):
        evaluator.build_source_chip_disposition(score_path, corrected_root, tmp_path)


def test_rejects_fixture_artifact_identity_mismatch(tmp_path: Path) -> None:
    """A score cannot redirect one fixture to another artifact root."""
    evaluator = load_evaluator()
    inputs = synthetic_documents(tmp_path)
    score_path, corrected_root, transcript, diagnostics, score = inputs
    score[0]["artifact_path"] = str(
        corrected_root / "other-fixture" / "corrected-transcript.json"
    )
    rebuild_inputs(score_path, corrected_root, transcript, diagnostics, score)

    with pytest.raises(
        evaluator.M05SourceChipDispositionError,
        match="corrected artifact is missing",
    ):
        evaluator.build_source_chip_disposition(score_path, corrected_root, tmp_path)


@pytest.mark.parametrize(
    ("mutate", "expected_error"),
    [
        (
            lambda transcript, diagnostics: diagnostics["rows"][0].update(
                {"row_index": 1}
            ),
            "diagnostic row identity differs",
        ),
        (
            lambda transcript, diagnostics: diagnostics["rows"][0].update(
                {"visible_role": "PATIENT"}
            ),
            "diagnostic visible role differs",
        ),
        (
            lambda transcript, diagnostics: diagnostics["rows"][0].update(
                {"start": 1.5}
            ),
            "diagnostic timing differs",
        ),
        (
            lambda transcript, diagnostics: diagnostics["rows"][0].update(
                {"end": 2.5}
            ),
            "diagnostic timing differs",
        ),
    ],
)
def test_rejects_every_diagnostic_identity_mismatch(
    tmp_path: Path,
    mutate: Callable[[dict[str, Any], dict[str, Any]], None],
    expected_error: str,
) -> None:
    """Diagnostic row, role, and both timing identities must align."""
    evaluator = load_evaluator()
    inputs = synthetic_documents(tmp_path)
    score_path, corrected_root, transcript, diagnostics, score = inputs
    mutate(transcript, diagnostics)
    rebuild_inputs(score_path, corrected_root, transcript, diagnostics, score)

    with pytest.raises(
        evaluator.M05SourceChipDispositionError,
        match=expected_error,
    ):
        evaluator.build_source_chip_disposition(score_path, corrected_root, tmp_path)


def test_rejects_duplicate_finding_identity(tmp_path: Path) -> None:
    """A repeated fixture/row/segment tuple cannot satisfy completeness."""
    evaluator = load_evaluator()
    inputs = synthetic_documents(tmp_path)
    score_path, corrected_root, transcript, diagnostics, score = inputs
    score[0]["findings"].append(deepcopy(score[0]["findings"][0]))
    score[0]["finding_count"] = 4
    score[0]["error_count"] = 4
    rebuild_inputs(score_path, corrected_root, transcript, diagnostics, score)

    with pytest.raises(
        evaluator.M05SourceChipDispositionError,
        match="duplicate finding identity",
    ):
        evaluator.build_source_chip_disposition(score_path, corrected_root, tmp_path)


@pytest.mark.parametrize(
    ("field_name", "replacement", "expected_error"),
    [
        ("row_count", 5, "score row count disagrees"),
        ("finding_count", 4, "score finding count disagrees"),
        ("error_count", 2, "severity counts disagree"),
    ],
)
def test_rejects_count_disagreement(
    tmp_path: Path,
    field_name: str,
    replacement: int,
    expected_error: str,
) -> None:
    """Saved row, finding, and severity totals cannot drift."""
    evaluator = load_evaluator()
    inputs = synthetic_documents(tmp_path)
    score_path, corrected_root, transcript, diagnostics, score = inputs
    score[0][field_name] = replacement
    rebuild_inputs(score_path, corrected_root, transcript, diagnostics, score)

    with pytest.raises(
        evaluator.M05SourceChipDispositionError,
        match=expected_error,
    ):
        evaluator.build_source_chip_disposition(score_path, corrected_root, tmp_path)


def test_rejects_unclassified_diagnostic(tmp_path: Path) -> None:
    """An unresolved wrong row cannot be silently forced into a class."""
    evaluator = load_evaluator()
    inputs = synthetic_documents(tmp_path)
    score_path, corrected_root, transcript, diagnostics, score = inputs
    diagnostics["rows"][1]["confidently_wrong"] = False
    rebuild_inputs(score_path, corrected_root, transcript, diagnostics, score)

    with pytest.raises(
        evaluator.M05SourceChipDispositionError,
        match="inconsistent confidently-wrong diagnostics",
    ):
        evaluator.build_source_chip_disposition(score_path, corrected_root, tmp_path)


def test_rejects_malformed_and_missing_inputs(tmp_path: Path) -> None:
    """Malformed scores and absent diagnostics both stop without output."""
    evaluator = load_evaluator()
    score_path, corrected_root, *_documents = synthetic_documents(tmp_path)
    score_path.write_text("{", encoding="utf-8")

    with pytest.raises(
        evaluator.M05SourceChipDispositionError,
        match="malformed JSON",
    ):
        evaluator.build_source_chip_disposition(score_path, corrected_root, tmp_path)

    score_path, corrected_root, *_documents = synthetic_documents(tmp_path)
    missing_root = tmp_path / "missing"
    with pytest.raises(
        evaluator.M05SourceChipDispositionError,
        match="corrected root is missing",
    ):
        evaluator.build_source_chip_disposition(score_path, missing_root, tmp_path)


def test_rejects_attempted_transcript_text_output(tmp_path: Path) -> None:
    """The final output guard rejects a newly introduced wording field."""
    evaluator, result = build_synthetic_disposition(tmp_path)
    result["findings"][0]["text"] = SYNTHETIC_WORDING

    with pytest.raises(
        evaluator.M05SourceChipDispositionError,
        match="output contains forbidden field",
    ):
        evaluator.render_source_chip_disposition(result)


def test_cli_failure_emits_no_disposition_document(tmp_path: Path) -> None:
    """A malformed score returns nonzero with no partial JSON on stdout."""
    score_path, corrected_root, *_documents = synthetic_documents(tmp_path)
    score_path.write_text("{", encoding="utf-8")

    result = subprocess.run(
        [
            sys.executable,
            str(EVALUATOR_PATH),
            "--repo-root",
            str(tmp_path),
            "--source-chip-score",
            str(score_path),
            "--corrected-root",
            str(corrected_root),
        ],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 1
    assert result.stdout == ""
    assert "malformed JSON" in result.stderr
