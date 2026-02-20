"""
Strands role inference agent — assigns DOCTOR/PATIENT roles to transcript segments.

=============================================================================
WHAT THIS FILE DOES
=============================================================================

This module creates a Strands agent that receives raw transcript segments
(labelled spk_0/spk_1 by NeMo) and infers which speaker is the DOCTOR and
which is the PATIENT.

The agent runs ASYNCHRONOUSLY from the main transcription pipeline:
  - Raw segments are published to Mercure immediately (low latency)
  - Role inference runs in a background queue (higher latency, acceptable)
  - Role updates are published to a separate Mercure topic

This separation means:
  1. The hot path (audio → NeMo → raw transcript) has no LLM overhead
  2. Role inference failures degrade gracefully (raw labels remain visible)
  3. Progressive confidence is a natural UX pattern, not a bolt-on

=============================================================================
TOOL BOUNDARY
=============================================================================

The assign_roles TOOL does programmatic state management:
  - Persists speaker→role mapping across invocations
  - Detects diarization label flips
  - Returns structured output (Pydantic, not free-text)
  - Tracks confidence as a running average

The AGENT's system prompt handles the reasoning:
  - Analyses speech content for clinical signals
  - Decides which speaker matches DOCTOR vs PATIENT patterns
  - Handles edge cases (monologues, silence, ambiguity)

=============================================================================
GPU CONSTRAINT
=============================================================================

NeMo owns the GPU exclusively. This agent MUST use:
  - AWS Bedrock (production/demo)
  - CPU-only Ollama with a small model (local dev, slower)

Do NOT configure this agent to use a GPU-accelerated model.
"""

from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

# Model configuration for the role inference agent
# NeMo owns the GPU — this agent uses Bedrock or CPU-only Ollama
ROLE_AGENT_MODEL_PROVIDER = os.environ.get("ROLE_AGENT_MODEL_PROVIDER", "bedrock")
ROLE_AGENT_MODEL_ID = os.environ.get(
    "ROLE_AGENT_MODEL_ID",
    "us.anthropic.claude-haiku-4-5-20251001-v1:0",
)

ROLE_INFERENCE_SYSTEM_PROMPT = """You are a medical transcription agent.

You receive transcript segments with speaker labels (spk_0, spk_1).
Your job is to determine which speaker is the DOCTOR and which is the PATIENT.

Reasoning signals:
- Doctors ask clinical questions, use medical terminology, give instructions
- Patients describe symptoms, ask about treatment, express concerns
- Doctors typically speak first in a consultation (greeting, opening)
- Medical jargon density is higher for the doctor

Edge cases:
- If only one speaker is present for an extended period, maintain the existing
  mapping. Do NOT reassign roles based on a monologue.
- If diarization labels flip (a known Sortformer issue), detect the flip by
  comparing speech content against established role patterns. Report the correction.
- During silence or minimal speech, return the existing mapping unchanged
  with the same confidence level.

Maintain your speaker→role mapping across the session. If you become more
confident over time, update the mapping.

You MUST respond with valid JSON only. No explanation text outside the JSON.
Output format:
{
    "mapping": {"spk_0": "DOCTOR", "spk_1": "PATIENT"},
    "attributed_segments": [{"role": "DOCTOR", "text": "...", "start": 0.0, "end": 1.0}],
    "confidence": 0.85,
    "flip_detected": false,
    "reasoning": "Brief explanation"
}
"""


def create_role_inference_agent():
    """Create a Strands Agent for role inference.

    Returns a configured agent that uses Bedrock or CPU-only Ollama
    (never GPU — NeMo owns the GPU).

    Returns:
        A Strands Agent configured for role inference.

    Raises:
        RuntimeError: If the agent cannot be created.
    """
    try:
        from strands import Agent

        model = _create_role_agent_model()

        return Agent(
            model=model,
            tools=[],  # Tools will be added in Milestone 3 (assign_roles tool)
            system_prompt=ROLE_INFERENCE_SYSTEM_PROMPT,
        )
    except Exception as e:
        raise RuntimeError(f"Failed to create role inference agent: {e}") from e


def _create_role_agent_model():
    """Create the model instance for the role inference agent.

    Returns:
        A Strands SDK model instance (BedrockModel or OllamaModel).
    """
    if ROLE_AGENT_MODEL_PROVIDER == "bedrock":
        from strands.models.bedrock import BedrockModel

        return BedrockModel(
            model_id=ROLE_AGENT_MODEL_ID,
            region_name=os.environ.get("AWS_DEFAULT_REGION", "ap-southeast-2"),
            streaming=True,
            max_tokens=1024,
        )
    elif ROLE_AGENT_MODEL_PROVIDER == "ollama":
        from strands.models.ollama import OllamaModel

        return OllamaModel(
            host=os.environ.get("OLLAMA_HOST", "http://localhost:11434"),
            model_id=os.environ.get("ROLE_AGENT_OLLAMA_MODEL", "llama3.1:8b"),
            max_tokens=1024,
        )
    else:
        raise ValueError(
            f"Unknown ROLE_AGENT_MODEL_PROVIDER: {ROLE_AGENT_MODEL_PROVIDER}. "
            "Use 'bedrock' or 'ollama'."
        )
