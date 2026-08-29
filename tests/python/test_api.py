"""
Tests for the FastAPI server endpoints.
"""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import MagicMock

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
        # The visit is live: its WebSocket session is still registered, so the
        # override must create/pin role state (lifecycle.clear() runs per test).
        lifecycle._active[TEST_SESSION_ID] = MagicMock()

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

    def test_unknown_override_clears_confirmed_override_so_agent_can_relabel(
        self, client, monkeypatch
    ):
        """Cycling back to Unknown is an undo, not a permanent UNKNOWN pin."""
        from tools.assign_roles import apply_role_mapping_result, get_or_create_state

        async def fake_publish(topic, data, event_id=None):
            """Publishing is not under test for the undo semantics."""
            return True

        monkeypatch.setattr(api_server, "publish_to_mercure", fake_publish)
        sessions.append_segment(
            TEST_SESSION_ID,
            {
                "speaker_id": "spk_0",
                "text": "The clinician labelled, then unlabelled this speaker.",
                "start": 0.0,
                "end": 1.0,
            },
        )

        first = client.post(
            f"/session/{TEST_SESSION_ID}/roles/override",
            json={"speaker_id": "spk_0", "role": "DOCTOR"},
        )
        undo = client.post(
            f"/session/{TEST_SESSION_ID}/roles/override",
            json={"speaker_id": "spk_0", "role": "UNKNOWN"},
        )
        assert first.status_code == 200
        assert undo.status_code == 200

        state = get_or_create_state(TEST_SESSION_ID)
        assert "spk_0" not in state.confirmed_overrides

        # With the override cleared, a later agent decision may relabel again.
        result = apply_role_mapping_result(
            session_id=TEST_SESSION_ID,
            segments=sessions.get_segments(TEST_SESSION_ID),
            mapping={"spk_0": "PATIENT"},
            confidence=0.95,
            reasoning="Agent relabels after the user removed their correction.",
        )
        assert result.mapping["spk_0"] == "PATIENT"

    def test_speaker_override_updates_corrected_rows_for_retried_summaries(
        self, client, monkeypatch
    ):
        """A corrected artifact must cite the clinician's latest speaker labels."""

        async def fake_publish(topic, data, event_id=None):
            """Publishing is not under test for corrected-row syncing."""
            return True

        monkeypatch.setattr(api_server, "publish_to_mercure", fake_publish)
        sessions.append_segment(
            TEST_SESSION_ID,
            {
                "speaker_id": "spk_0",
                "text": "row before correction",
                "start": 0.0,
                "end": 1.0,
            },
        )
        sessions.replace_corrected_segments(
            TEST_SESSION_ID,
            [
                {
                    "segment_id": "corrected-0001",
                    "speaker_id": "spk_0",
                    "role": "DOCTOR",
                    "text": "corrected text",
                    "start": 0.0,
                    "end": 1.0,
                    "source_model": "test-model",
                }
            ],
        )

        response = client.post(
            f"/session/{TEST_SESSION_ID}/roles/override",
            json={"speaker_id": "spk_0", "role": "PATIENT"},
        )
        assert response.status_code == 200

        corrected_rows = sessions.get_corrected_segments(TEST_SESSION_ID)
        assert corrected_rows[0]["role"] == "PATIENT"

    def test_speaker_override_after_role_state_cleanup_publishes_no_fabricated_state(
        self, client, monkeypatch
    ):
        """A post-visit speaker relabel must not resurrect or broadcast empty role state."""
        published_events = []

        async def fake_publish(topic, data, event_id=None):
            """Capture the roles-topic event the late speaker correction broadcasts."""
            published_events.append((topic, data))
            return True

        monkeypatch.setattr(api_server, "publish_to_mercure", fake_publish)

        # The visit ended: transcript rows and the corrected artifact persist in
        # storage, but grace-window teardown already destroyed the role state.
        sessions.append_segment(
            TEST_SESSION_ID,
            {
                "speaker_id": "spk_0",
                "text": "row the clinician relabels after the visit",
                "start": 0.0,
                "end": 0.9,
                "segment_id": "seg-0001",
            },
        )
        sessions.replace_corrected_segments(
            TEST_SESSION_ID,
            [
                {
                    "segment_id": "corrected-0001",
                    "speaker_id": "spk_0",
                    "role": "PATIENT",
                    "text": "row the clinician relabels after the visit",
                    "start": 0.0,
                    "end": 0.9,
                }
            ],
        )
        role_tools.cleanup_session(TEST_SESSION_ID)

        response = client.post(
            f"/session/{TEST_SESSION_ID}/roles/override",
            json={"speaker_id": "spk_0", "role": "DOCTOR"},
        )
        assert response.status_code == 200

        # The explicit correction still sticks to stored rows AND the corrected
        # artifact, so a retried summary cites the clinician's latest label.
        assert sessions.get_segments(TEST_SESSION_ID)[0]["role"] == "DOCTOR"
        assert sessions.get_corrected_segments(TEST_SESSION_ID)[0]["role"] == "DOCTOR"

        # The broadcast carries only the clinician's explicit partial mapping -
        # no fabricated confidence or re-judged exceptions for a dead session.
        _, role_event = published_events[-1]
        assert role_event["mapping"] == {"spk_0": "DOCTOR"}
        assert role_event["manual_override"] is True
        assert "confidence" not in role_event
        assert "row_exceptions" not in role_event

        # The dead session gained no resurrected role state.
        assert TEST_SESSION_ID not in role_tools._session_states

    def test_speaker_override_on_live_visit_before_first_role_update_pins_label(
        self, client, monkeypatch
    ):
        """Clicking a speaker label before the role worker has run must still pin it."""

        async def fake_publish(topic, data, event_id=None):
            """Pretend Mercure accepted the early manual override event."""
            return True

        monkeypatch.setattr(api_server, "publish_to_mercure", fake_publish)

        sessions.append_segment(
            TEST_SESSION_ID,
            {
                "speaker_id": "spk_0",
                "text": "labelled before any agent role update arrived",
                "start": 0.0,
                "end": 1.0,
                "segment_id": "seg-0001",
            },
        )
        # Live visit, but the role worker has not created any state yet.
        lifecycle._active[TEST_SESSION_ID] = MagicMock()
        assert TEST_SESSION_ID not in role_tools._session_states

        response = client.post(
            f"/session/{TEST_SESSION_ID}/roles/override",
            json={"speaker_id": "spk_0", "role": "DOCTOR"},
        )
        assert response.status_code == 200

        # The live override creates state and pins the label against later
        # agent proposals - the survival contract the UI relies on.
        state = role_tools.get_or_create_state(TEST_SESSION_ID)
        assert state.confirmed_overrides == {"spk_0": "DOCTOR"}
        assert state.current_mapping["spk_0"] == "DOCTOR"

    def test_roles_snapshot_after_cleanup_does_not_resurrect_state(self, client):
        """Reading roles for a finished visit must not create state as a side effect."""
        role_tools.cleanup_session(TEST_SESSION_ID)

        response = client.get(f"/session/{TEST_SESSION_ID}/roles")
        assert response.status_code == 200

        # The page honestly sees "no role news" - the same shape as the PHP fallback.
        payload = response.json()
        assert payload["mapping"] == {}
        assert payload["confidence"] == 0.0

        # A read is a read: the dead session gained no new state entry.
        assert TEST_SESSION_ID not in role_tools._session_states

    def test_role_override_rejects_incomplete_requests(self, client):
        """The UI gets clear validation errors instead of silent no-ops."""
        # No speaker: the server cannot know whose label the user clicked.
        response = client.post(
            f"/session/{TEST_SESSION_ID}/roles/override",
            json={"role": "DOCTOR"},
        )
        assert response.status_code == 400

        # No role: a click that never picked a label cannot relabel anything.
        response = client.post(
            f"/session/{TEST_SESSION_ID}/roles/override",
            json={"speaker_id": "spk_0"},
        )
        assert response.status_code == 400

    def test_role_override_rejects_a_body_it_cannot_read(self, client):
        """An unusable body is a validation failure, not a server error.

        The browser proxy forwards whatever the page sent, so a dropped or malformed body must come
        back as the same 400 an empty selection gets rather than as an unhandled exception.
        """
        # Nothing sent at all: the request names no speaker and no role.
        response = client.post(f"/session/{TEST_SESSION_ID}/roles/override", content=b"")
        assert response.status_code == 400

        # Valid JSON, but not an object, so it carries no fields to read.
        for unusable_body in ('"DOCTOR"', "5", "[]", "null"):
            response = client.post(
                f"/session/{TEST_SESSION_ID}/roles/override",
                content=unusable_body,
                headers={"Content-Type": "application/json"},
            )
            assert response.status_code == 400, unusable_body


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
                        # target with a per-row role correction (role-correction phase).
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


class TestSummaryModelPreflight:
    """The browser pre-flight must fail closed for every provider config."""

    def test_unknown_provider_is_unavailable(self, monkeypatch):
        """A typo'd provider must not let a consultation start."""
        monkeypatch.setenv("ROLE_AGENT_MODEL_PROVIDER", "bedrok")

        available, detail = api_server._probe_summary_model()

        assert available is False
        assert "bedrok" in detail

    def test_bedrock_without_credentials_is_unavailable(self, monkeypatch):
        """Empty AWS keys must surface before recording, not at summary time."""
        import boto3

        class NoCredentialsSession:
            region_name = "ap-southeast-2"

            def get_credentials(self):
                return None

        monkeypatch.setenv("ROLE_AGENT_MODEL_PROVIDER", "bedrock")
        monkeypatch.setattr(boto3, "Session", NoCredentialsSession)

        available, detail = api_server._probe_summary_model()

        assert available is False
        assert "credentials" in detail

    def test_bedrock_with_credentials_and_region_is_available(self, monkeypatch):
        """A resolvable credential chain plus region passes pre-flight."""
        import boto3

        class ConfiguredSession:
            region_name = "ap-southeast-2"

            def get_credentials(self):
                return object()

        monkeypatch.setenv("ROLE_AGENT_MODEL_PROVIDER", "bedrock")
        monkeypatch.delenv("AWS_DEFAULT_REGION", raising=False)
        monkeypatch.setattr(boto3, "Session", ConfiguredSession)

        available, detail = api_server._probe_summary_model()

        assert available is True
        assert detail == "bedrock:ap-southeast-2"

    def test_ollama_prefix_tag_is_not_treated_as_pulled(self, monkeypatch):
        """qwen3.5:7b on disk must not satisfy a qwen3.5:9b configuration."""

        class FakeTagsResponse:
            def raise_for_status(self):
                return None

            def json(self):
                return {"models": [{"name": "qwen3.5:7b"}]}

        monkeypatch.setenv("ROLE_AGENT_MODEL_PROVIDER", "ollama")
        monkeypatch.setenv("ROLE_AGENT_OLLAMA_MODEL", "qwen3.5:9b")
        monkeypatch.setattr(
            api_server.httpx, "get", lambda url, timeout: FakeTagsResponse()
        )

        available, detail = api_server._probe_summary_model()

        assert available is False
        assert "not pulled" in detail

    def test_ollama_exact_tag_passes(self, monkeypatch):
        """The exact configured tag (or :latest for bare names) is accepted."""

        class FakeTagsResponse:
            def raise_for_status(self):
                return None

            def json(self):
                return {"models": [{"name": "qwen3.5:9b"}, {"name": "phi4:latest"}]}

        monkeypatch.setenv("ROLE_AGENT_MODEL_PROVIDER", "ollama")
        monkeypatch.setattr(
            api_server.httpx, "get", lambda url, timeout: FakeTagsResponse()
        )

        monkeypatch.setenv("ROLE_AGENT_OLLAMA_MODEL", "qwen3.5:9b")
        assert api_server._probe_summary_model() == (True, "ollama:qwen3.5:9b")

        monkeypatch.setenv("ROLE_AGENT_OLLAMA_MODEL", "phi4")
        assert api_server._probe_summary_model() == (True, "ollama:phi4")


class TestAgentModelHealthGate:
    """Pre-flight gate the browser calls before a consultation starts.

    Recording is refused unless both halves of the visit's promise are ready: the off-GPU model behind
    roles and the note, and the correction model behind the reviewed transcript. Core `/health` stays a
    separate question about live NeMo, so this gate never changes container health.
    """

    def test_unreachable_note_provider_blocks_recording(self, client, monkeypatch):
        """A known provider failure returns before the slower correction proof starts."""
        correction_readiness_calls = []
        monkeypatch.setattr(
            api_server,
            "_probe_summary_model",
            lambda: api_server.SummaryModelProbe(False, "bedrock region is not configured"),
        )
        monkeypatch.setattr(
            api_server,
            "correction_readiness",
            lambda: correction_readiness_calls.append(True) or (True, ""),
        )

        payload = client.get("/agent/model-health").json()

        assert payload["available"] is False
        assert payload["detail"] == "bedrock region is not configured"
        assert correction_readiness_calls == []

    def test_missing_correction_model_blocks_recording(self, client, monkeypatch):
        """A healthy note provider is not enough when the reviewed transcript cannot be produced.

        This is the case live health cannot see: streaming stays perfect while every stopped visit
        would fall back to unreviewed wording.
        """
        monkeypatch.setattr(
            api_server,
            "_probe_summary_model",
            lambda: api_server.SummaryModelProbe(True, ""),
        )
        monkeypatch.setattr(
            api_server,
            "correction_readiness",
            lambda: (False, "the correction model cannot be loaded by this agent runtime (TypeError)"),
        )

        payload = client.get("/agent/model-health").json()

        assert payload["available"] is False
        assert "correction model" in payload["detail"]
        assert "/" not in payload["detail"]

    def test_both_halves_ready_allows_recording(self, client, monkeypatch):
        """With roles, note, and correction all ready, the clinician can start recording."""
        monkeypatch.setattr(
            api_server,
            "_probe_summary_model",
            lambda: api_server.SummaryModelProbe(True, ""),
        )
        monkeypatch.setattr(api_server, "correction_readiness", lambda: (True, ""))

        payload = client.get("/agent/model-health").json()

        assert payload == {"available": True, "detail": ""}

    def test_startup_proves_correction_readiness_off_the_request_path(self, monkeypatch):
        """The agent pays the correction proof at boot, not on a clinician's first Start click.

        The proof costs a full model restore, which is longer than the browser proxy waits, so leaving
        it on the request path reported a healthy correction model as an unreachable agent.
        """
        readiness_calls = []
        monkeypatch.setattr(
            api_server,
            "correction_readiness",
            lambda: readiness_calls.append(True) or (True, ""),
        )

        asyncio.run(api_server._prove_correction_readiness())

        assert readiness_calls == [True]

    def test_core_health_ignores_correction_readiness(self, client, monkeypatch):
        """Container health stays about live NeMo, so a dead correction lane never restarts the agent."""
        monkeypatch.setattr(
            api_server,
            "correction_readiness",
            lambda: (False, "the correction model cannot be loaded by this agent runtime (TypeError)"),
        )

        response = client.get("/health")

        assert response.status_code == 200
        assert response.json()["status"] == "ok"
