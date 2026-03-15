"""
Tests for session cleanup race conditions.

Validates that concurrent destroy + SSE read doesn't cause:
  - KeyError on missing session state
  - RuntimeError from dict mutation
  - 500 responses
"""

import asyncio

import pytest

import api.server as api_server
from api.server import app, lifecycle, sessions
from nemo_pipeline import NemoPipeline
from nemo_session import TranscriptionSession
import tools.assign_roles as role_tools
from tools.assign_roles import get_or_create_state, _session_states


@pytest.fixture(autouse=True)
def clear_state():
    """Isolate module-level stores across tests."""
    sessions._sessions.clear()
    lifecycle.clear()
    api_server._inference_queues.clear()
    api_server._inference_workers.clear()
    role_tools._session_states.clear()
    app.state.nemo_pipeline = NemoPipeline(load_models=False)
    app.state.nemo_input_format = "pcm"
    yield
    sessions._sessions.clear()
    lifecycle.clear()
    role_tools._session_states.clear()


class TestCleanupRace:
    """Tests for concurrent cleanup + state reads."""

    @pytest.mark.asyncio
    async def test_concurrent_destroy_and_sse_read(self):
        """SSE consumer active during destroy → no exception, state preserved."""
        pipeline = NemoPipeline(load_models=False)

        for i in range(50):
            sid = f"race-{i}"
            session = TranscriptionSession(sid, pipeline)
            await lifecycle.register(sid, session)
            get_or_create_state(sid).update({"spk_0": "DOCTOR"}, 0.9)

            # Simulate SSE starting before destroy
            lifecycle.sse_consumer_start(sid)

            # Destroy while SSE is active
            await lifecycle.destroy(sid)

            # State should survive for SSE reader
            assert sid in _session_states
            state = get_or_create_state(sid)
            assert state.current_mapping == {"spk_0": "DOCTOR"}

            # SSE ends → cleanup happens
            lifecycle.sse_consumer_end(sid)
            assert sid not in _session_states

    @pytest.mark.asyncio
    async def test_concurrent_destroy_without_sse(self):
        """Destroy without SSE consumers → immediate cleanup, no crash."""
        pipeline = NemoPipeline(load_models=False)

        for i in range(50):
            sid = f"clean-{i}"
            session = TranscriptionSession(sid, pipeline)
            await lifecycle.register(sid, session)
            get_or_create_state(sid).update({"spk_0": "PATIENT"}, 0.8)

            await lifecycle.destroy(sid)

            assert not lifecycle.is_active(sid)
            assert sid not in _session_states

    @pytest.mark.asyncio
    async def test_parallel_register_destroy_no_deadlock(self):
        """Rapid register/destroy for different sessions doesn't deadlock."""
        pipeline = NemoPipeline(load_models=False)

        async def register_and_destroy(sid):
            session = TranscriptionSession(sid, pipeline)
            await lifecycle.register(sid, session)
            get_or_create_state(sid)
            await lifecycle.destroy(sid)

        # Run 20 concurrent register/destroy cycles
        tasks = [register_and_destroy(f"parallel-{i}") for i in range(20)]
        await asyncio.gather(*tasks)

        assert lifecycle.active_count == 0
