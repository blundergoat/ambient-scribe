"""
Tests for the FastAPI server endpoints.
"""

import pytest
from fastapi.testclient import TestClient

import api.server as api_server
from api.server import app, lifecycle, session_history, sessions, transcribe_stream
from nemo_pipeline import NemoPipeline, Segment, TranscriptionResult
import tools.assign_roles as role_tools


@pytest.fixture(autouse=True)
def clear_sessions():
    """Keep the module-level in-memory stores isolated across tests."""
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


class TestHealthEndpoint:
    """Tests for the /health endpoint."""

    def test_health_returns_ok(self, client):
        response = client.get("/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"
        assert data["service"] == "ambient-scribe-agent"
        assert "models_loaded" in data

    @pytest.mark.asyncio
    async def test_health_degraded_on_load_error(self):
        app.state.nemo_pipeline._load_error = "CUDA out of memory"
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            response = await client.get("/health")

        assert response.status_code == 503
        data = response.json()
        assert data["status"] == "degraded"
        assert data["models_loaded"] is False
        assert data["load_error"] == "CUDA out of memory"


class TestSessionHistory:
    """Tests for the /session/{id}/history endpoint."""

    def test_history_nonexistent_session(self, client):
        response = client.get("/session/nonexistent-id/history")
        assert response.status_code == 200
        data = response.json()
        assert data["session_id"] == "nonexistent-id"
        assert data["segments"] == []

    @pytest.mark.asyncio
    async def test_history_accepts_post(self):
        """PHP StrandsClient uses postJson() — endpoint must accept POST."""
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            response = await client.post("/session/post-test/history")

        assert response.status_code == 200
        data = response.json()
        assert data["session_id"] == "post-test"

    @pytest.mark.asyncio
    async def test_roles_accepts_post(self):
        """PHP StrandsClient uses postJson() — endpoint must accept POST."""
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            response = await client.post("/session/post-test/roles")

        assert response.status_code == 200
        data = response.json()
        assert data["session_id"] == "post-test"
        assert isinstance(data["mapping"], dict)


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
    async def test_transcribe_file_endpoint_accepts_form_session_id(self):
        class StubPipeline:
            def transcribe_file(self, audio_path):
                assert audio_path.endswith(".wav")
                return TranscriptionResult(segments=[])

        app.state.nemo_pipeline = StubPipeline()

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            response = await client.post(
                "/transcribe/file",
                data={"session_id": "batch-form-session"},
                files={"file": ("sample.wav", b"RIFFtest", "audio/wav")},
            )

        assert response.status_code == 200
        payload = response.json()
        assert payload["session_id"] == "batch-form-session"

    @pytest.mark.asyncio
    async def test_transcribe_stream_publishes_and_persists_segments(self, monkeypatch):
        published_events = []
        enqueued_segments = []

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
            return True

        async def fake_enqueue(session_id, segments):
            enqueued_segments.append((session_id, segments))

        app.state.nemo_pipeline = StubPipeline()
        app.state.nemo_input_format = "pcm"
        monkeypatch.setattr("api.server.asyncio.get_event_loop", lambda: ImmediateLoop())
        monkeypatch.setattr("api.server.publish_to_mercure", fake_publish)
        monkeypatch.setattr("api.server.enqueue_role_inference", fake_enqueue)

        websocket = FakeWebSocket()
        await transcribe_stream(websocket, "test-session")

        assert websocket.accepted is True
        history = await session_history("test-session")
        assert history["segments"][0]["speaker_id"] == "spk_1"
        assert any(event[1]["type"] == "segment" for event in published_events)
        assert any(event[1]["type"] == "finalized" for event in published_events)
        assert enqueued_segments == [(
            "test-session",
            [{
                "speaker_id": "spk_1",
                "text": "I have had a cough for three days.",
                "start": 0.0,
                "end": 2.4,
                "is_interim": False,
            }],
        )]

    @pytest.mark.asyncio
    async def test_websocket_warns_on_mercure_failure(self, monkeypatch):
        """When Mercure publish fails, a system_error text frame is sent once."""
        sent_json_messages = []

        class ImmediateLoop:
            async def run_in_executor(self, executor, func, *args):
                return func(*args)

        class StubPipeline:
            def transcribe_buffer(self, audio_buffer):
                return TranscriptionResult(segments=[
                    Segment(speaker_id="spk_0", text="test", start=0.0, end=1.0)
                ])

        class FakeWebSocket:
            def __init__(self):
                self.headers = {}
                self.accepted = False
                self._chunks_sent = 0

            async def accept(self):
                self.accepted = True

            async def receive_bytes(self):
                self._chunks_sent += 1
                if self._chunks_sent > 2:
                    raise WebSocketDisconnect
                return b"\x00" * 3200

            async def send_json(self, data):
                sent_json_messages.append(data)

        async def failing_publish(topic, data):
            return False

        async def fake_enqueue(session_id, segments):
            pass

        app.state.nemo_pipeline = StubPipeline()
        app.state.nemo_input_format = "pcm"
        monkeypatch.setattr("api.server.asyncio.get_event_loop", lambda: ImmediateLoop())
        monkeypatch.setattr("api.server.publish_to_mercure", failing_publish)
        monkeypatch.setattr("api.server.enqueue_role_inference", fake_enqueue)

        websocket = FakeWebSocket()
        await transcribe_stream(websocket, "mercure-fail-session")

        # Should have sent exactly one system_error warning
        error_messages = [m for m in sent_json_messages if m.get("type") == "system_error"]
        assert len(error_messages) == 1
        assert error_messages[0]["message"] == "Real-time streaming unavailable"


class TestPublishToMercure:
    """Tests for publish_to_mercure return value."""

    @pytest.mark.asyncio
    async def test_publish_returns_false_on_empty_jwt(self, monkeypatch):
        from api.server import publish_to_mercure
        monkeypatch.setattr("api.server._resolve_mercure_jwt", lambda: "")
        result = await publish_to_mercure("test/topic", {"type": "test"})
        assert result is False


class TestSessionLifecycle:
    """Tests for the SessionLifecycle coordinator."""

    @pytest.mark.asyncio
    async def test_register_and_destroy_cleans_all_stores(self):
        """After destroy(), active sessions and role state are both empty."""
        from nemo_pipeline import NemoPipeline
        from nemo_session import TranscriptionSession

        pipeline = NemoPipeline()
        session = TranscriptionSession("lifecycle-test", pipeline)

        # Register
        await lifecycle.register("lifecycle-test", session)
        assert lifecycle.is_active("lifecycle-test")
        assert lifecycle.active_count == 1

        # Create role state
        from tools.assign_roles import get_or_create_state, _session_states
        state = get_or_create_state("lifecycle-test")
        state.update({"spk_0": "DOCTOR"}, 0.9)
        assert "lifecycle-test" in _session_states

        # Destroy
        await lifecycle.destroy("lifecycle-test")
        assert not lifecycle.is_active("lifecycle-test")
        assert lifecycle.active_count == 0
        assert "lifecycle-test" not in _session_states

    @pytest.mark.asyncio
    async def test_multiple_create_destroy_cycles_no_orphans(self):
        """10 register/destroy cycles leave zero orphaned entries."""
        from nemo_pipeline import NemoPipeline
        from nemo_session import TranscriptionSession
        from tools.assign_roles import get_or_create_state, _session_states

        pipeline = NemoPipeline()

        for i in range(10):
            sid = f"cycle-{i}"
            session = TranscriptionSession(sid, pipeline)
            await lifecycle.register(sid, session)
            get_or_create_state(sid).update({"spk_0": "DOCTOR"}, 0.8)
            await lifecycle.destroy(sid)

        assert lifecycle.active_count == 0
        # All role states should be cleaned up
        for i in range(10):
            assert f"cycle-{i}" not in _session_states

    @pytest.mark.asyncio
    async def test_sse_consumer_prevents_premature_role_cleanup(self):
        """Role state is preserved while SSE consumer is active."""
        from nemo_pipeline import NemoPipeline
        from nemo_session import TranscriptionSession
        from tools.assign_roles import get_or_create_state, _session_states

        pipeline = NemoPipeline()
        session = TranscriptionSession("sse-test", pipeline)
        await lifecycle.register("sse-test", session)
        get_or_create_state("sse-test").update({"spk_0": "DOCTOR"}, 0.9)

        # SSE consumer starts
        lifecycle.sse_consumer_start("sse-test")

        # Destroy while SSE is active — role state should survive
        await lifecycle.destroy("sse-test")
        assert not lifecycle.is_active("sse-test")
        assert "sse-test" in _session_states  # preserved for SSE reader

        # SSE consumer ends — now role state should be cleaned up
        lifecycle.sse_consumer_end("sse-test")
        assert "sse-test" not in _session_states

    @pytest.mark.asyncio
    async def test_destroy_lock_timeout_still_cleans_best_effort(self, monkeypatch):
        """Timeout during destroy still closes workers and clears role state."""
        from nemo_pipeline import NemoPipeline
        from nemo_session import TranscriptionSession
        from tools.assign_roles import get_or_create_state, _session_states

        pipeline = NemoPipeline()
        session = TranscriptionSession("timeout-test", pipeline)
        await lifecycle.register("timeout-test", session)
        get_or_create_state("timeout-test").update({"spk_0": "DOCTOR"}, 0.9)

        closed_sessions: list[str] = []

        async def fake_close(session_id: str) -> None:
            closed_sessions.append(session_id)

        async def timeout_wait_for(awaitable, timeout):
            close = getattr(awaitable, "close", None)
            if callable(close):
                close()
            raise asyncio.TimeoutError

        monkeypatch.setattr("session_lifecycle.asyncio.wait_for", timeout_wait_for)

        await lifecycle.destroy("timeout-test", fake_close)

        assert not lifecycle.is_active("timeout-test")
        assert "timeout-test" not in _session_states
        assert "timeout-test" not in lifecycle._locks
        assert closed_sessions == ["timeout-test"]


class TestThreadSafety:
    """Tests for thread safety of _session_states."""

    def test_concurrent_get_or_create_no_crash(self):
        """Two threads racing on get_or_create_state don't crash."""
        import threading
        from tools.assign_roles import get_or_create_state, _session_states

        errors = []

        def worker(session_id):
            try:
                for _ in range(100):
                    state = get_or_create_state(session_id)
                    state.update({"spk_0": "DOCTOR"}, 0.85)
            except Exception as e:
                errors.append(e)

        threads = [
            threading.Thread(target=worker, args=(f"thread-{i}",))
            for i in range(4)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == []

    def test_concurrent_cleanup_no_crash(self):
        """One thread creating, another cleaning up — no RuntimeError."""
        import threading
        from tools.assign_roles import get_or_create_state, cleanup_session

        errors = []

        def creator():
            try:
                for i in range(100):
                    get_or_create_state(f"race-{i}")
            except Exception as e:
                errors.append(e)

        def cleaner():
            try:
                for i in range(100):
                    cleanup_session(f"race-{i}")
            except Exception as e:
                errors.append(e)

        t1 = threading.Thread(target=creator)
        t2 = threading.Thread(target=cleaner)
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        assert errors == []


class TestSessionStore:
    """Tests for SessionStore TTL and eviction."""

    def test_custom_ttl_evicts_expired_sessions(self):
        """Sessions older than TTL are evicted on next access."""
        import time as _time
        from session import SessionStore

        store = SessionStore(max_sessions=100, ttl_seconds=1, max_segments=100)
        store.append_segment("old-session", {"speaker_id": "spk_0", "text": "old"})
        assert store.get_segments("old-session") == [{"speaker_id": "spk_0", "text": "old"}]

        # Wait for TTL to expire
        _time.sleep(1.1)

        # Should be evicted
        assert store.get_segments("old-session") == []

    def test_max_sessions_evicts_oldest(self):
        """When at capacity, oldest session is evicted."""
        from session import SessionStore

        store = SessionStore(max_sessions=3, ttl_seconds=7200, max_segments=100)
        store.append_segment("s1", {"text": "first"})
        store.append_segment("s2", {"text": "second"})
        store.append_segment("s3", {"text": "third"})

        # Adding a 4th should evict s1 (oldest)
        store.append_segment("s4", {"text": "fourth"})

        assert store.get_segments("s1") == []  # evicted
        assert store.get_segments("s2") != []
        assert store.get_segments("s3") != []
        assert store.get_segments("s4") != []
        assert store.session_count == 3
