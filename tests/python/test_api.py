"""
Tests for the FastAPI server endpoints.
"""

import pytest
from fastapi.testclient import TestClient

from api.server import app


@pytest.fixture
def client():
    """Create a FastAPI test client."""
    return TestClient(app)


class TestHealthEndpoint:
    """Tests for the /health endpoint."""

    def test_health_returns_ok(self, client):
        response = client.get("/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"
        assert data["service"] == "ambient-scribe-agent"


class TestSessionHistory:
    """Tests for the /session/{id}/history endpoint."""

    def test_history_nonexistent_session(self, client):
        response = client.get("/session/nonexistent-id/history")
        assert response.status_code == 200
        data = response.json()
        assert data["session_id"] == "nonexistent-id"
        assert data["segments"] == []
