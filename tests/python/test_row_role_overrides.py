"""Row-scoped role corrections must survive every rewrite of session history.

A clinician who corrects one wrong transcript row (e.g. a doctor question shown
as Patient) must keep that correction through later agent role updates, the
finalize history rebuild, and the summary flow's row replacement - in both the
in-memory store and the SQLite backend.
"""

from __future__ import annotations

import pytest

from api.summary_request import SummaryRequest, build_summary_context
from session import SessionStore
from storage import SqliteBackend

SESSION_ID = "row-override-session"


@pytest.fixture(params=["memory", "sqlite"])
def backend(request, tmp_path):
    """Both storage backends must honor the same row-correction contract."""
    if request.param == "memory":
        return SessionStore()
    return SqliteBackend(db_path=str(tmp_path / "sessions.db"))


def seed_rows(backend) -> None:
    """Store three identified rows the way the live emission path does."""
    for index, speaker_id in enumerate(["speaker_0", "speaker_1", "speaker_0"]):
        backend.append_segment(
            SESSION_ID,
            {
                "speaker_id": speaker_id,
                "text": f"row {index + 1}",
                "start": float(index),
                "end": float(index) + 0.9,
                "is_interim": False,
                "segment_id": f"seg-{index + 1:04d}",
            },
        )


class TestRowCorrectionPersistence:
    """The correction follows one row through every history rewrite."""

    def test_row_correction_survives_a_later_speaker_mapping(self, backend) -> None:
        """A whole-speaker relabel must not undo the clinician's row fix."""
        seed_rows(backend)
        assert backend.set_row_role(SESSION_ID, "seg-0002", "DOCTOR") is True

        # A later agent update relabels speaker_1 rows the other way.
        backend.apply_role_mapping(SESSION_ID, {"speaker_0": "DOCTOR", "speaker_1": "PATIENT"})

        rows = backend.get_segments(SESSION_ID)
        assert rows[1]["role"] == "DOCTOR"
        assert rows[1]["role_source"] == "user_row"
        # Uncorrected rows still follow the speaker mapping.
        assert rows[0]["role"] == "DOCTOR"
        assert "role_source" not in rows[0]

    def test_row_correction_survives_finalize_replace_and_reapply(self, backend) -> None:
        """Pressing Stop rebuilds history from raw rows; corrections must hold."""
        seed_rows(backend)
        backend.set_row_role(SESSION_ID, "seg-0002", "DOCTOR")

        # The finalize path replaces history with raw role-less rows...
        backend.replace_segments(
            SESSION_ID,
            [
                {
                    "speaker_id": speaker_id,
                    "text": f"row {index + 1}",
                    "start": float(index),
                    "end": float(index) + 0.9,
                    "is_interim": False,
                    "segment_id": f"seg-{index + 1:04d}",
                }
                for index, speaker_id in enumerate(["speaker_0", "speaker_1", "speaker_0"])
            ],
        )
        # ...then re-applies the current speaker mapping.
        backend.apply_role_mapping(SESSION_ID, {"speaker_0": "DOCTOR", "speaker_1": "PATIENT"})

        rows = backend.get_segments(SESSION_ID)
        assert rows[1]["role"] == "DOCTOR"
        assert rows[1]["role_source"] == "user_row"
        assert rows[2]["role"] == "DOCTOR"

    def test_row_correction_rejects_unknown_targets(self, backend) -> None:
        """Corrections cannot attach to sessions or rows the user cannot see."""
        # No transcript stored for the session yet.
        assert backend.set_row_role("unknown-session", "seg-0001", "DOCTOR") is False

        seed_rows(backend)
        # An empty row ID cannot identify which visible line was corrected.
        assert backend.set_row_role(SESSION_ID, "", "DOCTOR") is False

    def test_cleanup_forgets_row_corrections(self, backend) -> None:
        """A finished visit's corrections must not label a later recording."""
        seed_rows(backend)
        backend.set_row_role(SESSION_ID, "seg-0001", "PATIENT")
        backend.cleanup(SESSION_ID)

        # The same session ID starts fresh (e.g. an eval reusing an ID).
        seed_rows(backend)
        rows = backend.get_segments(SESSION_ID)
        assert "role" not in rows[0]
        assert "role_source" not in rows[0]


class TestAutoRowRolePrecedence:
    """Automatic row exceptions are derived state below user corrections."""

    def test_auto_roles_apply_and_are_outdated_by_the_next_mapping(self, backend) -> None:
        """A new speaker mapping clears stale automatic row labels for re-judgment."""
        seed_rows(backend)
        backend.apply_role_mapping(SESSION_ID, {"speaker_0": "DOCTOR", "speaker_1": "DOCTOR"})
        backend.set_auto_row_roles(SESSION_ID, {"seg-0002": "PATIENT"})

        rows = backend.get_segments(SESSION_ID)
        assert rows[1]["role"] == "PATIENT"
        assert rows[1]["role_source"] == "auto_row"

        # The next mapping application resets the row to its mapped role; the
        # cue lane re-judges immediately afterwards in the live flow.
        backend.apply_role_mapping(SESSION_ID, {"speaker_1": "DOCTOR"})
        rows = backend.get_segments(SESSION_ID)
        assert rows[1]["role"] == "DOCTOR"
        assert "role_source" not in rows[1] or rows[1].get("role_source") is None

    def test_auto_roles_never_touch_user_corrected_rows(self, backend) -> None:
        """The clinician's row correction outranks the automatic cue lane."""
        seed_rows(backend)
        backend.set_row_role(SESSION_ID, "seg-0002", "DOCTOR")

        backend.set_auto_row_roles(SESSION_ID, {"seg-0002": "UNKNOWN", "seg-0001": "PATIENT"})

        rows = backend.get_segments(SESSION_ID)
        # The user's row keeps their label and marker.
        assert rows[1]["role"] == "DOCTOR"
        assert rows[1]["role_source"] == "user_row"
        # Other rows accept the automatic judgment.
        assert rows[0]["role"] == "PATIENT"
        assert rows[0]["role_source"] == "auto_row"


class TestRowIdentityThroughSummaryFlow:
    """The full user journey: live labels -> correction -> finalize -> summary."""

    def test_row_correction_survives_live_finalize_and_summary_replacement(
        self, backend
    ) -> None:
        """One corrected row keeps its label across every stage the user sees."""
        # Live visit: rows emit, then an agent update labels both speakers.
        seed_rows(backend)
        backend.apply_role_mapping(SESSION_ID, {"speaker_0": "DOCTOR", "speaker_1": "DOCTOR"})

        # The clinician corrects the middle row to Patient.
        backend.set_row_role(SESSION_ID, "seg-0002", "PATIENT")

        # A later conflicting agent update relabels the whole speaker.
        backend.apply_role_mapping(SESSION_ID, {"speaker_1": "DOCTOR"})
        assert backend.get_segments(SESSION_ID)[1]["role"] == "PATIENT"

        # Summarise: the browser posts its visible rows (row-preserving, with
        # row IDs); the summary flow replaces server history with them.
        summary_request = SummaryRequest.model_validate(
            {
                "segments": [
                    {
                        "segment_id": f"seg-{index + 1:04d}",
                        "speaker_id": speaker_id,
                        # The browser reports the roles it displayed, including
                        # a stale value for the corrected row to prove the
                        # server-side correction store outranks it.
                        "role": "DOCTOR",
                        "text": f"row {index + 1}",
                        "start": float(index),
                        "end": float(index) + 0.9,
                    }
                    for index, speaker_id in enumerate(
                        ["speaker_0", "speaker_1", "speaker_0"]
                    )
                ]
            }
        )
        context = build_summary_context(SESSION_ID, summary_request, backend)

        # The summary context and stored history both keep the corrected row.
        stored_rows = backend.get_segments(SESSION_ID)
        assert stored_rows[1]["segment_id"] == "seg-0002"
        assert stored_rows[1]["role"] == "PATIENT"
        assert stored_rows[1]["role_source"] == "user_row"
        assert len(stored_rows) == 3
        assert context.source == "browser_visible_segments"
        # The note's transcript input uses the corrected role, not the stale
        # one the browser posted - the clinician's fix reaches the summary.
        assert "[PATIENT] row 2" in context.transcript
        assert context.selected_segments[1]["role"] == "PATIENT"


class TestSummaryMergeContract:
    """A summary POST must never shrink stored history (M21 merge contract)."""

    def seed_with_tail(self, backend) -> None:
        """Store five rows: three the browser will see, one blank, one tail."""
        seed_rows(backend)
        backend.append_segment(
            SESSION_ID,
            {
                "speaker_id": "speaker_0",
                "text": "",
                "start": 3.0,
                "end": 3.4,
                "is_interim": False,
                "segment_id": "seg-0004",
            },
        )
        backend.append_segment(
            SESSION_ID,
            {
                "speaker_id": "speaker_1",
                "text": "the finalize flush row",
                "start": 4.0,
                "end": 4.9,
                "is_interim": False,
                "segment_id": "seg-0005",
            },
        )

    def browser_post_of_first_three(self) -> SummaryRequest:
        """The browser saw only the first three rows (race dropped the tail)."""
        return SummaryRequest.model_validate(
            {
                "segments": [
                    {
                        "segment_id": f"seg-{index + 1:04d}",
                        "speaker_id": speaker_id,
                        "role": "PATIENT",
                        "text": f"row {index + 1}",
                        "start": float(index),
                        "end": float(index) + 0.9,
                    }
                    for index, speaker_id in enumerate(
                        ["speaker_0", "speaker_1", "speaker_0"]
                    )
                ]
            }
        )

    def test_summary_post_keeps_tail_rows_the_browser_missed(self, backend) -> None:
        """The flush row a raced browser never received still reaches the note."""
        self.seed_with_tail(backend)
        backend.apply_role_mapping(
            SESSION_ID, {"speaker_0": "PATIENT", "speaker_1": "DOCTOR"}
        )

        context = build_summary_context(
            SESSION_ID, self.browser_post_of_first_three(), backend
        )

        stored_rows = backend.get_segments(SESSION_ID)
        assert len(stored_rows) == 5
        # The tail row survives in history AND in the note's transcript input.
        assert stored_rows[4]["text"] == "the finalize flush row"
        assert "[DOCTOR] the finalize flush row" in context.transcript
        # The blank row stays stored but never reaches the note text.
        assert stored_rows[3]["text"] == ""
        assert "seg-0004" not in context.transcript

    def test_browser_roles_update_only_unowned_rows(self, backend) -> None:
        """user_row and auto_row labels outrank the roles the browser posted."""
        self.seed_with_tail(backend)
        backend.apply_role_mapping(
            SESSION_ID, {"speaker_0": "DOCTOR", "speaker_1": "DOCTOR"}
        )
        backend.set_row_role(SESSION_ID, "seg-0001", "DOCTOR")
        backend.set_auto_row_roles(SESSION_ID, {"seg-0002": "PATIENT"})

        result = backend.merge_browser_segments(
            SESSION_ID,
            [
                {"segment_id": "seg-0001", "role": "PATIENT", "text": "row 1"},
                {"segment_id": "seg-0002", "role": "DOCTOR", "text": "row 2"},
                {"segment_id": "seg-0003", "role": "PATIENT", "text": "row 3"},
            ],
        )

        rows = backend.get_segments(SESSION_ID)
        assert result == {"matched": 3, "unknown": 0, "restored": False}
        # Clinician correction untouched by the browser's stale label.
        assert rows[0]["role"] == "DOCTOR"
        assert rows[0]["role_source"] == "user_row"
        # Cue-lane exception untouched - the browser may have missed it.
        assert rows[1]["role"] == "PATIENT"
        assert rows[1]["role_source"] == "auto_row"
        # Mapping-derived row accepts the browser's resolved label.
        assert rows[2]["role"] == "PATIENT"

    def test_unknown_rows_never_append_to_a_populated_history(self, backend) -> None:
        """Rows the server never emitted are counted and skipped, not stored."""
        self.seed_with_tail(backend)

        result = backend.merge_browser_segments(
            SESSION_ID,
            [
                {"segment_id": "seg-9999", "role": "DOCTOR", "text": "phantom row"},
                {"segment_id": "", "role": "DOCTOR", "text": "identity-less row"},
                {"segment_id": "seg-0003", "role": "DOCTOR", "text": "row 3"},
            ],
        )

        assert result == {"matched": 1, "unknown": 2, "restored": False}
        stored_rows = backend.get_segments(SESSION_ID)
        assert len(stored_rows) == 5
        assert all(row.get("text") != "phantom row" for row in stored_rows)

    def test_empty_store_falls_back_to_browser_restore(self, backend) -> None:
        """A vanished server session still restores history from the browser."""
        result = backend.merge_browser_segments(
            "restore-session",
            [
                {
                    "segment_id": "seg-0001",
                    "speaker_id": "speaker_0",
                    "role": "PATIENT",
                    "text": "restored row",
                    "start": 0.0,
                    "end": 0.9,
                }
            ],
        )

        assert result == {"matched": 0, "unknown": 0, "restored": True}
        rows = backend.get_segments("restore-session")
        assert len(rows) == 1
        assert rows[0]["text"] == "restored row"
        assert rows[0]["role"] == "PATIENT"

    def test_merge_is_idempotent_across_repeated_summary_posts(self, backend) -> None:
        """Summarising twice converges to the same stored state."""
        self.seed_with_tail(backend)
        backend.apply_role_mapping(
            SESSION_ID, {"speaker_0": "PATIENT", "speaker_1": "DOCTOR"}
        )

        build_summary_context(SESSION_ID, self.browser_post_of_first_three(), backend)
        first_state = backend.get_segments(SESSION_ID)
        build_summary_context(SESSION_ID, self.browser_post_of_first_three(), backend)
        second_state = backend.get_segments(SESSION_ID)

        assert first_state == second_state
        assert len(second_state) == 5
