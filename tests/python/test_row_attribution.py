"""Tests for the automatic row-exception cue lane (M20 Phase 3).

The cue lane fixes the consult-03 complaint: single transcript rows whose
wording contradicts their speaker's mapped role (a doctor question rendered on
a Patient card). These tests pin the decision policy on the exact history rows
from the reported session slice, so the mechanism cannot silently regress the
user-visible failure it was built for - and cannot pass by marking everything
uncertain.
"""

from __future__ import annotations

from api.role_heuristics import (
    ROW_EXCEPTIONS_MAX,
    compute_row_role_exceptions,
    decide_row_role_exception,
)

# The actual consult-03 @83s history rows from baseline run 20260705T095233Z,
# 58-83s - the slice the clinician reported as confidently inverted. The
# run's global mapping was speaker_0 -> PATIENT, speaker_1 -> DOCTOR.
CONSULT03_SLICE_ROWS = [
    {"segment_id": "seg-0020", "speaker_id": "speaker_0", "start": 58.34, "end": 58.42,
     "text": "I"},
    {"segment_id": "seg-0021", "speaker_id": "speaker_1", "start": 58.48, "end": 60.64,
     "text": "really, it just happened okay. And are you"},
    {"segment_id": "seg-0022", "speaker_id": "speaker_0", "start": 60.78, "end": 66.38,
     "text": "are you able to describe what kind of headache it was? For example, was it throbbing or was it"},
    {"segment_id": "seg-0023", "speaker_id": "speaker_1", "start": 67.0, "end": 70.84,
     "text": "I guess it's throbbing on that left side. And"},
    {"segment_id": "seg-0024", "speaker_id": "speaker_1", "start": 71.16, "end": 72.68,
     "text": "is it moving"},
    {"segment_id": "seg-0025", "speaker_id": "speaker_1", "start": 72.98, "end": 74.90,
     "text": "is it moving anywhere else at"},
    {"segment_id": "seg-0026", "speaker_id": "speaker_1", "start": 75.70, "end": 77.86,
     "text": "all? No, but it's worse when"},
    {"segment_id": "seg-0027", "speaker_id": "speaker_0", "start": 78.64, "end": 80.96,
     "text": "Okay. Is that when you move your neck?"},
    {"segment_id": "seg-0028", "speaker_id": "speaker_1", "start": 81.98, "end": 83.01,
     "text": ""},
]
CONSULT03_MAPPING = {"speaker_0": "PATIENT", "speaker_1": "DOCTOR"}


class TestDecideRowRoleException:
    """Per-row decisions on real transcript wording."""

    def test_doctor_question_on_a_patient_row_flips_to_doctor(self) -> None:
        """The reported 01:00 row: a clinician question shown as Patient."""
        action, role = decide_row_role_exception(
            CONSULT03_SLICE_ROWS[2]["text"], "PATIENT"
        )
        assert (action, role) == ("flip", "DOCTOR")

    def test_first_person_symptoms_on_a_doctor_row_flip_to_patient(self) -> None:
        """The 01:07 row: the patient's own symptom shown as Doctor."""
        action, role = decide_row_role_exception(
            CONSULT03_SLICE_ROWS[3]["text"], "DOCTOR"
        )
        assert (action, role) == ("flip", "PATIENT")

    def test_question_answer_blend_goes_uncertain_not_confident(self) -> None:
        """One row holding 'all? No, but...' is two speakers: uncertain."""
        action, role = decide_row_role_exception(
            CONSULT03_SLICE_ROWS[6]["text"], "DOCTOR"
        )
        assert (action, role) == ("uncertain", "UNKNOWN")

    def test_mixed_cue_blend_goes_uncertain(self) -> None:
        """A patient answer running into the next question is a blend."""
        action, role = decide_row_role_exception(
            CONSULT03_SLICE_ROWS[1]["text"], "DOCTOR"
        )
        assert (action, role) == ("uncertain", "UNKNOWN")

    def test_agreeing_question_row_is_kept(self) -> None:
        """A doctor-sounding row already labeled Doctor needs no exception."""
        action, _ = decide_row_role_exception(
            CONSULT03_SLICE_ROWS[5]["text"], "DOCTOR"
        )
        assert action == "keep"

    def test_short_rows_are_left_alone(self) -> None:
        """Sub-5-word rows are seam-smear territory; no confident action."""
        action, _ = decide_row_role_exception("is it moving", "DOCTOR")
        assert action == "keep"

    def test_doctor_quoting_symptoms_is_not_mistaken_for_the_patient(self) -> None:
        """'was it throbbing' inside a question is quoting, not reporting."""
        action, _ = decide_row_role_exception(
            "with that pain, have you noticed any other symptoms at all? "
            "So, for example, let's focus firstly on",
            "DOCTOR",
        )
        assert action == "keep"

    def test_question_with_token_answer_stays_with_the_asker(self) -> None:
        """'Do you smoke at all, Kim? No. No.' is time-owned by the question."""
        action, _ = decide_row_role_exception(
            "Do you smoke at all, Kim? No. No.", "DOCTOR"
        )
        assert action == "keep"

    def test_doctor_clinical_reasoning_in_first_person_is_kept(self) -> None:
        """Doctors say 'I think...' constantly; that is not patient evidence."""
        action, _ = decide_row_role_exception(
            "I don't think there's a lot to worry about. I think you probably "
            "have a bit of a viral, what we say,",
            "DOCTOR",
        )
        assert action == "keep"

    def test_patient_echoing_the_doctor_goes_uncertain_not_flipped(self) -> None:
        """'...your tummy like the lower part of my tummy' is an echo blend."""
        action, role = decide_row_role_exception(
            "pain in your tummy like the lower part of my tummy okay", "PATIENT"
        )
        assert (action, role) == ("uncertain", "UNKNOWN")

    def test_second_person_statement_without_question_goes_uncertain(self) -> None:
        """Advice fragments smeared into patient rows must not flip confidently."""
        action, role = decide_row_role_exception(
            "your symptoms don't get better. Right.", "PATIENT"
        )
        assert (action, role) == ("uncertain", "UNKNOWN")


class TestConsult03SliceRegression:
    """The user-reported 01:00-01:21 slice, judged on actual history rows.

    The plan's gate: at least 3 of the 5 target rows must become CORRECT -
    marking every row uncertain cannot pass.
    """

    def test_target_rows_are_corrected_not_blanket_uncertain(self) -> None:
        """>=3/5 target rows flip to the reference-correct role."""
        exceptions = compute_row_role_exceptions(
            CONSULT03_SLICE_ROWS, CONSULT03_MAPPING
        )

        # The five target rows and their TextGrid-expected roles.
        target_expectations = {
            "seg-0022": "DOCTOR",   # 60.78s doctor question shown as Patient
            "seg-0023": "PATIENT",  # 67.00s patient answer shown as Doctor
            "seg-0024": "PATIENT",  # 71.16s smeared fragment
            "seg-0026": "PATIENT",  # 75.70s blended question+answer row
            "seg-0027": "DOCTOR",   # 78.64s doctor question shown as Patient
        }
        corrected_rows = sum(
            1
            for segment_id, expected_role in target_expectations.items()
            if exceptions.get(segment_id) == expected_role
        )
        assert corrected_rows >= 3

        # The gate cannot be passed by blanket uncertainty.
        confident_exceptions = [
            role for role in exceptions.values() if role in ("DOCTOR", "PATIENT")
        ]
        assert len(confident_exceptions) >= 3

        # The blended question+answer row is explicitly uncertain, not wrong.
        assert exceptions["seg-0026"] == "UNKNOWN"

    def test_user_corrected_rows_are_never_rejudged(self) -> None:
        """A clinician's own row correction outranks the cue lane."""
        rows = [dict(row) for row in CONSULT03_SLICE_ROWS]
        rows[2]["role_source"] = "user_row"

        exceptions = compute_row_role_exceptions(rows, CONSULT03_MAPPING)
        assert "seg-0022" not in exceptions

    def test_rows_without_identity_or_mapping_are_skipped(self) -> None:
        """Old histories and unmapped sessions produce no exceptions."""
        unidentified_rows = [
            {"speaker_id": "speaker_0", "text": CONSULT03_SLICE_ROWS[2]["text"]}
        ]
        assert compute_row_role_exceptions(unidentified_rows, CONSULT03_MAPPING) == {}
        assert compute_row_role_exceptions(CONSULT03_SLICE_ROWS, {}) == {}

    def test_exception_count_is_bounded(self) -> None:
        """Pathological sessions cannot flood the roles payload."""
        flood_rows = [
            {
                "segment_id": f"seg-{index:04d}",
                "speaker_id": "speaker_0",
                "text": "are you able to describe what kind of headache it was?",
            }
            for index in range(ROW_EXCEPTIONS_MAX + 20)
        ]
        exceptions = compute_row_role_exceptions(
            flood_rows, {"speaker_0": "PATIENT"}
        )
        assert len(exceptions) == ROW_EXCEPTIONS_MAX
