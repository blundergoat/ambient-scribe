"""Tests for the automatic row-exception cue lane (M20 Phase 3).

The cue lane fixes the consult-03 complaint: single transcript rows whose
wording contradicts their speaker's mapped role (a doctor question rendered on
a Patient card). These tests pin the decision policy on the exact history rows
from the reported session slice, so the mechanism cannot silently regress the
user-visible failure it was built for - and cannot pass by marking everything
uncertain.
"""

from __future__ import annotations

import json
from pathlib import Path

from api.role_heuristics import (
    ROW_EXCEPTIONS_MAX,
    compute_row_role_exceptions,
    decide_row_role_exception,
)

# The actual consult-03 @83s history rows from baseline run 20260705T095233Z,
# 58-83s - the slice the clinician reported as confidently inverted. The
# run's global mapping was speaker_0 -> PATIENT, speaker_1 -> DOCTOR.
CONSULT03_SLICE_ROWS = [
    {
        "segment_id": "seg-0020",
        "speaker_id": "speaker_0",
        "start": 58.34,
        "end": 58.42,
        "text": "I",
    },
    {
        "segment_id": "seg-0021",
        "speaker_id": "speaker_1",
        "start": 58.48,
        "end": 60.64,
        "text": "really, it just happened okay. And are you",
    },
    {
        "segment_id": "seg-0022",
        "speaker_id": "speaker_0",
        "start": 60.78,
        "end": 66.38,
        "text": "are you able to describe what kind of headache it was? For example, was it throbbing or was it",
    },
    {
        "segment_id": "seg-0023",
        "speaker_id": "speaker_1",
        "start": 67.0,
        "end": 70.84,
        "text": "I guess it's throbbing on that left side. And",
    },
    {
        "segment_id": "seg-0024",
        "speaker_id": "speaker_1",
        "start": 71.16,
        "end": 72.68,
        "text": "is it moving",
    },
    {
        "segment_id": "seg-0025",
        "speaker_id": "speaker_1",
        "start": 72.98,
        "end": 74.90,
        "text": "is it moving anywhere else at",
    },
    {
        "segment_id": "seg-0026",
        "speaker_id": "speaker_1",
        "start": 75.70,
        "end": 77.86,
        "text": "all? No, but it's worse when",
    },
    {
        "segment_id": "seg-0027",
        "speaker_id": "speaker_0",
        "start": 78.64,
        "end": 80.96,
        "text": "Okay. Is that when you move your neck?",
    },
    {
        "segment_id": "seg-0028",
        "speaker_id": "speaker_1",
        "start": 81.98,
        "end": 83.01,
        "text": "",
    },
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
        action, _ = decide_row_role_exception(CONSULT03_SLICE_ROWS[5]["text"], "DOCTOR")
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
            "seg-0022": "DOCTOR",  # 60.78s doctor question shown as Patient
            "seg-0023": "PATIENT",  # 67.00s patient answer shown as Doctor
            "seg-0024": "PATIENT",  # 71.16s smeared fragment
            "seg-0026": "PATIENT",  # 75.70s blended question+answer row
            "seg-0027": "DOCTOR",  # 78.64s doctor question shown as Patient
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
        exceptions = compute_row_role_exceptions(flood_rows, {"speaker_0": "PATIENT"})
        assert len(exceptions) == ROW_EXCEPTIONS_MAX


# The actual consult-08 streaming-engine rows from manual session 176b2bd6
# (2026-07-07): the engine minted `speaker_2` before its voice cache settled,
# role mapping labeled it PATIENT (duplicating speaker_0's role), and five
# doctor fragments rendered on Patient cards. Padding rows keep the realistic
# row-count ordering (speaker_1 43 > speaker_0 23 > speaker_2 12 in the run).
CONSULT08_ORPHAN_ROWS = [
    {
        "segment_id": "seg-0100",
        "speaker_id": "speaker_0",
        "start": 1.28,
        "end": 2.0,
        "text": "Okay. Oh, I can't do",
    },
    {
        "segment_id": "seg-0101",
        "speaker_id": "speaker_2",
        "start": 3.60,
        "end": 3.65,
        "text": "that.",
    },
    {
        "segment_id": "seg-0102",
        "speaker_id": "speaker_2",
        "start": 4.72,
        "end": 4.77,
        "text": "Hello.",
    },
    {
        "segment_id": "seg-0103",
        "speaker_id": "speaker_2",
        "start": 8.72,
        "end": 8.77,
        "text": "Right, so just before",
    },
    {
        "segment_id": "seg-0104",
        "speaker_id": "speaker_2",
        "start": 9.52,
        "end": 11.19,
        "text": "Any further? Can I confirm your name and age",
    },
    {
        "segment_id": "seg-0105",
        "speaker_id": "speaker_2",
        "start": 11.52,
        "end": 11.57,
        "text": "please?",
    },
    {
        "segment_id": "seg-0106",
        "speaker_id": "speaker_2",
        "start": 17.36,
        "end": 17.41,
        "text": "it, okay, and how can I",
    },
    {
        "segment_id": "seg-0107",
        "speaker_id": "speaker_2",
        "start": 18.16,
        "end": 18.21,
        "text": "help you this afternoon?",
    },
    {
        "segment_id": "seg-0108",
        "speaker_id": "speaker_0",
        "start": 22.4,
        "end": 23.0,
        "text": "I've been working",
    },
    {
        "segment_id": "seg-0109",
        "speaker_id": "speaker_0",
        "start": 24.3,
        "end": 25.0,
        "text": "for the past few days",
    },
    {
        "segment_id": "seg-0110",
        "speaker_id": "speaker_0",
        "start": 26.3,
        "end": 28.4,
        "text": "and I've realized that I've got really dry",
    },
    {
        "segment_id": "seg-0111",
        "speaker_id": "speaker_0",
        "start": 28.5,
        "end": 28.9,
        "text": "itchy skin",
    },
    {
        "segment_id": "seg-0112",
        "speaker_id": "speaker_0",
        "start": 43.1,
        "end": 43.9,
        "text": "all over my arms",
    },
    {
        "segment_id": "seg-0113",
        "speaker_id": "speaker_0",
        "start": 45.2,
        "end": 45.9,
        "text": "and my",
    },
    {
        "segment_id": "seg-0114",
        "speaker_id": "speaker_0",
        "start": 46.0,
        "end": 46.5,
        "text": "hands mainly.",
    },
    {
        "segment_id": "seg-0119",
        "speaker_id": "speaker_0",
        "start": 57.8,
        "end": 58.4,
        "text": "I think it started",
    },
    {
        "segment_id": "seg-0115",
        "speaker_id": "speaker_1",
        "start": 47.6,
        "end": 49.0,
        "text": "Okay, and is this something you've had before?",
    },
    {
        "segment_id": "seg-0116",
        "speaker_id": "speaker_1",
        "start": 55.4,
        "end": 56.3,
        "text": "or was it more kind of a gradual thing?",
    },
    {
        "segment_id": "seg-0117",
        "speaker_id": "speaker_1",
        "start": 150.4,
        "end": 152.9,
        "text": "okay and did your symptoms start after you went swimming",
    },
    {
        "segment_id": "seg-0118",
        "speaker_id": "speaker_2",
        "start": 156.32,
        "end": 156.37,
        "text": "yes",
    },
]
CONSULT08_ORPHAN_MAPPING = {
    "speaker_0": "PATIENT",
    "speaker_1": "DOCTOR",
    "speaker_2": "PATIENT",
}


class TestOrphanSpeakerRowLane:
    """M11: cue relabeling for orphan speaker IDs the engine minted pre-settle.

    Orphan = a speaker whose mapped role another, higher-row-count speaker
    already holds. Joins for cue matching may only use the immediate
    chronological neighbor WITH THE SAME speaker ID - the M11 gate's one
    false flip came from joining the patient's "yes" across the doctor's turn.
    """

    def test_orphan_doctor_fragments_relabel_to_doctor(self) -> None:
        """The four recoverable speaker_2 fragments flip to Doctor."""
        exceptions = compute_row_role_exceptions(
            CONSULT08_ORPHAN_ROWS, CONSULT08_ORPHAN_MAPPING
        )
        assert exceptions.get("seg-0103") == "DOCTOR"  # Right, so just before
        assert exceptions.get("seg-0105") == "DOCTOR"  # please?
        assert exceptions.get("seg-0106") == "DOCTOR"  # it, okay, and how can I
        assert exceptions.get("seg-0107") == "DOCTOR"  # help you this afternoon?

    def test_turn_boundary_answer_is_never_joined_across_speakers(self) -> None:
        """The patient's final 'yes' after the doctor's question must not flip."""
        exceptions = compute_row_role_exceptions(
            CONSULT08_ORPHAN_ROWS, CONSULT08_ORPHAN_MAPPING
        )
        assert "seg-0118" not in exceptions

    def test_cross_speaker_neighbor_join_is_refused(self) -> None:
        """'that.' must not inherit cues from the patient's preceding row."""
        exceptions = compute_row_role_exceptions(
            CONSULT08_ORPHAN_ROWS, CONSULT08_ORPHAN_MAPPING
        )
        assert "seg-0101" not in exceptions

    def test_weak_evidence_orphan_row_is_left_alone(self) -> None:
        """'Hello.' has no decisive cue even after same-speaker joins."""
        exceptions = compute_row_role_exceptions(
            CONSULT08_ORPHAN_ROWS, CONSULT08_ORPHAN_MAPPING
        )
        assert "seg-0102" not in exceptions

    def test_m20_lane_keeps_precedence_over_the_orphan_lane(self) -> None:
        """'Any further? Can I confirm...' is already flipped by the M20 cues."""
        exceptions = compute_row_role_exceptions(
            CONSULT08_ORPHAN_ROWS, CONSULT08_ORPHAN_MAPPING
        )
        assert exceptions.get("seg-0104") == "DOCTOR"

    def test_two_speaker_sessions_have_no_orphans(self) -> None:
        """A within-cap dyad gets zero orphan-lane exceptions on short rows."""
        rows = [
            {
                "segment_id": "seg-0200",
                "speaker_id": "speaker_0",
                "start": 1.0,
                "end": 2.0,
                "text": "please?",
            },
            {
                "segment_id": "seg-0201",
                "speaker_id": "speaker_1",
                "start": 3.0,
                "end": 4.0,
                "text": "My name's Isa and I'm 26.",
            },
        ]
        mapping = {"speaker_0": "PATIENT", "speaker_1": "DOCTOR"}
        assert compute_row_role_exceptions(rows, mapping) == {}

    def test_clinician_corrected_orphan_rows_stay_untouched(self) -> None:
        """A user row correction on an orphan row outranks the cue lane."""
        rows = [dict(row) for row in CONSULT08_ORPHAN_ROWS]
        for row in rows:
            if row["segment_id"] == "seg-0105":
                row["role_source"] = "user_row"
        exceptions = compute_row_role_exceptions(rows, CONSULT08_ORPHAN_MAPPING)
        assert "seg-0105" not in exceptions


# --- M05: consult 1.2 live-lane role targets (official TextGrid wording) ---


def test_patient_offer_question_is_not_flipped_to_doctor() -> None:
    """Consult 1.2: a patient OFFERING detail stays Patient.

    "do you want to know more about it?" is the patient asking whether the
    clinician wants more history - an offer, not clinical interviewing. The
    single `you_question` cue flipped it to Doctor on the live card.
    """
    action, role = decide_row_role_exception(
        "do you want to know more about it?", "PATIENT"
    )

    assert (action, role) == ("keep", "PATIENT")


def test_patient_offer_question_with_stutter_is_not_flipped() -> None:
    """The official ground-truth form stutters ("want to, to know") and must
    receive the same protection as the clean form."""
    action, role = decide_row_role_exception(
        "I mean, do you want to, to know more about it?", "PATIENT"
    )

    assert (action, role) == ("keep", "PATIENT")


def test_doctor_discourse_ive_got_to_say_stays_doctor() -> None:
    """Consult 1.2: "I've got to say ..." is clinician discourse, not a complaint.

    The presenting-complaint cue treated "got to say" like "got a rash" and
    pushed the doctor's remark toward Patient evidence.
    """
    action, role = decide_row_role_exception(
        "I've got to say, at this stage this sound quality is not great.",
        "DOCTOR",
    )

    assert (action, role) == ("keep", "DOCTOR")


def test_real_presenting_complaints_still_count_as_patient_evidence() -> None:
    """Guarding "got to say" must not weaken genuine complaint wording."""
    # A genuine complaint on a doctor-mapped row still contradicts the label.
    action, role = decide_row_role_exception(
        "I've got a really itchy rash on my arms and it just started.",
        "DOCTOR",
    )

    assert action != "keep"


# --- M05: consult 3.1 role no-regression/diagnostic fixture ---

_DAY3C01_ROLE_FIXTURE_PATH = (
    Path(__file__).resolve().parents[1]
    / "fixtures"
    / "scribe"
    / "role-noregression-day3c01.json"
)


def _day3c01_role_fixture() -> dict:
    """Load the frozen consult 3.1 role pin; a missing file fails loudly.

    Returns:
        Parsed fixture dict; never empty because the freeze asserted every
        anchor id resolves before the file was written.
    """
    with open(_DAY3C01_ROLE_FIXTURE_PATH, encoding="utf-8") as fixture_file:
        return json.load(fixture_file)


class TestDay3C01RoleNoRegression:
    """Consult 3.1 stays honestly messy.

    The retained M02-acceptance replay carries the visit's identity debt:
    three speaker IDs, eight phantom-speaker merges, 0.806 final confidence,
    and sub-5-word overlap rows holding the other speaker's words. The five
    phase-1 narrow guards do NOT solve that upstream debt; these pins make
    sure no later cue edit hides it behind confident labels or moves the
    emergency tail.
    """

    def test_live_cue_lanes_leave_the_retained_tail_alone(self) -> None:
        """Zero cue exceptions on the retained emergency tail at HEAD."""
        fixture = _day3c01_role_fixture()

        exceptions = compute_row_role_exceptions(
            fixture["live_tail_rows"], fixture["terminal_mapping"]
        )

        assert exceptions == {}

    def test_emergency_tail_and_interjections_keep_recorded_ownership(self) -> None:
        """Doctor disposition rows stay DOCTOR; interjections stay PATIENT."""
        fixture = _day3c01_role_fixture()
        rows_by_id = {row["segment_id"]: row for row in fixture["live_tail_rows"]}

        # The complete emergency disposition is clinician speech end to end.
        for segment_id in fixture["anchors"]["emergency_tail_doctor"]:
            assert rows_by_id[segment_id]["role"] == "DOCTOR", segment_id
        # The patient's real interjections keep their own card.
        for segment_id in fixture["anchors"]["patient_interjections"]:
            assert rows_by_id[segment_id]["role"] == "PATIENT", segment_id

    def test_identity_debt_is_documented_not_silently_relabelled(self) -> None:
        """Overlap-debt rows are seam territory the cue lane must keep leaving alone."""
        fixture = _day3c01_role_fixture()
        rows_by_id = {row["segment_id"]: row for row in fixture["live_tail_rows"]}

        # The diagnostic numbers this fixture exists to preserve.
        assert fixture["quality_pins"]["phantom_speaker_merges"] == 8
        assert fixture["quality_pins"]["final_confidence"] == 0.806
        assert len(fixture["terminal_mapping"]) == 3

        # Each debt row is sub-5-word seam smear: kept, never confidently flipped.
        for segment_id in fixture["anchors"]["identity_overlap_debt"]:
            debt_row = rows_by_id[segment_id]
            action, _ = decide_row_role_exception(
                str(debt_row["text"]), str(debt_row["role"])
            )
            assert action == "keep", segment_id
