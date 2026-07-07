"""
Tests for completed-session quality records.

These cover the operator/eval record emitted after a browser recording ends.
They keep transcript text out of assertions and focus on the metrics the dev
State tab and fixture trend loop need to verify transcription health.
"""

from __future__ import annotations

import json
import time
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from api.streaming_session import StreamState, _finalize_after_disconnect
from session_quality import (
    TranscriptionQualityStats,
    build_session_quality_record,
    persist_session_quality_record,
)


def test_quality_record_summarizes_session_without_transcript_text(tmp_path):
    """A finalized visit becomes one compact metrics row for logs and evals."""
    quality_stats = TranscriptionQualityStats()
    quality_stats.record_window(32000)
    quality_stats.record_window(96000)
    quality_stats.record_segment_flow(emitted_segments=3, held_segments=1)
    quality_stats.record_speaker_anchor_remaps(2)
    quality_stats.record_phantom_speaker_merges(1)

    audio_session = SimpleNamespace(
        buffer=SimpleNamespace(duration_seconds=4.5),
        started_at=datetime(2026, 7, 5, 0, 0, tzinfo=UTC).timestamp(),
        accumulated_transcript=[SimpleNamespace(), SimpleNamespace(), SimpleNamespace()],
        quality_stats=quality_stats,
        chunk_count=5,
    )
    # The socket saw fewer chunks than the session (a reconnect happened);
    # the record must report the session-wide count.
    stream_state = SimpleNamespace(
        chunk_count=2,
        chunk_inference_ms=[100, 300],
        chunk_total_ms=[120, 360],
        error_count=1,
    )
    role_state = SimpleNamespace(
        mapping_history=[
            {"spk_0": "DOCTOR", "spk_1": "PATIENT"},
            {"spk_0": "PATIENT", "spk_1": "DOCTOR"},
        ],
        running_confidence=0.8764,
        suppressed_flip_count=3,
        truncation_events=2,
    )

    record = build_session_quality_record(
        session_id="quality-session",
        audio_session=audio_session,
        stream_state=stream_state,
        role_state=role_state,
        finalized_at=datetime(2026, 7, 5, 0, 0, 7, tzinfo=UTC),
    )
    record_path = persist_session_quality_record(record, base_directory=tmp_path)

    assert record["type"] == "quality"
    assert record["chunks"] == 5
    assert record["emitted_segments"] == 3
    assert record["held_segments"] == 1
    assert record["phantom_speaker_merges"] == 1
    assert record["window_seconds"]["p50"] == 2.0
    assert record["chunk_inference_ms"]["p95"] == 290.0
    assert record["role_flips_accepted"] == 1
    assert record["role_flips_suppressed"] == 3
    assert record["role_truncation_events"] == 2
    assert record["final_confidence"] == 0.876
    assert "DOCTOR" not in json.dumps(record)
    assert json.loads(record_path.read_text(encoding="utf-8").strip()) == record


@pytest.mark.asyncio
async def test_finalize_emits_logs_and_persists_quality_record(monkeypatch, caplog):
    """Stopping a browser session emits one quality event before finalized."""
    import api.streaming_session as streaming_session

    published_events: list[tuple[str, dict, int | None]] = []
    persisted_records: list[dict] = []

    class ImmediateLoop:
        """Runs finalize synchronously like the test user ended the socket."""

        async def run_in_executor(self, executor, func, *args):
            """Call executor work inline so the test sees the final state."""
            return func(*args)

    class SegmentStore:
        """Captures transcript writes the finalized session would expose to history."""

        def __init__(self) -> None:
            """Prepare empty captured history state for the test session."""
            self.segments = []
            self.applied_mapping = None

        def replace_segments(self, session_id, segments):
            """Store final transcript rows; empty means no text was captured."""
            self.segments = segments

        def apply_role_mapping(self, session_id, mapping):
            """Record visible role labels applied to stored transcript rows."""
            self.applied_mapping = mapping

        def get_segments(self, session_id):
            """Return captured rows for the finalize row-exception re-judgment."""
            return list(self.segments)

        def set_auto_row_roles(self, session_id, row_roles):
            """Accept automatic row exceptions; this test has none to apply."""
            self.auto_row_roles = row_roles

    class FakeAudioSession:
        """Minimal finalized audio session with quality counters."""

        def __init__(self) -> None:
            """Seed counters as if the user streamed two chunks and stopped."""
            self.buffer = SimpleNamespace(duration_seconds=5.0)
            self.started_at = time.time() - 6
            self.accumulated_transcript = []
            self.chunk_count = 2
            self.quality_stats = TranscriptionQualityStats()
            self.quality_stats.record_window(32000)
            self.quality_stats.record_segment_flow(emitted_segments=1, held_segments=0)

        def finalize(self):
            """Return no held tail so the test focuses on quality finalization."""
            return []

    async def fake_publish(topic, data, event_id=None):
        """Capture Mercure payloads the browser would receive."""
        published_events.append((topic, data, event_id))
        return True

    async def fake_enqueue(session_id, segments):
        """No role work is queued because the fake session has no tail."""

    def fake_persist(record):
        """Capture the exact quality row that would be appended to JSONL."""
        persisted_records.append(record)
        return "var/quality/sessions.jsonl"

    role_state = SimpleNamespace(
        current_mapping={"spk_0": "DOCTOR"},
        mapping_history=[{"spk_0": "DOCTOR"}],
        running_confidence=0.9,
        truncation_events=1,
    )
    services = SimpleNamespace(
        executor=None,
        sessions=SegmentStore(),
        publish_to_mercure=fake_publish,
        enqueue_role_inference=fake_enqueue,
        mercure_event_ids={},
    )
    state = StreamState(chunk_count=2)
    state.chunk_inference_ms.extend([111, 222])
    state.chunk_total_ms.extend([120, 260])

    monkeypatch.setattr(
        streaming_session, "get_or_create_state", lambda session_id: role_state
    )
    monkeypatch.setattr(streaming_session, "persist_session_quality_record", fake_persist)

    with caplog.at_level("INFO"):
        await _finalize_after_disconnect(
            SimpleNamespace(),
            "quality-finalize-session",
            FakeAudioSession(),
            services,
            ImmediateLoop(),
            state,
        )

    quality_events = [event for event in published_events if event[1]["type"] == "quality"]
    finalized_events = [
        event for event in published_events if event[1]["type"] == "finalized"
    ]
    quality_logs = [
        record for record in caplog.records if record.getMessage().startswith("session.quality")
    ]

    assert persisted_records
    assert quality_events
    assert finalized_events
    assert quality_events[0][2] < finalized_events[0][2]
    assert quality_events[0][1]["quality"] == persisted_records[0]
    assert quality_events[0][1]["quality"]["chunks"] == 2
    assert quality_events[0][1]["quality"]["final_confidence"] == 0.9
    assert quality_events[0][1]["quality"]["role_truncation_events"] == 1
    assert quality_logs


class TestQualityTailRecord:
    """Post-finalize role churn must be observable without touching `session.quality`."""

    def make_role_state(self, *, accepted_mappings, suppressed, snapshot):
        """Build a role-state stand-in with a mapping history and snapshot."""
        from tools.assign_roles import RoleMappingState

        state = RoleMappingState()
        state.mapping_history = accepted_mappings
        state.suppressed_flip_count = suppressed
        state.quality_flip_snapshot = snapshot
        return state

    def test_no_snapshot_means_no_tail_record(self):
        """Sessions that never finalized a quality record report no tail."""
        from session_quality import build_quality_tail_record

        state = self.make_role_state(
            accepted_mappings=[{"speaker_0": "DOCTOR"}],
            suppressed=2,
            snapshot=None,
        )
        assert build_quality_tail_record("s1", state) is None

    def test_quiet_tail_emits_nothing(self):
        """Unchanged counters after finalize produce no artifact row."""
        from session_quality import build_quality_tail_record

        state = self.make_role_state(
            accepted_mappings=[
                {"speaker_0": "DOCTOR", "speaker_1": "PATIENT"},
            ],
            suppressed=3,
            snapshot={"role_flips_accepted": 0, "role_flips_suppressed": 3},
        )
        assert build_quality_tail_record("s1", state) is None

    def test_tail_flips_report_the_delta_since_the_quality_record(self):
        """Flips landing after finalize become an additive quality_tail row."""
        from session_quality import build_quality_tail_record

        # One flip in history (DOCTOR->PATIENT for speaker_0) after a snapshot
        # taken at zero accepted / one suppressed; one more suppression landed.
        state = self.make_role_state(
            accepted_mappings=[
                {"speaker_0": "DOCTOR", "speaker_1": "PATIENT"},
                {"speaker_0": "PATIENT", "speaker_1": "DOCTOR"},
            ],
            suppressed=2,
            snapshot={"role_flips_accepted": 0, "role_flips_suppressed": 1},
        )

        record = build_quality_tail_record(
            "s1", state, recorded_at=datetime(2026, 7, 6, tzinfo=UTC)
        )

        assert record is not None
        assert record["type"] == "quality_tail"
        assert record["session_id"] == "s1"
        assert record["tail_role_flips_accepted"] == 1
        assert record["tail_role_flips_suppressed"] == 1
        assert record["final_role_flips_accepted"] == 1
        assert record["final_role_flips_suppressed"] == 2
        # The additive record must never masquerade as the quality record.
        assert record["type"] != "quality"

    def test_finalize_stamps_the_flip_snapshot_on_role_state(self, tmp_path, monkeypatch):
        """The quality emit leaves the baseline the tail delta is computed from."""
        from tools.assign_roles import RoleMappingState

        monkeypatch.setenv("SESSION_QUALITY_DIR", str(tmp_path))
        role_state = RoleMappingState()
        role_state.mapping_history = [
            {"speaker_0": "DOCTOR", "speaker_1": "PATIENT"},
        ]
        role_state.suppressed_flip_count = 3

        record = build_session_quality_record(
            session_id="snapshot-session",
            audio_session=SimpleNamespace(
                quality_stats=TranscriptionQualityStats(),
                buffer=SimpleNamespace(duration_seconds=1.0),
                started_at=time.time(),
                accumulated_transcript=[],
            ),
            stream_state=StreamState(),
            role_state=role_state,
        )
        # The streaming session stamps the snapshot right after building.
        role_state.quality_flip_snapshot = {
            "role_flips_accepted": record["role_flips_accepted"],
            "role_flips_suppressed": record["role_flips_suppressed"],
        }

        assert role_state.quality_flip_snapshot == {
            "role_flips_accepted": 0,
            "role_flips_suppressed": 3,
        }
