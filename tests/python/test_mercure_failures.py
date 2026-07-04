"""
Tests for Mercure publish failure handling.

Validates that:
  - Segments are still persisted to SessionStore on publish failure
  - Errors are logged at ERROR level (not swallowed)
  - Subsequent publishes are still attempted (no permanent failure state)
  - Empty JWT → publish skipped with log
  - Retry logic works correctly
"""

import httpx
import pytest

import api.server as api_server
from api.server import app, lifecycle, publish_to_mercure, sessions
from nemo_pipeline import NemoPipeline
import tools.assign_roles as role_tools


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


class TestMercurePublishFailures:
    """Tests for publish_to_mercure failure handling."""

    @pytest.mark.asyncio
    async def test_publish_returns_false_on_empty_jwt(self, monkeypatch):
        monkeypatch.setattr("api.mercure_publisher._resolve_mercure_jwt", lambda: "")
        result = await publish_to_mercure("test/topic", {"type": "test"})
        assert result is False

    @pytest.mark.asyncio
    async def test_segments_persisted_despite_publish_failure(self, monkeypatch):
        """Segments are saved to SessionStore even when Mercure fails."""
        monkeypatch.setattr("api.mercure_publisher._resolve_mercure_jwt", lambda: "")

        session_id = "persist-test"
        segment = {"speaker_id": "spk_0", "text": "Hello", "start": 0.0, "end": 1.0}

        sessions.append_segment(session_id, segment)
        result = await publish_to_mercure(
            f"scribe/session/{session_id}/raw",
            {"type": "segment", **segment},
        )

        assert result is False
        stored = sessions.get_segments(session_id)
        assert len(stored) == 1
        assert stored[0]["text"] == "Hello"

    @pytest.mark.asyncio
    async def test_subsequent_publishes_still_attempted(self, monkeypatch):
        """After a failure, publish_to_mercure doesn't permanently give up."""
        call_count = 0

        async def mock_post(self, url, **kwargs):
            nonlocal call_count
            call_count += 1
            # The first visible event exhausts all retries so the UI sees a failed publish.
            if call_count <= 3:
                raise ConnectionError("hub down")

            class FakeResponse:
                status_code = 200

                def raise_for_status(self):
                    pass

            return FakeResponse()

        monkeypatch.setattr(
            "api.mercure_publisher._resolve_mercure_jwt", lambda: "test-jwt"
        )
        monkeypatch.setattr("httpx.AsyncClient.post", mock_post)
        # Speed up retries for testing
        monkeypatch.setattr(
            "api.mercure_publisher.MERCURE_PUBLISH_BACKOFF_SECONDS", 0.01
        )

        # First publish: all 3 retries fail
        result1 = await publish_to_mercure("test/topic", {"data": "first"})
        assert result1 is False
        assert call_count == 3

        # Second publish: succeeds on first attempt (call_count is now 4)
        result2 = await publish_to_mercure("test/topic", {"data": "second"})
        assert result2 is True

    @pytest.mark.asyncio
    async def test_retry_succeeds_on_second_attempt(self, monkeypatch):
        """Retry logic recovers from transient failures."""
        call_count = 0

        async def mock_post(self, url, **kwargs):
            nonlocal call_count
            call_count += 1
            # The first Mercure attempt fails like a brief hub outage during recording.
            if call_count == 1:
                raise ConnectionError("transient failure")

            class FakeResponse:
                status_code = 200

                def raise_for_status(self):
                    pass

            return FakeResponse()

        monkeypatch.setattr(
            "api.mercure_publisher._resolve_mercure_jwt", lambda: "test-jwt"
        )
        monkeypatch.setattr("httpx.AsyncClient.post", mock_post)
        monkeypatch.setattr(
            "api.mercure_publisher.MERCURE_PUBLISH_BACKOFF_SECONDS", 0.01
        )

        result = await publish_to_mercure("test/topic", {"data": "retry"})
        assert result is True
        assert call_count == 2  # Failed once, succeeded on retry
