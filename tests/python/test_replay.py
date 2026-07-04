"""
Tests for the replay demo endpoint.
"""

import asyncio
import io
import struct
from contextlib import suppress
from unittest.mock import patch

import httpx
import pytest
from fastapi.testclient import TestClient

import api.server as api_server
from api.server import app, sessions
from nemo_pipeline import NemoPipeline, Segment, TranscriptionResult

TEST_SESSION_ID = "00000000-0000-4000-8000-000000000088"


def _cancel_replay_tasks() -> None:
    for task in list(api_server._replay_tasks.values()):
        if not task.done():
            task.cancel()
    api_server._replay_tasks.clear()


def _make_wav_bytes(duration_seconds: float = 1.0, sample_rate: int = 16000) -> bytes:
    """Generate a minimal valid WAV file with silence."""
    num_samples = int(sample_rate * duration_seconds)
    data = struct.pack(f"<{num_samples}h", *([0] * num_samples))
    header = struct.pack(
        "<4sI4s4sIHHIIHH4sI",
        b"RIFF",
        36 + len(data),
        b"WAVE",
        b"fmt ",
        16,
        1,  # PCM
        1,  # mono
        sample_rate,
        sample_rate * 2,
        2,
        16,
        b"data",
        len(data),
    )
    return header + data


class TestReplayEndpoint:
    """Tests for the POST /session/{id}/replay endpoint."""

    def setup_method(self):
        sessions._sessions.clear()
        api_server._mercure_event_ids.clear()
        _cancel_replay_tasks()
        app.state.nemo_pipeline = NemoPipeline()
        app.state.http_client = httpx.AsyncClient(timeout=5.0)

    def teardown_method(self):
        _cancel_replay_tasks()

    def test_replay_returns_segment_count(self):
        mock_result = TranscriptionResult(
            segments=[
                Segment(speaker_id="spk_0", text="Hello", start=0.0, end=2.0),
                Segment(speaker_id="spk_1", text="Hi there", start=2.5, end=4.0),
            ]
        )

        with patch.object(NemoPipeline, "transcribe_file", return_value=mock_result):
            client = TestClient(app, raise_server_exceptions=False)
            wav = _make_wav_bytes(4.0)
            response = client.post(
                f"/session/{TEST_SESSION_ID}/replay",
                files={"file": ("demo.wav", io.BytesIO(wav), "audio/wav")},
            )

        assert response.status_code == 200
        data = response.json()
        assert data["session_id"] == TEST_SESSION_ID
        assert data["segments"] == 2
        assert data["transcript_segments"][0]["text"] == "Hello"
        assert data["duration_seconds"] == 4.0
        assert data["speed"] == 1.0
        assert data["pacing"] == "browser_audio_clock"

    def test_replay_empty_wav_returns_zero_segments(self):
        mock_result = TranscriptionResult(segments=[])

        with patch.object(NemoPipeline, "transcribe_file", return_value=mock_result):
            client = TestClient(app, raise_server_exceptions=False)
            wav = _make_wav_bytes(0.5)
            response = client.post(
                f"/session/{TEST_SESSION_ID}/replay",
                files={"file": ("empty.wav", io.BytesIO(wav), "audio/wav")},
            )

        assert response.status_code == 200
        data = response.json()
        assert data["segments"] == 0

    def test_replay_invalid_session_returns_400(self):
        client = TestClient(app, raise_server_exceptions=False)
        wav = _make_wav_bytes()
        response = client.post(
            "/session/not-a-uuid/replay",
            files={"file": ("demo.wav", io.BytesIO(wav), "audio/wav")},
        )
        assert response.status_code == 400

    def test_replay_accepts_speed_parameter(self):
        mock_result = TranscriptionResult(
            segments=[
                Segment(speaker_id="spk_0", text="Fast", start=0.0, end=1.0),
            ]
        )

        with patch.object(NemoPipeline, "transcribe_file", return_value=mock_result):
            client = TestClient(app, raise_server_exceptions=False)
            wav = _make_wav_bytes(1.0)
            response = client.post(
                f"/session/{TEST_SESSION_ID}/replay?speed=2.0",
                files={"file": ("demo.wav", io.BytesIO(wav), "audio/wav")},
            )

        assert response.status_code == 200
        assert response.json()["speed"] == 2.0

    def test_replay_returns_browser_clock_segments_and_stores_history(self):
        """Replayed segments should be returned for audio-clock rendering and stored."""
        mock_result = TranscriptionResult(
            segments=[
                Segment(speaker_id="spk_0", text="Stored segment", start=0.0, end=1.0),
            ]
        )

        with patch.object(NemoPipeline, "transcribe_file", return_value=mock_result):
            with patch("api.server.publish_to_mercure", return_value=True):
                client = TestClient(app, raise_server_exceptions=False)
                wav = _make_wav_bytes(1.0)
                response = client.post(
                    f"/session/{TEST_SESSION_ID}/replay?speed=10.0",
                    files={"file": ("demo.wav", io.BytesIO(wav), "audio/wav")},
                )

        assert response.status_code == 200
        assert response.json()["segments"] == 1
        assert response.json()["transcript_segments"][0]["text"] == "Stored segment"
        assert sessions.get_segments(TEST_SESSION_ID)[0]["text"] == "Stored segment"
        assert TEST_SESSION_ID not in api_server._replay_tasks

    def test_replay_duration_from_max_segment_end(self):
        """Duration should be the max end time across all segments."""
        mock_result = TranscriptionResult(
            segments=[
                Segment(speaker_id="spk_0", text="First", start=0.0, end=5.0),
                Segment(speaker_id="spk_1", text="Second", start=5.0, end=12.5),
                Segment(speaker_id="spk_0", text="Third", start=12.5, end=18.0),
            ]
        )

        with patch.object(NemoPipeline, "transcribe_file", return_value=mock_result):
            client = TestClient(app, raise_server_exceptions=False)
            wav = _make_wav_bytes(18.0)
            response = client.post(
                f"/session/{TEST_SESSION_ID}/replay",
                files={"file": ("demo.wav", io.BytesIO(wav), "audio/wav")},
            )

        assert response.status_code == 200
        assert response.json()["duration_seconds"] == 18.0

    def test_replay_speed_boundaries(self):
        """Speed at boundaries should be accepted."""
        mock_result = TranscriptionResult(
            segments=[
                Segment(speaker_id="spk_0", text="Fast", start=0.0, end=1.0),
            ]
        )

        with patch.object(NemoPipeline, "transcribe_file", return_value=mock_result):
            client = TestClient(app, raise_server_exceptions=False)
            wav = _make_wav_bytes(1.0)

            # Min speed
            r = client.post(
                f"/session/{TEST_SESSION_ID}/replay?speed=0.25",
                files={"file": ("min.wav", io.BytesIO(wav), "audio/wav")},
            )
            assert r.status_code == 200
            assert r.json()["speed"] == 0.25

            # Max speed
            r = client.post(
                f"/session/{TEST_SESSION_ID}/replay?speed=10.0",
                files={"file": ("max.wav", io.BytesIO(wav), "audio/wav")},
            )
            assert r.status_code == 200
            assert r.json()["speed"] == 10.0

    def test_replay_speed_out_of_range_rejected(self):
        """Speed outside 0.25-10.0 should be rejected by FastAPI validation."""
        client = TestClient(app, raise_server_exceptions=False)
        wav = _make_wav_bytes(1.0)
        r = client.post(
            f"/session/{TEST_SESSION_ID}/replay?speed=0.1",
            files={"file": ("slow.wav", io.BytesIO(wav), "audio/wav")},
        )
        assert r.status_code == 422  # Validation error

    @pytest.mark.asyncio
    async def test_replay_stop_route_cancels_active_task(self):
        """Stopping replay should cancel only the user's active demo task."""

        async def parked_replay_task():
            await asyncio.sleep(60)

        replay_task = asyncio.create_task(parked_replay_task())
        api_server._replay_tasks[TEST_SESSION_ID] = replay_task

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://testserver",
        ) as client:
            response = await client.post(f"/session/{TEST_SESSION_ID}/replay/stop")

        assert response.status_code == 200
        assert response.json() == {
            "session_id": TEST_SESSION_ID,
            "cancelled": True,
            "visible_segments": 0,
        }
        assert TEST_SESSION_ID not in api_server._replay_tasks
        assert replay_task.cancelling() > 0

        with suppress(asyncio.CancelledError):
            await replay_task

    def test_replay_stop_route_stores_visible_segments(self):
        """Stopping replay stores only the transcript rows revealed by audio time."""
        client = TestClient(app, raise_server_exceptions=False)
        response = client.post(
            f"/session/{TEST_SESSION_ID}/replay/stop",
            json={
                "visible_segments": [
                    {
                        "speaker_id": "spk_0",
                        "role": "DOCTOR",
                        "text": "What can I help with?",
                        "start": 0.0,
                        "end": 1.5,
                    }
                ],
                "audio_time_seconds": 1.5,
                "was_completed": False,
            },
        )

        assert response.status_code == 200
        assert response.json()["visible_segments"] == 1
        assert sessions.get_segments(TEST_SESSION_ID)[0]["text"] == "What can I help with?"

    @pytest.mark.asyncio
    async def test_replay_task_cleanup_preserves_newer_replacement(self, monkeypatch):
        """A cancelled old replay task must not clear a newer task for the session."""
        started = asyncio.Event()
        release = asyncio.Event()

        async def slow_publish(topic, data, event_id=None):
            started.set()
            await release.wait()
            return True

        async def fake_enqueue(session_id, segments):
            pass

        monkeypatch.setattr(api_server, "publish_to_mercure", slow_publish)
        monkeypatch.setattr(api_server, "enqueue_role_inference", fake_enqueue)

        old_task = asyncio.create_task(
            api_server._replay_segments(
                TEST_SESSION_ID,
                [{"speaker_id": "spk_0", "text": "old", "start": 0.0, "end": 1.0}],
                1.0,
            )
        )
        api_server._replay_tasks[TEST_SESSION_ID] = old_task

        await started.wait()
        replacement_task = asyncio.create_task(asyncio.sleep(60))
        old_task.cancel()
        api_server._replay_tasks[TEST_SESSION_ID] = replacement_task

        with suppress(asyncio.CancelledError):
            await old_task

        assert api_server._replay_tasks[TEST_SESSION_ID] is replacement_task

        replacement_task.cancel()
        with suppress(asyncio.CancelledError):
            await replacement_task
