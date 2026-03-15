"""
Pytest configuration and shared fixtures for Python tests.
"""

import os
import sys
from pathlib import Path

import pytest

os.environ.setdefault("NEMO_SKIP_MODEL_LOAD", "1")
os.environ.setdefault("NEMO_STREAM_INPUT_FORMAT", "pcm")

# Add the strands_agents directory to the Python path so tests can import modules
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "strands_agents"))

# Force mock mode for NemoPipeline so tests never attempt GPU model loading
os.environ.setdefault("NEMO_MODEL_PROVIDER", "mock")


@pytest.fixture
def fixtures_dir() -> Path:
    """Path to the test fixtures directory."""
    return Path(__file__).parent.parent / "fixtures"


@pytest.fixture
def audio_fixtures_dir(fixtures_dir: Path) -> Path:
    """Path to the audio test fixtures directory."""
    return fixtures_dir / "audio"


@pytest.fixture
def sample_segments() -> list[dict]:
    """Sample transcript segments for testing."""
    return [
        {
            "speaker_id": "spk_0",
            "text": "Good morning, what brings you in today?",
            "start": 0.0,
            "end": 2.5,
            "is_interim": False,
        },
        {
            "speaker_id": "spk_1",
            "text": "I've been having chest pain for the last two days.",
            "start": 3.0,
            "end": 6.0,
            "is_interim": False,
        },
        {
            "speaker_id": "spk_0",
            "text": "Can you describe the pain? Is it sharp or dull?",
            "start": 6.5,
            "end": 9.0,
            "is_interim": False,
        },
    ]
