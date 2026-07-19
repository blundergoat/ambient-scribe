"""Validate the medication/allergy cross-lane fixture before quality scoring.

These CPU-only checks preserve what the clinician could actually read in each lane.
They keep speech truth, persisted evidence, and SOAP authorization independent.
Use this gate before comparing a new transcript or clinician-editable draft.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
EXPECTATION_FIXTURE_PATH = (
    REPOSITORY_ROOT
    / "tests/fixtures/scribe/consult-2.9-cross-lane-expectations-v1.json"
)
EXPECTATION_DOCUMENT = json.loads(EXPECTATION_FIXTURE_PATH.read_text(encoding="utf-8"))
EXPECTED_CANONICAL_TERMS = ("metformin", "losartan", "amlodipine", "penicillin")
EXPECTED_OUTCOME_CLASSES = (
    "omission",
    "wrong_term",
    "garble",
    "false_insertion",
    "cross_speaker_contamination",
    "low_trust_flag",
    "faithful_soap_abstention",
)


def artifact_sha256(artifact_path: Path) -> str:
    """Return the digest that identifies what the clinician-facing check uses.
    Use before scoring; an empty artifact still has its standard SHA-256 identity.
    """
    return hashlib.sha256(artifact_path.read_bytes()).hexdigest()


def retained_rows_overlapping_envelope(
    transcript_document: dict[str, object],
) -> list[dict[str, object]]:
    """Keep stored rows that touch the clinician's frozen review window.
    Use when a boundary row must remain visible even if it ends outside the window.
    """
    frozen_envelope = EXPECTATION_DOCUMENT["frozen_envelope"]

    return [
        retained_row
        # Every stored row is considered in the order the clinician's source retained it.
        for retained_row in transcript_document["segments"]
        # A partial overlap keeps the allergy answer visible at the window boundary.
        if retained_row["start"] < frozen_envelope["end_seconds"]
        and retained_row["end"] > frozen_envelope["start_seconds"]
    ]


def clinician_visible_row_fields(
    retained_row: dict[str, object],
) -> dict[str, object]:
    """Return fields that control what a clinician can trust in one transcript row.
    Use when comparing retained evidence with the frozen lane hypothesis.
    """
    # A missing value appears as null: the clinician has no confidence or provenance claim.
    return {
        "speaker_id": retained_row.get("speaker_id"),
        "role": retained_row.get("role"),
        "text": retained_row.get("text"),
        "start": retained_row.get("start"),
        "end": retained_row.get("end"),
        "confidence": retained_row.get("confidence"),
        "role_source": retained_row.get("role_source"),
        "source": retained_row.get("source"),
        "source_model": retained_row.get("source_model"),
    }


def test_fixture_freezes_identity_order_and_three_truth_layers() -> None:
    """The reviewer gets one ordered fixture with separate speech, source, and SOAP truth."""
    # Each official term stays in plan order so reports remain comparable for the clinician.
    canonical_terms = tuple(
        term_expectation["canonical_term"]
        # Every medication/allergy expectation contributes one clinician-readable label.
        for term_expectation in EXPECTATION_DOCUMENT["canonical_term_expectations"]
    )

    assert EXPECTATION_DOCUMENT["schema_version"] == (
        "ambient-scribe-consult-cross-lane-expectations/v1"
    )
    assert EXPECTATION_DOCUMENT["fixture_id"] == (
        "primock57-day2-consultation09-i-cant-move-my-left-arm"
    )
    assert len(EXPECTATION_DOCUMENT["description"]) == 4
    assert canonical_terms == EXPECTED_CANONICAL_TERMS
    assert EXPECTATION_DOCUMENT["truth_contract"] == {
        "speech_truth": "Official TextGrid truth scores transcript accuracy only.",
        "selected_source_truth": "NOT_OBSERVED",
        "required_note_behavior": "Omit or qualify every unsupported canonical name.",
        "source_transition_rule": (
            "A canonical name becomes note-authorized only through a newly accepted "
            "persisted source artifact and SHA-256 that safely supports its text and speaker."
        ),
    }


def test_all_frozen_development_artifact_identities_are_current() -> None:
    """The reviewer scores exact development bytes before trusting a transcript comparison."""
    artifact_records = list(EXPECTATION_DOCUMENT["speech_truth_artifacts"].values())

    # Every retained run contributes both clinician-visible and corrected source evidence.
    for retained_run in EXPECTATION_DOCUMENT["retained_runs"]:
        # Both lanes must retain independent hashes; an empty lane would be visible missing evidence.
        for artifact_key in ("live_artifact", "corrected_artifact"):
            artifact_records.append(retained_run[artifact_key])

    # Every named artifact must still match the bytes frozen before candidate work began.
    for artifact_record in artifact_records:
        frozen_artifact_path = REPOSITORY_ROOT / artifact_record["path"]
        assert frozen_artifact_path.stat().st_size == artifact_record["bytes"]
        assert artifact_sha256(frozen_artifact_path) == artifact_record["sha256"]


@pytest.mark.parametrize("lane_name", ("live", "corrected"))
@pytest.mark.parametrize(
    "retained_run",
    EXPECTATION_DOCUMENT["retained_runs"],
    ids=[
        retained_run["run_id"]
        # Every test result names the exact retained visit run a reviewer can inspect.
        for retained_run in EXPECTATION_DOCUMENT["retained_runs"]
    ],
)
def test_each_retained_lane_matches_exact_hypotheses_and_order(
    retained_run: dict[str, object],
    lane_name: str,
) -> None:
    """Every clinician-facing lane preserves exact wording, ownership, timing, and order.
    Use before treating a newly scored transcript as better than the retained baseline.
    """
    retained_artifact_path = (
        REPOSITORY_ROOT / retained_run[f"{lane_name}_artifact"]["path"]
    )
    retained_document = json.loads(retained_artifact_path.read_text(encoding="utf-8"))
    rows_in_artifact_order = retained_rows_overlapping_envelope(retained_document)
    expected_artifact_order = retained_run[f"{lane_name}_segment_ids_in_artifact_order"]
    expected_time_order = retained_run[f"{lane_name}_segment_ids_in_time_order"]
    frozen_lane_rows = EXPECTATION_DOCUMENT["lane_hypotheses"][lane_name][
        "rows_in_time_order"
    ]

    # The original order explains what a clinician-facing consumer would receive from storage.
    assert [
        row["segment_id"]
        # Every stored row keeps its arrival position for the clinician-facing consumer.
        for row in rows_in_artifact_order
    ] == expected_artifact_order

    # Time order makes the clinical exchange comparable without discarding original row identity.
    rows_in_time_order = sorted(
        rows_in_artifact_order,
        key=lambda retained_row: (retained_row["start"], retained_row["end"]),
    )
    assert [
        row["segment_id"]
        # Every timed row keeps its identity after chronological presentation.
        for row in rows_in_time_order
    ] == expected_time_order

    # Each stored row must match its frozen UI-visible hypothesis, not a reconstructed term.
    for retained_row, frozen_lane_row in zip(
        rows_in_time_order, frozen_lane_rows, strict=True
    ):
        expected_visible_fields = dict(frozen_lane_row)
        expected_visible_fields.pop("row_ref")
        assert clinician_visible_row_fields(retained_row) == expected_visible_fields


def test_each_scoring_outcome_has_one_independent_cpu_only_probe() -> None:
    """Each quality outcome can be shown alone before a developer runs NeMo or a provider."""
    scoring_probes = EXPECTATION_DOCUMENT["independent_scoring_probes"]

    # Stable probe order gives the clinician's quality report one addressable result per defect.
    outcome_classes = tuple(
        scoring_probe["outcome_class"]
        # Every mock contributes one clinician-visible quality outcome.
        for scoring_probe in scoring_probes
    )
    assert outcome_classes == EXPECTED_OUTCOME_CLASSES
    assert len(outcome_classes) == len(set(outcome_classes))

    # One expected outcome per mock proves a favorable result cannot mask a second defect.
    for scoring_probe in scoring_probes:
        assert scoring_probe["expected_score"]["outcomes"] == [
            scoring_probe["outcome_class"]
        ]
        assert scoring_probe["scoring_scope"] in {
            "transcript_lane",
            "selected_source_review",
            "saved_note",
        }


def test_canonical_names_remain_unauthorized_for_the_current_soap_source() -> None:
    """The clinician's draft cannot gain a clean medication/allergy name from gold or garble."""
    # Every term is checked separately because one safe omission cannot excuse another claim.
    for term_expectation in EXPECTATION_DOCUMENT["canonical_term_expectations"]:
        selected_source_truth = term_expectation["selected_source_truth"]
        required_note_behavior = term_expectation["required_note_behavior"]
        assert selected_source_truth == {
            "state": "NOT_OBSERVED",
            "canonical_supported": False,
        }
        assert "clean_canonical_claim" in required_note_behavior["prohibited_states"]
        assert "omit_canonical_term" in required_note_behavior["allowed_states"]

    assert EXPECTATION_DOCUMENT["candidate_policy"] == {
        "promoted_candidates": [],
        "mouth_forming": "observed_garble_not_an_unconditional_rewrite",
        "corrected_lane_lexicon_normalization": "not_applied",
        "runtime_changes_authorized": False,
    }


def test_null_confidence_stays_unmeasured_and_marker_silent() -> None:
    """An unmeasured corrected row stays visible without inventing a reassuring or warning flag."""
    # Row references let a reviewer inspect the two exact null-confidence words independently.
    corrected_rows_by_reference = {
        retained_row["row_ref"]: retained_row
        # Every corrected row stays addressable from a clinician-facing review result.
        for retained_row in EXPECTATION_DOCUMENT["lane_hypotheses"]["corrected"][
            "rows_in_time_order"
        ]
    }

    # Null means the UI has no measurement; it is not silently converted into low confidence.
    assert corrected_rows_by_reference["C02"]["confidence"] is None
    assert corrected_rows_by_reference["C03"]["confidence"] is None
    assert EXPECTATION_DOCUMENT["confidence_null_policy"] == {
        "state": "unmeasured",
        "low_confidence": False,
        "high_confidence": False,
        "included_in_measured_majority_denominator": False,
        "markers_produced_by_null_alone": [],
    }


def test_allergy_boundary_uses_overlap_instead_of_strict_containment() -> None:
    """The clinician keeps the partial allergy answer at the review-window boundary."""
    frozen_envelope = EXPECTATION_DOCUMENT["frozen_envelope"]
    boundary_probe = EXPECTATION_DOCUMENT["overlap_boundary_probe"]
    intersection_seconds = min(
        boundary_probe["row_end_seconds"], frozen_envelope["end_seconds"]
    ) - max(boundary_probe["row_start_seconds"], frozen_envelope["start_seconds"])
    row_end_overhang_seconds = (
        boundary_probe["row_end_seconds"] - frozen_envelope["end_seconds"]
    )

    assert intersection_seconds == pytest.approx(0.516)
    assert row_end_overhang_seconds == pytest.approx(0.254)
    assert boundary_probe["strict_containment"] is False
    assert boundary_probe["time_overlap_membership"] is True
