"""
Tests for the live role inference queue.
"""

import asyncio
import threading

import pytest

import api.server as api_server
import tools.assign_roles as role_tools
from api.role_inference_queue import (
    ROLE_EVIDENCE_MAX_TEXT_CHARS,
    ROLE_EVIDENCE_OPENING_UTTERANCES,
    ROLE_EVIDENCE_RECENT_UTTERANCES,
    ROLE_EVIDENCE_REPRESENTATIVE_UTTERANCES,
    _build_bounded_role_evidence,
)
from session_quality import TranscriptionQualityStats


@pytest.fixture(autouse=True)
def clear_inference_state():
    """Keep module-level role inference state isolated across tests."""
    api_server.sessions._sessions.clear()
    api_server.lifecycle.clear()
    api_server._inference_queues.clear()
    api_server._inference_workers.clear()
    api_server._mercure_event_ids.clear()
    role_tools._session_states.clear()
    role_tools._pending_role_segments.clear()
    yield
    api_server.sessions._sessions.clear()
    api_server.lifecycle.clear()
    api_server._inference_queues.clear()
    api_server._inference_workers.clear()
    api_server._mercure_event_ids.clear()
    role_tools._session_states.clear()
    role_tools._pending_role_segments.clear()


class TestInferenceQueue:
    """Tests for sequential queue processing and live role publication."""

    @pytest.mark.asyncio
    async def test_queue_publishes_role_updates_and_applies_mapping(
        self,
        sample_segments,
        monkeypatch,
    ):
        published_events = []
        session_id = "role-queue-session"

        for segment in sample_segments:
            api_server.sessions.append_segment(session_id, dict(segment))

        async def fake_publish(topic, data, event_id=None):
            published_events.append((topic, data))

        monkeypatch.setattr(api_server, "publish_to_mercure", fake_publish)
        monkeypatch.setattr(
            api_server,
            "_run_role_inference",
            lambda session_id, segments, role_evidence: {
                "mapping": {"spk_0": "DOCTOR", "spk_1": "PATIENT"},
                "confidence": 0.88,
                "reasoning": "Opening clinical question identifies the doctor.",
            },
        )

        await api_server.enqueue_role_inference(session_id, sample_segments)
        worker = api_server._inference_workers[session_id]
        await api_server.close_role_inference(session_id)
        await asyncio.wait_for(worker, timeout=1.0)

        assert len(published_events) == 1
        topic, event = published_events[0]
        assert topic == f"scribe/session/{session_id}/roles"
        assert event["type"] == "role_update"
        assert event["mapping"] == {"spk_0": "DOCTOR", "spk_1": "PATIENT"}
        assert event["confidence"] == 0.88

        stored_segments = api_server.sessions.get_segments(session_id)
        assert stored_segments[0]["role"] == "DOCTOR"
        assert stored_segments[1]["role"] == "PATIENT"

    @pytest.mark.asyncio
    async def test_queue_processes_batches_sequentially_for_one_session(
        self,
        sample_segments,
        monkeypatch,
    ):
        session_id = "sequential-session"
        invocations = []
        started = threading.Event()
        release = threading.Event()

        for segment in sample_segments:
            api_server.sessions.append_segment(session_id, dict(segment))

        async def fake_publish(topic, data, event_id=None):
            return None

        def fake_run_role_inference(session_id, segments, role_evidence):
            invocations.append([segment["text"] for segment in segments])
            if len(invocations) == 1:
                started.set()
                assert release.wait(timeout=1.0)

            return {
                "mapping": {"spk_0": "DOCTOR", "spk_1": "PATIENT"},
                "confidence": 0.91,
                "reasoning": "Stable mapping.",
            }

        monkeypatch.setattr(api_server, "publish_to_mercure", fake_publish)
        monkeypatch.setattr(api_server, "_run_role_inference", fake_run_role_inference)

        await api_server.enqueue_role_inference(session_id, [sample_segments[0]])
        worker = api_server._inference_workers[session_id]

        loop = asyncio.get_running_loop()
        started_ok = await loop.run_in_executor(None, started.wait, 1.0)
        assert started_ok is True

        await api_server.enqueue_role_inference(session_id, [sample_segments[1]])
        assert len(invocations) == 1

        release.set()
        await api_server.close_role_inference(session_id)
        await asyncio.wait_for(worker, timeout=1.0)

        assert len(invocations) == 2
        assert invocations[0] == [sample_segments[0]["text"]]
        assert invocations[1] == [sample_segments[1]["text"]]

    @pytest.mark.asyncio
    async def test_queue_skips_single_speaker_sessions(self, monkeypatch):
        session_id = "single-speaker-session"
        single_speaker_segments = [
            {
                "speaker_id": "spk_0",
                "text": "I've had a cough for three days.",
                "start": 0.0,
                "end": 2.0,
                "is_interim": False,
            }
        ]
        published_events = []

        for segment in single_speaker_segments:
            api_server.sessions.append_segment(session_id, dict(segment))

        async def fake_publish(topic, data, event_id=None):
            published_events.append((topic, data))

        def fail_if_called(*args, **kwargs):
            raise AssertionError(
                "role inference should be skipped for single-speaker sessions"
            )

        monkeypatch.setattr(api_server, "publish_to_mercure", fake_publish)
        monkeypatch.setattr(api_server, "_run_role_inference", fail_if_called)

        await api_server.enqueue_role_inference(session_id, single_speaker_segments)
        worker = api_server._inference_workers[session_id]
        await api_server.close_role_inference(session_id)
        await asyncio.wait_for(worker, timeout=1.0)

        assert published_events == []
        assert "role" not in api_server.sessions.get_segments(session_id)[0]


class _StubAudioSession:
    """Minimal live audio session exposing the identity counters the badge needs.

    The stability payload reads only `quality_stats`, so tests can register
    this on the lifecycle instead of a full GPU-backed TranscriptionSession.
    """

    def __init__(self, windows: int, anchor_remaps: int, phantom_merges: int) -> None:
        self.quality_stats = TranscriptionQualityStats()
        # Each recorded window stands for one ~5s chunk the clinician sent.
        for _ in range(windows):
            self.quality_stats.record_window(32000)
        self.quality_stats.record_speaker_anchor_remaps(anchor_remaps)
        self.quality_stats.record_phantom_speaker_merges(phantom_merges)


class TestRoleStabilityPayload:
    """The roles topic must tell the browser when speaker identity is churning.

    M20 Phase 0 measured confident mappings (0.86-0.89) over sessions with
    30-53% wrong clean rows; these tests pin the additive `role_stability`
    field that lets the badge refuse a green "Roles identified" in that state.
    """

    async def _publish_one_role_update(
        self, session_id, sample_segments, monkeypatch
    ) -> list[dict]:
        """Drive one role update through the queue and return published events."""
        published_events = []

        for segment in sample_segments:
            api_server.sessions.append_segment(session_id, dict(segment))

        async def fake_publish(topic, data, event_id=None):
            published_events.append(data)

        monkeypatch.setattr(api_server, "publish_to_mercure", fake_publish)
        monkeypatch.setattr(
            api_server,
            "_run_role_inference",
            lambda session_id, segments, role_evidence: {
                "mapping": {"spk_0": "DOCTOR", "spk_1": "PATIENT"},
                "confidence": 0.9,
                "reasoning": "Confident mapping.",
            },
        )

        await api_server.enqueue_role_inference(session_id, sample_segments)
        worker = api_server._inference_workers[session_id]
        await api_server.close_role_inference(session_id)
        await asyncio.wait_for(worker, timeout=1.0)
        return published_events

    @pytest.mark.asyncio
    async def test_unstable_identity_counters_mark_the_update_unstable(
        self, sample_segments, monkeypatch
    ):
        """Churning speaker IDs must reach the browser even at high confidence."""
        session_id = "stability-unstable-session"
        # The consult-03 baseline shape: ~1 remap per window plus phantom merges.
        await api_server.lifecycle.register(
            session_id, _StubAudioSession(windows=18, anchor_remaps=17, phantom_merges=6)
        )

        events = await self._publish_one_role_update(
            session_id, sample_segments, monkeypatch
        )

        assert len(events) == 1
        stability = events[0]["role_stability"]
        assert stability["level"] == "unstable"
        assert stability["anchor_remaps"] == 17
        assert stability["phantom_merges"] == 6
        assert stability["windows"] == 18
        assert stability["anchor_remap_rate"] == round(17 / 18, 3)
        assert stability["pending_contrary_mapping"] is False

    @pytest.mark.asyncio
    async def test_quiet_identity_counters_mark_the_update_stable(
        self, sample_segments, monkeypatch
    ):
        """A clean-identity session may show the green confident badge."""
        session_id = "stability-stable-session"
        # One correction across ten windows stays under the stability gate.
        await api_server.lifecycle.register(
            session_id, _StubAudioSession(windows=10, anchor_remaps=1, phantom_merges=0)
        )

        events = await self._publish_one_role_update(
            session_id, sample_segments, monkeypatch
        )

        assert len(events) == 1
        assert events[0]["role_stability"]["level"] == "stable"

    @pytest.mark.asyncio
    async def test_missing_audio_session_omits_the_stability_field(
        self, sample_segments, monkeypatch
    ):
        """Post-disconnect drains keep the browser's last stability state."""
        session_id = "stability-drained-session"

        events = await self._publish_one_role_update(
            session_id, sample_segments, monkeypatch
        )

        assert len(events) == 1
        # No live audio session means no fresh identity evidence to report.
        assert "role_stability" not in events[0]

    @pytest.mark.asyncio
    async def test_role_update_carries_row_exceptions_and_applies_them(
        self, sample_segments, monkeypatch
    ):
        """A row contradicting its mapped role reaches the browser as an exception."""
        session_id = "row-exception-session"
        # spk_1 will be mapped PATIENT, but this row is a clinician question.
        api_server.sessions.append_segment(
            session_id,
            {
                "speaker_id": "spk_1",
                "text": "are you able to describe what kind of headache it was?",
                "start": 10.0,
                "end": 14.0,
                "segment_id": "seg-0009",
            },
        )

        events = await self._publish_one_role_update(
            session_id, sample_segments, monkeypatch
        )

        assert len(events) == 1
        # The browser learns exactly which row disagrees with the mapping.
        assert events[0]["row_exceptions"] == {"seg-0009": "DOCTOR"}

        # Stored history shows the same row-level judgment.
        contradicting_row = next(
            row
            for row in api_server.sessions.get_segments(session_id)
            if row.get("segment_id") == "seg-0009"
        )
        assert contradicting_row["role"] == "DOCTOR"
        assert contradicting_row["role_source"] == "auto_row"


class TestBoundedRoleEvidence:
    """Tests for capped role-agent input built from visible transcript rows."""

    def test_role_evidence_is_capped_per_speaker(self, sample_segments):
        """Long visits keep constant-size text evidence for the role agent."""
        session_id = "bounded-evidence-session"

        for index in range(10):
            api_server.sessions.append_segment(
                session_id,
                {
                    "speaker_id": "spk_0" if index % 2 == 0 else "spk_1",
                    "text": f"utterance {index} " + ("x" * 300),
                    "start": float(index),
                    "end": float(index + 1),
                },
            )

        evidence = _build_bounded_role_evidence(
            session_id, api_server.sessions, sample_segments
        )

        assert evidence["new_segment_count"] == len(sample_segments)
        assert evidence["total_segments_considered"] == 10
        assert evidence["speaker_count"] == 2
        assert (
            evidence["caps"]["opening_utterances_per_speaker"]
            == ROLE_EVIDENCE_OPENING_UTTERANCES
        )
        for speaker_evidence in evidence["speakers"]:
            assert (
                len(speaker_evidence["recent_utterances"])
                <= ROLE_EVIDENCE_RECENT_UTTERANCES
            )
            assert (
                len(speaker_evidence["representative_utterances"])
                == ROLE_EVIDENCE_REPRESENTATIVE_UTTERANCES
            )
            assert all(
                len(utterance["text"]) <= ROLE_EVIDENCE_MAX_TEXT_CHARS
                for utterance in speaker_evidence["recent_utterances"]
            )

    def test_role_evidence_refreshes_representatives_with_later_clean_cues(self):
        """Later clean rows replace garbled openers in role-agent evidence."""
        session_id = "role-evidence-refresh-session"
        transcript_rows = [
            {"speaker_id": "spk_0", "text": "Hello", "start": 0.0, "end": 0.5},
            {"speaker_id": "spk_1", "text": "there", "start": 1.0, "end": 1.4},
            {
                "speaker_id": "spk_1",
                "text": "there, it's Dr. Seed here. How can I help you?",
                "start": 2.0,
                "end": 6.5,
            },
            {"speaker_id": "spk_0", "text": "yeah", "start": 6.6, "end": 6.9},
            {
                "speaker_id": "spk_0",
                "text": "I've just got a terrible headache and I need to vomit.",
                "start": 7.0,
                "end": 14.0,
            },
            {
                "speaker_id": "spk_1",
                "text": "Are you able to describe what kind of headache it was?",
                "start": 60.7,
                "end": 66.4,
            },
        ]

        # These rows mirror the UI path where early cross-talk arrives before clean evidence.
        for transcript_row in transcript_rows:
            api_server.sessions.append_segment(session_id, transcript_row)

        evidence = _build_bounded_role_evidence(session_id, api_server.sessions, [])
        by_speaker = {
            speaker_evidence["speaker_id"]: speaker_evidence
            for speaker_evidence in evidence["speakers"]
        }

        patient_representatives = by_speaker["spk_0"]["representative_utterances"]
        doctor_representatives = by_speaker["spk_1"]["representative_utterances"]

        assert any(
            "describe what kind of headache" in row["text"]
            for row in doctor_representatives
        )
        assert any("Dr. Seed here" in row["text"] for row in doctor_representatives)
        assert all(row["text"] != "Hello" for row in patient_representatives)
        assert by_speaker["spk_1"]["role_cue_counts"]["doctor"] >= 3
        assert by_speaker["spk_0"]["role_cue_counts"]["patient"] >= 1
        assert by_speaker["spk_1"]["opening_role_cue_counts"]["doctor"] >= 2
        assert by_speaker["spk_0"]["opening_role_cue_counts"]["patient"] >= 1
        assert by_speaker["spk_0"]["first_seen_index"] == 0
        assert evidence["establishment_hint"] == {
            "mapping": {"spk_1": "DOCTOR", "spk_0": "PATIENT"},
            "source": "opening_role_cue_counts",
        }
