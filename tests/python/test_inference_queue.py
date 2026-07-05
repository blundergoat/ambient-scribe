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
    ROLE_EVIDENCE_RECENT_UTTERANCES,
    _build_bounded_role_evidence,
)


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
        for speaker_evidence in evidence["speakers"]:
            assert (
                len(speaker_evidence["recent_utterances"])
                <= ROLE_EVIDENCE_RECENT_UTTERANCES
            )
            assert len(speaker_evidence["representative_utterances"]) == 2
            assert all(
                len(utterance["text"]) <= ROLE_EVIDENCE_MAX_TEXT_CHARS
                for utterance in speaker_evidence["recent_utterances"]
            )
