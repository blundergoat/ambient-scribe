"""Validate the anxiety-consult truth fixture before note-quality scoring.

These CPU-only checks keep speech, selected rows, and SOAP behavior separate.
They bind every unsafe probe to the red-first scorer specimens already retained.
Use this gate before a developer compares a transcript or clinician draft.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType

import pytest


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
EXPECTATION_FIXTURE_PATH = (
    REPOSITORY_ROOT / "tests/fixtures/scribe/consult-5.3-expectations-v1.json"
)
RED_ACCEPTANCE_TEST_PATH = (
    REPOSITORY_ROOT / "tests/python/test_quality_harness_acceptance.py"
)
EXPECTATION_DOCUMENT = json.loads(EXPECTATION_FIXTURE_PATH.read_text(encoding="utf-8"))
EXPECTED_EXPECTATION_IDS = (
    "C53-01",
    "C53-02",
    "C53-03",
    "C53-04",
    "C53-05",
    "C53-06",
    "C53-07",
)
EXPECTED_OUTCOME_STATES = (
    ("C53-01", "supported_bounded_therapy", "CBT"),
    ("C53-02", "current_snapshot_only", "increased"),
    ("C53-03", "conflicting_conditional", "clean_denial"),
    ("C53-04", "omitted_or_asked_response_unclear", "clean_denial"),
    ("C53-05", "omit_or_explicitly_qualify", "gold_reconstruction"),
    ("C53-06", "denial_with_contextual_qualifier", "omitted"),
    ("C53-07", "clinician_recommended", "ordered"),
)

# Each clinical rule is addressable by its stable reviewer-facing expectation ID.
EXPECTATIONS_BY_ID = {
    clinical_expectation["expectation_id"]: clinical_expectation
    # Every fixture row remains independent so one failure cannot mask another.
    for clinical_expectation in EXPECTATION_DOCUMENT["expectations"]
}


def load_red_acceptance_specimens() -> ModuleType:
    """Load the earlier mock failures without running a scorer or provider.
    Use when a reviewer needs proof that the JSON fixture matches the red-first contract.
    """
    red_test_spec = importlib.util.spec_from_file_location(
        "ambient_scribe_consult_53_red_specimens",
        RED_ACCEPTANCE_TEST_PATH,
    )

    # No import specification means the saved red-first contract cannot be compared.
    assert red_test_spec is not None
    # No loader means the reviewer cannot prove the fixture matches the earlier failures.
    assert red_test_spec.loader is not None

    red_acceptance_specimens = importlib.util.module_from_spec(red_test_spec)
    sys.modules[red_test_spec.name] = red_acceptance_specimens
    red_test_spec.loader.exec_module(red_acceptance_specimens)
    return red_acceptance_specimens


def artifact_sha256(artifact_path: Path) -> str:
    """Return one fixture digest for the reviewer-visible source identity.
    Use before scoring; an empty file still produces its real standard SHA-256.
    """
    return hashlib.sha256(artifact_path.read_bytes()).hexdigest()


def test_expectation_fixture_has_frozen_identity_and_order() -> None:
    """The reviewer gets one versioned seven-rule fixture in the approved order."""
    assert EXPECTATION_DOCUMENT["schema_version"] == (
        "ambient-scribe-consult-expectations/v1"
    )
    assert EXPECTATION_DOCUMENT["fixture_id"] == (
        "primock57-day5-consultation03-im-feeling-very-anxious"
    )
    assert len(EXPECTATION_DOCUMENT["description"]) == 4
    assert tuple(EXPECTATIONS_BY_ID) == EXPECTED_EXPECTATION_IDS
    assert len(EXPECTATIONS_BY_ID) == len(set(EXPECTATIONS_BY_ID)) == 7
    assert EXPECTATION_DOCUMENT["source_transition_rule"] == (
        "selected_source_truth changes only with a newly accepted persisted artifact and SHA-256"
    )


@pytest.mark.parametrize(
    "artifact_record",
    (
        EXPECTATION_DOCUMENT["speech_truth_artifacts"]["doctor"],
        EXPECTATION_DOCUMENT["speech_truth_artifacts"]["patient"],
        EXPECTATION_DOCUMENT["selected_source_artifact"],
    ),
)
def test_truth_artifact_identity_is_current(artifact_record: dict[str, object]) -> None:
    """The reviewer scores the exact frozen truth/source bytes, never a nearby artifact."""
    truth_artifact_path = REPOSITORY_ROOT / str(artifact_record["path"])

    assert truth_artifact_path.stat().st_size == artifact_record["bytes"]
    assert artifact_sha256(truth_artifact_path) == artifact_record["sha256"]


@pytest.mark.parametrize(
    ("expectation_id", "required_state", "prohibited_state"),
    EXPECTED_OUTCOME_STATES,
)
def test_each_outcome_has_an_independent_required_and_prohibited_state(
    expectation_id: str,
    required_state: str,
    prohibited_state: str,
) -> None:
    """Each unsafe clinical outcome fails on its own when a reviewer checks the draft."""
    clinical_expectation = EXPECTATIONS_BY_ID[expectation_id]
    required_note_behavior = clinical_expectation["required_note_behavior"]

    assert set(clinical_expectation) >= {
        "speech_truth",
        "selected_source_truth",
        "required_note_behavior",
        "unsafe_probe",
    }
    assert required_state in required_note_behavior["required_states"]
    assert prohibited_state in required_note_behavior["prohibited_states"]
    assert clinical_expectation["criticality"] == "L1-CLINICAL"


def test_selected_source_units_resolve_in_the_frozen_artifact() -> None:
    """Every cited source row exists before its behavior can constrain the SOAP draft."""
    selected_source_path = (
        REPOSITORY_ROOT / EXPECTATION_DOCUMENT["selected_source_artifact"]["path"]
    )
    selected_source_document = json.loads(
        selected_source_path.read_text(encoding="utf-8")
    )
    selected_source_unit_ids = {
        selected_source_unit["segment_id"]
        # Every persisted row contributes its stable ID to the source-resolution gate.
        for selected_source_unit in selected_source_document["segments"]
    }

    # Each clinical expectation must resolve all of its endpoint/source-unit references.
    for clinical_expectation in EXPECTATION_DOCUMENT["expectations"]:
        assert set(
            clinical_expectation["selected_source_truth"]["source_unit_ids"]
        ).issubset(selected_source_unit_ids)


def test_unsafe_probes_match_the_red_first_specimens() -> None:
    """The JSON fixture preserves every earlier failure a developer must reproduce."""
    red_acceptance_specimens = load_red_acceptance_specimens()
    red_cases_by_id = {
        red_case["expectation"]["expectation_id"]: red_case
        # Every prior red case is compared with the matching JSON expectation only.
        for red_case in red_acceptance_specimens.CONSULT_53_CASES
    }

    # Each unsafe mock retains the same rows, note, and independent failure reason.
    for expectation_id in EXPECTED_EXPECTATION_IDS:
        unsafe_probe = EXPECTATIONS_BY_ID[expectation_id]["unsafe_probe"]
        red_case = red_cases_by_id[expectation_id]
        assert unsafe_probe["selected_source_rows"] == red_case["selected_source_rows"]
        assert unsafe_probe["note"] == red_case["note"]
        assert unsafe_probe["expected_failure"] == red_case["expected_failure"]
