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


def _stub_agent_returning(monkeypatch, drafts: list) -> list[str]:
    """Stub the summary agent to return each draft in turn; returns the prompt log.

    Args:
        monkeypatch: pytest fixture.
        drafts: One SessionSummaryOutput per expected generation call; a test
            fails loudly if the pipeline asks for more drafts than provided.

    Returns:
        Live list that accumulates every prompt the pipeline sends.
    """
    seen_prompts: list[str] = []

    class _StubbedAgent:
        def __call__(self, prompt, structured_output_model=None):
            seen_prompts.append(prompt)

            class _Result:
                structured_output = drafts[len(seen_prompts) - 1]

            return _Result()

    import agents as agents_module  # noqa: F401 - imported for monkeypatch target

    monkeypatch.setattr("agents.create_summary_agent", lambda: _StubbedAgent())
    return seen_prompts


def test_worse_regeneration_never_replaces_a_better_first_draft(monkeypatch, caplog) -> None:
    """Field shape: attempt 0 has one violation, attempt 1 has three - attempt 0 ships."""
    import logging

    from api import summary_generation as generation_module
    from api.summary_generation import SessionSummaryOutput, SummarySectionOutput

    one_violation = SessionSummaryOutput(
        title="Visit note",
        sections=[SummarySectionOutput(heading="Objective", content="Patient denies dyspnea.")],
        key_points=[],
    )
    three_violations = SessionSummaryOutput(
        title="Visit note",
        sections=[
            SummarySectionOutput(
                heading="Objective",
                content=(
                    "Patient denies dyspnea. Denies recent head injury. "
                    "Neurological status normal on examination."
                ),
            )
        ],
        key_points=[],
    )
    _stub_agent_returning(monkeypatch, [one_violation, three_violations])

    with caplog.at_level(logging.INFO, logger="api.summary_generation"):
        payload = generation_module.run_summary_generation(
            "00000000-0000-4000-8000-000000000779",
            "DOCTOR: your breathing is okay.",
            citation_segments=[],
            transcript_segments=C03_ROWS,
        )

    # The one-violation first draft ships, with exactly its one visible flag.
    assert payload is not None
    assert payload["sections"][0]["content"] == "Patient denies dyspnea."
    assert payload["sections"][0]["unverified"] == ["Patient denies dyspnea."]

    # Selection is auditable: which attempt shipped and both violation counts.
    selection_records = [
        record for record in caplog.records if getattr(record, "selected_attempt", None) is not None
    ]
    assert selection_records, "expected a summary.fidelity_draft_selected log record"
    selected = selection_records[-1]
    assert selected.selected_attempt == 0
    assert selected.attempt_0_count == 1
    assert selected.attempt_1_count == 3


def test_clean_retry_still_ships_and_no_third_generation_runs(monkeypatch) -> None:
    """Normal improvement shape: attempt 0 violates, attempt 1 is clean - attempt 1 ships."""
    from api import summary_generation as generation_module
    from api.summary_generation import SessionSummaryOutput, SummarySectionOutput

    fabricated = SessionSummaryOutput(
        title="Visit note",
        sections=[SummarySectionOutput(heading="Objective", content="Patient denies dyspnea.")],
        key_points=[],
    )
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
    seen_prompts = _stub_agent_returning(monkeypatch, [fabricated, honest])

    payload = generation_module.run_summary_generation(
        "00000000-0000-4000-8000-000000000780",
        "DOCTOR: your breathing is okay.",
        citation_segments=[],
        transcript_segments=C03_ROWS,
    )

    assert len(seen_prompts) == 2
    assert payload is not None
    assert payload["sections"][0]["content"].startswith("No physical examination findings")
    assert "unverified" not in payload["sections"][0]


def test_fidelity_logs_never_contain_clinical_sentences(monkeypatch, caplog) -> None:
    """Violation diagnostics identify rule/location/ordinal but never note prose."""
    import logging

    from api import summary_generation as generation_module
    from api.summary_generation import SessionSummaryOutput, SummarySectionOutput

    fabricated_sentence = "Patient denies dyspnea."
    fabricated = SessionSummaryOutput(
        title="Visit note",
        sections=[SummarySectionOutput(heading="Objective", content=fabricated_sentence)],
        key_points=[],
    )
    _stub_agent_returning(monkeypatch, [fabricated, fabricated])

    with caplog.at_level(logging.DEBUG, logger="api.summary_generation"):
        generation_module.run_summary_generation(
            "00000000-0000-4000-8000-000000000781",
            "DOCTOR: your breathing is okay.",
            citation_segments=[],
            transcript_segments=C03_ROWS,
        )

    violation_records = [
        record for record in caplog.records if getattr(record, "violations", None)
    ]
    assert violation_records, "expected structured summary.fidelity_violations records"
    for record in violation_records:
        for entry in record.violations:
            # Useful, non-clinical fields are present...
            assert entry["rule"] == "negative-without-denial"
            assert entry["location"] == "section 'Objective'"
            assert entry["subtype"]
            assert entry["sentence_ordinal"] >= 0
            assert entry["word_count"] > 0
            # ...and no clinical prose leaks in any shape.
            assert "sentence" not in entry
            assert "topic" not in entry
    # The exact note sentence appears nowhere in any captured log line or field.
    for record in caplog.records:
        assert fabricated_sentence not in record.getMessage()
        assert fabricated_sentence not in str(getattr(record, "violations", ""))


# --- M10 field specimens (sessions 203d1d35 and d97a9dbe, 2026-07-08 manual round) ---

# The day3 chief-complaint monologue that laundered any lip-related denial: it
# mentions lips and contains epistemic "don't know"/"don't think" phrases, but
# never denies anything.
DAY3_MONOLOGUE = (
    "yeah, that is, well, i don't think it wasn't a sandwich, but it was something "
    "with chron. so basically i sometimes go with my friends to this place called "
    "fat fuck. we regularly have like usually i have like a normal vegetarian soup "
    "or something and yeah and then i wanted to try something new so what happened "
    "was i ordered a prawn soup it's called a luxic soup i ordered a prawn one this "
    "time and yeah and then my feels like my lips start feeling a little way just "
    "in the corner so on the left first and then i don't know and then we went back "
    "we had a little bit of a chat event now i feel that that there is a swelling "
    "there is like a swelling on my on my upper lip and it's kind of getting bigger "
    "i don't know what to do"
)

# The visit ends on the doctor's lip-swelling-history question; no answer exists.
DAY3_ROWS = [
    {"role": "PATIENT", "text": "i feel a little weird today to be honest."},
    {"role": "PATIENT", "text": DAY3_MONOLOGUE},
    {"role": "DOCTOR", "text": "all right. so when did you have the prawn soup? what time"},
    {"role": "PATIENT", "text": "well i wasn't really paying attention so it must have been like an half an hour maybe"},
    {"role": "PATIENT", "text": "i don't know if it's the chest just generally feels a little difficult but yeah it might be i'm not sure"},
    {"role": "DOCTOR", "text": "have you ever had these any kind of lip swelling in the past without eating any food"},
]

# The c03 fever/rash screening exchange exactly as the corrected pass stores it -
# the rash denial row is 41 characters WITH its trailing period.
C03_SCREENING_ROWS = [
    {"role": "DOCTOR", "text": "Okay, any temperatures or fevers?"},
    {"role": "PATIENT", "text": "No, I don't feel feverish."},
    {"role": "DOCTOR", "text": "Okay, any other funny skin rashes that you may have noticed?"},
    {"role": "PATIENT", "text": "No, I haven't noticed anything like that."},
]


def test_fabricated_lip_denial_is_caught_despite_the_monologue() -> None:
    """day3 false negative: the unanswered lip-swelling question is not a denial."""
    found = find_fidelity_violations(
        [{"heading": "Subjective", "content": (
            "She denies prior history of lip swelling after eating food, "
            "though the question was not fully answered in the transcript."
        )}],
        [],
        DAY3_ROWS,
    )
    assert [violation.rule for violation in found] == ["negative-without-denial"]


def test_punctuated_41_char_denial_supports_fever_and_rash() -> None:
    """c03 false positive: the real denial must not fail on one character of punctuation."""
    found = find_fidelity_violations(
        [{"heading": "Subjective", "content": (
            "Patient denies fever or feverish sensation and denies any skin rashes."
        )}],
        ["Associated nausea with two episodes of vomiting, denies fever or rash"],
        C03_SCREENING_ROWS,
    )
    assert found == []


def test_concludes_before_examination_is_honest_absence() -> None:
    """c03 false positive: honest scribe phrasing needs no literal negation token."""
    found = find_fidelity_violations(
        [{"heading": "Objective", "content": (
            "The consultation concludes before clinical examination is performed."
        )}],
        [],
        C03_SCREENING_ROWS,
    )
    assert found == []


def test_radiation_denial_matches_the_moving_anywhere_question() -> None:
    """2026-07-10 c03 replay false positive: 'spreading to other locations' is the
    note's paraphrase of the clinician's 'moving anywhere else' question."""
    found = find_fidelity_violations(
        [{"heading": "Subjective", "content": (
            "Patient denies headache spreading to other locations."
        )}],
        [],
        [
            {"role": "DOCTOR", "text": "And is it moving anywhere else"},
            {"role": "PATIENT", "text": "No, but it's worse when I move."},
        ],
    )
    assert found == []


def test_proposed_examination_with_findings_to_follow_is_not_an_exam_claim() -> None:
    """2026-07-10 c03 replay false positive: stating the doctor's exam INTENT is honest."""
    found = find_fidelity_violations(
        [{"heading": "Plan", "content": (
            "Doctor proposed taking a full history and performing a physical examination, "
            "with discussion to follow regarding findings."
        )}],
        [],
        C03_SCREENING_ROWS,
    )
    assert found == []


def test_positive_exam_claims_still_flag_on_an_exam_free_transcript() -> None:
    """The absence frames must not exempt claims that an exam actually happened."""
    found = find_fidelity_violations(
        [{"heading": "Objective", "content": (
            "Examination was performed and revealed no abnormalities. "
            "Neurological status normal on examination."
        )}],
        [],
        C03_SCREENING_ROWS,
    )
    assert [violation.rule for violation in found] == ["exam-not-performed", "exam-not-performed"]


def test_epistemic_phrases_are_not_denials() -> None:
    """'I don't know what to do about my upper lip' proves uncertainty, not denial."""
    found = find_fidelity_violations(
        [{"heading": "Subjective", "content": "Denies lip swelling."}],
        [],
        [{"role": "PATIENT", "text": "i don't know what to do about my upper lip"}],
    )
    assert [violation.rule for violation in found] == ["negative-without-denial"]


def test_contrast_clause_keeps_denial_and_symptom_apart() -> None:
    """'I don't think it was a sandwich, but my lip is swelling' denies no prior swelling."""
    found = find_fidelity_violations(
        [{"heading": "Subjective", "content": "Denies prior lip swelling."}],
        [],
        [{"role": "PATIENT", "text": "i don't think it was a sandwich, but my lip is swelling"}],
    )
    assert [violation.rule for violation in found] == ["negative-without-denial"]


def test_direct_denial_and_coordinated_list_still_pass() -> None:
    """'I don't have a rash' and 'no fever or rash' are genuine denials of both topics."""
    found = find_fidelity_violations(
        [{"heading": "Subjective", "content": "Denies rash. Patient denies fever or rash."}],
        [],
        [
            {"role": "PATIENT", "text": "i don't have a rash"},
            {"role": "PATIENT", "text": "no fever or rash"},
        ],
    )
    assert found == []


def test_denial_admitting_the_question_was_unanswered_is_flagged() -> None:
    """A sentence cannot claim a denial while admitting nobody answered the question."""
    found = find_fidelity_violations(
        [{"heading": "Subjective", "content": (
            "Denies skin rashes, although the question was not fully answered."
        )}],
        [],
        C03_SCREENING_ROWS,
    )
    assert [violation.rule for violation in found] == ["negative-without-denial"]


def test_long_monologue_starting_with_no_is_not_a_universal_answer() -> None:
    """A 'No ...' monologue answers nothing beyond eight words; topics need local support."""
    found = find_fidelity_violations(
        [{"heading": "Subjective", "content": "Denies chest pain."}],
        [],
        [
            {"role": "DOCTOR", "text": "any chest pain at all?"},
            {"role": "PATIENT", "text": (
                "no well actually my friend said the soup place uses a lot of chilli "
                "and my chest just feels a bit funny after eating there sometimes"
            )},
        ],
    )
    assert [violation.rule for violation in found] == ["negative-without-denial"]


def test_short_denial_answer_needs_a_doctor_question_naming_the_topic() -> None:
    """A bare 'No.' after another PATIENT row naming the topic verifies nothing."""
    found = find_fidelity_violations(
        [{"heading": "Subjective", "content": "Denies headache."}],
        [],
        [
            {"role": "PATIENT", "text": "my sister gets headaches all the time"},
            {"role": "PATIENT", "text": "No."},
        ],
    )
    assert [violation.rule for violation in found] == ["negative-without-denial"]
