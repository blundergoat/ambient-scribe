"""Protect clinician-visible notes with deterministic fidelity specimens.

The cases pin real replayed uncertainty, denial, exam, patient-state, and quote
wording. They prove unsupported sentences receive a visible warning while
transcript-supported phrasing stays clean when the user reviews the note.
"""

from __future__ import annotations

from api.summary_fidelity import FidelityViolation, find_fidelity_violations

# Minimal c03 visit shape: the sudden-vs-gradual exchange the patient could not
# answer, the verbal neuro screen with its bare "No.", and the visit ending on
# the doctor's unanswered breathing line.
C03_ROWS = [
    {
        "role": "DOCTOR",
        "text": "Did a pain come on quite suddenly or was it more of a gradual buildup?",
    },
    {"role": "PATIENT", "text": "I don't know really. It just happened."},
    {
        "role": "DOCTOR",
        "text": "Any problems with your speech, any weakness or numbness in your arms and legs, any balance or coordination problems?",
    },
    {"role": "PATIENT", "text": "No."},
    {
        "role": "DOCTOR",
        "text": "Have you had any injuries to your head, a fall recently or been knocked on the head?",
    },
    {
        "role": "DOCTOR",
        "text": "Okay, all right. So for example, your breathing is okay. You're not feeling breathless.",
    },
]


def _violation_rules(
    sections: list[dict], key_points: list[str] | None = None
) -> list[str]:
    """Return UI warning rule IDs in the order the clinician reads them."""
    found = find_fidelity_violations(sections, key_points or [], C03_ROWS)
    return [violation.rule for violation in found]


def test_resolving_uncertain_onset_to_sudden_is_flagged() -> None:
    """'Onset was sudden' misleads when the patient answered 'I don't know'."""
    rules = _violation_rules(
        [
            {
                "heading": "Subjective",
                "content": "The pain onset was sudden, and the patient is uncertain whether it developed gradually.",
            }
        ]
    )
    assert rules == ["uncertainty-resolved"]


def test_described_as_sudden_is_flagged_as_invented_attribution() -> None:
    """Nobody 'described' the onset as sudden; the doctor only asked about it."""
    rules = _violation_rules(
        [
            {
                "heading": "Subjective",
                "content": "Onset timing described as sudden, though patient expressed uncertainty.",
            }
        ]
    )
    assert rules == ["uncertainty-resolved"]


def test_onset_documented_as_unclear_stays_clean() -> None:
    """The honest form the clinician should see passes untouched."""
    rules = _violation_rules(
        [
            {
                "heading": "Subjective",
                "content": "Onset is unclear; patient states 'I don't know really' when asked whether it came on suddenly or gradually.",
            }
        ]
    )
    assert rules == []


def test_verbatim_patient_quote_counts_as_preserving_uncertainty() -> None:
    """Quoting the patient's own answer is the as-stated form, not a resolution."""
    rules = _violation_rules(
        [
            {
                "heading": "Subjective",
                "content": "Onset of pain timing described as 'it just happened' when asked about gradual versus sudden onset.",
            }
        ]
    )
    assert rules == []


def test_fabricated_breathing_denial_is_flagged() -> None:
    """The visit ends on the doctor's unanswered breathing line; no denial exists."""
    rules = _violation_rules(
        [
            {
                "heading": "Objective",
                "content": "Patient reports normal breathing and denies dyspnea.",
            }
        ]
    )
    assert "negative-without-denial" in rules


def test_denial_list_with_unanswered_head_injury_question_is_flagged() -> None:
    """The bare 'No.' answered the neuro screen, not the later head-injury question."""
    rules = _violation_rules(
        [
            {
                "heading": "Subjective",
                "content": "Denies recent head injury, falls, or difficulty breathing.",
            }
        ]
    )
    assert rules == ["negative-without-denial"]


def test_denied_all_with_unanswered_topic_before_the_verb_is_flagged() -> None:
    """gen1 escape: 'denied all' hides the topics before the verb - head injury was never answered."""
    rules = _violation_rules(
        [
            {
                "heading": "Subjective",
                "content": "When screened for neurological symptoms including speech difficulties, weakness, numbness, balance problems, or head injury, the patient denied all.",
            }
        ]
    )
    assert rules == ["negative-without-denial"]


def test_screening_negative_key_point_with_unanswered_item_is_flagged() -> None:
    """gen1 escape: 'screening (...) negative' claims every listed item was denied."""
    rules = _violation_rules(
        [],
        key_points=[
            "Neurological screening (speech, strength, sensory, balance, head injury) negative"
        ],
    )
    assert "negative-without-denial" in rules


def test_denials_the_patient_actually_gave_stay_clean() -> None:
    """The neuro-screen batch was really denied by the patient's 'No.'."""
    rules = _violation_rules(
        [
            {
                "heading": "Subjective",
                "content": "Denies speech difficulties, weakness, or numbness in arms and legs.",
            }
        ]
    )
    assert rules == []


def test_screening_answers_dressed_as_exam_findings_are_flagged() -> None:
    """Verbal screening is history; 'examination findings ... intact' claims an exam."""
    rules = _violation_rules(
        [],
        key_points=[
            "Neurological examination findings negative (speech, motor, sensory, balance intact)"
        ],
    )
    assert rules == ["exam-not-performed"]


def test_exam_initiated_claim_without_exam_is_flagged() -> None:
    """The doctor only promised an exam ('let me examine you'); none happened."""
    rules = _violation_rules(
        [
            {
                "heading": "Objective",
                "content": "Physical examination has been initiated but is incomplete in this transcript.",
            }
        ]
    )
    assert rules == ["exam-not-performed"]


def test_honest_no_examination_documented_stays_clean() -> None:
    """Saying no exam happened is exactly the behavior the rules want."""
    rules = _violation_rules(
        [
            {
                "heading": "Objective",
                "content": "No physical examination findings are documented in this transcript. Vital signs were not recorded.",
            }
        ]
    )
    assert rules == []


def test_sentence_split_inside_a_quotation_never_creates_a_fragment_flag() -> None:
    """gen5 false positive: a period INSIDE the patient's quote must not split the sentence."""
    rules = _violation_rules(
        [
            {
                "heading": "Subjective",
                "content": 'Patient stated "I don\'t know really. It just happened" when asked if it came on suddenly or gradually.',
            }
        ]
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
        sections=[
            SummarySectionOutput(heading="Objective", content="Patient denies dyspnea.")
        ],
        key_points=[],
    )
    seen_prompts: list[str] = []

    class _StubbedResult:
        """Stand in for one generated note returned by the model.

        The pipeline reads this object before deciding what the user sees.
        It keeps the test independent of a live model provider.
        """

        structured_output = fabricated

    class _StubbedAgent:
        """Return the same unsupported note for both generation attempts.

        This imitates a model that repeats a defect after the user's summary request.
        The pipeline must then expose the warning in the visible note.
        """

        def __call__(self, prompt, structured_output_model=None):
            """Record one prompt and return the note the clinician would receive."""
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
        """Stand in for a clean generated note returned by the model.

        The pipeline reads this object before presenting the note to the user.
        It keeps the clean-path test independent of a live provider.
        """

        structured_output = honest

    class _StubbedAgent:
        """Return one clean note for the user's summary request.

        The test uses it to prove no retry or warning reaches the UI.
        It also records how often generation was requested.
        """

        def __call__(self, prompt, structured_output_model=None):
            """Record the prompt and return the clean clinician-facing note."""
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
        """Return successive note drafts without calling the configured model.

        Tests use it to simulate the first draft and one retry a user may trigger.
        The prompt log proves which draft the pipeline chose to display.
        """

        def __call__(self, prompt, structured_output_model=None):
            """Return the next prepared note for this summary-generation attempt."""
            seen_prompts.append(prompt)

            class _Result:
                """Wrap the prepared draft in the model result shape.

                The summary pipeline reads this field before choosing the visible note.
                Each instance represents one generation attempt.
                """

                structured_output = drafts[len(seen_prompts) - 1]

            return _Result()

    import agents as agents_module  # noqa: F401 - imported for monkeypatch target

    monkeypatch.setattr("agents.create_summary_agent", lambda: _StubbedAgent())
    return seen_prompts


def test_worse_regeneration_never_replaces_a_better_first_draft(
    monkeypatch, caplog
) -> None:
    """Field shape: attempt 0 has one violation, attempt 1 has three - attempt 0 ships."""
    import logging

    from api import summary_generation as generation_module
    from api.summary_generation import SessionSummaryOutput, SummarySectionOutput

    one_violation = SessionSummaryOutput(
        title="Visit note",
        sections=[
            SummarySectionOutput(heading="Objective", content="Patient denies dyspnea.")
        ],
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
        record
        for record in caplog.records
        if getattr(record, "selected_attempt", None) is not None
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
        sections=[
            SummarySectionOutput(heading="Objective", content="Patient denies dyspnea.")
        ],
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
    assert payload["sections"][0]["content"].startswith(
        "No physical examination findings"
    )
    assert "unverified" not in payload["sections"][0]


def test_fidelity_logs_never_contain_clinical_sentences(monkeypatch, caplog) -> None:
    """Violation diagnostics identify rule/location/ordinal but never note prose."""
    import logging

    from api import summary_generation as generation_module
    from api.summary_generation import SessionSummaryOutput, SummarySectionOutput

    fabricated_sentence = "Patient denies dyspnea."
    fabricated = SessionSummaryOutput(
        title="Visit note",
        sections=[
            SummarySectionOutput(heading="Objective", content=fabricated_sentence)
        ],
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
    # Each structured log record must stay useful without exposing note prose.
    for record in violation_records:
        # Every violation entry follows the same PHI-safe diagnostic contract.
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
    {
        "role": "DOCTOR",
        "text": "all right. so when did you have the prawn soup? what time",
    },
    {
        "role": "PATIENT",
        "text": "well i wasn't really paying attention so it must have been like an half an hour maybe",
    },
    {
        "role": "PATIENT",
        "text": "i don't know if it's the chest just generally feels a little difficult but yeah it might be i'm not sure",
    },
    {
        "role": "DOCTOR",
        "text": "have you ever had these any kind of lip swelling in the past without eating any food",
    },
]

# The c03 fever/rash screening exchange exactly as the corrected pass stores it -
# the rash denial row is 41 characters WITH its trailing period.
C03_SCREENING_ROWS = [
    {"role": "DOCTOR", "text": "Okay, any temperatures or fevers?"},
    {"role": "PATIENT", "text": "No, I don't feel feverish."},
    {
        "role": "DOCTOR",
        "text": "Okay, any other funny skin rashes that you may have noticed?",
    },
    {"role": "PATIENT", "text": "No, I haven't noticed anything like that."},
]

# Full-length c03 corrected rows behind the two clinician-visible false flags.
# The neurological answers were folded into fragmented DOCTOR rows, while the
# mood answer uses "down" where the note says "depressed".
C03_COMPOSITE_DENIAL_ROWS = [
    {"role": "DOCTOR", "text": "any temperatures or fevers?"},
    {"role": "PATIENT", "text": "Um, no, I"},
    {"role": "PATIENT", "text": "don't feel feverish."},
    {"role": "DOCTOR", "text": "any other funny skin rashes that you may"},
    {"role": "DOCTOR", "text": "have noticed?"},
    {"role": "PATIENT", "text": "No, I haven't noticed anything like that."},
    {"role": "DOCTOR", "text": "Um have you noticed any problems with"},
    {"role": "DOCTOR", "text": "your speech at all? Any difficulty with your words?"},
    {"role": "DOCTOR", "text": "No. Any problems"},
    {"role": "DOCTOR", "text": "with your arms and legs,"},
    {"role": "DOCTOR", "text": "for example, numbness or weakness?"},
    {"role": "DOCTOR", "text": "No, any difficulty with"},
    {"role": "DOCTOR", "text": "balance your balance or coordination?"},
    {"role": "PATIENT", "text": "No."},
    {"role": "DOCTOR", "text": "Okay, and have you had any"},
    {"role": "DOCTOR", "text": "injuries to your head? Have you"},
    {"role": "DOCTOR", "text": "had a fall recently or been knocked in on the head?"},
    {"role": "DOCTOR", "text": "No. Okay, all"},
    {"role": "DOCTOR", "text": "Is your work is your job"},
    {"role": "DOCTOR", "text": "quite stressful at the moment?"},
    {"role": "PATIENT", "text": "Yeah, it's really stressful. For actually"},
    {"role": "DOCTOR", "text": "having"},
    {"role": "PATIENT", "text": "problems with like people at"},
    {"role": "PATIENT", "text": "work, you know, managers"},
    {"role": "PATIENT", "text": "like putting a lot of pressure on me to deliver"},
    {"role": "PATIENT", "text": "and oh yeah, it's not a good time."},
    {"role": "DOCTOR", "text": "nothing"},
    {"role": "DOCTOR", "text": "Is it getting you down?"},
    {"role": "PATIENT", "text": "I don't feel down, just feel a bit"},
    {"role": "PATIENT", "text": "stressed. Okay,"},
]

# Later c03 answers used by fresh M02 notes: no prior migraine diagnosis,
# exercise, tobacco, and alcohol. The checker sees these exact corrected rows
# after the user requests a note from the retained visit.
C03_LATER_DENIAL_ROWS = [
    {"role": "DOCTOR", "text": "So for example, have you had if anyone"},
    {"role": "DOCTOR", "text": "told you you've had migraines in the past?"},
    {"role": "PATIENT", "text": "No."},
    {"role": "PATIENT", "text": "My mum has has migraines, but I've I've not"},
    {"role": "PATIENT", "text": "been"},
    {"role": "PATIENT", "text": "diagnosed."},
    {"role": "DOCTOR", "text": "Do you do much in a way of exercise?"},
    {"role": "PATIENT", "text": "No."},
    {"role": "DOCTOR", "text": "Yeah, okay. Um do you smoke at all?"},
    {"role": "PATIENT", "text": "No."},
    {"role": "DOCTOR", "text": "And do you"},
    {"role": "DOCTOR", "text": "drink much in a way of alcohol?"},
    {"role": "PATIENT", "text": "No. Okay,"},
]

# Retained day5 rows behind the three additional split-question families.
# These preserve exactly what the note checker saw after the user generated a
# note: fragmented respiratory, GI/urinary, and joint-swelling exchanges.
DAY5_SPLIT_SCREENING_ROWS = [
    {"role": "DOCTOR", "text": "Okay, so just tell me what symptoms you've been"},
    {"role": "DOCTOR", "text": "having in terms of"},
    {"role": "DOCTOR", "text": "just your general"},
    {"role": "DOCTOR", "text": "health, any persistent"},
    {"role": "PATIENT", "text": "all started afterwards"},
    {"role": "DOCTOR", "text": "cold symptoms"},
    {"role": "DOCTOR", "text": "or irate sore throat, chest,"},
    {"role": "DOCTOR", "text": "cough, shortness of"},
    {"role": "DOCTOR", "text": "breath, flirm,"},
    {"role": "DOCTOR", "text": "anything like that."},
    {"role": "PATIENT", "text": "no"},
    {"role": "DOCTOR", "text": "or any difficulty swallowing or acid in your"},
    {"role": "DOCTOR", "text": "throat or abdominal"},
    {"role": "DOCTOR", "text": "pain?"},
    {"role": "PATIENT", "text": "no"},
    {"role": "DOCTOR", "text": "If you have any change in"},
    {"role": "DOCTOR", "text": "your bowel habit, diarrhea"},
    {"role": "DOCTOR", "text": "vomiting or blood in the stool,"},
    {"role": "DOCTOR", "text": "any difficulty passing urine or blood in the urine."},
    {"role": "PATIENT", "text": "that's all fine"},
    {"role": "DOCTOR", "text": "Apart from that, Rash,"},
    {"role": "DOCTOR", "text": "any other rashes on your skin"},
    {"role": "DOCTOR", "text": "or swelling of your joints or pain in your joints?"},
    {"role": "PATIENT", "text": "yeah sort of"},
    {"role": "PATIENT", "text": "shoulders back"},
    {"role": "PATIENT", "text": "the hips knees but sort of"},
    {"role": "PATIENT", "text": "yeah not sedition"},
    {"role": "DOCTOR", "text": "Any"},
    {"role": "DOCTOR", "text": "swelling that you've noticed"},
    {"role": "PATIENT", "text": "no"},
]

# This answer ends with a small change rather than a definitive denial. The UI
# must keep warning on a generated "denies weight change" claim.
DAY5_SELF_CORRECTED_WEIGHT_ROWS = [
    {"role": "DOCTOR", "text": "Any change in your weight at all?"},
    {"role": "PATIENT", "text": "I don't think so"},
    {"role": "PATIENT", "text": "I'm probably less hungry but no no"},
    {"role": "PATIENT", "text": "weight change just a bit"},
]

# The corrected c03 family-history exchange folds the clinician's final question
# fragment and the patient's answer into one PATIENT row.
C03_FAMILY_HISTORY_ROWS = [
    {"role": "DOCTOR", "text": "or um you mentioned brain cancer. Any other family"},
    {"role": "PATIENT", "text": "history of that? No."},
    {"role": "DOCTOR", "text": "no"},
]


def test_fabricated_lip_denial_is_caught_despite_the_monologue() -> None:
    """day3 false negative: the unanswered lip-swelling question is not a denial."""
    found = find_fidelity_violations(
        [
            {
                "heading": "Subjective",
                "content": (
                    "She denies prior history of lip swelling after eating food, "
                    "though the question was not fully answered in the transcript."
                ),
            }
        ],
        [],
        DAY3_ROWS,
    )
    assert [violation.rule for violation in found] == ["negative-without-denial"]


def test_punctuated_41_char_denial_supports_fever_and_rash() -> None:
    """c03 false positive: the real denial must not fail on one character of punctuation."""
    found = find_fidelity_violations(
        [
            {
                "heading": "Subjective",
                "content": (
                    "Patient denies fever or feverish sensation and denies any skin rashes."
                ),
            }
        ],
        ["Associated nausea with two episodes of vomiting, denies fever or rash"],
        C03_SCREENING_ROWS,
    )
    assert found == []


def test_c03_composite_denials_survive_fragmented_question_rows() -> None:
    """The full c03 neuro-screen denials stay plain despite corrected-row splits."""
    found = find_fidelity_violations(
        [
            {
                "heading": "Subjective",
                "content": (
                    "She denies fever, rashes, speech problems, arm/leg weakness, balance "
                    "difficulties, or recent head injury."
                ),
            }
        ],
        [],
        C03_COMPOSITE_DENIAL_ROWS,
    )
    assert found == []


def test_c03_down_answer_supports_denies_feeling_depressed() -> None:
    """The patient's explicit 'don't feel down' answer supports the note synonym."""
    found = find_fidelity_violations(
        [
            {
                "heading": "Subjective",
                "content": (
                    "Works in fashion industry; describes work as very stressful with manager "
                    "pressure, though denies feeling depressed."
                ),
            }
        ],
        [],
        C03_COMPOSITE_DENIAL_ROWS,
    )
    assert found == []


def test_day5_split_respiratory_question_supports_the_composite_denial() -> None:
    """The user's short no covers one respiratory question split across UI rows."""
    found = find_fidelity_violations(
        [
            {
                "heading": "Subjective",
                "content": (
                    "Denies persistent cold symptoms, sore throat, cough, or shortness of breath."
                ),
            }
        ],
        [],
        DAY5_SPLIT_SCREENING_ROWS,
    )
    assert found == []


def test_day5_split_gi_and_urinary_answers_support_each_denied_topic() -> None:
    """Two local answers verify the GI/urinary list shown in the generated note."""
    found = find_fidelity_violations(
        [
            {
                "heading": "Subjective",
                "content": (
                    "Denies difficulty swallowing, acid reflux, abdominal pain, bowel changes, "
                    "diarrhea, vomiting, blood in stool, or urinary symptoms."
                ),
            }
        ],
        [],
        DAY5_SPLIT_SCREENING_ROWS,
    )
    assert found == []


def test_day5_joint_swelling_denial_ignores_the_reported_pain_clause() -> None:
    """The UI warning scopes denial to swelling, not the pain reported after 'but'."""
    found = find_fidelity_violations(
        [
            {
                "heading": "Subjective",
                "content": (
                    "Denies joint swelling but reports aching in shoulders, back, hips, and knees."
                ),
            }
        ],
        [],
        DAY5_SPLIT_SCREENING_ROWS,
    )
    assert found == []


def test_day5_self_corrected_weight_change_still_flags() -> None:
    """An equivocal small weight change cannot display as a definite denial."""
    found = find_fidelity_violations(
        [{"heading": "Subjective", "content": "Patient denies weight change."}],
        [],
        DAY5_SELF_CORRECTED_WEIGHT_ROWS,
    )
    assert [violation.rule for violation in found] == ["negative-without-denial"]


def test_clinician_denial_never_supplies_patient_evidence() -> None:
    """A doctor's own negative wording cannot clear a patient symptom in the UI."""
    found = find_fidelity_violations(
        [{"heading": "Subjective", "content": "Patient denies fever or rash."}],
        [],
        [
            {
                "role": "DOCTOR",
                "text": "No fever or rash was documented by the referrer.",
            }
        ],
    )
    assert [violation.rule for violation in found] == ["negative-without-denial"]


def test_clinician_unanswered_statement_is_not_a_folded_patient_no() -> None:
    """A doctor's 'No' explanation cannot imitate a split patient answer."""
    found = find_fidelity_violations(
        [{"heading": "Subjective", "content": "Patient denies chest pain."}],
        [],
        [
            {"role": "DOCTOR", "text": "Any chest pain?"},
            {"role": "DOCTOR", "text": "No. The patient has not answered yet."},
        ],
    )
    assert [violation.rule for violation in found] == ["negative-without-denial"]


def test_c03_exercise_tobacco_and_alcohol_denials_stay_clean() -> None:
    """Fresh campaign wording combines three explicit no answers safely."""
    found = find_fidelity_violations(
        [
            {
                "heading": "Subjective",
                "content": ("Denies regular exercise, tobacco use, or alcohol."),
            }
        ],
        [],
        C03_LATER_DENIAL_ROWS,
    )
    assert found == []


def test_c03_head_injury_or_falls_denial_stays_clean() -> None:
    """The user's head-injury answer also covers the clinician's 'fall' wording."""
    found = find_fidelity_violations(
        [
            {
                "heading": "Subjective",
                "content": (
                    "She denies fever, skin rashes, difficulty with speech, numbness or weakness "
                    "in arms and legs, balance or coordination problems, and recent head injury "
                    "or falls."
                ),
            }
        ],
        [],
        C03_COMPOSITE_DENIAL_ROWS,
    )
    assert found == []


def test_c03_no_prior_migraine_diagnosis_stays_clean() -> None:
    """A direct no to prior migraines supports the generated diagnosis wording."""
    found = find_fidelity_violations(
        [
            {
                "heading": "Subjective",
                "content": ("She denies her own prior migraine diagnosis."),
            }
        ],
        [],
        C03_LATER_DENIAL_ROWS,
    )
    assert found == []


def test_c03_smoking_and_alcohol_denial_uses_the_smoke_question() -> None:
    """Fresh campaign wording maps smoking to the user's explicit smoke answer."""
    found = find_fidelity_violations(
        [
            {
                "heading": "Subjective",
                "content": (
                    "Social history: works in the fashion industry in a stressful environment "
                    "with workplace pressure; lives alone; denies smoking and alcohol use; does "
                    "not exercise and reports no usual stress-relief activities."
                ),
            }
        ],
        [],
        C03_LATER_DENIAL_ROWS,
    )
    assert found == []


def test_c03_negative_social_list_stops_before_lives_alone() -> None:
    """The note's affirmative living situation is not parsed as another denial."""
    found = find_fidelity_violations(
        [
            {
                "heading": "Subjective",
                "content": (
                    "She reports no exercise, does not smoke, does not drink alcohol, and lives alone."
                ),
            }
        ],
        [],
        C03_LATER_DENIAL_ROWS,
    )
    assert found == []


def test_second_negative_clause_after_and_still_requires_evidence() -> None:
    """A later 'reports no rash' claim cannot hide behind a supported fever denial."""
    found = find_fidelity_violations(
        [
            {
                "heading": "Subjective",
                "content": ("Patient denies fever and reports no rash."),
            }
        ],
        [],
        [
            {"role": "DOCTOR", "text": "Any temperatures or fevers?"},
            {"role": "PATIENT", "text": "No, I don't feel feverish."},
        ],
    )
    assert [violation.rule for violation in found] == ["negative-without-denial"]


def test_later_short_no_cannot_answer_an_earlier_affirmed_topic() -> None:
    """A fever no cannot overwrite the user's earlier affirmative rash answer."""
    found = find_fidelity_violations(
        [{"heading": "Subjective", "content": "Patient denies rash."}],
        [],
        [
            {"role": "DOCTOR", "text": "Have you noticed a rash?"},
            {"role": "PATIENT", "text": "Yes, I have a rash."},
            {"role": "DOCTOR", "text": "Any fever?"},
            {"role": "PATIENT", "text": "No."},
        ],
    )
    assert [violation.rule for violation in found] == ["negative-without-denial"]


def test_c03_family_brain_cancer_denial_handles_same_row_question_tail() -> None:
    """The patient's same-row `? No.` answer supports the visible family-history denial."""
    found = find_fidelity_violations(
        [
            {
                "heading": "Subjective",
                "content": (
                    "Family history includes mother with migraines and underactive thyroid; "
                    "denies family history of brain cancer."
                ),
            }
        ],
        [],
        C03_FAMILY_HISTORY_ROWS,
    )
    assert found == []


def test_concludes_before_examination_is_honest_absence() -> None:
    """c03 false positive: honest scribe phrasing needs no literal negation token."""
    found = find_fidelity_violations(
        [
            {
                "heading": "Objective",
                "content": (
                    "The consultation concludes before clinical examination is performed."
                ),
            }
        ],
        [],
        C03_SCREENING_ROWS,
    )
    assert found == []


def test_radiation_denial_matches_the_moving_anywhere_question() -> None:
    """2026-07-10 c03 replay false positive: 'spreading to other locations' is the
    note's paraphrase of the clinician's 'moving anywhere else' question."""
    found = find_fidelity_violations(
        [
            {
                "heading": "Subjective",
                "content": ("Patient denies headache spreading to other locations."),
            }
        ],
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
        [
            {
                "heading": "Plan",
                "content": (
                    "Doctor proposed taking a full history and performing a physical examination, "
                    "with discussion to follow regarding findings."
                ),
            }
        ],
        [],
        C03_SCREENING_ROWS,
    )
    assert found == []


def test_positive_exam_claims_still_flag_on_an_exam_free_transcript() -> None:
    """The absence frames must not exempt claims that an exam actually happened."""
    found = find_fidelity_violations(
        [
            {
                "heading": "Objective",
                "content": (
                    "Examination was performed and revealed no abnormalities. "
                    "Neurological status normal on examination."
                ),
            }
        ],
        [],
        C03_SCREENING_ROWS,
    )
    assert [violation.rule for violation in found] == [
        "exam-not-performed",
        "exam-not-performed",
    ]


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
        [
            {
                "role": "PATIENT",
                "text": "i don't think it was a sandwich, but my lip is swelling",
            }
        ],
    )
    assert [violation.rule for violation in found] == ["negative-without-denial"]


def test_direct_denial_and_coordinated_list_still_pass() -> None:
    """'I don't have a rash' and 'no fever or rash' are genuine denials of both topics."""
    found = find_fidelity_violations(
        [
            {
                "heading": "Subjective",
                "content": "Denies rash. Patient denies fever or rash.",
            }
        ],
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
        [
            {
                "heading": "Subjective",
                "content": (
                    "Denies skin rashes, although the question was not fully answered."
                ),
            }
        ],
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
            {
                "role": "PATIENT",
                "text": (
                    "no well actually my friend said the soup place uses a lot of chilli "
                    "and my chest just feels a bit funny after eating there sometimes"
                ),
            },
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


# --- M11 field specimens (sessions 203d1d35 and 0a40e243, 2026-07-08 manual round) ---

DAY5_MEDICATION_ROWS = [
    {"role": "DOCTOR", "text": "Are you taking any other medication?"},
    {
        "role": "PATIENT",
        "text": "Um, I take one of the antihistamines. I get that from the pharmacy.",
    },
]

DAY3_QUOTE_ROWS = [
    {"role": "PATIENT", "text": "I feel a little weird today, to be honest."},
    {
        "role": "PATIENT",
        "text": "You can still feel it like really pumping blood and it is getting bigger.",
    },
    {"role": "PATIENT", "text": "I ordered a prawn soup; just relax while I explain."},
]


def test_patient_unsure_of_unanswered_food_reactions_is_flagged() -> None:
    """day3 field defect: a trailing clinician question is not patient uncertainty."""
    found = find_fidelity_violations(
        [
            {
                "heading": "Subjective",
                "content": "The patient was unsure of prior similar food reactions.",
            }
        ],
        [],
        DAY3_ROWS,
    )
    assert [violation.rule for violation in found] == ["patient-state-without-evidence"]


def test_patient_unable_to_recall_medication_specifics_is_flagged() -> None:
    """day5 field defect: garbled naming cannot become a patient memory failure."""
    found = find_fidelity_violations(
        [
            {
                "heading": "Subjective",
                "content": "The patient was unable to recall the medication specifics.",
            }
        ],
        [],
        DAY5_MEDICATION_ROWS,
    )
    assert [violation.rule for violation in found] == ["patient-state-without-evidence"]


def test_topic_scoped_uncertainty_answer_after_question_stays_clean() -> None:
    """A short uncertainty answer inherits the topic from its clinician question."""
    found = find_fidelity_violations(
        [
            {
                "heading": "Subjective",
                "content": "The patient was unsure which antihistamine she takes.",
            }
        ],
        [],
        [
            {"role": "DOCTOR", "text": "Which antihistamine do you take?"},
            {"role": "PATIENT", "text": "I'm not sure which one."},
        ],
    )
    assert found == []


def test_patient_memory_claim_needs_local_topic_support() -> None:
    """An unrelated 'I don't know' elsewhere cannot support a medication memory claim."""
    found = find_fidelity_violations(
        [
            {
                "heading": "Subjective",
                "content": "She could not remember the medication name.",
            }
        ],
        [],
        [
            {"role": "PATIENT", "text": "I don't know what to do about my lip."},
            {"role": "DOCTOR", "text": "Which medication do you take?"},
        ],
    )
    assert [violation.rule for violation in found] == ["patient-state-without-evidence"]


def test_patient_memory_claim_with_local_evidence_stays_clean() -> None:
    """The patient's own topic-scoped inability to remember supports the attribution."""
    found = find_fidelity_violations(
        [
            {
                "heading": "Subjective",
                "content": "She could not remember the medication name.",
            }
        ],
        [],
        [
            {
                "role": "PATIENT",
                "text": "I cannot remember the medication name, sorry.",
            }
        ],
    )
    assert found == []


def test_patient_refusal_needs_the_patients_own_words() -> None:
    """A clinician's unanswered question cannot become a patient refusal."""
    unsupported = find_fidelity_violations(
        [
            {
                "heading": "Subjective",
                "content": "The patient declined to name the medication.",
            }
        ],
        [],
        [{"role": "DOCTOR", "text": "Can you name the medication?"}],
    )
    supported = find_fidelity_violations(
        [
            {
                "heading": "Subjective",
                "content": "The patient declined to name the medication.",
            }
        ],
        [],
        [
            {"role": "DOCTOR", "text": "Can you name the medication?"},
            {"role": "PATIENT", "text": "I would rather not name the medication."},
        ],
    )
    assert [violation.rule for violation in unsupported] == [
        "patient-state-without-evidence"
    ]
    assert supported == []


def test_record_level_not_captured_phrase_is_not_a_patient_state() -> None:
    """Honest record wording stays exempt because it claims nothing about cognition."""
    found = find_fidelity_violations(
        [
            {
                "heading": "Subjective",
                "content": "The medication name was not clearly captured in the transcript.",
            }
        ],
        [],
        DAY5_MEDICATION_ROWS,
    )
    assert found == []


def test_invented_straight_double_quoted_phrase_is_flagged() -> None:
    """day3 field defect: 'really pumping a lot' is absent from the transcript."""
    found = find_fidelity_violations(
        [
            {
                "heading": "Subjective",
                "content": 'The patient described the lip as "really pumping a lot".',
            }
        ],
        [],
        DAY3_QUOTE_ROWS,
    )
    assert [violation.rule for violation in found] == ["non-verbatim-quote"]


def test_invented_straight_single_quoted_word_is_flagged() -> None:
    """One quoted word must match a token, not a substring inside 'relax'."""
    found = find_fidelity_violations(
        [
            {
                "heading": "Subjective",
                "content": "The soup was called 'lax'.",
            }
        ],
        [],
        DAY3_QUOTE_ROWS,
    )
    assert [violation.rule for violation in found] == ["non-verbatim-quote"]


def test_straight_and_curly_verbatim_quotes_stay_clean() -> None:
    """Straight double and curly single spans both preserve transcript words."""
    found = find_fidelity_violations(
        [
            {
                "heading": "Subjective",
                "content": 'The patient felt "a little weird" and called it ‘weird’.',
            }
        ],
        [],
        DAY3_QUOTE_ROWS,
    )
    assert found == []


def test_curly_double_non_verbatim_quote_is_flagged() -> None:
    """Curly double quotation marks carry the same verbatim contract."""
    found = find_fidelity_violations(
        [
            {
                "heading": "Subjective",
                "content": "The patient described it as “really pumping a lot”.",
            }
        ],
        [],
        DAY3_QUOTE_ROWS,
    )
    assert [violation.rule for violation in found] == ["non-verbatim-quote"]


def test_verbatim_quote_can_span_adjacent_same_role_rows() -> None:
    """ASR row boundaries do not invalidate one contiguous patient phrase."""
    found = find_fidelity_violations(
        [
            {
                "heading": "Subjective",
                "content": 'The patient felt "a little weird".',
            }
        ],
        [],
        [
            {"role": "PATIENT", "text": "I feel a little"},
            {"role": "PATIENT", "text": "weird today"},
        ],
    )
    assert found == []


def test_quote_cannot_span_a_role_change() -> None:
    """Adjacent doctor and patient rows are not one speaker's verbatim phrase."""
    found = find_fidelity_violations(
        [
            {
                "heading": "Subjective",
                "content": 'The note quotes "a little weird".',
            }
        ],
        [],
        [
            {"role": "DOCTOR", "text": "a little"},
            {"role": "PATIENT", "text": "weird"},
        ],
    )
    assert [violation.rule for violation in found] == ["non-verbatim-quote"]


def test_explicit_quote_attribution_requires_the_named_role() -> None:
    """A patient's words cannot be laundered as a clinician quotation."""
    clinician_claim = find_fidelity_violations(
        [
            {
                "heading": "Plan",
                "content": 'The clinician said "take the tablet tomorrow".',
            }
        ],
        [],
        [{"role": "PATIENT", "text": "take the tablet tomorrow"}],
    )
    unattributed = find_fidelity_violations(
        [
            {
                "heading": "Plan",
                "content": 'The phrase "take the tablet tomorrow" was recorded.',
            }
        ],
        [],
        [{"role": "PATIENT", "text": "take the tablet tomorrow"}],
    )
    assert [violation.rule for violation in clinician_claim] == ["non-verbatim-quote"]
    assert unattributed == []


def test_one_valid_quote_cannot_launder_an_invalid_second_span() -> None:
    """Every span must verify independently, even when one quote is genuine."""
    found = find_fidelity_violations(
        [
            {
                "heading": "Subjective",
                "content": (
                    'The patient felt "a little weird" with the lip '
                    '"really pumping a lot".'
                ),
            }
        ],
        [],
        DAY3_QUOTE_ROWS,
    )
    assert [violation.rule for violation in found] == ["non-verbatim-quote"]


def test_contraction_apostrophes_are_not_quote_spans() -> None:
    """Possessives and contractions never create accidental one-word quotes."""
    found = find_fidelity_violations(
        [
            {
                "heading": "Subjective",
                "content": "The patient's symptoms don't include fever.",
            }
        ],
        [],
        [{"role": "PATIENT", "text": "my symptoms do not include fever"}],
    )
    assert found == []


def test_subjective_patient_action_carries_an_implicit_patient_subject() -> None:
    """Live miss: 'Takes ... but cannot recall' still attributes memory to the patient."""
    found = find_fidelity_violations(
        [
            {
                "heading": "Subjective",
                "content": (
                    "Takes a preventor inhaler daily but cannot recall other medications "
                    "without checking pharmacy records."
                ),
            }
        ],
        [],
        DAY5_MEDICATION_ROWS,
    )
    assert [violation.rule for violation in found] == ["patient-state-without-evidence"]


def test_patient_state_evidence_can_span_adjacent_patient_rows() -> None:
    """Live false flag: one patient answer may be split before its topic words."""
    found = find_fidelity_violations(
        [
            {
                "heading": "Subjective",
                "content": (
                    "Patient has continued attending work but is unsure of effectiveness."
                ),
            }
        ],
        [],
        [
            {"role": "DOCTOR", "text": "Have you been able to go to work as usual?"},
            {"role": "PATIENT", "text": "I've been going to work; I don't know how"},
            {"role": "PATIENT", "text": "effective it's been, but yes."},
        ],
    )
    assert found == []


def test_patient_state_topic_can_precede_the_state_marker() -> None:
    """Live false flag: 'asked about chest ... uncertain' names its topic first."""
    found = find_fidelity_violations(
        [
            {
                "heading": "Subjective",
                "content": (
                    "When asked about chest tightness, the patient was uncertain, stating "
                    "\"I don't know if it's the chest, it just generally feels difficult.\""
                ),
            }
        ],
        [],
        [
            {"role": "DOCTOR", "text": "Do you feel like your chest is tight?"},
            {"role": "PATIENT", "text": "I don't know if it's the chest"},
            {"role": "PATIENT", "text": "it just generally feels difficult"},
        ],
    )
    assert found == []


def test_memory_answer_uses_the_bounded_clinician_question_context() -> None:
    """Live false flag: a fragmented bite exchange still supports 'does not recall'."""
    found = find_fidelity_violations(
        [
            {
                "heading": "Subjective",
                "content": "The patient does not recall any insect bites.",
            }
        ],
        [],
        [
            {"role": "DOCTOR", "text": "Did you get any insect bites?"},
            {"role": "PATIENT", "text": "I did quite a lot of walking"},
            {"role": "PATIENT", "text": "while we were away on holiday"},
            {"role": "DOCTOR", "text": "Anything that you noticed?"},
            {"role": "PATIENT", "text": "I don't recall any."},
        ],
    )
    assert found == []


def test_exercise_uncertainty_matches_ability_and_motivation_words() -> None:
    """Live false flag: 'able or lazy' supports ability-or-motivation uncertainty."""
    found = find_fidelity_violations(
        [
            {
                "heading": "Subjective",
                "content": (
                    "Reports exercising less, uncertain whether due to lack of ability "
                    "or reduced motivation."
                ),
            }
        ],
        [],
        [
            {"role": "DOCTOR", "text": "Have you been able to exercise okay?"},
            {"role": "PATIENT", "text": "Not as much. I don't know"},
            {
                "role": "PATIENT",
                "text": "if I haven't been able to or I've just been a bit lazy.",
            },
        ],
    )
    assert found == []


def test_optional_clearly_in_memory_claim_still_flags_unanswered_question() -> None:
    """Live miss: wording like 'does not clearly recall' is still patient cognition."""
    found = find_fidelity_violations(
        [
            {
                "heading": "Subjective",
                "content": (
                    "The patient does not clearly recall previous episodes of lip swelling "
                    "after food."
                ),
            }
        ],
        [],
        DAY3_ROWS,
    )
    assert [violation.rule for violation in found] == ["patient-state-without-evidence"]


def test_unable_to_specify_or_detail_medication_both_flag() -> None:
    """Live misses: specify/detail paraphrases cannot turn garble into inability."""
    found = find_fidelity_violations(
        [
            {
                "heading": "Subjective",
                "content": (
                    "The patient is unable to specify the other medication. "
                    "Patient is unable to clearly detail current medications."
                ),
            }
        ],
        [],
        DAY5_MEDICATION_ROWS,
    )
    assert [violation.rule for violation in found] == [
        "patient-state-without-evidence",
        "patient-state-without-evidence",
    ]


def test_chest_tightness_uncertainty_matches_clinician_tight_question() -> None:
    """Live false flag: 'tightness' in the note matches 'tight' in the question."""
    found = find_fidelity_violations(
        [],
        ["Patient uncertain about chest tightness; transcript incomplete"],
        [
            {"role": "DOCTOR", "text": "Do you feel like your chest is tight?"},
            {"role": "PATIENT", "text": "I don't know if it's the chest"},
            {"role": "PATIENT", "text": "It might be. I'm not sure."},
        ],
    )
    assert found == []


def test_subjective_sentence_beginning_unable_has_implicit_patient_subject() -> None:
    """Live miss: standalone Subjective 'Unable...' wording still means the patient."""
    found = find_fidelity_violations(
        [
            {
                "heading": "Subjective",
                "content": "Unable to clearly specify other medications at consultation.",
            }
        ],
        [],
        DAY5_MEDICATION_ROWS,
    )
    assert [violation.rule for violation in found] == ["patient-state-without-evidence"]


def test_medication_names_cannot_borrow_unrelated_bite_memory() -> None:
    """Live miss: insect-bite recall cannot support inability to name medication."""
    found = find_fidelity_violations(
        [
            {
                "heading": "Subjective",
                "content": (
                    "Takes additional medications (patient unable to specify names)."
                ),
            }
        ],
        [],
        [
            {"role": "DOCTOR", "text": "Did you notice any insect bites?"},
            {"role": "PATIENT", "text": "I don't recall any."},
        ],
    )
    assert [violation.rule for violation in found] == ["patient-state-without-evidence"]


def test_exercise_uncertainty_allows_this_is_inability_wording() -> None:
    """Live false flag: 'this is inability' still refers to exercise ability."""
    found = find_fidelity_violations(
        [
            {
                "heading": "Subjective",
                "content": (
                    "Has not exercised as much, though uncertain whether this is inability "
                    "or lack of motivation."
                ),
            }
        ],
        [],
        [
            {"role": "DOCTOR", "text": "Have you been able to exercise okay?"},
            {"role": "PATIENT", "text": "Not as much. I don't know"},
            {
                "role": "PATIENT",
                "text": "if I haven't been able to or I've just been lazy.",
            },
        ],
    )
    assert found == []


def test_chest_uncertainty_ignores_specifically_as_topic_grammar() -> None:
    """Live false flag: 'specifically' does not create a second clinical topic."""
    found = find_fidelity_violations(
        [
            {
                "heading": "Subjective",
                "content": (
                    "She was unsure whether the sensation is specifically in the chest."
                ),
            }
        ],
        [],
        [
            {"role": "DOCTOR", "text": "Do you feel like your chest is tight?"},
            {"role": "PATIENT", "text": "I don't know if it's the chest"},
            {"role": "PATIENT", "text": "It might be. I'm not sure."},
        ],
    )
    assert found == []


def test_state_topic_accepts_one_local_clinical_stem_among_note_prose() -> None:
    """Live false flags: prose modifiers must not outweigh the shared chest topic."""
    found = find_fidelity_violations(
        [
            {
                "heading": "Subjective",
                "content": (
                    "She was unsure whether chest involvement was present. "
                    "Patient does not recall specific insect bites or seeing marks."
                ),
            }
        ],
        [],
        [
            {"role": "DOCTOR", "text": "Do you feel like your chest is tight?"},
            {
                "role": "PATIENT",
                "text": "I don't know if it's the chest. I'm not sure.",
            },
            {"role": "DOCTOR", "text": "Did you notice any bites?"},
            {"role": "PATIENT", "text": "I don't recall any marks."},
        ],
    )
    assert found == []


def test_preceding_chest_topic_is_not_confused_by_an_unrelated_tail() -> None:
    """Live false flag: a later cut-off question cannot replace the stated chest topic."""
    found = find_fidelity_violations(
        [
            {
                "heading": "Subjective",
                "content": (
                    "When asked about chest tightness, she was uncertain, saying "
                    "\"I don't know if it's the chest, it's just generally feels a little "
                    'difficult".'
                ),
            }
        ],
        [],
        [
            {"role": "DOCTOR", "text": "Do you feel like your chest is tight?"},
            {"role": "PATIENT", "text": "I don't know if it's the chest, it's just"},
            {"role": "PATIENT", "text": "generally feels a little difficult"},
            {"role": "DOCTOR", "text": "Okay, and just a few"},
        ],
    )
    assert found == []


def test_exercise_uncertainty_matches_laziness_to_lazy_patient_words() -> None:
    """Live false flag: note noun 'laziness' matches the patient's adjective 'lazy'."""
    found = find_fidelity_violations(
        [
            {
                "heading": "Subjective",
                "content": (
                    "Patient reports not exercising as much as usual, uncertain whether "
                    "due to inability or laziness."
                ),
            }
        ],
        [],
        [
            {"role": "DOCTOR", "text": "Have you been able to exercise okay?"},
            {"role": "PATIENT", "text": "Not as much. I don't know"},
            {
                "role": "PATIENT",
                "text": "if I haven't been able to or I've just been a bit lazy with it.",
            },
        ],
    )
    assert found == []


def test_m11_prompt_rules_name_record_state_and_verbatim_quotes() -> None:
    """Prompt prevention stays aligned with both deterministic M11 backstops."""
    from agents.summary_agent import MEDICAL_SUMMARY_PROMPT

    lowered = MEDICAL_SUMMARY_PROMPT.lower()
    assert "not clearly captured or not documented" in lowered
    assert "unless the patient's own words" in lowered
    assert "exact contiguous phrase in the transcript" in lowered
    assert "paraphrases and inferred names" in lowered
