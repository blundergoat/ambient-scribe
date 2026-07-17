"""Row-level label state through mappings and the summary merge contract.

Automatic row exceptions (the cue lane) are derived state: a new speaker
mapping outdates them for re-judgment, and the labels they own must survive
the browser's summary POST. The merge contract (M21) also guarantees a
summary POST never shrinks stored history - in both the in-memory store and
the SQLite backend.
"""

from __future__ import annotations

import pytest

from api.summary_request import SummaryRequest, build_summary_context
from session import SessionStore
from storage import SqliteBackend

SESSION_ID = "row-merge-session"


@pytest.fixture(params=["memory", "sqlite"])
def backend(request, tmp_path):
    """Both storage backends must honor the same row and merge contracts."""
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


class TestAutoRowRolePrecedence:
    """Automatic row exceptions are derived state below the speaker mapping."""

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
        """auto_row labels outrank the stale roles the browser posted back."""
        self.seed_with_tail(backend)
        backend.apply_role_mapping(
            SESSION_ID, {"speaker_0": "DOCTOR", "speaker_1": "DOCTOR"}
        )
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
        # Cue-lane exception untouched - the browser may have missed it.
        assert rows[1]["role"] == "PATIENT"
        assert rows[1]["role_source"] == "auto_row"
        # Mapping-derived rows accept the browser's resolved labels.
        assert rows[0]["role"] == "PATIENT"
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
