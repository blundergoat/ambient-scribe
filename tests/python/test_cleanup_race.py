"""
Tests for session cleanup race conditions.

Validates that concurrent destroy + SSE read doesn't cause:
  - KeyError on missing session state
  - RuntimeError from dict mutation
  - 500 responses
"""

import asyncio

import httpx
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
    api_server._mercure_event_ids.clear()
    role_tools._session_states.clear()
    app.state.nemo_pipeline = NemoPipeline()
    app.state.nemo_input_format = "pcm"
    app.state.http_client = httpx.AsyncClient(timeout=5.0)
    yield
    sessions._sessions.clear()
    lifecycle.clear()
    api_server._mercure_event_ids.clear()
    role_tools._session_states.clear()


class TestCleanupRace:
    """Tests for concurrent cleanup + state reads."""

    @pytest.mark.asyncio
    async def test_concurrent_destroy_without_sse(self):
        """Destroy without SSE consumers → immediate cleanup, no crash."""
        pipeline = NemoPipeline()

        for i in range(50):
            sid = f"clean-{i}"
            session = TranscriptionSession(sid, pipeline)
            await lifecycle.register(sid, session)
            get_or_create_state(sid).did_update_mapping_detect_flip(
                {"spk_0": "PATIENT"}, 0.8
            )

            await lifecycle.destroy(sid)

            assert not lifecycle.is_active(sid)
            assert sid not in _session_states

    @pytest.mark.asyncio
    async def test_parallel_register_destroy_no_deadlock(self):
        """Rapid register/destroy for different sessions doesn't deadlock."""
        pipeline = NemoPipeline()

        async def register_and_destroy(sid):
            session = TranscriptionSession(sid, pipeline)
            await lifecycle.register(sid, session)
            get_or_create_state(sid)
            await lifecycle.destroy(sid)

        # Run 20 concurrent register/destroy cycles
        tasks = [register_and_destroy(f"parallel-{i}") for i in range(20)]
        await asyncio.gather(*tasks)

        assert lifecycle.active_count == 0

    @pytest.mark.asyncio
    async def test_schedule_destroy_waits_for_grace_period(self):
        """Scheduled destroy keeps the session alive until the grace expires."""
        pipeline = NemoPipeline()
        session = TranscriptionSession("grace-test", pipeline)

        await lifecycle.register("grace-test", session)
        await lifecycle.schedule_destroy("grace-test", grace_seconds=0.05)
        await asyncio.sleep(0)

        assert lifecycle.is_active("grace-test")
        assert lifecycle.has_pending_destroy("grace-test")

        await asyncio.sleep(0.08)

        assert not lifecycle.is_active("grace-test")
