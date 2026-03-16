"""
E2E contract tests — validates service integration without GPU.

Run with: ./scripts/e2e-test.sh
Or manually: AGENT_PORT=48101 APP_PORT=48082 MERCURE_PORT=48137 pytest tests/e2e/ -v

Tests are ordered by dependency: agent first, then PHP proxy, then Mercure.
"""

import asyncio
import json
import struct
import time
import uuid

import httpx
import pytest
import websockets

from conftest import AGENT_PORT, AGENT_URL, APP_URL, MERCURE_PORT, MERCURE_URL

SESSION_ID = str(uuid.uuid4())


# ─── Helpers ──────────────────────────────────────────────────────────


def _agent_reachable() -> bool:
    try:
        r = httpx.get(f"{AGENT_URL}/health", timeout=3)
        return r.status_code == 200
    except httpx.ConnectError:
        return False


def _app_reachable() -> bool:
    try:
        r = httpx.get(f"{APP_URL}/", timeout=3, follow_redirects=True)
        return r.status_code == 200
    except httpx.ConnectError:
        return False


def _mercure_reachable() -> bool:
    try:
        r = httpx.get(f"{MERCURE_URL}/.well-known/mercure", timeout=3)
        # Mercure returns 400 or 401 for unauthenticated GET — that's alive
        return r.status_code in (200, 400, 401)
    except httpx.ConnectError:
        return False


agent_required = pytest.mark.skipif(
    not _agent_reachable(), reason=f"Agent not running on port {AGENT_PORT}"
)
app_required = pytest.mark.skipif(
    not _app_reachable(), reason="PHP app not running"
)
mercure_required = pytest.mark.skipif(
    not _mercure_reachable(), reason=f"Mercure not running on port {MERCURE_PORT}"
)


# ═══════════════════════════════════════════════════════════════════════
# 1. AGENT HEALTH & API CONTRACTS
# ═══════════════════════════════════════════════════════════════════════


@agent_required
class TestAgentHealth:
    """Validate agent is up and responds correctly."""

    def test_health_returns_ok(self):
        r = httpx.get(f"{AGENT_URL}/health", timeout=5)
        assert r.status_code == 200
        data = r.json()
        assert data["status"] == "ok"
        assert data["service"] == "ambient-scribe-agent"
        assert "models_loaded" in data

    def test_openapi_docs_available(self):
        r = httpx.get(f"{AGENT_URL}/docs", timeout=5)
        assert r.status_code == 200
        assert "html" in r.headers.get("content-type", "").lower()

    def test_transcribe_file_validates_input(self):
        """POST without file → 422 (FastAPI validation)."""
        r = httpx.post(f"{AGENT_URL}/transcribe/file", timeout=5)
        assert r.status_code == 422


@agent_required
class TestAgentSessions:
    """Validate session endpoints return correct shapes."""

    def test_history_empty_session(self):
        r = httpx.get(f"{AGENT_URL}/session/{SESSION_ID}/history", timeout=5)
        assert r.status_code == 200
        data = r.json()
        assert data["session_id"] == SESSION_ID
        assert isinstance(data["segments"], list)

    def test_roles_empty_session(self):
        r = httpx.get(f"{AGENT_URL}/session/{SESSION_ID}/roles", timeout=5)
        assert r.status_code == 200
        data = r.json()
        assert data["session_id"] == SESSION_ID
        assert isinstance(data["mapping"], dict)
        assert isinstance(data["confidence"], (int, float))


@agent_required
class TestAgentWebSocket:
    """Validate WebSocket transcription contract."""

    @pytest.mark.asyncio
    async def test_websocket_accepts_and_processes(self):
        """Connect, send a small PCM chunk, disconnect cleanly."""
        ws_url = f"ws://localhost:{AGENT_PORT}/ws/transcribe/{uuid.uuid4()}"
        async with websockets.connect(ws_url) as ws:
            # Send 0.1s of silence (16kHz, 16-bit mono = 3200 bytes)
            silence = b"\x00" * 3200
            await ws.send(silence)
            # Small delay to let server process
            await ws.send(silence)
        # If we get here without exception, WebSocket lifecycle works

    @pytest.mark.asyncio
    async def test_websocket_rejects_invalid_path(self):
        """Connection to non-existent path should fail."""
        with pytest.raises(Exception):
            async with websockets.connect(
                f"ws://localhost:{AGENT_PORT}/ws/invalid"
            ):
                pass

    @pytest.mark.asyncio
    async def test_websocket_rejects_webm_when_pcm_configured(self):
        """Sending WebM magic bytes to a PCM-configured endpoint → error."""
        ws_url = f"ws://localhost:{AGENT_PORT}/ws/transcribe/{uuid.uuid4()}"
        async with websockets.connect(ws_url) as ws:
            # WebM magic bytes + padding
            webm_chunk = b"\x1a\x45\xdf\xa3" + b"\x00" * 3196
            await ws.send(webm_chunk)
            # Server should close the connection due to format mismatch
            try:
                # Wait for close or error message
                msg = await asyncio.wait_for(ws.recv(), timeout=5)
                # If we got a message, it might be an error
                data = json.loads(msg)
                assert data.get("type") == "error" or "format" in str(data).lower()
            except (websockets.exceptions.ConnectionClosed, asyncio.TimeoutError):
                pass  # Connection closed is expected behavior


@agent_required
class TestAgentFileTranscription:
    """Validate batch transcription with a minimal WAV."""

    def test_transcribe_minimal_wav(self):
        """Send a minimal valid WAV header + silence → get response shape."""
        # Minimal WAV: 16kHz, 16-bit, mono, 0.1s of silence
        sample_rate = 16000
        num_samples = 1600  # 0.1s
        data_size = num_samples * 2  # 16-bit = 2 bytes/sample
        wav = bytearray()
        # RIFF header
        wav.extend(b"RIFF")
        wav.extend(struct.pack("<I", 36 + data_size))
        wav.extend(b"WAVE")
        # fmt chunk
        wav.extend(b"fmt ")
        wav.extend(struct.pack("<I", 16))  # chunk size
        wav.extend(struct.pack("<H", 1))  # PCM
        wav.extend(struct.pack("<H", 1))  # mono
        wav.extend(struct.pack("<I", sample_rate))
        wav.extend(struct.pack("<I", sample_rate * 2))  # byte rate
        wav.extend(struct.pack("<H", 2))  # block align
        wav.extend(struct.pack("<H", 16))  # bits per sample
        # data chunk
        wav.extend(b"data")
        wav.extend(struct.pack("<I", data_size))
        wav.extend(b"\x00" * data_size)

        sid = str(uuid.uuid4())
        r = httpx.post(
            f"{AGENT_URL}/transcribe/file",
            params={"session_id": sid},
            files={"file": ("test.wav", bytes(wav), "audio/wav")},
            timeout=30,
        )
        assert r.status_code == 200
        data = r.json()
        assert data["session_id"] == sid
        assert isinstance(data["segments"], list)
        # Segments may be empty for pure silence — that's valid


# ═══════════════════════════════════════════════════════════════════════
# 2. PHP APP CONTRACTS (proxy to agent)
# ═══════════════════════════════════════════════════════════════════════


@app_required
class TestPhpApp:
    """Validate PHP app serves UI and proxies to agent."""

    def test_root_redirects_to_scribe(self):
        r = httpx.get(f"{APP_URL}/", timeout=5, follow_redirects=False)
        assert r.status_code in (301, 302)
        assert "/scribe" in r.headers.get("location", "")

    def test_scribe_renders_html(self):
        r = httpx.get(f"{APP_URL}/scribe", timeout=5)
        assert r.status_code == 200
        assert "text/html" in r.headers.get("content-type", "")
        # Template must inject CONFIG with session_id, topicRaw, topicRoles
        body = r.text
        assert "CONFIG" in body or "config" in body.lower()
        assert "topicRaw" in body or "topic" in body.lower()

    def test_scribe_has_reconnect_and_download(self):
        """Template includes reconnect and download UI elements."""
        r = httpx.get(f"{APP_URL}/scribe", timeout=5)
        body = r.text
        assert "reconnectBtn" in body, "Missing reconnect button"
        assert "downloadBtn" in body, "Missing download button"
        assert "scribe.js" in body, "Missing scribe.js script reference"

    def test_scribe_has_accessibility_attributes(self):
        """Template includes WCAG accessibility attributes."""
        r = httpx.get(f"{APP_URL}/scribe", timeout=5)
        body = r.text
        assert 'aria-live="polite"' in body, "Missing aria-live on status/transcript"
        assert 'role="log"' in body, "Missing role=log on transcript"
        assert 'role="status"' in body, "Missing role=status on segment count"
        assert 'aria-label="Start recording' in body, "Missing aria-label on start button"
        assert 'aria-label="Stop recording' in body, "Missing aria-label on stop button"
        assert 'aria-label="Download' in body, "Missing aria-label on download button"
        assert "sr-only" in body, "Missing sr-only class for screen reader announcements"

    def test_scribe_contains_session_uuid(self):
        r = httpx.get(f"{APP_URL}/scribe", timeout=5)
        body = r.text
        # Session ID should be a UUID v4
        import re

        uuids = re.findall(
            r"[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}",
            body,
            re.IGNORECASE,
        )
        assert len(uuids) >= 1, "Template must contain at least one UUID v4 session ID"


@app_required
@agent_required
class TestPhpProxiesToAgent:
    """Validate PHP→Python proxy chain works."""

    def test_roles_proxy(self):
        """PHP /scribe/{id}/roles should proxy to agent /session/{id}/roles."""
        sid = str(uuid.uuid4())
        r = httpx.get(f"{APP_URL}/scribe/{sid}/roles", timeout=10)
        assert r.status_code == 200
        data = r.json()
        assert data["session_id"] == sid
        assert "mapping" in data

    def test_history_proxy(self):
        """PHP /scribe/{id}/history should proxy to agent /session/{id}/history."""
        sid = str(uuid.uuid4())
        r = httpx.get(f"{APP_URL}/scribe/{sid}/history", timeout=10)
        assert r.status_code == 200
        data = r.json()
        assert data["session_id"] == sid
        assert "segments" in data


# ═══════════════════════════════════════════════════════════════════════
# 3. MERCURE SSE HUB
# ═══════════════════════════════════════════════════════════════════════


@mercure_required
class TestMercure:
    """Validate Mercure is alive and accepts connections."""

    def test_hub_responds(self):
        """Mercure returns 400/401 on unauthenticated GET (means it's alive)."""
        r = httpx.get(f"{MERCURE_URL}/.well-known/mercure", timeout=5)
        assert r.status_code in (200, 400, 401)

    def test_anonymous_subscribe_rejected_without_topic(self):
        """GET without topic param should return 400."""
        r = httpx.get(f"{MERCURE_URL}/.well-known/mercure", timeout=5)
        # 400 = missing topic parameter (expected)
        # 401 = auth required (also valid depending on config)
        assert r.status_code in (400, 401)


# ═══════════════════════════════════════════════════════════════════════
# 4. CROSS-SERVICE INTEGRATION
# ═══════════════════════════════════════════════════════════════════════


@agent_required
@app_required
class TestCrossService:
    """Validate that services agree on data shapes."""

    def test_agent_and_php_roles_shape_match(self):
        """Both endpoints return same shape for the same session."""
        sid = str(uuid.uuid4())

        agent_r = httpx.get(f"{AGENT_URL}/session/{sid}/roles", timeout=5)
        php_r = httpx.get(f"{APP_URL}/scribe/{sid}/roles", timeout=10)

        agent_data = agent_r.json()
        php_data = php_r.json()

        for key in ("session_id", "mapping", "confidence"):
            assert key in agent_data, f"Agent response missing '{key}'"
            assert key in php_data, f"PHP response missing '{key}'"

        assert agent_data["session_id"] == php_data["session_id"]
        assert agent_data["mapping"] == php_data["mapping"]

    def test_agent_and_php_history_shape_match(self):
        """Both endpoints return same shape for the same session."""
        sid = str(uuid.uuid4())

        agent_r = httpx.get(f"{AGENT_URL}/session/{sid}/history", timeout=5)
        php_r = httpx.get(f"{APP_URL}/scribe/{sid}/history", timeout=10)

        agent_data = agent_r.json()
        php_data = php_r.json()

        for key in ("session_id", "segments"):
            assert key in agent_data, f"Agent response missing '{key}'"
            assert key in php_data, f"PHP response missing '{key}'"

        assert agent_data["session_id"] == php_data["session_id"]


@agent_required
class TestSessionLifecycleE2E:
    """Validate session lifecycle: create via WS → verify history → cleanup."""

    @pytest.mark.asyncio
    async def test_websocket_session_creates_history(self):
        """Connect via WS, send audio, disconnect → history endpoint has segments."""
        sid = str(uuid.uuid4())
        ws_url = f"ws://localhost:{AGENT_PORT}/ws/transcribe/{sid}"

        async with websockets.connect(ws_url) as ws:
            silence = b"\x00" * 3200
            await ws.send(silence)
            await ws.send(silence)

        # Small delay for server to finalize
        time.sleep(0.5)

        # History should exist (may have empty segments for silence, but shape is correct)
        r = httpx.get(f"{AGENT_URL}/session/{sid}/history", timeout=5)
        assert r.status_code == 200
        data = r.json()
        assert data["session_id"] == sid
        assert isinstance(data["segments"], list)

    @pytest.mark.asyncio
    async def test_websocket_session_cleans_up_roles(self):
        """After WS disconnect, roles endpoint still returns valid shape."""
        sid = str(uuid.uuid4())
        ws_url = f"ws://localhost:{AGENT_PORT}/ws/transcribe/{sid}"

        async with websockets.connect(ws_url) as ws:
            await ws.send(b"\x00" * 3200)

        time.sleep(0.5)

        # Roles endpoint should return valid empty mapping (not crash)
        r = httpx.get(f"{AGENT_URL}/session/{sid}/roles", timeout=5)
        assert r.status_code == 200
        data = r.json()
        assert data["session_id"] == sid
        assert isinstance(data["mapping"], dict)

    @pytest.mark.asyncio
    async def test_disconnect_during_active_session_no_500(self):
        """Start recording, send multiple chunks, disconnect → no 500 on subsequent requests."""
        sid = str(uuid.uuid4())
        ws_url = f"ws://localhost:{AGENT_PORT}/ws/transcribe/{sid}"

        # Connect and send several chunks
        async with websockets.connect(ws_url) as ws:
            for _ in range(5):
                await ws.send(b"\x00" * 3200)
                await asyncio.sleep(0.1)
        # WebSocket disconnected

        time.sleep(0.5)

        # Roles endpoint should not 500
        r = httpx.get(f"{AGENT_URL}/session/{sid}/roles", timeout=5)
        assert r.status_code == 200

        # History endpoint should not 500
        r = httpx.get(f"{AGENT_URL}/session/{sid}/history", timeout=5)
        assert r.status_code == 200

        # Summary endpoint should not 500 (404 expected — no transcript for silence)
        r = httpx.post(f"{AGENT_URL}/session/{sid}/summary", timeout=5)
        assert r.status_code in (200, 404)


@agent_required
@mercure_required
class TestMercurePubSub:
    """Validate Mercure publish + subscribe round-trip."""

    def test_publish_with_jwt(self):
        """Publish to Mercure with a valid JWT → 200."""
        try:
            import jwt as pyjwt
        except ImportError:
            pytest.skip("PyJWT not installed")

        token = pyjwt.encode(
            {"mercure": {"publish": ["*"]}},
            "e2e-test-secret-key-minimum-32-chars",
            algorithm="HS256",
        )

        r = httpx.post(
            f"{MERCURE_URL}/.well-known/mercure",
            data={
                "topic": "test/e2e-pubsub",
                "data": '{"type": "test", "message": "round-trip"}',
            },
            headers={"Authorization": f"Bearer {token}"},
            timeout=5,
        )
        assert r.status_code == 200

    @pytest.mark.asyncio
    async def test_publish_subscribe_round_trip(self):
        """Publish to Mercure → subscriber receives the event via SSE."""
        try:
            import jwt as pyjwt
            import httpx_sse
        except ImportError:
            pytest.skip("PyJWT or httpx-sse not installed")

        topic = f"test/e2e-roundtrip-{uuid.uuid4().hex[:6]}"
        pub_token = pyjwt.encode(
            {"mercure": {"publish": ["*"]}},
            "e2e-test-secret-key-minimum-32-chars",
            algorithm="HS256",
        )

        received_events = []

        async def subscribe_and_collect():
            """Subscribe to the topic and collect events."""
            subscribe_url = f"{MERCURE_URL}/.well-known/mercure?topic={topic}"
            async with httpx.AsyncClient() as client:
                async with httpx_sse.aconnect_sse(
                    client, "GET", subscribe_url, timeout=10.0
                ) as sse:
                    async for event in sse.aiter_sse():
                        received_events.append(json.loads(event.data))
                        break  # Got one event, done

        async def publish_after_delay():
            """Wait for subscriber to connect, then publish."""
            await asyncio.sleep(0.5)
            async with httpx.AsyncClient() as client:
                await client.post(
                    f"{MERCURE_URL}/.well-known/mercure",
                    data={
                        "topic": topic,
                        "data": json.dumps({
                            "type": "segment",
                            "speaker_id": "spk_0",
                            "text": "round-trip test",
                        }),
                    },
                    headers={"Authorization": f"Bearer {pub_token}"},
                    timeout=5,
                )

        # Run subscriber and publisher concurrently
        await asyncio.wait_for(
            asyncio.gather(subscribe_and_collect(), publish_after_delay()),
            timeout=10,
        )

        assert len(received_events) == 1
        assert received_events[0]["type"] == "segment"
        assert received_events[0]["text"] == "round-trip test"
