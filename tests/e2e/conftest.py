"""
E2E test configuration — runs against live local services.

Prerequisites (started by scripts/e2e-test.sh):
  - Python agent on AGENT_PORT (NEMO_MODEL_PROVIDER=mock)
  - Mercure on MERCURE_PORT
  - PHP app on APP_PORT (optional, for proxy tests)
"""

import os

import pytest

AGENT_PORT = int(os.environ.get("AGENT_PORT", "48101"))
APP_PORT = int(os.environ.get("APP_PORT", "48082"))
MERCURE_PORT = int(os.environ.get("MERCURE_PORT", "48137"))

AGENT_URL = f"http://localhost:{AGENT_PORT}"
APP_URL = f"http://localhost:{APP_PORT}"
MERCURE_URL = f"http://localhost:{MERCURE_PORT}"


@pytest.fixture
def agent_url():
    return AGENT_URL


@pytest.fixture
def app_url():
    return APP_URL


@pytest.fixture
def mercure_url():
    return MERCURE_URL
