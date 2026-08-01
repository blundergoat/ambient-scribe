"""
Tests for per-session evidence bundles.

These cover the developer evidence written as a visit progresses so a manual test
consultation can be analysed offline without a capture script running alongside
the browser. Evidence is a diagnostic side channel, so the behaviour that matters
most here is that it never breaks the clinician-facing request that triggered it.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from session_evidence import (
    EVIDENCE_SCHEMA_VERSION,
    evidence_enabled,
    runtime_identity,
    session_evidence_dir,
    write_session_evidence,
)


def test_bundle_keeps_transcript_wording_for_accuracy_analysis(tmp_path):
    """Full row wording is stored: scoring a note against the audio needs it."""
    written = write_session_evidence(
        "session-abc",
        "live-history",
        {
            "source": "live_segments",
            "segment_count": 2,
            "segments": [
                {"segment_id": "seg-0001", "text": "like Lauratidine or"},
                {"segment_id": "seg-0002", "text": "Puritan, which can help"},
            ],
        },
        base_directory=tmp_path,
    )

    assert written is not None
    record = json.loads(written.read_text(encoding="utf-8"))
    assert record["schema_version"] == EVIDENCE_SCHEMA_VERSION
    assert record["session_id"] == "session-abc"
    assert record["artifact"] == "live-history"
    assert record["written_at"].endswith("Z")
    assert record["segments"][1]["text"] == "Puritan, which can help"


def test_each_session_gets_its_own_directory(tmp_path):
    """Two visits never overwrite each other's evidence."""
    write_session_evidence("first", "summary", {"a": 1}, base_directory=tmp_path)
    write_session_evidence("second", "summary", {"a": 2}, base_directory=tmp_path)

    first = json.loads((tmp_path / "first" / "summary.json").read_text())
    second = json.loads((tmp_path / "second" / "summary.json").read_text())
    assert (first["a"], second["a"]) == (1, 2)


def test_rewriting_an_artifact_replaces_it_without_leaving_staging_files(tmp_path):
    """A regenerated note replaces the old one and leaves no partial file behind."""
    write_session_evidence("s", "summary", {"attempt": 1}, base_directory=tmp_path)
    write_session_evidence("s", "summary", {"attempt": 2}, base_directory=tmp_path)

    directory = tmp_path / "s"
    assert [path.name for path in sorted(directory.iterdir())] == ["summary.json"]
    assert json.loads((directory / "summary.json").read_text())["attempt"] == 2


def test_disabled_evidence_writes_nothing(tmp_path, monkeypatch):
    """An operator can turn the whole side channel off."""
    monkeypatch.setenv("SESSION_EVIDENCE_ENABLED", "0")

    assert evidence_enabled() is False
    assert (
        write_session_evidence("s", "live-history", {}, base_directory=tmp_path) is None
    )
    assert not (tmp_path / "s").exists()


@pytest.mark.parametrize("flag", ["1", "true", "yes", "", "anything-else"])
def test_evidence_is_on_unless_explicitly_disabled(flag, monkeypatch):
    """Default-on: a proof-of-concept run should not need extra setup to be analysable."""
    monkeypatch.setenv("SESSION_EVIDENCE_ENABLED", flag)
    assert evidence_enabled() is True


def test_missing_session_id_writes_nothing(tmp_path):
    """An unkeyed bundle cannot be matched to a visit, so it is not written."""
    assert (
        write_session_evidence("", "live-history", {"a": 1}, base_directory=tmp_path)
        is None
    )


def test_write_failure_does_not_raise_into_the_request(tmp_path):
    """A full or read-only disk must not turn a finalize or summary into a 500."""
    blocker = tmp_path / "blocked"
    blocker.write_text("not a directory", encoding="utf-8")

    assert (
        write_session_evidence("s", "live-history", {"a": 1}, base_directory=blocker)
        is None
    )


def test_unserialisable_payload_does_not_raise(tmp_path):
    """An unexpected object in a payload degrades to text rather than failing the visit."""
    written = write_session_evidence(
        "s", "live-history", {"obj": object()}, base_directory=tmp_path
    )

    assert written is not None
    assert "object object" in json.loads(written.read_text())["obj"]


def test_evidence_dir_prefers_explicit_then_environment_then_default(
    tmp_path, monkeypatch
):
    """Tests pass a directory; operators set one; otherwise the gitignored default applies."""
    assert session_evidence_dir("s", base_directory=tmp_path) == tmp_path / "s"

    monkeypatch.setenv("SESSION_EVIDENCE_DIR", str(tmp_path / "from-env"))
    assert session_evidence_dir("s") == tmp_path / "from-env" / "s"

    monkeypatch.setenv("SESSION_EVIDENCE_DIR", "")
    assert session_evidence_dir("s") == Path("var/session-evidence/s")


def test_runtime_identity_reports_decoder_settings_and_hides_secret_values(monkeypatch):
    """An accuracy claim needs the running config; it never needs a credential."""
    # Deliberately not shaped like a real AWS key: a committed file carrying the
    # `AKIA` prefix trips credential scanners forever, fake value or not.
    sentinel = "sentinel-value-that-must-never-be-serialised"
    monkeypatch.setenv("NEMO_CORRECTION_REDIARIZATION", "0")
    monkeypatch.setenv("ROLE_AGENT_MODEL_PROVIDER", "bedrock")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", sentinel)

    identity = runtime_identity()

    assert identity["settings"]["NEMO_CORRECTION_REDIARIZATION"] == "0"
    assert identity["settings"]["ROLE_AGENT_MODEL_PROVIDER"] == "bedrock"
    assert identity["secrets_present"]["AWS_ACCESS_KEY_ID"] is True
    assert sentinel not in json.dumps(identity)


def test_runtime_identity_omits_unset_settings(monkeypatch):
    """Absent configuration reads as absent rather than as an empty string."""
    monkeypatch.delenv("NEMO_SPEAKER_CAP", raising=False)
    assert "NEMO_SPEAKER_CAP" not in runtime_identity()["settings"]


@pytest.mark.asyncio
async def test_finalizing_a_visit_saves_the_live_lane_without_operator_action(
    tmp_path, monkeypatch
):
    """Stopping a recording writes the rows by itself - no capture script involved."""
    from types import SimpleNamespace

    from api import streaming_session
    from api.streaming_session import StreamState, _finalize_after_disconnect
    from session_quality import TranscriptionQualityStats

    monkeypatch.setenv("SESSION_EVIDENCE_DIR", str(tmp_path))

    class SegmentStore:
        """Minimal history store holding the rows the visit produced."""

        def __init__(self) -> None:
            """Seed the two rows this visit is meant to preserve."""
            self.segments = [
                {
                    "segment_id": "seg-0001",
                    "text": "like Lauratidine or",
                    "confidence": 0.69,
                },
                {
                    "segment_id": "seg-0002",
                    "text": "Puritan, which can help",
                    "confidence": 0.78,
                },
            ]

        def replace_segments(self, session_id, segments):
            """Accept the finalized rows; this visit has no held tail."""
            self.segments = segments or self.segments

        def apply_role_mapping(self, session_id, mapping):
            """Accept the settled mapping applied to stored rows."""

        def get_segments(self, session_id):
            """Return the rows evidence should preserve."""
            return list(self.segments)

        def set_auto_row_roles(self, session_id, row_roles):
            """Accept automatic row exceptions; none apply here."""

    class FakeAudioSession:
        """Finalized audio session carrying the quality counters."""

        def __init__(self) -> None:
            """Seed counters as if one chunk streamed and the user stopped."""
            self.buffer = SimpleNamespace(duration_seconds=5.0)
            self.started_at = 0.0
            self.accumulated_transcript = []
            self.chunk_count = 1
            self.quality_stats = TranscriptionQualityStats()
            self.quality_stats.record_window(16000)
            self.quality_stats.record_segment_flow(emitted_segments=2, held_segments=0)

        def finalize(self):
            """Return no held tail so finalize proceeds straight to evidence."""
            return []

    class ImmediateLoop:
        """Run the executor work inline so the test needs no real loop."""

        async def run_in_executor(self, _executor, func, *args):
            """Call the work directly and return its result."""
            return func(*args)

    async def fake_publish(topic, data, event_id=None):
        """Swallow Mercure publishes; this test asserts on disk, not the browser."""
        return True

    async def fake_enqueue(session_id, segments):
        """No role work is queued for a visit with no tail."""

    role_state = SimpleNamespace(
        current_mapping={"speaker_0": "DOCTOR", "speaker_1": "PATIENT"},
        mapping_history=[],
        running_confidence=0.98,
        truncation_events=0,
    )
    services = SimpleNamespace(
        executor=None,
        sessions=SegmentStore(),
        publish_to_mercure=fake_publish,
        enqueue_role_inference=fake_enqueue,
        mercure_event_ids={},
    )

    monkeypatch.setattr(
        streaming_session, "get_or_create_state", lambda session_id: role_state
    )
    monkeypatch.setattr(
        streaming_session, "persist_session_quality_record", lambda record: None
    )

    await _finalize_after_disconnect(
        SimpleNamespace(),
        "evidence-session",
        FakeAudioSession(),
        services,
        ImmediateLoop(),
        StreamState(chunk_count=1),
    )

    bundle = tmp_path / "evidence-session" / "live-history.json"
    assert bundle.exists()

    record = json.loads(bundle.read_text(encoding="utf-8"))
    assert record["segment_count"] == 2
    assert record["segments"][1]["text"] == "Puritan, which can help"
    assert record["role_mapping"] == {"speaker_0": "DOCTOR", "speaker_1": "PATIENT"}
    # The quality record travels with the rows so one file answers "was this run healthy".
    assert record["quality"]["final_confidence"] == 0.98
    assert "settings" in record["runtime"]
