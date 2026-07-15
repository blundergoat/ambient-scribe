"""
Regression coverage for note sentences backed by uncertain corrected wording.

Tests mirror the browser payload the clinician receives after summary generation,
including the retained day5 calf/carp substitution. They prove markers are
deterministic, citation-bound, strict-threshold, and prose-preserving.
"""

from copy import deepcopy
from unittest.mock import patch

from api.summary_confidence import add_low_confidence_note_flags
from api.summary_generation import (
    SessionSummaryOutput,
    SummaryCitationOutput,
    SummarySectionOutput,
    run_summary_generation,
)


def _note_section(content: str, citation_ids: list[str]) -> dict:
    """Build one browser-visible section; empty citations model an uncited note."""
    return {
        "heading": "Subjective",
        "content": content,
        "citations": [{"segment_id": segment_id} for segment_id in citation_ids],
    }


def _corrected_row(
    segment_id: str,
    text: str,
    confidence: float | None,
) -> dict:
    """Build stored corrected wording; null confidence means the row was unmeasured."""
    corrected_row = {"segment_id": segment_id, "text": text}
    # Unmeasured rows preserve the production absent-is-absent contract.
    if confidence is not None:
        corrected_row["confidence"] = confidence
    return corrected_row


def test_day5_carp_sentence_is_flagged_without_rewriting_note_text() -> None:
    """The retained garble sentence becomes reviewable while prose stays byte-identical."""
    day5_sentence = "Patient reports a rash on the back of his carp, noticed at the end of February."
    plan_sentence = "Blood tests were arranged for the following week."
    note_payload = {
        "sections": [
            _note_section(
                f"{day5_sentence} {plan_sentence}",
                ["corrected-0093", "corrected-0094"],
            )
        ]
    }
    original_payload = deepcopy(note_payload)
    corrected_rows = [
        _corrected_row(
            "corrected-0093",
            "Uh I think sort of carp, back of my",
            0.77,
        ),
        _corrected_row(
            "corrected-0094",
            "car, because I didn't notice it,",
            0.7616,
        ),
    ]

    flagged_note = add_low_confidence_note_flags(note_payload, corrected_rows)

    assert (
        flagged_note["sections"][0]["content"]
        == original_payload["sections"][0]["content"]
    )
    assert flagged_note["sections"][0]["low_confidence"] == [day5_sentence]
    assert note_payload == original_payload


def test_threshold_equality_absence_and_uncited_sections_stay_plain() -> None:
    """No marker is invented for exact-boundary, unmeasured, or uncited wording."""
    sentence = "Patient reports a rash on the lower leg."
    rows = [
        _corrected_row("corrected-boundary", "rash on the lower leg", 0.78),
        _corrected_row("corrected-unmeasured", "rash on the lower leg", None),
    ]

    boundary_note = {"sections": [_note_section(sentence, ["corrected-boundary"])]}
    unmeasured_note = {"sections": [_note_section(sentence, ["corrected-unmeasured"])]}
    uncited_note = {"sections": [_note_section(sentence, [])]}

    assert (
        "low_confidence"
        not in add_low_confidence_note_flags(boundary_note, rows)["sections"][0]
    )
    assert (
        "low_confidence"
        not in add_low_confidence_note_flags(unmeasured_note, rows)["sections"][0]
    )
    assert (
        "low_confidence"
        not in add_low_confidence_note_flags(uncited_note, rows)["sections"][0]
    )


def test_low_high_tie_does_not_dominate_sentence_evidence() -> None:
    """One low and one high relevant row keep the sentence unmarked."""
    sentence = "Patient reports a red rash on the lower leg."
    rows = [
        _corrected_row("corrected-low", "red rash on lower leg", 0.7),
        _corrected_row("corrected-high", "red rash on lower leg", 0.9),
    ]
    note_payload = {
        "sections": [_note_section(sentence, ["corrected-low", "corrected-high"])]
    }

    flagged_note = add_low_confidence_note_flags(note_payload, rows)

    assert "low_confidence" not in flagged_note["sections"][0]


def test_generation_junction_adds_flag_after_validation() -> None:
    """The real generation helper publishes the additive browser field."""
    sentence = "Patient reports a rash on the back of his carp."
    structured_note = SessionSummaryOutput(
        title="Skin review",
        sections=[
            SummarySectionOutput(
                heading="Subjective",
                content=sentence,
                citations=[SummaryCitationOutput(segment_id="corrected-0093")],
            )
        ],
        key_points=[],
    )
    corrected_rows = [
        {
            "segment_id": "corrected-0093",
            "speaker_id": "speaker_0",
            "role": "PATIENT",
            "text": "rash on the back of my carp",
            "start": 10.0,
            "end": 12.0,
            "confidence": 0.77,
        }
    ]

    with (
        patch("api.summary_generation.retrieve_clinical_context", return_value=[]),
        patch(
            "api.summary_generation._generate_validated_draft",
            return_value=(structured_note, {}),
        ),
        patch("api.summary_generation.find_fidelity_violations", return_value=[]),
    ):
        generated_note = run_summary_generation(
            "day5-test-session",
            "[PATIENT] rash on the back of my carp",
            citation_segments=corrected_rows,
            transcript_segments=corrected_rows,
        )

    assert generated_note is not None
    assert generated_note["sections"][0]["low_confidence"] == [sentence]
    assert generated_note["sections"][0]["content"] == sentence


# The four consult-1.2 medication rows exactly as the corrected lane stored
# them (m02-acceptance replay, session 7fce47c4): known misspellings below the
# 0.78 corrected-lane threshold, plus the correctly spelled emollients row
# ABOVE threshold that must never be flagged.
CONSULT12_MEDICATION_ROWS = [
    _corrected_row("corrected-0268", "like Luratidine or", 0.656),
    _corrected_row(
        "corrected-0269", "Pyritin, which can help with the itchiness", 0.7367
    ),
    _corrected_row(
        "corrected-0293", "Um something like Fexaphenidine, which I", 0.6478
    ),
    _corrected_row(
        "corrected-0297",
        "I certainly think using the steroids and the emolons um on a regular",
        0.7696,
    ),
    _corrected_row(
        "corrected-0259",
        "as I'm gonna give you something some emollients, which helps to moisturize",
        0.7956,
    ),
]


def test_consult12_plan_medication_sentences_reach_the_review_lane() -> None:
    """A misspelled low-confidence medication cannot be an unmarked definite Plan item.

    The Plan sentence shares only ONE meaningful word with its source row - the
    drug name itself - so the generic two-word overlap rule never linked it and
    consult 1.2's note printed "Fexaphenidine" as a confident prescription.
    """
    plan_section = {
        "heading": "Plan",
        "content": (
            "Trial of stronger antihistamine Fexaphenidine recommended. "
            "Antihistamines such as Luratidine may help with itching. "
            "Pyritin suggested for night-time itch relief. "
            "Regular use of steroids and emolons advised."
        ),
        "citations": [
            {"segment_id": "corrected-0268"},
            {"segment_id": "corrected-0269"},
            {"segment_id": "corrected-0293"},
            {"segment_id": "corrected-0297"},
        ],
    }

    reviewed_note = add_low_confidence_note_flags(
        {"sections": [plan_section]}, CONSULT12_MEDICATION_ROWS
    )

    flagged_sentences = reviewed_note["sections"][0].get("low_confidence", [])
    # Every one of the four observed misspellings must carry the review cue.
    assert any("Fexaphenidine" in sentence for sentence in flagged_sentences)
    assert any("Luratidine" in sentence for sentence in flagged_sentences)
    assert any("Pyritin" in sentence for sentence in flagged_sentences)
    assert any("emolons" in sentence for sentence in flagged_sentences)


def test_consult12_canonical_term_above_threshold_stays_unflagged() -> None:
    """A correctly spelled term backed by an above-threshold row keeps plain prose."""
    plan_section = {
        "heading": "Plan",
        "content": "Emollients recommended to moisturize the affected skin.",
        "citations": [{"segment_id": "corrected-0259"}],
    }

    reviewed_note = add_low_confidence_note_flags(
        {"sections": [plan_section]}, CONSULT12_MEDICATION_ROWS
    )

    # 0.7956 is above the 0.78 lane threshold: no warning wall over good rows.
    assert "low_confidence" not in reviewed_note["sections"][0]


def test_multiword_variant_fragments_do_not_become_link_tokens() -> None:
    """Ordinary words inside multiword variants (e.g. "metro pro lol") never link.

    A sentence about the metro line must not inherit a drug row's low
    confidence just because the lexicon knows "metro pro lol" as a variant.
    """
    section = {
        "heading": "Subjective",
        "content": "Patient commutes by metro and reports no chest pain.",
        "citations": [{"segment_id": "row-metro"}],
    }
    rows = [_corrected_row("row-metro", "started metro pro lol last month", 0.5)]

    reviewed_note = add_low_confidence_note_flags({"sections": [section]}, rows)

    assert "low_confidence" not in reviewed_note["sections"][0]


def test_review_reasons_are_machine_readable_for_downstream_milestones() -> None:
    """Flagged non-canonical terms expose `source_low_confidence` internally.

    M05/M06 consume this reason lane; the browser payload itself is unchanged.
    """
    from api.summary_confidence import low_confidence_review_reasons

    plan_section = {
        "heading": "Plan",
        "content": "Trial of stronger antihistamine Fexaphenidine recommended.",
        "citations": [{"segment_id": "corrected-0293"}],
    }

    review_reasons = low_confidence_review_reasons(
        {"sections": [plan_section]}, CONSULT12_MEDICATION_ROWS
    )

    assert review_reasons, "the misspelled Plan medication must produce a reason"
    reason_row = review_reasons[0]
    assert reason_row["reason"] == "source_low_confidence"
    assert reason_row["section"] == "Plan"
    assert "Fexaphenidine" in reason_row["sentence"]
    assert "fexaphenidine" in reason_row["terms"]
    assert reason_row["segment_ids"] == ["corrected-0293"]
