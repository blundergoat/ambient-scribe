"""
Tests for the deterministic note fidelity checks (0.4.0 M07).

Every specimen sentence below is taken verbatim from the M00 replay campaign
notes (var/quality/m00-fidelity-replays/), so the checker is pinned against
the exact fabrications the independent verifier confirmed and the exact honest
phrasings that must stay unflagged.
"""

from __future__ import annotations

from api.summary_fidelity import FidelityViolation, find_fidelity_violations

# Minimal c03 visit shape: the sudden-vs-gradual exchange the patient could not
# answer, the verbal neuro screen with its bare "No.", and the visit ending on
# the doctor's unanswered breathing line.
C03_ROWS = [
    {"role": "DOCTOR", "text": "Did a pain come on quite suddenly or was it more of a gradual buildup?"},
    {"role": "PATIENT", "text": "I don't know really. It just happened."},
    {"role": "DOCTOR", "text": "Any problems with your speech, any weakness or numbness in your arms and legs, any balance or coordination problems?"},
    {"role": "PATIENT", "text": "No."},
    {"role": "DOCTOR", "text": "Have you had any injuries to your head, a fall recently or been knocked on the head?"},
    {"role": "DOCTOR", "text": "Okay, all right. So for example, your breathing is okay. You're not feeling breathless."},
]


def _violation_rules(sections: list[dict], key_points: list[str] | None = None) -> list[str]:
    """Run the checker and return just the rule ids, in reading order."""
    found = find_fidelity_violations(sections, key_points or [], C03_ROWS)
    return [violation.rule for violation in found]


def test_resolving_uncertain_onset_to_sudden_is_flagged() -> None:
    """'Onset was sudden' misleads when the patient answered 'I don't know'."""
    rules = _violation_rules(
        [{"heading": "Subjective", "content": "The pain onset was sudden, and the patient is uncertain whether it developed gradually."}]
    )
    assert rules == ["uncertainty-resolved"]


def test_described_as_sudden_is_flagged_as_invented_attribution() -> None:
    """Nobody 'described' the onset as sudden; the doctor only asked about it."""
    rules = _violation_rules(
        [{"heading": "Subjective", "content": "Onset timing described as sudden, though patient expressed uncertainty."}]
    )
    assert rules == ["uncertainty-resolved"]


def test_onset_documented_as_unclear_stays_clean() -> None:
    """The honest form the clinician should see passes untouched."""
    rules = _violation_rules(
        [{"heading": "Subjective", "content": "Onset is unclear; patient states 'I don't know really' when asked whether it came on suddenly or gradually."}]
    )
    assert rules == []


def test_verbatim_patient_quote_counts_as_preserving_uncertainty() -> None:
    """Quoting the patient's own answer is the as-stated form, not a resolution."""
    rules = _violation_rules(
        [{"heading": "Subjective", "content": "Onset of pain timing described as 'it just happened' when asked about gradual versus sudden onset."}]
    )
    assert rules == []


def test_fabricated_breathing_denial_is_flagged() -> None:
    """The visit ends on the doctor's unanswered breathing line; no denial exists."""
    rules = _violation_rules(
        [{"heading": "Objective", "content": "Patient reports normal breathing and denies dyspnea."}]
    )
    assert "negative-without-denial" in rules


def test_denial_list_with_unanswered_head_injury_question_is_flagged() -> None:
    """The bare 'No.' answered the neuro screen, not the later head-injury question."""
    rules = _violation_rules(
        [{"heading": "Subjective", "content": "Denies recent head injury, falls, or difficulty breathing."}]
    )
    assert rules == ["negative-without-denial"]


def test_denied_all_with_unanswered_topic_before_the_verb_is_flagged() -> None:
    """gen1 escape: 'denied all' hides the topics before the verb - head injury was never answered."""
    rules = _violation_rules(
        [{"heading": "Subjective", "content": "When screened for neurological symptoms including speech difficulties, weakness, numbness, balance problems, or head injury, the patient denied all."}]
    )
    assert rules == ["negative-without-denial"]


def test_screening_negative_key_point_with_unanswered_item_is_flagged() -> None:
    """gen1 escape: 'screening (...) negative' claims every listed item was denied."""
    rules = _violation_rules(
        [],
        key_points=["Neurological screening (speech, strength, sensory, balance, head injury) negative"],
    )
    assert "negative-without-denial" in rules


def test_denials_the_patient_actually_gave_stay_clean() -> None:
    """The neuro-screen batch was really denied by the patient's 'No.'."""
    rules = _violation_rules(
        [{"heading": "Subjective", "content": "Denies speech difficulties, weakness, or numbness in arms and legs."}]
    )
    assert rules == []


def test_screening_answers_dressed_as_exam_findings_are_flagged() -> None:
    """Verbal screening is history; 'examination findings ... intact' claims an exam."""
    rules = _violation_rules(
        [],
        key_points=["Neurological examination findings negative (speech, motor, sensory, balance intact)"],
    )
    assert rules == ["exam-not-performed"]


def test_exam_initiated_claim_without_exam_is_flagged() -> None:
    """The doctor only promised an exam ('let me examine you'); none happened."""
    rules = _violation_rules(
        [{"heading": "Objective", "content": "Physical examination has been initiated but is incomplete in this transcript."}]
    )
    assert rules == ["exam-not-performed"]


def test_honest_no_examination_documented_stays_clean() -> None:
    """Saying no exam happened is exactly the behavior the rules want."""
    rules = _violation_rules(
        [{"heading": "Objective", "content": "No physical examination findings are documented in this transcript. Vital signs were not recorded."}]
    )
    assert rules == []


def test_sentence_split_inside_a_quotation_never_creates_a_fragment_flag() -> None:
    """gen5 false positive: a period INSIDE the patient's quote must not split the sentence."""
    rules = _violation_rules(
        [{"heading": "Subjective", "content": 'Patient stated "I don\'t know really. It just happened" when asked if it came on suddenly or gradually.'}]
    )
    assert rules == []


def test_empty_transcript_never_raises_violations() -> None:
    """With no rows there is no evidence base; the short-transcript rule owns it."""
    found = find_fidelity_violations(
        [{"heading": "Objective", "content": "Patient denies dyspnea."}], [], []
    )
    assert found == []


def test_regeneration_and_flagging_pipeline(monkeypatch) -> None:
    """A fabricated denial earns one redo; when the redo repeats it, the note ships flagged."""
    from api import summary_generation as generation_module
    from api.summary_generation import SessionSummaryOutput, SummarySectionOutput

    fabricated = SessionSummaryOutput(
        title="Visit note",
        sections=[SummarySectionOutput(heading="Objective", content="Patient denies dyspnea.")],
        key_points=[],
    )
    seen_prompts: list[str] = []

    class _StubbedResult:
        structured_output = fabricated

    class _StubbedAgent:
        def __call__(self, prompt, structured_output_model=None):
            seen_prompts.append(prompt)
            return _StubbedResult()

    monkeypatch.setattr("agents.create_summary_agent", lambda: _StubbedAgent())

    payload = generation_module.run_summary_generation(
        "00000000-0000-4000-8000-000000000777",
        "DOCTOR: your breathing is okay.",
        citation_segments=[],
        transcript_segments=C03_ROWS,
    )

    # The model was asked twice: the redo prompt names the rejected sentence.
    assert len(seen_prompts) == 2
    assert "Patient denies dyspnea." in seen_prompts[1]
    assert "REJECTED" in seen_prompts[1]

    # The surviving sentence ships visibly flagged, never silently stripped.
    assert payload is not None
    assert payload["sections"][0]["content"] == "Patient denies dyspnea."
    assert payload["sections"][0]["unverified"] == ["Patient denies dyspnea."]


def test_clean_note_ships_without_flag_keys(monkeypatch) -> None:
    """A fidelity-clean note renders byte-identically to the pre-M07 payload shape."""
    from api import summary_generation as generation_module
    from api.summary_generation import SessionSummaryOutput, SummarySectionOutput

    honest = SessionSummaryOutput(
        title="Visit note",
        sections=[
            SummarySectionOutput(
                heading="Objective",
                content="No physical examination findings are documented in this transcript.",
            )
        ],
        key_points=[],
    )
    calls: list[str] = []

    class _StubbedResult:
        structured_output = honest

    class _StubbedAgent:
        def __call__(self, prompt, structured_output_model=None):
            calls.append(prompt)
            return _StubbedResult()

    monkeypatch.setattr("agents.create_summary_agent", lambda: _StubbedAgent())

    payload = generation_module.run_summary_generation(
        "00000000-0000-4000-8000-000000000778",
        "DOCTOR: hello.",
        citation_segments=[],
        transcript_segments=C03_ROWS,
    )

    # One generation, no retry, and no unverified keys anywhere in the payload.
    assert len(calls) == 1
    assert payload is not None
    assert "unverified" not in payload["sections"][0]
    assert "unverified_key_points" not in payload


def test_violation_carries_location_and_reason_for_the_flag() -> None:
    """The browser flag and the regeneration prompt both need the exact sentence."""
    found = find_fidelity_violations(
        [{"heading": "Objective", "content": "Patient denies dyspnea."}], [], C03_ROWS
    )
    assert len(found) == 1
    violation = found[0]
    assert isinstance(violation, FidelityViolation)
    assert violation.location == "section 'Objective'"
    assert violation.sentence == "Patient denies dyspnea."
    assert "no patient denial" in violation.reason
