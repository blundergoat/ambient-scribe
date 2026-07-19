"""Tests for the standalone saved-note quality reviewer.

The reviewer compares one immutable note with its exact persisted source rows.
These CPU-only specimens pin source identity, citations, clinical state, numbers,
named terms, and required-screen behavior without starting application services.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
NOTE_QUALITY_SCORER_PATH = REPO_ROOT / "scripts/note-quality.py"


def load_note_quality_scorer() -> ModuleType:
    """Load the CPU-only reviewer a developer uses before trusting a saved note.
    Use when a focused test needs exact scoring without API or provider services.
    """
    scorer_spec = importlib.util.spec_from_file_location(
        "ambient_scribe_note_quality_test_module",
        NOTE_QUALITY_SCORER_PATH,
    )
    # No import specification means the clinician-facing note reviewer cannot run.
    assert scorer_spec is not None
    # No loader means the test cannot execute the exact standalone scorer file.
    assert scorer_spec.loader is not None

    note_quality_scorer = importlib.util.module_from_spec(scorer_spec)
    sys.modules[scorer_spec.name] = note_quality_scorer
    scorer_spec.loader.exec_module(note_quality_scorer)
    return note_quality_scorer


def test_claim_grounding_keeps_unsupported_and_miscited_outcomes_separate() -> None:
    """A reviewer sees invented wording separately from a supported claim citing the wrong row."""
    note_quality_scorer = load_note_quality_scorer()
    selected_source_rows = [
        {
            "source_unit_id": "source-medication",
            "role": "PATIENT",
            "text": "I take metformin 500 mg once daily",
        },
        {
            "source_unit_id": "source-plan",
            "role": "DOCTOR",
            "text": "Blood tests are recommended",
        },
    ]
    structured_claims = [
        {
            "claim_id": "claim-medication",
            "text": "Patient takes metformin",
            "citation_ids": ["source-plan"],
            "source_requirements": {
                "required_phrases": ["metformin"],
                "required_role": "PATIENT",
                "family": "named_term",
            },
        },
        {
            "claim_id": "claim-allergy",
            "text": "Patient is allergic to penicillin",
            "citation_ids": ["source-medication"],
            "source_requirements": {
                "required_phrases": ["penicillin"],
                "required_role": "PATIENT",
                "family": "named_term",
            },
        },
        {
            "claim_id": "claim-dose",
            "text": "Metformin 500 mg once daily",
            "citation_ids": ["source-medication"],
            "source_requirements": {
                "required_phrases": ["metformin", "500 mg", "once daily"],
                "required_role": "PATIENT",
                "family": "number",
            },
        },
    ]

    grounding_score = note_quality_scorer.score_claim_grounding(
        structured_claims, selected_source_rows
    )

    assert grounding_score == {
        "claim_count": 3,
        "unsupported_anywhere_count": 1,
        "unsupported_anywhere_claim_ids": ["claim-allergy"],
        "miscited_count": 1,
        "miscited_claim_ids": ["claim-medication"],
        "unresolved_citation_count": 0,
        "unresolved_citation_ids": [],
        "supported_claim_ids": ["claim-dose", "claim-medication"],
    }


def test_claim_grounding_reports_unresolved_citation_ids_independently() -> None:
    """A deleted source chip remains visible even when another row supports the claim."""
    note_quality_scorer = load_note_quality_scorer()
    selected_source_rows = [
        {
            "source_unit_id": "source-screen",
            "role": "PATIENT",
            "text": "No suicidal thoughts",
        }
    ]
    structured_claims = [
        {
            "claim_id": "claim-screen",
            "text": "Denies suicidal thoughts",
            "citation_ids": ["missing-source-unit"],
            "source_requirements": {
                "required_phrases": ["no suicidal thoughts"],
                "required_role": "PATIENT",
                "family": "screen",
            },
        }
    ]

    grounding_score = note_quality_scorer.score_claim_grounding(
        structured_claims, selected_source_rows
    )

    assert grounding_score["unsupported_anywhere_count"] == 0
    assert grounding_score["miscited_claim_ids"] == ["claim-screen"]
    assert grounding_score["unresolved_citation_ids"] == ["missing-source-unit"]


def test_source_identity_fails_closed_on_wrong_hash_and_missing_required_unit() -> None:
    """A reviewer cannot compare a note built from a different or incomplete saved source."""
    note_quality_scorer = load_note_quality_scorer()
    expected_source_identity = {
        "sha256": "a" * 64,
        "bytes": 120,
        "session_id": "session-a",
        "attestation_id": "attestation-a",
        "lane": "whole_visit_corrected",
    }
    actual_source_identity = {
        "sha256": "b" * 64,
        "bytes": 120,
        "session_id": "session-a",
        "attestation_id": "attestation-a",
        "lane": "whole_visit_corrected",
        "terminal_complete": True,
    }
    selected_source_rows = [
        {"segment_id": "corrected-0001", "role": "DOCTOR", "text": "Hello"}
    ]

    identity_score = note_quality_scorer.score_source_identity(
        expected_source_identity=expected_source_identity,
        actual_source_identity=actual_source_identity,
        selected_source_rows=selected_source_rows,
        required_source_unit_ids=["corrected-0001", "corrected-0002"],
    )

    assert identity_score == {
        "passed": False,
        "failure_reasons": [
            "artifact_sha256_mismatch",
            "required_source_unit_missing:corrected-0002",
        ],
        "resolved_required_source_units": 1,
        "required_source_units": 2,
    }


def test_saved_note_blocks_downstream_scoring_for_a_different_source() -> None:
    """A note from another source stays not-scorable instead of receiving clinical scores."""
    note_quality_scorer = load_note_quality_scorer()
    expectation_document = {
        "fixture_id": "mock-consult",
        "selected_source_artifact": {
            "sha256": "a" * 64,
            "bytes": 120,
            "session_id": "session-a",
            "attestation_id": "attestation-a",
            "lane": "whole_visit_corrected",
        },
        "expectations": [
            {
                "expectation_id": "C53-01",
                "selected_source_truth": {
                    "source_unit_ids": ["corrected-0001"],
                    "unsupported_terms": ["CBT"],
                },
                "required_note_behavior": {"prohibited": ["CBT"]},
            }
        ],
    }
    selected_source_document = {
        "segments": [
            {
                "segment_id": "corrected-0001",
                "role": "DOCTOR",
                "text": "Talking therapy was recommended",
            }
        ]
    }

    quality_report = note_quality_scorer.score_saved_note_document(
        expectation_document=expectation_document,
        note={"claims": ["CBT was recommended"]},
        selected_source_document=selected_source_document,
        actual_source_identity={
            "sha256": "b" * 64,
            "bytes": 120,
            "session_id": "session-a",
            "attestation_id": "attestation-a",
            "lane": "whole_visit_corrected",
            "terminal_complete": True,
        },
    )

    assert quality_report["verdict"] == "not_scorable_source"
    assert quality_report["expectations"] == []
    assert quality_report["source_identity"]["failure_reasons"] == [
        "artifact_sha256_mismatch"
    ]


def test_v2_section_and_key_point_claims_are_grounded_from_source_units() -> None:
    """Production v2 claims keep their source-unit provenance in the offline report."""
    note_quality_scorer = load_note_quality_scorer()
    selected_source_document = {
        "segments": [
            {
                "segment_id": "corrected-0001",
                "role": "PATIENT",
                "text": "I take metformin",
                "start": 1.0,
                "end": 2.0,
            }
        ]
    }
    note = {
        "schema_version": 2,
        "sections": [
            {
                "heading": "Subjective",
                "claims": [
                    {
                        "claim_id": "subjective-01",
                        "text": "Patient takes metformin.",
                        "evidence_basis": "source_unit",
                        "source_unit_ids": ["unit-0001-0001"],
                    }
                ],
            }
        ],
        "key_points": [
            {
                "claim_id": "key-point-01",
                "text": "No examination findings were documented.",
                "evidence_basis": "transcript_absence",
                "source_unit_ids": [],
            }
        ],
        "source_units": [
            {
                "unit_id": "unit-0001-0001",
                "role": "PATIENT",
                "start": 1.0,
                "end": 2.0,
                "rows": [
                    {
                        "segment_id": "corrected-0001",
                        "text": "I take metformin",
                        "start": 1.0,
                        "end": 2.0,
                    }
                ],
            }
        ],
    }

    quality_report = note_quality_scorer.score_saved_note_document(
        expectation_document={"fixture_id": "v2-note", "expectations": []},
        note=note,
        selected_source_document=selected_source_document,
        actual_source_identity={"terminal_complete": True},
    )

    assert quality_report["verdict"] == "pass"
    assert quality_report["claim_grounding"] == {
        "claim_count": 2,
        "unsupported_anywhere_count": 0,
        "unsupported_anywhere_claim_ids": [],
        "miscited_count": 0,
        "miscited_claim_ids": [],
        "unresolved_citation_count": 0,
        "unresolved_citation_ids": [],
        "supported_claim_ids": ["key-point-01", "subjective-01"],
    }


def test_v2_section_claim_text_reaches_clinical_expectation_rules() -> None:
    """Clinical guards inspect production section claims, not only legacy top-level prose."""
    note_quality_scorer = load_note_quality_scorer()

    expectation_score = note_quality_scorer.score_note_expectation(
        expectation={
            "expectation_id": "C53-01",
            "selected_source_truth": {"unsupported_terms": ["CBT"]},
            "required_note_behavior": {"prohibited_states": ["CBT"]},
        },
        selected_source_rows=[],
        note={
            "schema_version": 2,
            "sections": [
                {
                    "heading": "Plan",
                    "claims": [
                        {
                            "claim_id": "plan-01",
                            "text": "CBT was recommended.",
                            "evidence_basis": "none",
                            "source_unit_ids": [],
                        }
                    ],
                }
            ],
            "key_points": [],
        },
    )

    assert expectation_score == {
        "expectation_id": "C53-01",
        "passed": False,
        "failure_reasons": ["unsupported_named_term_reconstruction"],
    }


def test_v2_unit_with_altered_nested_row_stays_unresolved() -> None:
    """A note-controlled unit cannot substitute different prose for a selected source row."""
    note_quality_scorer = load_note_quality_scorer()
    quality_report = note_quality_scorer.score_saved_note_document(
        expectation_document={"fixture_id": "v2-note", "expectations": []},
        note={
            "schema_version": 2,
            "sections": [
                {
                    "heading": "Subjective",
                    "claims": [
                        {
                            "claim_id": "subjective-01",
                            "text": "Patient takes warfarin.",
                            "evidence_basis": "source_unit",
                            "source_unit_ids": ["unit-0001-0001"],
                        }
                    ],
                }
            ],
            "key_points": [],
            "source_units": [
                {
                    "unit_id": "unit-0001-0001",
                    "role": "PATIENT",
                    "rows": [
                        {
                            "segment_id": "corrected-0001",
                            "text": "I take warfarin",
                        }
                    ],
                }
            ],
        },
        selected_source_document={
            "segments": [
                {
                    "segment_id": "corrected-0001",
                    "role": "PATIENT",
                    "text": "I take metformin",
                }
            ]
        },
        actual_source_identity={"terminal_complete": True},
    )

    assert quality_report["verdict"] == "not_scorable_source"
    assert quality_report["claim_grounding"]["unresolved_citation_ids"] == [
        "unit-0001-0001"
    ]


def test_v2_missing_and_unbased_claims_fail_closed_before_expectations() -> None:
    """A missing v2 unit and an evidence-basis none claim cannot yield a passing note."""
    note_quality_scorer = load_note_quality_scorer()
    note = {
        "schema_version": 2,
        "sections": [
            {
                "heading": "Subjective",
                "claims": [
                    {
                        "claim_id": "subjective-01",
                        "text": "Patient is allergic to penicillin.",
                        "evidence_basis": "source_unit",
                        "source_unit_ids": ["unit-missing"],
                    }
                ],
            }
        ],
        "key_points": [
            {
                "claim_id": "key-point-01",
                "text": "Patient takes warfarin.",
                "evidence_basis": "none",
                "source_unit_ids": [],
            }
        ],
        "source_units": [],
    }

    quality_report = note_quality_scorer.score_saved_note_document(
        expectation_document={"fixture_id": "v2-note", "expectations": []},
        note=note,
        selected_source_document={
            "segments": [
                {
                    "segment_id": "corrected-0001",
                    "role": "PATIENT",
                    "text": "No known medication allergies",
                }
            ]
        },
        actual_source_identity={"terminal_complete": True},
    )

    assert quality_report["verdict"] == "not_scorable_source"
    assert quality_report["expectations"] == []
    assert quality_report["claim_grounding"]["claim_count"] == 2
    assert quality_report["claim_grounding"]["unsupported_anywhere_claim_ids"] == [
        "key-point-01",
        "subjective-01",
    ]
    assert quality_report["claim_grounding"]["unresolved_citation_ids"] == [
        "unit-missing"
    ]


def test_unresolved_claim_citation_blocks_downstream_expectation_scoring() -> None:
    """A missing claim chip is a source gate, not an ordinary note failure."""
    note_quality_scorer = load_note_quality_scorer()
    quality_report = note_quality_scorer.score_saved_note_document(
        expectation_document={
            "fixture_id": "claim-citation",
            "expectations": [
                {
                    "expectation_id": "C53-01",
                    "selected_source_truth": {"unsupported_terms": ["CBT"]},
                    "required_note_behavior": {"prohibited": ["CBT"]},
                }
            ],
        },
        note={
            "claims": [
                {
                    "claim_id": "claim-therapy",
                    "text": "Talking therapy was discussed.",
                    "citation_ids": ["missing-source-unit"],
                    "source_requirements": {
                        "required_phrases": ["talking therapy"],
                        "required_role": "DOCTOR",
                    },
                }
            ]
        },
        selected_source_document={
            "segments": [
                {
                    "segment_id": "corrected-0001",
                    "role": "DOCTOR",
                    "text": "Talking therapy was discussed",
                }
            ]
        },
        actual_source_identity={"terminal_complete": True},
    )

    assert quality_report["verdict"] == "not_scorable_source"
    assert quality_report["expectations"] == []
    assert quality_report["claim_grounding"]["unresolved_citation_count"] == 1


def test_supported_suicidality_and_safe_blood_test_states_pass() -> None:
    """The saved note keeps the supported denial and avoids strengthening recommended tests."""
    note_quality_scorer = load_note_quality_scorer()
    suicidality_expectation = {
        "expectation_id": "C53-06",
        "selected_source_truth": {
            "state": "supported_denial_with_qualifier",
            "speaker": "PATIENT",
            "qualifier": "does not want to go on like this",
        },
        "required_note_behavior": {
            "required_state": "denial_with_contextual_qualifier"
        },
    }
    blood_test_expectation = {
        "expectation_id": "C53-07",
        "selected_source_truth": {
            "state": "recommended_and_call_instruction",
            "tests_state": "recommended",
            "patient_intent_speaker": "unsafe_wrong_role",
        },
        "required_note_behavior": {
            "required_states": ["recommended", "asked_to_call_to_arrange"],
            "prohibited_states": [
                "ordered",
                "booked",
                "completed",
                "confirmed",
                "already_arranged",
            ],
        },
    }

    suicidality_score = note_quality_scorer.score_note_expectation(
        expectation=suicidality_expectation,
        selected_source_rows=[
            {
                "segment_id": "source-screen",
                "role": "PATIENT",
                "text": "No suicidal thoughts; I do not want to go on like this",
            }
        ],
        note={
            "claims": [
                "The patient denies suicidal thoughts but says she does not want to go on like this"
            ]
        },
    )
    blood_test_score = note_quality_scorer.score_note_expectation(
        expectation=blood_test_expectation,
        selected_source_rows=[
            {
                "segment_id": "source-plan",
                "role": "DOCTOR",
                "text": "Blood tests are worth having; call the line to arrange them",
            }
        ],
        note={
            "claims": [
                "Blood tests were recommended",
                "The patient was asked to call to arrange them",
            ]
        },
    )

    assert suicidality_score == {
        "expectation_id": "C53-06",
        "passed": True,
        "failure_reasons": [],
    }
    assert blood_test_score == {
        "expectation_id": "C53-07",
        "passed": True,
        "failure_reasons": [],
    }


def test_alcohol_trend_words_stay_scoped_to_the_alcohol_proposition() -> None:
    """Reduced sleep cannot turn a current alcohol snapshot into a longitudinal failure."""
    note_quality_scorer = load_note_quality_scorer()
    alcohol_expectation = {
        "expectation_id": "C53-02",
        "selected_source_truth": {
            "state": "single_current_snapshot",
            "safe_longitudinal_endpoints": 1,
        },
        "required_note_behavior": {
            "minimum_endpoints_for_change": 2,
            "prohibited_states": [
                "increased",
                "reduced",
                "resumed",
                "stopped",
                "unchanged",
            ],
        },
    }

    alcohol_score = note_quality_scorer.score_note_expectation(
        expectation=alcohol_expectation,
        selected_source_rows=[
            {
                "segment_id": "source-alcohol",
                "role": "PATIENT",
                "text": "I drink at weekends but do not track it reliably",
            }
        ],
        note={
            "claims": [
                "Sleep has significantly reduced",
                "Alcohol use is moderate at weekends",
            ]
        },
    )

    assert alcohol_score == {
        "expectation_id": "C53-02",
        "passed": True,
        "failure_reasons": [],
    }


def test_therapy_subtype_terms_stay_scoped_to_the_therapy_proposition() -> None:
    """An unrelated cognitive-status claim cannot become a reconstructed therapy subtype."""
    note_quality_scorer = load_note_quality_scorer()

    therapy_score = note_quality_scorer.score_note_expectation(
        expectation={
            "expectation_id": "C53-01",
            "selected_source_truth": {"unsupported_terms": ["CBT", "cognitive"]},
            "required_note_behavior": {
                "prohibited_states": ["cognitive_behavioural_therapy"]
            },
        },
        selected_source_rows=[],
        note={
            "claims": [
                "Talking therapy was discussed.",
                "Cognitive function was intact.",
            ]
        },
    )

    assert therapy_score == {
        "expectation_id": "C53-01",
        "passed": True,
        "failure_reasons": [],
    }


def test_chest_pain_conflict_and_condition_require_one_proposition() -> None:
    """Unrelated conflict and heartbeat wording cannot repair a clean chest-pain denial."""
    note_quality_scorer = load_note_quality_scorer()

    chest_pain_score = note_quality_scorer.score_note_expectation(
        expectation={"expectation_id": "C53-03"},
        selected_source_rows=[],
        note={
            "claims": [
                "The patient denies chest pain.",
                "However, the medication history was inconsistent.",
                "Palpitations occur when the heart is beating fast.",
            ]
        },
    )

    assert chest_pain_score == {
        "expectation_id": "C53-03",
        "passed": False,
        "failure_reasons": ["conflicting_evidence_flattened"],
    }


def test_panic_uncertainty_must_qualify_the_panic_proposition() -> None:
    """Unclear sleep wording cannot make a clean panic denial safe."""
    note_quality_scorer = load_note_quality_scorer()

    panic_score = note_quality_scorer.score_note_expectation(
        expectation={"expectation_id": "C53-04"},
        selected_source_rows=[],
        note={
            "claims": [
                "The patient denies panic attacks.",
                "The response about sleep was unclear.",
            ]
        },
    )

    assert panic_score == {
        "expectation_id": "C53-04",
        "passed": False,
        "failure_reasons": ["unsafe_ambiguous_denial"],
    }


def test_suicidality_qualifier_does_not_replace_denial_semantics() -> None:
    """Positive suicidality plus the contextual qualifier is not a supported denial."""
    note_quality_scorer = load_note_quality_scorer()

    suicidality_score = note_quality_scorer.score_note_expectation(
        expectation={
            "expectation_id": "C53-06",
            "selected_source_truth": {"qualifier": "does not want to go on like this"},
        },
        selected_source_rows=[],
        note={
            "claims": [
                "The patient reports suicidal thoughts.",
                "She does not want to go on like this.",
            ]
        },
    )

    assert suicidality_score == {
        "expectation_id": "C53-06",
        "passed": False,
        "failure_reasons": ["required_supported_screen_omitted"],
    }


def test_unknown_note_expectation_id_fails_closed() -> None:
    """A misspelled clinical rule cannot inherit the synthetic specimen's pass path."""
    note_quality_scorer = load_note_quality_scorer()

    with pytest.raises(
        note_quality_scorer.QualityHarnessError,
        match="unsupported_expectation_id:C53-99",
    ):
        note_quality_scorer.score_note_expectation(
            expectation={"expectation_id": "C53-99"},
            selected_source_rows=[],
            note={"claims": []},
        )


def test_consult_29_null_confidence_never_becomes_a_low_trust_measurement() -> None:
    """An unmeasured garble supports abstention but never invents a confidence warning."""
    note_quality_scorer = load_note_quality_scorer()
    abstention_probe = {
        "probe_id": "C29-07",
        "outcome_class": "tampered_expected_label",
        "scoring_scope": "saved_note",
        "input": {
            "selected_source_rows": [
                {
                    "role": "PATIENT",
                    "text": "mouth forming and penithy",
                    "confidence": None,
                }
            ],
            "note": {"claims": []},
        },
    }

    scored_probe = note_quality_scorer.score_consult_29_probe(abstention_probe)

    assert scored_probe == {
        "probe_id": "C29-07",
        "outcome_class": "faithful_soap_abstention",
        "outcomes": ["faithful_soap_abstention"],
        "affected_terms": ["metformin", "penicillin"],
        "passed": True,
    }


def test_consult_29_confidence_is_bound_to_the_matching_term_row() -> None:
    """An unrelated low-confidence row cannot flag a high-confidence medication term."""
    note_quality_scorer = load_note_quality_scorer()
    scored_probe = note_quality_scorer.score_consult_29_probe(
        {
            "probe_id": "C29-row-confidence",
            "scoring_scope": "selected_source_review",
            "input": {
                "selected_source_rows": [
                    {
                        "role": "PATIENT",
                        "text": "I take metformin",
                        "confidence": 0.95,
                    },
                    {
                        "role": "PATIENT",
                        "text": "The dose was unclear",
                        "confidence": 0.20,
                    },
                ],
                "note": {"claims": ["Patient takes metformin"]},
            },
        }
    )

    assert scored_probe == {
        "probe_id": "C29-row-confidence",
        "outcome_class": "no_low_trust_flag",
        "outcomes": ["no_low_trust_flag"],
        "affected_terms": [],
        "review_required": False,
    }


def test_consult_29_garble_phrase_cannot_span_source_rows() -> None:
    """Adjacent row fragments cannot manufacture a registered diagnostic garble."""
    note_quality_scorer = load_note_quality_scorer()
    scored_probe = note_quality_scorer.score_consult_29_probe(
        {
            "probe_id": "C29-row-boundary",
            "scoring_scope": "saved_note",
            "input": {
                "selected_source_rows": [
                    {"role": "PATIENT", "text": "mouth", "confidence": 0.5},
                    {"role": "PATIENT", "text": "forming", "confidence": 0.5},
                ],
                "note": {"claims": []},
            },
        }
    )

    assert scored_probe == {
        "probe_id": "C29-row-boundary",
        "outcome_class": "faithful_soap_abstention",
        "outcomes": ["faithful_soap_abstention"],
        "affected_terms": [],
        "passed": True,
    }


def test_source_identity_comes_from_one_authenticated_source_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Parsing, hash, size, lane, attestation, and completeness share one byte buffer."""
    note_quality_scorer = load_note_quality_scorer()
    source_path = tmp_path / "selected-source.json"
    source_document = {
        "session_id": "session-a",
        "attestation_id": "attestation-a",
        "source_state": "whole_visit_corrected",
        "terminal_complete": True,
        "segments": [
            {
                "segment_id": "corrected-0001",
                "role": "PATIENT",
                "text": "I take metformin",
            }
        ],
    }
    source_bytes = note_quality_scorer.stable_json_bytes(source_document)
    source_path.write_bytes(source_bytes)
    original_read_bytes = Path.read_bytes
    source_read_count = 0

    def counted_read_bytes(path: Path) -> bytes:
        nonlocal source_read_count
        if path == source_path:
            source_read_count += 1
        return original_read_bytes(path)

    monkeypatch.setattr(Path, "read_bytes", counted_read_bytes)

    loaded_document, actual_identity = (
        note_quality_scorer.load_source_document_with_identity(source_path)
    )

    assert loaded_document == source_document
    assert source_read_count == 1
    assert actual_identity == {
        "sha256": hashlib.sha256(source_bytes).hexdigest(),
        "bytes": len(source_bytes),
        "session_id": "session-a",
        "attestation_id": "attestation-a",
        "lane": "whole_visit_corrected",
        "terminal_complete": True,
    }


def test_source_identity_fails_closed_without_source_completeness_metadata(
    tmp_path: Path,
) -> None:
    """Rows alone cannot prove that the selected source reached terminal state."""
    note_quality_scorer = load_note_quality_scorer()
    source_path = tmp_path / "selected-source.json"
    source_path.write_bytes(
        note_quality_scorer.stable_json_bytes(
            {
                "session_id": "session-a",
                "segments": [
                    {
                        "segment_id": "corrected-0001",
                        "role": "PATIENT",
                        "text": "I take metformin",
                    }
                ],
            }
        )
    )

    selected_source_document, actual_identity = (
        note_quality_scorer.load_source_document_with_identity(source_path)
    )
    identity_score = note_quality_scorer.score_source_identity(
        expected_source_identity={},
        actual_source_identity=actual_identity,
        selected_source_rows=selected_source_document["segments"],
        required_source_unit_ids=[],
    )

    assert actual_identity["terminal_complete"] is False
    assert identity_score["failure_reasons"] == ["source_not_terminal_complete"]


def test_saved_note_report_is_stable_and_cpu_only() -> None:
    """The reviewer emits repeatable bytes without importing app, provider, or GPU modules."""
    dependency_probe = subprocess.run(
        [
            sys.executable,
            "-c",
            """
import importlib.util, json, sys
from pathlib import Path
path = Path(sys.argv[1])
spec = importlib.util.spec_from_file_location("cpu_only_note_probe", path)
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)
roots = {name.partition(".")[0] for name in sys.modules}
forbidden = {"fastapi", "nemo", "strands", "strands_agents", "torch"}
print(json.dumps(sorted(forbidden & roots)))
""",
            str(NOTE_QUALITY_SCORER_PATH),
        ],
        check=True,
        text=True,
        capture_output=True,
    )
    assert json.loads(dependency_probe.stdout) == []

    note_quality_scorer = load_note_quality_scorer()
    quality_report = {
        "verdict": "fail",
        "failures": [{"reason": "miscited", "claim_id": "claim-1"}],
        "schema_version": "ambient-scribe-note-quality/v1",
    }

    first_report_bytes = note_quality_scorer.stable_json_bytes(quality_report)
    second_report_bytes = note_quality_scorer.stable_json_bytes(
        json.loads(first_report_bytes)
    )

    assert first_report_bytes == second_report_bytes
    assert first_report_bytes.endswith(b"\n")


def test_required_json_reports_missing_and_invalid_evidence(tmp_path: Path) -> None:
    """A missing or malformed saved artifact fails visibly instead of becoming an empty note."""
    note_quality_scorer = load_note_quality_scorer()
    missing_path = tmp_path / "missing-note.json"
    invalid_path = tmp_path / "invalid-note.json"
    invalid_path.write_text("{not-json", encoding="utf-8")

    with pytest.raises(
        note_quality_scorer.QualityHarnessError, match="missing_fixture"
    ):
        note_quality_scorer.load_required_json(missing_path)
    with pytest.raises(note_quality_scorer.QualityHarnessError, match="invalid_json"):
        note_quality_scorer.load_required_json(invalid_path)
