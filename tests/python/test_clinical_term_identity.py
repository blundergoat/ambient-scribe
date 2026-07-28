"""Tests for the standalone M05 clinical-term identity evaluator."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
CLINICAL_TERM_IDENTITY_PATH = REPO_ROOT / "scripts/clinical-term-identity.py"


def load_clinical_term_identity() -> ModuleType:
    """Load the exact standalone evaluator without importing runtime services."""
    evaluator_spec = importlib.util.spec_from_file_location(
        "ambient_scribe_clinical_term_identity_test_module",
        CLINICAL_TERM_IDENTITY_PATH,
    )
    assert evaluator_spec is not None
    assert evaluator_spec.loader is not None

    evaluator = importlib.util.module_from_spec(evaluator_spec)
    sys.modules[evaluator_spec.name] = evaluator
    evaluator_spec.loader.exec_module(evaluator)
    return evaluator


def test_clinical_term_identity_ignores_timestamps_roles_and_speakers() -> None:
    """Canonical support reads wording, not unreliable timing or ownership."""
    evaluator = load_clinical_term_identity()
    original_rows = [
        {
            "start": 293.0,
            "end": 296.0,
            "speaker_id": "speaker_1",
            "role": "PATIENT",
            "text": "metformin and penicillin",
        },
        {
            "start": 300.0,
            "end": 302.0,
            "speaker_id": "speaker_0",
            "role": "DOCTOR",
            "text": "amlodipine",
        },
    ]
    metadata_mutated_rows = [
        {
            "start": 900.0,
            "end": 900.05,
            "speaker_id": "mutated_speaker",
            "role": "UNKNOWN",
            "text": row["text"],
        }
        for row in original_rows
    ]
    required_terms = ["metformin", "losartan", "amlodipine", "penicillin"]

    original_score = evaluator.score_clinical_term_identity(
        required_terms,
        original_rows,
    )
    metadata_mutated_score = evaluator.score_clinical_term_identity(
        required_terms,
        metadata_mutated_rows,
    )

    assert original_score == metadata_mutated_score
    assert original_score["supported_term_indices"] == [0, 2, 3]
    assert original_score["missing_term_indices"] == [1]
    assert original_score["recall"] == 0.75
    assert (
        original_score["term_set_sha256"]
        == "11951dc6462821f17a0d44f814166fee96cbcfa831564148e764ca1290af8605"
    )
    assert "metformin" not in json.dumps(original_score)


def test_clinical_term_identity_does_not_join_words_across_rows() -> None:
    """A multiword clinical phrase must appear inside one clinician-visible row."""
    evaluator = load_clinical_term_identity()

    score = evaluator.score_clinical_term_identity(
        ["high blood pressure"],
        [{"text": "high blood"}, {"text": "pressure"}],
    )

    assert score["supported_term_indices"] == []
    assert score["missing_term_indices"] == [0]
    assert score["recall"] == 0.0


@pytest.mark.parametrize(
    "required_terms, expected_message",
    [
        ([""], "clinical terms must contain normalized words"),
        (["metformin", "Metformin!"], "clinical terms must be unique"),
    ],
)
def test_clinical_term_identity_rejects_unsafe_term_sets(
    required_terms: list[str],
    expected_message: str,
) -> None:
    """Empty and duplicate canonical terms fail before transcript scoring."""
    evaluator = load_clinical_term_identity()

    with pytest.raises(ValueError, match=expected_message):
        evaluator.score_clinical_term_identity(required_terms, [])


def test_empty_clinical_term_set_is_unmeasured() -> None:
    """No registered terms produces unavailable recall rather than a pass."""
    evaluator = load_clinical_term_identity()

    score = evaluator.score_clinical_term_identity([], [{"text": "metformin"}])

    assert score["required_term_count"] == 0
    assert score["supported_term_count"] == 0
    assert score["recall"] is None
