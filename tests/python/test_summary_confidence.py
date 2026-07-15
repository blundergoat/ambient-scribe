"""
Regression coverage for note sentences backed by uncertain corrected wording.

Tests mirror the browser payload the clinician receives after summary generation,
including the retained day5 calf/carp substitution. They prove markers are
deterministic, citation-bound, strict-threshold, and prose-preserving.
"""

from copy import deepcopy
import json
from pathlib import Path
from unittest.mock import patch

from api.summary_confidence import (
    SOURCE_LOW_CONFIDENCE_REASON,
    add_low_confidence_note_flags,
    low_confidence_review_reasons,
)
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


# --- M05 family 4: manifest specimens executed as frozen classifications ---

_DETECTOR_SPECIMEN_MANIFEST_PATH = (
    Path(__file__).resolve().parents[1]
    / "fixtures"
    / "scribe"
    / "note-review-detector-specimens.json"
)

# a17/a18 are the family's pre-declared reason-lane deferral: their correct
# source rows hold garbled ORDINARY words (carp/back/car), never a lexicon
# variant, so reaching them needs fuzzy matching the family contract forbids.
# The deferral is frozen in M05-detector-family-specs.md; the visible-marker
# true positives they correspond to (hc12/hc13) are guarded by the baseline
# re-score, not by this lane.
_TERM_CONFIDENCE_DEFERRED_IDS = {"a17", "a18"}


def _term_confidence_claim_specimens() -> list[dict]:
    """Load the frozen term-confidence claim specimens this family ships against.

    Returns:
        Manifest specimens for the lane, minus the pre-declared deferrals;
        never empty while the manifest holds the c25-c28 controls.
    """
    manifest = json.loads(_DETECTOR_SPECIMEN_MANIFEST_PATH.read_text(encoding="utf-8"))
    return [
        specimen
        for specimen in manifest["specimens"]
        if specimen["lane"] == "term_confidence"
        and specimen["kind"] == "claim"
        and specimen["id"] not in _TERM_CONFIDENCE_DEFERRED_IDS
    ]


def _specimen_note_and_rows(specimen: dict) -> tuple[dict, list[dict]]:
    """Build the detector inputs one manifest specimen describes.

    The section cites exactly the specimen's embedded rows, so the test
    exercises linkage, threshold, and variant gating end to end.

    Args:
        specimen: Manifest entry with claim text and embedded source rows.

    Returns:
        (note payload, citation rows) ready for the reason lane.
    """
    section = {
        "heading": "Plan",
        "content": specimen["claim"],
        "citations": [
            {"segment_id": row["segment_id"]} for row in specimen["source_rows"]
        ],
    }
    return {"sections": [section]}, specimen["source_rows"]


def test_manifest_term_confidence_specimens_classify_as_frozen() -> None:
    """Every shipped term-confidence specimen keeps its frozen flag/no-flag label.

    Positives must emit `source_low_confidence`; hard negatives (canonical
    spelling above threshold, the M04-REJECTED steroid-cream mapping, and
    ordinary low-confidence wording) must stay silent in the reason lane.
    """
    specimens = _term_confidence_claim_specimens()
    assert specimens, "manifest must supply the c25-c28 controls"

    for specimen in specimens:
        note_payload, citation_rows = _specimen_note_and_rows(specimen)
        review_reasons = low_confidence_review_reasons(note_payload, citation_rows)

        if specimen["expected"] == "flag":
            assert review_reasons, f"{specimen['id']} must produce a reason"
            assert all(
                reason["reason"] == SOURCE_LOW_CONFIDENCE_REASON
                for reason in review_reasons
            ), specimen["id"]
        else:
            assert review_reasons == [], f"{specimen['id']} must stay reason-silent"


def test_visible_marker_without_clinical_term_emits_no_reason() -> None:
    """c28: the threshold marker may fire while the reason lane stays silent.

    'She lives with her parents.' links to the 0.7728 row, so the clinician
    sees the wording cue - but with no lexicon variant in the sentence there
    is nothing for the machine-readable clinical-term lane to say.
    """
    manifest = json.loads(_DETECTOR_SPECIMEN_MANIFEST_PATH.read_text(encoding="utf-8"))
    c28 = next(s for s in manifest["specimens"] if s["id"] == "c28")
    note_payload, citation_rows = _specimen_note_and_rows(c28)

    reviewed_note = add_low_confidence_note_flags(note_payload, citation_rows)
    review_reasons = low_confidence_review_reasons(note_payload, citation_rows)

    # The visible lane marks the sentence for wording review...
    assert reviewed_note["sections"][0].get("low_confidence") == [c28["claim"]]
    # ...while the clinical-term reason lane has nothing to name.
    assert review_reasons == []


def test_note_review_reasons_aggregates_term_and_temporal_families() -> None:
    """The M05 aggregation point returns both families' reasons for one note."""
    from api.summary_confidence import note_review_reasons

    note_payload = {
        "sections": [
            {
                "heading": "Plan",
                "content": (
                    "Trial of stronger antihistamine Fexaphenidine recommended. "
                    "Blood tests ordered to exclude other causes."
                ),
                "citations": [{"segment_id": "corrected-0293"}],
            }
        ],
        "key_points": [],
    }
    citation_rows = CONSULT12_MEDICATION_ROWS + [
        _corrected_row(
            "corrected-4001", "it's probably worth having a couple of blood tests", 0.9
        )
    ]

    review_reasons = note_review_reasons(note_payload, citation_rows)

    reason_codes = {reason["reason"] for reason in review_reasons}
    assert "source_low_confidence" in reason_codes
    assert "action_not_confirmed_done" in reason_codes


def test_coverage_misses_feed_retry_but_never_the_payload() -> None:
    """A surviving coverage miss stays in the reason lane, not the note payload.

    The synthetic critical-coverage violation drives the bounded retry, but
    its sentence matches no note text, so `unverified`/`unverified_key_points`
    never carry it - the browser contract is unchanged (M05).
    """
    structured_note = SessionSummaryOutput(
        title="Emergency review",
        sections=[
            SummarySectionOutput(
                heading="Plan",
                content="Emergency ambulance requested. Antihistamines in the interim.",
                citations=[SummaryCitationOutput(segment_id="d-01")],
            )
        ],
        key_points=[],
    )
    emergency_rows = [
        {
            "segment_id": "d-01",
            "speaker_id": "speaker_0",
            "role": "DOCTOR",
            "text": "I'll call the ambulance.",
            "start": 1.0,
            "end": 2.0,
            "confidence": 0.9,
        },
        {
            "segment_id": "d-02",
            "speaker_id": "speaker_0",
            "role": "DOCTOR",
            "text": "mom, if you can call the 999.",
            "start": 3.0,
            "end": 4.0,
            "confidence": 0.9,
        },
    ]

    with (
        patch("api.summary_generation.retrieve_clinical_context", return_value=[]),
        patch(
            "api.summary_generation._generate_validated_draft",
            return_value=(structured_note, {}),
        ),
    ):
        generated_note = run_summary_generation(
            "coverage-test-session",
            "[DOCTOR] I'll call the ambulance.",
            citation_segments=emergency_rows,
            transcript_segments=emergency_rows,
        )

    assert generated_note is not None
    # The 999 omission survived both drafts; the payload must stay clean.
    note_text = json.dumps(
        {key: value for key, value in generated_note.items() if key != "_agent_metrics"}
    )
    assert "critical-coverage" not in note_text
    assert "emergency_number" not in note_text
    assert "unverified" not in generated_note["sections"][0]
    assert generated_note.get("unverified_key_points", []) == []
    # The reason lane still reports the miss for M03/M06 surfacing.
    from api.summary_confidence import note_review_reasons

    survivor_reasons = note_review_reasons(generated_note, emergency_rows)
    assert any(
        reason["reason"] == "emergency_disposition_incomplete"
        for reason in survivor_reasons
    )
