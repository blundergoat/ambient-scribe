"""Re-export for backward compatibility. Agent moved to agents/transcription_agent.py."""

from agents.transcription_agent import (  # noqa: F401
    ROLE_AGENT_MODEL_ID,
    ROLE_AGENT_MODEL_PROVIDER,
    ROLE_INFERENCE_SYSTEM_PROMPT,
    create_role_inference_agent,
)
