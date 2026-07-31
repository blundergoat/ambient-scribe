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


@pytest.fixture(scope="session", autouse=True)
def _no_summary_provider_calls():
    """Fail fast if any test reaches the real summary agent.

    This box carries live AWS credentials for approved replay campaigns, so an
    unpatched generation path in a test would silently make a PAID Bedrock
    call (it happened on 2026-07-15 during the schema switch). Tests that
    exercise generation must patch `_generate_validated_v2_draft` or the agent
    itself (their patch simply overrides this stub for their scope); reaching
    this guard is a test bug, never a provider call. Session-scoped with a
    single setattr: a per-test fixture perturbed event-loop timing enough to
    trip the latent grace-destroy/executor race in the transcription tests.
    """
    import agents

    def _blocked_summary_agent(*_args, **_kwargs):
        raise AssertionError(
            "create_summary_agent() reached from a test - patch the generation"
            " draft helper instead of letting the call reach a real provider"
        )

    original_factory = agents.create_summary_agent
    agents.create_summary_agent = _blocked_summary_agent
    yield
    agents.create_summary_agent = original_factory


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
