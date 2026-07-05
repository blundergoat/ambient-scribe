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
        assert context.stored_segments[1]["role"] == "PATIENT"
