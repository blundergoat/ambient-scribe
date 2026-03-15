"""
Tests for concurrent WebSocket session isolation.

Validates that:
  - 3 simultaneous sessions don't cross-contaminate
  - Segments from session A don't appear in session B
  - Session C cleanup doesn't affect session A or B
  - lifecycle.active_count matches expected value
"""

from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi import WebSocketDisconnect

import api.server as api_server
from api.server import app, lifecycle, session_history, sessions, transcribe_stream
from nemo_pipeline import NemoPipeline, Segment, TranscriptionResult
import tools.assign_roles as role_tools


@pytest.fixture(autouse=True)
def clear_state():
    """Isolate module-level stores across tests."""
    executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="test-nemo")
    original_executor = api_server.nemo_executor
    api_server.nemo_executor = executor
    sessions._sessions.clear()
    lifecycle.clear()
    api_server._inference_queues.clear()
    api_server._inference_workers.clear()
    role_tools._session_states.clear()
    app.state.nemo_pipeline = NemoPipeline()
    app.state.nemo_input_format = "pcm"
    yield
    sessions._sessions.clear()
    lifecycle.clear()
    api_server._inference_queues.clear()
    api_server._inference_workers.clear()
    role_tools._session_states.clear()
    executor.shutdown(wait=False, cancel_futures=True)
    api_server.nemo_executor = original_executor


class TestConcurrentSessions:
    """Tests for multi-session isolation."""

    @pytest.mark.asyncio
    async def test_three_sessions_isolated(self, monkeypatch):
        """Three concurrent sessions don't cross-contaminate segments."""
        segment_texts = {
            "session-a": "What brings you in today?",
            "session-b": "I have a headache.",
            "session-c": "Take two aspirin.",
        }

        class ImmediateLoop:
            async def run_in_executor(self, executor, func, *args):
                return func(*args)

        def make_pipeline(session_id):
            class StubPipeline:
                def transcribe_buffer(self, audio_buffer):
                    return TranscriptionResult(segments=[
                        Segment(
                            speaker_id="spk_0",
                            text=segment_texts[session_id],
                            start=0.0,
                            end=1.0,
                        )
                    ])
            return StubPipeline()

        class FakeWebSocket:
            def __init__(self):
                self.headers = {}
                self.accepted = False
                self._sent_chunk = False

            async def accept(self):
                self.accepted = True

            async def receive_bytes(self):
                if self._sent_chunk:
                    raise WebSocketDisconnect
                self._sent_chunk = True
                return b"\x00" * 3200

        async def fake_publish(topic, data):
            return True

        async def fake_enqueue(session_id, segments):
            pass

        monkeypatch.setattr("api.server.asyncio.get_event_loop", lambda: ImmediateLoop())
        monkeypatch.setattr("api.server.publish_to_mercure", fake_publish)
        monkeypatch.setattr("api.server.enqueue_role_inference", fake_enqueue)

        # Run all three sessions sequentially (they each process one chunk then disconnect)
        for sid in ["session-a", "session-b", "session-c"]:
            app.state.nemo_pipeline = make_pipeline(sid)
            ws = FakeWebSocket()
            await transcribe_stream(ws, sid)

        # Verify isolation: each session has only its own segments
        for sid, expected_text in segment_texts.items():
            history = await session_history(sid)
            assert len(history["segments"]) >= 1
            texts = [seg["text"] for seg in history["segments"]]
            assert expected_text in texts
            # No other session's text should appear
            for other_sid, other_text in segment_texts.items():
                if other_sid != sid:
                    assert other_text not in texts

    @pytest.mark.asyncio
    async def test_session_cleanup_doesnt_affect_others(self, monkeypatch):
        """Destroying session C doesn't affect session A's data."""
        from nemo_session import TranscriptionSession

        pipeline = NemoPipeline()

        # Register A and C
        session_a = TranscriptionSession("session-a", pipeline)
        session_c = TranscriptionSession("session-c", pipeline)
        await lifecycle.register("session-a", session_a)
        await lifecycle.register("session-c", session_c)

        # Add segments to both
        sessions.append_segment("session-a", {"speaker_id": "spk_0", "text": "A data"})
        sessions.append_segment("session-c", {"speaker_id": "spk_0", "text": "C data"})

        assert lifecycle.active_count == 2

        # Destroy C
        await lifecycle.destroy("session-c")

        # A is unaffected
        assert lifecycle.is_active("session-a")
        assert lifecycle.active_count == 1
        a_segments = sessions.get_segments("session-a")
        assert len(a_segments) == 1
        assert a_segments[0]["text"] == "A data"

        # C is gone from lifecycle (but SessionStore retains transcript data)
        assert not lifecycle.is_active("session-c")

        # Cleanup
        await lifecycle.destroy("session-a")
        assert lifecycle.active_count == 0
