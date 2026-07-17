"""Protect the 0.5.0 quality contract before any clinician-facing comparison.

These CPU-only checks keep transcript and SOAP arithmetic complete and separate.
They also pin the seven anxiety-consult outcomes and valid promotion verdicts.
Use this file before a replay or candidate campaign; it opens no clinical fixture.
"""

from pathlib import Path

import pytest


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
QUALITY_CONTRACT_PATH = REPOSITORY_ROOT / ".goat-flow/plans/0.5.0/QUALITY-CONTRACT.md"
QUALITY_CONTRACT_TEXT = QUALITY_CONTRACT_PATH.read_text(encoding="utf-8")

TRANSCRIPT_METRIC_FORMULAS = (
    ("TX-01", "clean_WER=(S+I+D)/N_ref"),
    ("TX-02", "overlap_WER=(S+I+D)/N_ref"),
    ("TX-03", "strict_attribution=correct/eligible"),
    ("TX-04", "critical_term_recall=passed_spans/required_spans"),
    ("TX-05", "critical_turn_recall=passed_turns/required_turns"),
    ("TX-06", "false_insertion_rate=I/N_ref"),
    ("TX-07", "omission_rate=D/N_ref"),
    ("TX-08", "duplicate_word_rate=surplus_words/N_hyp"),
    ("TX-09", "turn_coherence=coherent/eligible"),
    ("TX-10", "All timed words/rows requiring those checks"),
    ("TX-11", "Completed eligible events in that family"),
    ("TX-12", "All resource samples captured at the frozen cadence"),
    ("TX-13", "correction_completion=completed/attempted"),
    ("TX-14", "live_fallback_rate=fallbacks/attempted"),
    ("TX-15", "runtime_failure_rate=failed/attempted"),
)

NOTE_METRIC_FORMULAS = (
    ("NT-01", "critical_unsupported_count"),
    ("NT-02", "unsupported_claim_rate=unsupported/N_claims"),
    ("NT-03", "miscitation_rate=miscited/N_supported_claims"),
    ("NT-04", "response_state_error_rate=errors/N_response_states"),
    ("NT-05", "actor_action_error_rate=errors/N_actor_actions"),
    ("NT-06", "certainty_error_rate=errors/N_certainty_claims"),
    ("NT-07", "unsupported_named_term_rate=errors/N_named_terms"),
    ("NT-08", "numeric_provenance_error_rate=errors/N_numeric_claims"),
    ("NT-09", "unsupported_longitudinal_rate=errors/N_longitudinal_claims"),
    ("NT-10", "faithful_screen_coverage=passed/N_expected"),
    ("NT-11", "review_precision=TP/(TP+FP)"),
    ("NT-12", "review_recall=TP/(TP+FN)"),
    ("NT-13", "source_identity_pass_rate=matching/attempted"),
    ("NT-14", "signable/reviewed"),
    ("NT-15", "zero-request approved branch is `not-run`"),
    ("NT-16", "abstention_rate=abstained/eligible"),
)

CONSULT_53_OUTCOME_GUARDS = (
    ("C53-01", "never reconstruct CBT"),
    ("C53-02", "without two selected-source endpoints"),
    ("C53-03", "Preserve the conflict and condition"),
    ("C53-04", "never emit a clean denial"),
    ("C53-05", "current transcript lane fails while faithful SOAP abstention passes"),
    ("C53-06", "Include the supported denial"),
    ("C53-07", "never say ordered, booked, completed, confirmed, or already arranged"),
)


def quality_contract_section(section_heading: str, next_heading: str) -> str:
    """Return one contract section for a focused reviewer gate.
    Use it when a developer needs to verify only the rule behind a user-visible result.
    """
    section_marker = f"### {section_heading}"
    next_section_marker = f"### {next_heading}"
    _, matched_section_marker, text_after_section_marker = (
        QUALITY_CONTRACT_TEXT.partition(section_marker)
    )
    assert matched_section_marker == section_marker
    contract_section_text, matched_next_marker, _ = text_after_section_marker.partition(
        next_section_marker
    )
    assert matched_next_marker == next_section_marker
    return contract_section_text


def test_scorecards_cannot_compensate_for_each_other() -> None:
    """Keep a safer transcript from hiding an unsafe draft when users review a candidate."""
    independent_scorecards = quality_contract_section(
        "Independent scorecards and criticality", "Campaign-level decisions"
    )

    assert "L0-INTEGRITY" in independent_scorecards
    assert "L1-CLINICAL" in independent_scorecards
    assert "transcript_pass =" in independent_scorecards
    assert "note_pass =" in independent_scorecards
    assert (
        "candidate_eligible = transcript_pass AND note_pass" in independent_scorecards
    )
    assert "favourable average cannot dilute it" in independent_scorecards


@pytest.mark.parametrize(
    ("metric_id", "required_formula"),
    TRANSCRIPT_METRIC_FORMULAS,
)
def test_transcript_metrics_pin_arithmetic(
    metric_id: str, required_formula: str
) -> None:
    """Keep every transcript metric reproducible before a clinician sees improvement claims."""
    transcript_arithmetic = quality_contract_section(
        "Transcript metric arithmetic", "SOAP-note scorecard decisions"
    )

    assert f"`{metric_id}`" in transcript_arithmetic
    assert required_formula in transcript_arithmetic


@pytest.mark.parametrize(
    ("metric_id", "required_formula"),
    NOTE_METRIC_FORMULAS,
)
def test_note_metrics_pin_arithmetic(metric_id: str, required_formula: str) -> None:
    """Keep every note metric source-grounded before a clinician edits the SOAP draft."""
    note_arithmetic = quality_contract_section(
        "SOAP-note metric arithmetic", "Consult-5.3 executable outcome matrix"
    )

    assert f"`{metric_id}`" in note_arithmetic
    assert required_formula in note_arithmetic


@pytest.mark.parametrize(
    ("expectation_id", "required_guard"),
    CONSULT_53_OUTCOME_GUARDS,
)
def test_consult_53_outcomes_keep_their_clinical_guard(
    expectation_id: str, required_guard: str
) -> None:
    """Keep each anxiety-consult truth rule independent when a reviewer checks the draft."""
    consult_outcomes = quality_contract_section(
        "Consult-5.3 executable outcome matrix",
        "Clinician edit-burden rubric decisions",
    )

    assert f"`{expectation_id}`" in consult_outcomes
    assert required_guard in consult_outcomes


def test_promotion_arithmetic_keeps_failures_and_zero_call_branches() -> None:
    """Keep failed runs and approved no-call paths visible before a user trusts promotion."""
    promotion_arithmetic = quality_contract_section(
        "Promotion and rollback arithmetic", "Universal rollback rules"
    )

    assert "F = 10" in promotion_arithmetic
    assert "R_ASR = HUMAN-PENDING" in promotion_arithmetic
    assert "G_NOTE = HUMAN-PENDING" in promotion_arithmetic
    assert "0 requests / 0 generations" in promotion_arithmetic
    assert "Missing, failed, fallback" in promotion_arithmetic
    assert "no-candidate" in promotion_arithmetic
    assert "not-triggered" in promotion_arithmetic
    assert "not-run" in promotion_arithmetic
    assert "invalid" in promotion_arithmetic
    assert "candidate_eligible AND primary_absolute_pass" in promotion_arithmetic
