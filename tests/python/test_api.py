"""
Tests for the FastAPI server endpoints.
"""

import asyncio
from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest
from fastapi import WebSocketDisconnect
from fastapi.testclient import TestClient

import api.server as api_server
import tools.assign_roles as role_tools
from api.server import app, lifecycle, session_history, sessions, transcribe_stream
from nemo_pipeline import NemoPipeline, Segment, TranscriptionResult

# Valid UUID for tests (session_id validation requires UUID format)
TEST_SESSION_ID = "00000000-0000-4000-8000-000000000001"
TEST_SESSION_ID_2 = "00000000-0000-4000-8000-000000000002"
TEST_SESSION_ID_3 = "00000000-0000-4000-8000-000000000003"


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
    role_tools._pending_role_segments.clear()
    api_server._mercure_event_ids.clear()
    app.state.nemo_pipeline = NemoPipeline()
    app.state.nemo_input_format = "pcm"
    app.state.http_client = httpx.AsyncClient(timeout=5.0)
    yield
    sessions._sessions.clear()
    lifecycle.clear()
    api_server._inference_queues.clear()
    api_server._inference_workers.clear()
    role_tools._session_states.clear()
    role_tools._pending_role_segments.clear()
    api_server._mercure_event_ids.clear()
    executor.shutdown(wait=False, cancel_futures=True)
    api_server.nemo_executor = original_executor


@pytest.fixture
def client():
    """Synchronous test client for FastAPI app."""
    return TestClient(app)


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
        async with httpx.AsyncClient(
            transport=transport, base_url="http://testserver"
        ) as client:
            response = await client.get("/health")

        assert response.status_code == 503
        data = response.json()
        assert data["status"] == "degraded"
        assert data["models_loaded"] is False
        assert data["load_error"] == "CUDA out of memory"


class TestSessionHistory:
    """Tests for the /session/{id}/history endpoint."""

    def test_history_nonexistent_session(self, client):
        response = client.get(f"/session/{TEST_SESSION_ID}/history")
        assert response.status_code == 200
        data = response.json()
        assert data["session_id"] == TEST_SESSION_ID
        assert data["segments"] == []

    @pytest.mark.asyncio
    async def test_history_accepts_post(self):
        """PHP StrandsClient uses postJson() - endpoint must accept POST."""
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://testserver"
        ) as client:
            response = await client.post(f"/session/{TEST_SESSION_ID}/history")

        assert response.status_code == 200
        data = response.json()
        assert data["session_id"] == TEST_SESSION_ID

    @pytest.mark.asyncio
    async def test_roles_accepts_post(self):
        """PHP StrandsClient uses postJson() - endpoint must accept POST."""
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://testserver"
        ) as client:
            response = await client.post(f"/session/{TEST_SESSION_ID}/roles")

        assert response.status_code == 200
        data = response.json()
        assert data["session_id"] == TEST_SESSION_ID
        assert isinstance(data["mapping"], dict)

    def test_roles_override_survives_later_agent_update(self, client, monkeypatch):
        """Manual UI correction wins when a later role-agent update disagrees."""
        from tools.assign_roles import apply_role_mapping_result, get_or_create_state

        async def fake_publish(topic, data, event_id=None):
            """Pretend Mercure accepted the manual override event."""
            return True

        monkeypatch.setattr(api_server, "publish_to_mercure", fake_publish)

        sessions.append_segment(
            TEST_SESSION_ID,
            {
                "speaker_id": "spk_0",
                "text": "The clinician clicked this speaker label.",
                "start": 0.0,
                "end": 1.0,
            },
        )

        response = client.post(
            f"/session/{TEST_SESSION_ID}/roles/override",
            json={"speaker_id": "spk_0", "role": "PATIENT"},
        )
        assert response.status_code == 200

        result = apply_role_mapping_result(
            session_id=TEST_SESSION_ID,
            segments=sessions.get_segments(TEST_SESSION_ID),
            mapping={"spk_0": "DOCTOR"},
            confidence=0.95,
            reasoning="Agent disagreed after the user correction.",
        )

        state = get_or_create_state(TEST_SESSION_ID)
        assert result.mapping["spk_0"] == "PATIENT"
        assert state.current_mapping["spk_0"] == "PATIENT"
        assert sessions.get_segments(TEST_SESSION_ID)[0]["role"] == "PATIENT"

    def test_row_override_pins_one_row_without_touching_the_speaker(
        self, client, monkeypatch
    ):
        """Correcting one row must not relabel the speaker's other rows."""
        from tools.assign_roles import get_or_create_state

        published_events = []

        async def fake_publish(topic, data, event_id=None):
            """Capture the roles-topic event the correction broadcasts."""
            published_events.append((topic, data))
            return True

        monkeypatch.setattr(api_server, "publish_to_mercure", fake_publish)

        # Two rows from the same speaker; the clinician says row 2 is the patient.
        for index in (1, 2):
            sessions.append_segment(
                TEST_SESSION_ID,
                {
                    "speaker_id": "spk_0",
                    "text": f"row {index}",
                    "start": float(index),
                    "end": float(index) + 0.9,
                    "segment_id": f"seg-{index:04d}",
                },
            )

        response = client.post(
            f"/session/{TEST_SESSION_ID}/roles/override",
            json={"segment_id": "seg-0002", "role": "PATIENT"},
        )
        assert response.status_code == 200
        assert response.json() == {
            "status": "ok",
            "segment_id": "seg-0002",
            "role": "PATIENT",
        }

        # A later agent-style speaker mapping cannot undo the row correction.
        sessions.apply_role_mapping(TEST_SESSION_ID, {"spk_0": "DOCTOR"})
        stored_rows = sessions.get_segments(TEST_SESSION_ID)
        assert stored_rows[0]["role"] == "DOCTOR"
        assert stored_rows[1]["role"] == "PATIENT"
        assert stored_rows[1]["role_source"] == "user_row"

        # The speaker-scoped stores stay untouched by a row correction.
        state = get_or_create_state(TEST_SESSION_ID)
        assert "spk_0" not in state.confirmed_overrides
        assert "spk_0" not in state.current_mapping

        # Other tabs learn about the row via the additive row_overrides field.
        roles_topic, role_event = published_events[-1]
        assert roles_topic.endswith("/roles")
        assert role_event["row_overrides"] == {"seg-0002": "PATIENT"}
        assert role_event["manual_override"] is True

    def test_row_override_after_role_state_cleanup_publishes_no_fabricated_mapping(
        self, client, monkeypatch
    ):
        """A post-visit row fix must not broadcast empty mapping or zero confidence."""
        published_events = []

        async def fake_publish(topic, data, event_id=None):
            """Capture the roles-topic event the late correction broadcasts."""
            published_events.append((topic, data))
            return True

        monkeypatch.setattr(api_server, "publish_to_mercure", fake_publish)

        # The visit ended: transcript rows persist in storage, but grace-window
        # teardown already destroyed the session's role state.
        sessions.append_segment(
            TEST_SESSION_ID,
            {
                "speaker_id": "spk_0",
                "text": "row the clinician reviews after the visit",
                "start": 0.0,
                "end": 0.9,
                "segment_id": "seg-0001",
            },
        )
        role_tools.cleanup_session(TEST_SESSION_ID)

        response = client.post(
            f"/session/{TEST_SESSION_ID}/roles/override",
            json={"segment_id": "seg-0001", "role": "PATIENT"},
        )
        assert response.status_code == 200

        # The correction still sticks to the stored row.
        assert sessions.get_segments(TEST_SESSION_ID)[0]["role"] == "PATIENT"

        # The broadcast carries only the row signal - no fabricated speaker state
        # that would wipe another tab's earned confidence badge.
        _, role_event = published_events[-1]
        assert role_event["row_overrides"] == {"seg-0001": "PATIENT"}
        assert role_event["manual_override"] is True
        assert "mapping" not in role_event
        assert "confidence" not in role_event

        # The dead session gained no resurrected role state either.
        assert TEST_SESSION_ID not in role_tools._session_states

    def test_row_override_rejects_ambiguous_or_unknown_targets(self, client):
        """The UI gets clear validation errors instead of silent no-ops."""
        # Neither scope: the server cannot know what the user corrected.
        response = client.post(
            f"/session/{TEST_SESSION_ID}/roles/override",
            json={"role": "DOCTOR"},
        )
        assert response.status_code == 400

        # Both scopes at once is ambiguous and likely a client bug.
        response = client.post(
            f"/session/{TEST_SESSION_ID}/roles/override",
            json={"speaker_id": "spk_0", "segment_id": "seg-0001", "role": "DOCTOR"},
        )
        assert response.status_code == 400

        # A row correction for a session with no stored transcript cannot stick.
        response = client.post(
            f"/session/{TEST_SESSION_ID}/roles/override",
            json={"segment_id": "seg-0001", "role": "DOCTOR"},
        )
        assert response.status_code == 404


class TestTranscriptionEndpoints:
    """Tests for batch and streaming transcription routes."""

    @pytest.mark.asyncio
    async def test_transcribe_file_endpoint_returns_segments(self):
        class StubPipeline:
            def transcribe_file(self, audio_path):
                assert audio_path.endswith(".wav")
                return TranscriptionResult(
                    segments=[
                        Segment(
                            speaker_id="spk_0",
                            text="What brings you in today?",
                            start=0.0,
                            end=1.9,
                        )
                    ]
                )

        app.state.nemo_pipeline = StubPipeline()

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://testserver"
        ) as client:
            response = await client.post(
                "/transcribe/file",
                params={"session_id": TEST_SESSION_ID},
                files={"file": ("sample.wav", b"RIFFtest", "audio/wav")},
            )

        assert response.status_code == 200
        payload = response.json()
        assert payload["session_id"] == TEST_SESSION_ID
        assert payload["segments"][0]["speaker_id"] == "spk_0"

    @pytest.mark.asyncio
    async def test_transcribe_file_endpoint_accepts_form_session_id(self):
        class StubPipeline:
            def transcribe_file(self, audio_path):
                assert audio_path.endswith(".wav")
                return TranscriptionResult(segments=[])

        app.state.nemo_pipeline = StubPipeline()

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://testserver"
        ) as client:
            response = await client.post(
                "/transcribe/file",
                data={"session_id": TEST_SESSION_ID_2},
                files={"file": ("sample.wav", b"RIFFtest", "audio/wav")},
            )

        assert response.status_code == 200
        payload = response.json()
        assert payload["session_id"] == TEST_SESSION_ID_2

    @pytest.mark.asyncio
    async def test_transcribe_stream_publishes_and_persists_segments(self, monkeypatch):
        published_events = []
        enqueued_segments = []

        class StubPipeline:
            def transcribe_buffer(self, audio_buffer):
                assert audio_buffer != b""
                return TranscriptionResult(
                    segments=[
                        Segment(
                            speaker_id="spk_1",
                            text="I have had a cough for three days.",
                            start=0.0,
                            end=2.4,
                        )
                    ]
                )

        class FakeWebSocket:
            def __init__(self):
                self.headers = {}
                self.query_params = {}
                self.accepted = False
                self._sent_chunk = False

            async def accept(self):
                self.accepted = True

            async def receive_bytes(self):
                if self._sent_chunk:
                    raise WebSocketDisconnect

                self._sent_chunk = True
                return b"\x00" * 3200

        async def fake_publish(topic, data, event_id=None):
            published_events.append((topic, data))
            return True

        async def fake_enqueue(session_id, segments):
            enqueued_segments.append((session_id, segments))

        # Use a real executor that runs synchronously in-process
        sync_executor = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="test-sync"
        )

        app.state.nemo_pipeline = StubPipeline()
        app.state.nemo_input_format = "pcm"
        monkeypatch.setattr("api.server.nemo_executor", sync_executor)
        monkeypatch.setattr("api.server.publish_to_mercure", fake_publish)
        monkeypatch.setattr("api.server.enqueue_role_inference", fake_enqueue)
        # Use 0 grace period so schedule_destroy fires immediately in tests
        monkeypatch.setattr("api.server.SESSION_RECONNECT_GRACE_SECONDS", 0.0)

        websocket = FakeWebSocket()
        await transcribe_stream(websocket, TEST_SESSION_ID)

        assert websocket.accepted is True
        history = await session_history(TEST_SESSION_ID)
        assert history["segments"][0]["speaker_id"] == "spk_1"
        assert any(event[1]["type"] == "segment" for event in published_events)
        assert any(event[1]["type"] == "finalized" for event in published_events)
        sync_executor.shutdown(wait=False)
        assert enqueued_segments == [
            (
                TEST_SESSION_ID,
                [
                    {
                        "speaker_id": "spk_1",
                        "text": "I have had a cough for three days.",
                        "start": 0.0,
                        "end": 2.4,
                        "is_interim": False,
                        # Every emitted row carries the stable ID clinicians can
                        # target with a per-row role correction (M20 Phase 2).
                        "segment_id": "seg-0001",
                        "revision": 1,
                    }
                ],
            )
        ]

    @pytest.mark.asyncio
    async def test_websocket_warns_on_mercure_failure(self, monkeypatch):
        """When Mercure publish fails, a system_error text frame is sent once."""
        sent_json_messages = []

        class ImmediateLoop:
            async def run_in_executor(self, executor, func, *args):
                return func(*args)

        class StubPipeline:
            def transcribe_buffer(self, audio_buffer):
                return TranscriptionResult(
                    segments=[
                        Segment(speaker_id="spk_0", text="test", start=0.0, end=1.0)
                    ]
                )

        class FakeWebSocket:
            def __init__(self):
                self.headers = {}
                self.query_params = {}
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

        async def failing_publish(topic, data, event_id=None):
            return False

        async def fake_enqueue(session_id, segments):
            pass

        app.state.nemo_pipeline = StubPipeline()
        app.state.nemo_input_format = "pcm"
        monkeypatch.setattr(
            "api.server.asyncio.get_running_loop", lambda: ImmediateLoop()
        )
        monkeypatch.setattr("api.server.publish_to_mercure", failing_publish)
        monkeypatch.setattr("api.server.enqueue_role_inference", fake_enqueue)
        # Use 0 grace period so schedule_destroy fires immediately in tests
        monkeypatch.setattr("api.server.SESSION_RECONNECT_GRACE_SECONDS", 0.0)

        websocket = FakeWebSocket()
        await transcribe_stream(websocket, TEST_SESSION_ID)

        # Should have sent exactly one system_error warning
        error_messages = [
            m for m in sent_json_messages if m.get("type") == "system_error"
        ]
        assert len(error_messages) == 1
        assert error_messages[0]["message"] == "Real-time streaming unavailable"


class TestPublishToMercure:
    """Tests for publish_to_mercure return value."""

    @pytest.mark.asyncio
    async def test_publish_returns_false_on_empty_jwt(self, monkeypatch):
        from api.server import publish_to_mercure

        monkeypatch.setattr("api.mercure_publisher._resolve_mercure_jwt", lambda: "")
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
        state.did_update_mapping_detect_flip({"spk_0": "DOCTOR"}, 0.9)
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
            get_or_create_state(sid).did_update_mapping_detect_flip(
                {"spk_0": "DOCTOR"}, 0.8
            )
            await lifecycle.destroy(sid)

        assert lifecycle.active_count == 0
        # All role states should be cleaned up
        for i in range(10):
            assert f"cycle-{i}" not in _session_states

    @pytest.mark.asyncio
    async def test_destroy_lock_timeout_still_cleans_best_effort(self, monkeypatch):
        """Timeout during destroy still closes workers and clears role state."""
        from nemo_pipeline import NemoPipeline
        from nemo_session import TranscriptionSession
        from tools.assign_roles import get_or_create_state, _session_states

        pipeline = NemoPipeline()
        session = TranscriptionSession("timeout-test", pipeline)
        await lifecycle.register("timeout-test", session)
        get_or_create_state("timeout-test").did_update_mapping_detect_flip(
            {"spk_0": "DOCTOR"}, 0.9
        )

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
        from tools.assign_roles import get_or_create_state

        errors = []

        def worker(session_id):
            try:
                for _ in range(100):
                    state = get_or_create_state(session_id)
                    state.did_update_mapping_detect_flip(
                        {"spk_0": "DOCTOR"}, 0.85
                    )
            except Exception as e:
                errors.append(e)

        threads = [
            threading.Thread(target=worker, args=(f"thread-{i}",)) for i in range(4)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == []

    def test_concurrent_cleanup_no_crash(self):
        """One thread creating, another cleaning up - no RuntimeError."""
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
        assert store.get_segments("old-session") == [
            {"speaker_id": "spk_0", "text": "old"}
        ]

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

    def test_transcript_preview_respects_max_chars(self):
        from session import SessionStore

        store = SessionStore(max_sessions=100, ttl_seconds=7200, max_segments=100)
        for i in range(20):
            store.append_segment(
                "s1", {"speaker_id": "spk_0", "text": f"segment {i} " + ("x" * 40)}
            )

        result = store.get_transcript_text("s1", max_chars=120)

        assert "\n...\n" in result
        assert len(result) <= 120
