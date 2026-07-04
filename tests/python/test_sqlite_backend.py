"""
Tests for the SqliteBackend persistent storage.

Uses a temp file for the database so tests are isolated and leave no artifacts.
"""

from __future__ import annotations

import pytest

from storage import SqliteBackend


@pytest.fixture
def db_path(tmp_path):
    """Return a temp path for the SQLite database."""
    return str(tmp_path / "test_sessions.db")


@pytest.fixture
def backend(db_path):
    """Create a fresh SqliteBackend for each test."""
    b = SqliteBackend(db_path=db_path)
    yield b
    b.close()


def _make_segment(
    speaker_id: str = "spk_0",
    text: str = "Hello",
    start: float = 0.0,
    end: float = 1.0,
    is_interim: bool = False,
    role: str | None = None,
) -> dict:
    """Helper to build a segment dict."""
    seg: dict = {
        "speaker_id": speaker_id,
        "text": text,
        "start": start,
        "end": end,
        "is_interim": is_interim,
    }
    if role is not None:
        seg["role"] = role
    return seg


class TestAppendAndGetSegments:
    """Test append_segment + get_segments roundtrip."""

    def test_append_single_segment(self, backend):
        seg = _make_segment(text="Good morning")
        backend.append_segment("s1", seg)

        result = backend.get_segments("s1")
        assert len(result) == 1
        assert result[0]["text"] == "Good morning"
        assert result[0]["speaker_id"] == "spk_0"
        assert result[0]["start"] == 0.0
        assert result[0]["end"] == 1.0
        assert result[0]["is_interim"] is False

    def test_append_multiple_segments_preserves_order(self, backend):
        backend.append_segment("s1", _make_segment(text="First", start=0.0, end=1.0))
        backend.append_segment("s1", _make_segment(text="Second", start=1.0, end=2.0))
        backend.append_segment("s1", _make_segment(text="Third", start=2.0, end=3.0))

        result = backend.get_segments("s1")
        assert len(result) == 3
        assert [s["text"] for s in result] == ["First", "Second", "Third"]

    def test_get_segments_empty_session(self, backend):
        result = backend.get_segments("nonexistent")
        assert result == []

    def test_segments_isolated_between_sessions(self, backend):
        backend.append_segment("s1", _make_segment(text="Session 1"))
        backend.append_segment("s2", _make_segment(text="Session 2"))

        assert len(backend.get_segments("s1")) == 1
        assert backend.get_segments("s1")[0]["text"] == "Session 1"
        assert len(backend.get_segments("s2")) == 1
        assert backend.get_segments("s2")[0]["text"] == "Session 2"

    def test_role_preserved_on_append(self, backend):
        seg = _make_segment(text="With role", role="DOCTOR")
        backend.append_segment("s1", seg)

        result = backend.get_segments("s1")
        assert result[0]["role"] == "DOCTOR"

    def test_no_role_key_when_none(self, backend):
        seg = _make_segment(text="No role")
        backend.append_segment("s1", seg)

        result = backend.get_segments("s1")
        assert "role" not in result[0]


class TestReplaceSegments:
    """Test replace_segments replaces (not appends)."""

    def test_replace_overwrites_existing(self, backend):
        backend.append_segment("s1", _make_segment(text="Original 1"))
        backend.append_segment("s1", _make_segment(text="Original 2"))

        new_segments = [
            _make_segment(text="Replaced A", start=0.0, end=1.5),
            _make_segment(text="Replaced B", start=1.5, end=3.0),
            _make_segment(text="Replaced C", start=3.0, end=4.5),
        ]
        backend.replace_segments("s1", new_segments)

        result = backend.get_segments("s1")
        assert len(result) == 3
        assert [s["text"] for s in result] == ["Replaced A", "Replaced B", "Replaced C"]

    def test_replace_with_empty_list(self, backend):
        backend.append_segment("s1", _make_segment(text="Will be cleared"))
        backend.replace_segments("s1", [])

        result = backend.get_segments("s1")
        assert result == []

    def test_replace_creates_session_if_needed(self, backend):
        segments = [_make_segment(text="New session")]
        backend.replace_segments("new_session", segments)

        result = backend.get_segments("new_session")
        assert len(result) == 1
        assert result[0]["text"] == "New session"


class TestApplyRoleMapping:
    """Test apply_role_mapping updates role column."""

    def test_maps_speaker_to_role(self, backend):
        backend.append_segment(
            "s1", _make_segment(speaker_id="spk_0", text="Doctor says")
        )
        backend.append_segment(
            "s1", _make_segment(speaker_id="spk_1", text="Patient says")
        )

        backend.apply_role_mapping("s1", {"spk_0": "DOCTOR", "spk_1": "PATIENT"})

        result = backend.get_segments("s1")
        assert result[0]["role"] == "DOCTOR"
        assert result[1]["role"] == "PATIENT"

    def test_partial_mapping_only_updates_matched(self, backend):
        backend.append_segment("s1", _make_segment(speaker_id="spk_0", text="First"))
        backend.append_segment("s1", _make_segment(speaker_id="spk_1", text="Second"))

        backend.apply_role_mapping("s1", {"spk_0": "DOCTOR"})

        result = backend.get_segments("s1")
        assert result[0]["role"] == "DOCTOR"
        assert "role" not in result[1]

    def test_mapping_overwrites_previous_role(self, backend):
        backend.append_segment(
            "s1", _make_segment(speaker_id="spk_0", text="Hello", role="PATIENT")
        )

        backend.apply_role_mapping("s1", {"spk_0": "DOCTOR"})

        result = backend.get_segments("s1")
        assert result[0]["role"] == "DOCTOR"

    def test_mapping_on_empty_session_is_noop(self, backend):
        # Should not raise
        backend.apply_role_mapping("nonexistent", {"spk_0": "DOCTOR"})


class TestGetTranscriptText:
    """Test get_transcript_text returns formatted text."""

    def test_basic_transcript(self, backend):
        backend.append_segment(
            "s1", _make_segment(speaker_id="spk_0", text="Good morning")
        )
        backend.append_segment(
            "s1", _make_segment(speaker_id="spk_1", text="Hi doctor")
        )

        result = backend.get_transcript_text("s1")
        assert result == "[spk_0] Good morning\n[spk_1] Hi doctor"

    def test_uses_role_when_available(self, backend):
        backend.append_segment(
            "s1", _make_segment(speaker_id="spk_0", text="How are you", role="DOCTOR")
        )
        backend.append_segment(
            "s1", _make_segment(speaker_id="spk_1", text="Not great")
        )

        result = backend.get_transcript_text("s1")
        assert "[DOCTOR] How are you" in result
        assert "[spk_1] Not great" in result

    def test_truncation_with_ellipsis(self, backend):
        # Create enough text to exceed max_chars
        for i in range(100):
            backend.append_segment(
                "s1",
                _make_segment(
                    text=f"Segment number {i} with some extra padding text",
                    start=float(i),
                    end=float(i + 1),
                ),
            )

        result = backend.get_transcript_text("s1", max_chars=200)
        assert "\n...\n" in result
        assert len(result) <= 200

    def test_tiny_truncation_budget(self, backend):
        backend.append_segment("s1", _make_segment(text="A very long segment"))

        result = backend.get_transcript_text("s1", max_chars=4)

        assert len(result) <= 4

    def test_empty_session(self, backend):
        result = backend.get_transcript_text("nonexistent")
        assert result == ""

    def test_short_transcript_no_truncation(self, backend):
        backend.append_segment("s1", _make_segment(text="Short"))

        result = backend.get_transcript_text("s1", max_chars=3500)
        assert "\n...\n" not in result


class TestCleanup:
    """Test cleanup removes session data."""

    def test_cleanup_removes_session_and_segments(self, backend):
        backend.append_segment("s1", _make_segment(text="Will be cleaned"))
        backend.append_segment("s1", _make_segment(text="Also cleaned"))

        assert backend.session_count == 1
        assert len(backend.get_segments("s1")) == 2

        backend.cleanup("s1")

        assert backend.session_count == 0
        assert backend.get_segments("s1") == []

    def test_cleanup_nonexistent_session_is_noop(self, backend):
        # Should not raise
        backend.cleanup("nonexistent")

    def test_cleanup_does_not_affect_other_sessions(self, backend):
        backend.append_segment("s1", _make_segment(text="Keep this"))
        backend.append_segment("s2", _make_segment(text="Remove this"))

        backend.cleanup("s2")

        assert backend.session_count == 1
        assert len(backend.get_segments("s1")) == 1
        assert backend.get_segments("s2") == []


class TestSessionCount:
    """Test session_count property."""

    def test_zero_initially(self, backend):
        assert backend.session_count == 0

    def test_increments_on_new_sessions(self, backend):
        backend.append_segment("s1", _make_segment())
        assert backend.session_count == 1

        backend.append_segment("s2", _make_segment())
        assert backend.session_count == 2

    def test_same_session_does_not_double_count(self, backend):
        backend.append_segment("s1", _make_segment(text="First"))
        backend.append_segment("s1", _make_segment(text="Second"))
        assert backend.session_count == 1

    def test_decrements_on_cleanup(self, backend):
        backend.append_segment("s1", _make_segment())
        backend.append_segment("s2", _make_segment())
        assert backend.session_count == 2

        backend.cleanup("s1")
        assert backend.session_count == 1


class TestPersistence:
    """Test that data survives closing and reopening the database."""

    def test_data_persists_across_connections(self, db_path):
        backend1 = SqliteBackend(db_path=db_path)
        backend1.append_segment("s1", _make_segment(text="Persistent data"))
        backend1.apply_role_mapping("s1", {"spk_0": "DOCTOR"})
        backend1.close()

        backend2 = SqliteBackend(db_path=db_path)
        result = backend2.get_segments("s1")
        assert len(result) == 1
        assert result[0]["text"] == "Persistent data"
        assert result[0]["role"] == "DOCTOR"
        assert backend2.session_count == 1
        backend2.close()
