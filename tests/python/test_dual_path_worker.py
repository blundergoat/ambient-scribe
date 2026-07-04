"""
Tests for the dual-path role inference worker (tool invocation vs free-text).
"""

import asyncio

import pytest

import api.server as api_server
import tools.assign_roles as role_tools
from nemo_session import TranscriptionSession
from nemo_pipeline import NemoPipeline
from tools.assign_roles import get_or_create_state


@pytest.fixture(autouse=True)
def clear_state():
    api_server.sessions._sessions.clear()
    api_server.lifecycle.clear()
    api_server._inference_queues.clear()
    api_server._inference_workers.clear()
    api_server._mercure_event_ids.clear()
    role_tools._session_states.clear()
    yield
    api_server.sessions._sessions.clear()
    api_server.lifecycle.clear()
    api_server._inference_queues.clear()
    api_server._inference_workers.clear()
    api_server._mercure_event_ids.clear()
    role_tools._session_states.clear()


SEGMENTS = [
    {"speaker_id": "spk_0", "text": "What brings you in?", "start": 0.0, "end": 2.0},
    {"speaker_id": "spk_1", "text": "Chest pain.", "start": 2.5, "end": 4.0},
]


async def _register_session(session_id: str):
    """Register a session in lifecycle so worker cleanup doesn't wipe state."""
    pipeline = NemoPipeline()
    session = TranscriptionSession(session_id, pipeline=pipeline, input_format="pcm")
    await api_server.lifecycle.register(session_id, session)


class TestDualPathToolInvoked:
    """When the agent invokes the assign_roles tool, _tool_invoked=True."""

    @pytest.mark.asyncio
    async def test_tool_invoked_skips_apply_role_mapping(self, monkeypatch):
        """When tool is invoked, worker reads state directly (no double apply)."""
        session_id = "dual-tool-session"
        published = []

        await _register_session(session_id)
        for seg in SEGMENTS:
            api_server.sessions.append_segment(session_id, dict(seg))

        async def fake_publish(topic, data, event_id=None):
            published.append((topic, data))

        def fake_run_with_tool(sid, segments, transcript):
            # Simulate tool invocation: update state directly
            state = get_or_create_state(sid)
            state.update({"spk_0": "DOCTOR", "spk_1": "PATIENT"}, 0.92)
            return {
                "mapping": {"spk_0": "DOCTOR", "spk_1": "PATIENT"},
                "confidence": 0.92,
                "reasoning": "Tool was called",
                "_tool_invoked": True,
            }

        monkeypatch.setattr(api_server, "publish_to_mercure", fake_publish)
        monkeypatch.setattr(api_server, "_run_role_inference", fake_run_with_tool)

        await api_server.enqueue_role_inference(session_id, SEGMENTS)
        worker = api_server._inference_workers[session_id]
        await api_server.close_role_inference(session_id)
        await asyncio.wait_for(worker, timeout=2.0)

        # Should have published exactly one role_update
        role_events = [e for t, e in published if "roles" in t]
        assert len(role_events) == 1
        assert role_events[0]["mapping"] == {"spk_0": "DOCTOR", "spk_1": "PATIENT"}
        assert role_events[0]["confidence"] == 0.92
        assert role_events[0]["flip_detected"] is False

        # State should have exactly 1 mapping entry (not 2)
        state = get_or_create_state(session_id)
        assert len(state.mapping_history) == 1

    @pytest.mark.asyncio
    async def test_tool_invoked_with_flip_detected(self, monkeypatch):
        """When tool detects a flip, worker reports flip_detected=True."""
        session_id = "dual-flip-session"
        published = []

        await _register_session(session_id)
        for seg in SEGMENTS:
            api_server.sessions.append_segment(session_id, dict(seg))

        async def fake_publish(topic, data, event_id=None):
            published.append((topic, data))

        call_count = [0]

        def fake_run_with_tool(sid, segments, transcript):
            state = get_or_create_state(sid)
            call_count[0] += 1
            if call_count[0] == 1:
                state.update({"spk_0": "DOCTOR", "spk_1": "PATIENT"}, 0.8)
            else:
                # Swap roles — triggers flip
                state.update({"spk_0": "PATIENT", "spk_1": "DOCTOR"}, 0.9)
            return {
                "mapping": state.current_mapping,
                "confidence": state.running_confidence,
                "reasoning": "",
                "_tool_invoked": True,
            }

        monkeypatch.setattr(api_server, "publish_to_mercure", fake_publish)
        monkeypatch.setattr(api_server, "_run_role_inference", fake_run_with_tool)

        # First call — establishes mapping
        await api_server.enqueue_role_inference(session_id, SEGMENTS)
        worker = api_server._inference_workers[session_id]
        await api_server.close_role_inference(session_id)
        await asyncio.wait_for(worker, timeout=2.0)

        # Second call — flips roles
        await api_server.enqueue_role_inference(session_id, SEGMENTS)
        worker = api_server._inference_workers[session_id]
        await api_server.close_role_inference(session_id)
        await asyncio.wait_for(worker, timeout=2.0)

        role_events = [e for t, e in published if "roles" in t]
        assert len(role_events) == 2
        assert role_events[0]["flip_detected"] is False
        assert role_events[1]["flip_detected"] is True


class TestDualPathFreeText:
    """When the agent does NOT invoke the tool (free-text JSON fallback)."""

    @pytest.mark.asyncio
    async def test_freetext_path_applies_mapping(self, monkeypatch):
        """Free-text JSON result calls apply_role_mapping_result."""
        session_id = "dual-freetext-session"
        published = []

        await _register_session(session_id)
        for seg in SEGMENTS:
            api_server.sessions.append_segment(session_id, dict(seg))

        async def fake_publish(topic, data, event_id=None):
            published.append((topic, data))

        def fake_run_freetext(sid, segments, transcript):
            # No tool invocation — state unchanged, return plain dict
            return {
                "mapping": {"spk_0": "DOCTOR", "spk_1": "PATIENT"},
                "confidence": 0.85,
                "reasoning": "Free text response",
            }

        monkeypatch.setattr(api_server, "publish_to_mercure", fake_publish)
        monkeypatch.setattr(api_server, "_run_role_inference", fake_run_freetext)

        await api_server.enqueue_role_inference(session_id, SEGMENTS)
        worker = api_server._inference_workers[session_id]
        await api_server.close_role_inference(session_id)
        await asyncio.wait_for(worker, timeout=2.0)

        role_events = [e for t, e in published if "roles" in t]
        assert len(role_events) == 1
        assert role_events[0]["mapping"] == {"spk_0": "DOCTOR", "spk_1": "PATIENT"}

        # State should have been updated via apply_role_mapping_result
        state = get_or_create_state(session_id)
        assert len(state.mapping_history) == 1

    @pytest.mark.asyncio
    async def test_none_result_logs_warning(self, monkeypatch):
        """When _run_role_inference returns None, no Mercure event published."""
        session_id = "dual-none-session"
        published = []

        await _register_session(session_id)
        for seg in SEGMENTS:
            api_server.sessions.append_segment(session_id, dict(seg))

        async def fake_publish(topic, data, event_id=None):
            published.append((topic, data))

        monkeypatch.setattr(api_server, "publish_to_mercure", fake_publish)
        monkeypatch.setattr(
            api_server,
            "_run_role_inference",
            lambda *a, **kw: None,
        )

        await api_server.enqueue_role_inference(session_id, SEGMENTS)
        worker = api_server._inference_workers[session_id]
        await api_server.close_role_inference(session_id)
        await asyncio.wait_for(worker, timeout=2.0)

        role_events = [e for t, e in published if "roles" in t]
        assert len(role_events) == 0

    @pytest.mark.asyncio
    async def test_provider_unreachable_publishes_system_error_once(self, monkeypatch):
        """A provider-unreachable signal warns the browser once, without a role update."""
        from api import role_inference_queue

        session_id = "dual-provider-down-session"
        role_inference_queue._provider_unavailable_warned.discard(session_id)
        published = []

        await _register_session(session_id)
        for seg in SEGMENTS:
            api_server.sessions.append_segment(session_id, dict(seg))

        async def fake_publish(topic, data, event_id=None):
            published.append((topic, data))

        monkeypatch.setattr(api_server, "publish_to_mercure", fake_publish)
        monkeypatch.setattr(
            api_server,
            "_run_role_inference",
            lambda *a, **kw: {"path": "none", "mapping": {}, "provider_unreachable": True},
        )

        await api_server.enqueue_role_inference(session_id, SEGMENTS)
        worker = api_server._inference_workers[session_id]
        await api_server.close_role_inference(session_id)
        await asyncio.wait_for(worker, timeout=2.0)

        role_events = [e for t, e in published if "roles" in t]
        system_errors = [e for e in role_events if e.get("type") == "system_error"]
        role_updates = [e for e in role_events if e.get("type") == "role_update"]
        assert len(system_errors) == 1
        assert "README_STACK.md" in system_errors[0]["message"]
        assert len(role_updates) == 0


class TestToolInvokedAttributedSegments:
    """Verify attributed_segments are correctly built in the tool-invoked path."""

    @pytest.mark.asyncio
    async def test_segments_get_role_from_state_mapping(self, monkeypatch):
        session_id = "dual-attr-session"
        published = []

        await _register_session(session_id)
        for seg in SEGMENTS:
            api_server.sessions.append_segment(session_id, dict(seg))

        async def fake_publish(topic, data, event_id=None):
            published.append((topic, data))

        def fake_run(sid, segments, transcript):
            state = get_or_create_state(sid)
            state.update({"spk_0": "DOCTOR", "spk_1": "PATIENT"}, 0.88)
            return {
                "mapping": state.current_mapping,
                "confidence": 0.88,
                "reasoning": "",
                "_tool_invoked": True,
            }

        monkeypatch.setattr(api_server, "publish_to_mercure", fake_publish)
        monkeypatch.setattr(api_server, "_run_role_inference", fake_run)

        await api_server.enqueue_role_inference(session_id, SEGMENTS)
        worker = api_server._inference_workers[session_id]
        await api_server.close_role_inference(session_id)
        await asyncio.wait_for(worker, timeout=2.0)

        role_events = [e for t, e in published if "roles" in t]
        attributed = role_events[0]["attributed_segments"]
        assert attributed[0]["role"] == "DOCTOR"
        assert attributed[0]["text"] == "What brings you in?"
        assert attributed[1]["role"] == "PATIENT"
        assert attributed[1]["text"] == "Chest pain."
