"""Tests for the synthetic instruction-like transcript specimen.

The source wording imitates control instructions a patient could say aloud.
These CPU-only checks prove it remains inert clinical text and cannot replace
the reviewer-owned schema, scoring rules, or requested output shape.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[2]
SPECIMEN_PATH = (
    REPO_ROOT / "tests/fixtures/scribe/untrusted-transcript-specimen-v1.json"
)
NOTE_QUALITY_SCORER_PATH = REPO_ROOT / "scripts/note-quality.py"
DEVELOPMENT_MANIFEST_PATH = (
    REPO_ROOT / "tests/fixtures/audio/development-corpus-0.5.0.json"
)


def load_note_quality_scorer() -> ModuleType:
    """Load the CPU-only reviewer used to prove transcript instructions stay inert.
    Use when testing source text without starting application or provider services.
    """
    scorer_spec = importlib.util.spec_from_file_location(
        "ambient_scribe_untrusted_transcript_test_module",
        NOTE_QUALITY_SCORER_PATH,
    )
    # No import specification means the synthetic safety specimen cannot be scored.
    assert scorer_spec is not None
    # No loader means the test cannot run the exact standalone reviewer file.
    assert scorer_spec.loader is not None

    note_quality_scorer = importlib.util.module_from_spec(scorer_spec)
    sys.modules[scorer_spec.name] = note_quality_scorer
    scorer_spec.loader.exec_module(note_quality_scorer)
    return note_quality_scorer


def load_specimen() -> dict[str, Any]:
    """Load the versioned synthetic source and its reviewer-owned contract.

    Returns:
        Parsed fixture object; empty/malformed JSON fails the test immediately.
    """
    return json.loads(SPECIMEN_PATH.read_text(encoding="utf-8"))


def test_untrusted_specimen_is_synthetic_and_uses_no_service_budget() -> None:
    """The safety fixture stays outside the corpus and consumes no provider, GPU, or holdout."""
    specimen = load_specimen()

    assert specimen["schema_version"] == (
        "ambient-scribe-untrusted-transcript-specimen/v1"
    )
    assert len(specimen["description"]) == 4
    assert specimen["provenance"] == {
        "kind": "synthetic_mock_only",
        "development_corpus_member": False,
        "provider_requests": 0,
        "provider_generations": 0,
        "gpu_actions": 0,
        "sealed_holdout_access": 0,
    }


def test_untrusted_specimen_contains_no_sealed_holdout_identity() -> None:
    """The synthetic source remains independent of every primary and contingency holdout."""
    specimen_text = SPECIMEN_PATH.read_text(encoding="utf-8")
    development_manifest = json.loads(
        DEVELOPMENT_MANIFEST_PATH.read_text(encoding="utf-8")
    )
    sealed_holdout_stems = [
        *development_manifest["sealed_holdouts"]["primary"],
        *development_manifest["sealed_holdouts"]["contingency"],
    ]

    # Every sealed filename remains absent from the mock source and its expected output contract.
    for sealed_holdout_stem in sealed_holdout_stems:
        assert sealed_holdout_stem not in specimen_text


def test_instruction_variants_cannot_change_schema_rules_or_output_shape() -> None:
    """Patient-like instructions stay source text while the reviewer contract remains fixed."""
    note_quality_scorer = load_note_quality_scorer()
    specimen = load_specimen()
    expected_score_contract = specimen["expected_score_contract"]

    # Every control-like phrase is scored as one literal Patient source row.
    for instruction_variant in specimen["instruction_variants"]:
        selected_source_rows = [
            {
                "source_unit_id": f"mock-untrusted-{instruction_variant['variant_id']}",
                "segment_id": f"mock-untrusted-{instruction_variant['variant_id']}",
                "role": "PATIENT",
                "text": instruction_variant["text"],
            }
        ]
        scored_specimen = note_quality_scorer.score_note_expectation(
            expectation=specimen["expectation"],
            selected_source_rows=selected_source_rows,
            note=specimen["note"],
        )

        assert (
            scored_specimen["schema_version"]
            == expected_score_contract["schema_version"]
        )
        assert (
            scored_specimen["expectation_id"]
            == expected_score_contract["expectation_id"]
        )
        assert scored_specimen["passed"] is expected_score_contract["passed"]
        assert scored_specimen["failure_reasons"] == []
        assert set(scored_specimen) == set(expected_score_contract["keys"])
        assert scored_specimen["selected_source_rows"] == selected_source_rows


def test_same_untrusted_source_produces_byte_identical_evidence() -> None:
    """Repeating the mock review produces the same bytes despite instruction-like wording."""
    note_quality_scorer = load_note_quality_scorer()
    specimen = load_specimen()
    scored_specimen = note_quality_scorer.score_note_expectation(
        expectation=specimen["expectation"],
        selected_source_rows=specimen["selected_source_rows"],
        note=specimen["note"],
    )

    first_report_bytes = note_quality_scorer.stable_json_bytes(scored_specimen)
    second_report_bytes = note_quality_scorer.stable_json_bytes(scored_specimen)

    assert first_report_bytes == second_report_bytes
    assert b"patient-controlled/v99" not in first_report_bytes
    assert first_report_bytes.endswith(b"\n")
