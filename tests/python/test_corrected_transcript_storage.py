"""Corrected transcript storage stays separate from live preview history."""

from __future__ import annotations

import pytest

from api.summary_request import SummaryRequest, build_summary_context
from session import SessionStore
from storage import SqliteBackend

SESSION_ID = "corrected-transcript-session"


@pytest.fixture(params=["memory", "sqlite"])
def backend(request, tmp_path):
    """Both storage backends must expose the same corrected transcript contract."""
    if request.param == "memory":
        yield SessionStore()
        return

    sqlite_backend = SqliteBackend(db_path=str(tmp_path / "sessions.db"))
    yield sqlite_backend
    sqlite_backend.close()


def _live_row(text: str = "live typo") -> dict:
    """Build one browser-visible live transcript row."""
    return {
        "speaker_id": "speaker_0",
        "role": "PATIENT",
        "text": text,
        "start": 1.0,
        "end": 2.0,
        "is_interim": False,
        "segment_id": "live-0001",
    }


def _corrected_row(text: str = "corrected transcript") -> dict:
    """Build one post-visit corrected transcript row."""
    return {
        "speaker_id": "speaker_0",
        "role": "DOCTOR",
        "text": text,
        "start": 1.0,
        "end": 2.0,
        "is_interim": False,
        "segment_id": "corrected-0001",
        "source": "second_pass",
        "source_model": "nvidia/parakeet-tdt-0.6b-v3",
    }


class TestCorrectedTranscriptStorage:
    """Corrected rows are an opt-in artifact, not a live history rewrite."""

    def test_corrected_segments_do_not_replace_live_segments(self, backend) -> None:
        """Live history and corrected history can coexist for one session."""
        backend.append_segment(SESSION_ID, _live_row())
        backend.replace_corrected_segments(SESSION_ID, [_corrected_row()])

        live_rows = backend.get_segments(SESSION_ID)
        corrected_rows = backend.get_corrected_segments(SESSION_ID)

        assert [row["text"] for row in live_rows] == ["live typo"]
        assert [row["text"] for row in corrected_rows] == ["corrected transcript"]
        assert corrected_rows[0]["source"] == "second_pass"
        assert corrected_rows[0]["source_model"] == "nvidia/parakeet-tdt-0.6b-v3"
        assert "[PATIENT] live typo" in backend.get_transcript_text(SESSION_ID)
        assert "[DOCTOR] corrected transcript" in backend.get_corrected_transcript_text(
            SESSION_ID
        )

    def test_replacing_corrected_segments_can_clear_only_corrected_rows(
        self, backend
    ) -> None:
        """Clearing corrected rows leaves the live preview transcript intact."""
        backend.append_segment(SESSION_ID, _live_row())
        backend.replace_corrected_segments(SESSION_ID, [_corrected_row()])
        backend.replace_corrected_segments(SESSION_ID, [])

        assert backend.get_segments(SESSION_ID)[0]["text"] == "live typo"
        assert backend.get_corrected_segments(SESSION_ID) == []
        assert backend.get_corrected_transcript_text(SESSION_ID) == ""

    def test_cleanup_removes_live_and_corrected_rows(self, backend) -> None:
        """A finished session cannot leak corrected transcript into a later visit."""
        backend.append_segment(SESSION_ID, _live_row())
        backend.replace_corrected_segments(SESSION_ID, [_corrected_row()])

        backend.cleanup(SESSION_ID)

        assert backend.get_segments(SESSION_ID) == []
        assert backend.get_corrected_segments(SESSION_ID) == []

    def test_summary_prefers_corrected_rows_when_they_exist(self, backend) -> None:
        """Corrected transcript text wins even if the browser posts live rows."""
        backend.append_segment(SESSION_ID, _live_row("browser visible live text"))
        backend.replace_corrected_segments(
            SESSION_ID, [_corrected_row("high accuracy corrected text")]
        )

        summary_request = SummaryRequest.model_validate(
            {"segments": [_live_row("browser visible live text")]}
        )
        context = build_summary_context(SESSION_ID, summary_request, backend)

        assert context.source == "corrected_segments"
        assert context.stored_segments[0]["text"] == "high accuracy corrected text"
        assert "[DOCTOR] high accuracy corrected text" in context.transcript
        assert "browser visible live text" not in context.transcript
        assert backend.get_segments(SESSION_ID)[0]["text"] == "browser visible live text"


def test_sqlite_corrected_segments_persist_across_connections(tmp_path) -> None:
    """SQLite stores corrected transcript artifacts across process restarts."""
    db_path = str(tmp_path / "sessions.db")
    backend1 = SqliteBackend(db_path=db_path)
    backend1.append_segment(SESSION_ID, _live_row())
    backend1.replace_corrected_segments(SESSION_ID, [_corrected_row()])
    backend1.close()

    backend2 = SqliteBackend(db_path=db_path)
    corrected_rows = backend2.get_corrected_segments(SESSION_ID)

    assert len(corrected_rows) == 1
    assert corrected_rows[0]["text"] == "corrected transcript"
    assert backend2.get_segments(SESSION_ID)[0]["text"] == "live typo"
    backend2.close()
