"""
Tests for the FastAPI server endpoints.
"""

from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest
from fastapi import WebSocketDisconnect

import api.server as api_server
from api.server import active_sessions, app, session_history, sessions, transcribe_stream
from nemo_pipeline import NemoPipeline, Segment, TranscriptionResult


@pytest.fixture(autouse=True)
def clear_sessions():
    """Keep the module-level in-memory stores isolated across tests."""
    executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="test-nemo")
    original_executor = api_server.nemo_executor
    api_server.nemo_executor = executor
    sessions._sessions.clear()
    active_sessions.clear()
    app.state.nemo_pipeline = NemoPipeline(load_models=False)
    app.state.nemo_input_format = "pcm"
    yield
    sessions._sessions.clear()
    active_sessions.clear()
    executor.shutdown(wait=False, cancel_futures=True)
    api_server.nemo_executor = original_executor


class TestHealthEndpoint:
    """Tests for the /health endpoint."""

    @pytest.mark.asyncio
    async def test_health_returns_ok(self):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            response = await client.get("/health")

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"
        assert data["service"] == "ambient-scribe-agent"


class TestSessionHistory:
    """Tests for the /session/{id}/history endpoint."""

    @pytest.mark.asyncio
    async def test_history_nonexistent_session(self):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            response = await client.get("/session/nonexistent-id/history")

        assert response.status_code == 200
        data = response.json()
        assert data["session_id"] == "nonexistent-id"
        assert data["segments"] == []


class TestTranscriptionEndpoints:
    """Tests for batch and streaming transcription routes."""

    @pytest.mark.asyncio
    async def test_transcribe_file_endpoint_returns_segments(self):
        class StubPipeline:
            def transcribe_file(self, audio_path):
                assert audio_path.endswith(".wav")
                return TranscriptionResult(segments=[
                    Segment(
                        speaker_id="spk_0",
                        text="What brings you in today?",
                        start=0.0,
                        end=1.9,
                    )
                ])

        app.state.nemo_pipeline = StubPipeline()

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            response = await client.post(
                "/transcribe/file",
                params={"session_id": "batch-session"},
                files={"file": ("sample.wav", b"RIFFtest", "audio/wav")},
            )

        assert response.status_code == 200
        payload = response.json()
        assert payload["session_id"] == "batch-session"
        assert payload["segments"][0]["speaker_id"] == "spk_0"

    @pytest.mark.asyncio
    async def test_transcribe_stream_publishes_and_persists_segments(self, monkeypatch):
        published_events = []

        class ImmediateLoop:
            async def run_in_executor(self, executor, func, *args):
                return func(*args)

        class StubPipeline:
            def transcribe_buffer(self, audio_buffer):
                assert audio_buffer != b""
                return TranscriptionResult(segments=[
                    Segment(
                        speaker_id="spk_1",
                        text="I have had a cough for three days.",
                        start=0.0,
                        end=2.4,
                    )
                ])

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
            published_events.append((topic, data))

        app.state.nemo_pipeline = StubPipeline()
        app.state.nemo_input_format = "pcm"
        monkeypatch.setattr("api.server.asyncio.get_event_loop", lambda: ImmediateLoop())
        monkeypatch.setattr("api.server.publish_to_mercure", fake_publish)

        websocket = FakeWebSocket()
        await transcribe_stream(websocket, "test-session")

        assert websocket.accepted is True
        history = await session_history("test-session")
        assert history["segments"][0]["speaker_id"] == "spk_1"
        assert any(event[1]["type"] == "segment" for event in published_events)
        assert any(event[1]["type"] == "finalized" for event in published_events)
