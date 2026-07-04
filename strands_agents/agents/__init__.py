"""
Agent registry for the ambient scribe.

This module provides the public API for creating agents used by the scribe:

  1. Role inference agent - assigns DOCTOR/PATIENT roles to transcript segments
     (uses Bedrock or CPU-only Ollama, NOT the GPU)
  2. Summary agent - generates structured session summaries on session end

The NeMo transcription pipeline is NOT an agent - it's a separate GPU-bound
pipeline accessed via NemoPipeline. It does not go through the Strands SDK.

DIRECTORY STRUCTURE (halaxy-agents-lab pattern):
  agents/                   - Agent definitions (LLM reasoning, system prompts)
    transcription_agent.py  - Role inference agent
    summary_agent.py        - Session summary agent
  tools/                    - Tool implementations (programmatic state management)
    assign_roles.py         - Role mapping persistence, flip detection, confidence tracking
"""

from agents.summary_agent import MEDICAL_SUMMARY_PROMPT, create_summary_agent
from agents.transcription_agent import (
    MEDICAL_ROLE_INSTRUCTION,
    MEDICAL_ROLE_PROMPT,
    create_role_inference_agent,
)

__all__ = [
    "MEDICAL_ROLE_INSTRUCTION",
    "MEDICAL_ROLE_PROMPT",
    "MEDICAL_SUMMARY_PROMPT",
    "create_role_inference_agent",
    "create_summary_agent",
]
