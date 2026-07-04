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
from functools import lru_cache

logger = logging.getLogger(__name__)

# Model configuration for the role inference agent
# NeMo owns the GPU — this agent uses Bedrock or CPU-only Ollama
ROLE_AGENT_MODEL_PROVIDER = os.environ.get("ROLE_AGENT_MODEL_PROVIDER", "bedrock")
ROLE_AGENT_MODEL_ID = os.environ.get(
    "ROLE_AGENT_MODEL_ID",
    "us.anthropic.claude-haiku-4-5-20251001-v1:0",
)

_SHARED_EDGE_CASES = """
Edge cases:
- If only one speaker is present for an extended period, maintain the existing
  mapping. Do NOT reassign roles based on a monologue.
- If diarization labels flip (a known Sortformer issue), detect the flip by
  comparing speech content against established role patterns. Report the correction.
- During silence or minimal speech, return the existing mapping unchanged
  with the same confidence level.

Maintain your speaker-to-role mapping across the session. If you become more
confident over time, update the mapping.

You MUST call the assign_roles tool with your decision. Pass:
- session_id: from the input payload
- mapping: JSON string of speaker→role mapping
- segments: JSON string of the new_segments from the input
- confidence: your confidence (0.0–1.0)
- reasoning: brief explanation

If the tool is unavailable, fall back to responding with valid JSON only:
"""

_SHARED_OUTPUT_FORMAT = """{
    "mapping": {"spk_0": "<ROLE_A>", "spk_1": "<ROLE_B>"},
    "attributed_segments": [{"role": "<ROLE>", "text": "...", "start": 0.0, "end": 1.0}],
    "confidence": 0.85,
    "flip_detected": false,
    "reasoning": "Brief explanation"
}
"""

MEDICAL_ROLE_PROMPT = f"""You are a medical transcription agent.

You receive transcript segments with speaker labels (spk_0, spk_1).
Your job is to determine which speaker is the DOCTOR and which is the PATIENT.
Additional roles you may assign when evidence is strong: NURSE, FAMILY_MEMBER.

Reasoning signals:
- Doctors ask clinical questions, use medical terminology, give instructions
- Patients describe symptoms, ask about treatment, express concerns
- Doctors typically speak first in a consultation (greeting, opening)
- Medical jargon density is higher for the doctor
- Nurses may relay vitals or prep instructions
- Family members advocate or translate for the patient
{_SHARED_EDGE_CASES}{_SHARED_OUTPUT_FORMAT}"""

MEDICAL_ROLE_INSTRUCTION = (
    "Assign DOCTOR/PATIENT roles for this consultation transcript."
)


@lru_cache(maxsize=1)
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
        from tools.assign_roles import assign_roles

        model = _create_role_agent_model()

        return Agent(
            model=model,
            tools=[assign_roles],
            system_prompt=MEDICAL_ROLE_PROMPT,
            name="role-inference",
            agent_id="ambient-scribe-role-inference",
            trace_attributes={"scribe.specialty": "medical"},
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
            model_id=os.environ.get("ROLE_AGENT_OLLAMA_MODEL", "qwen2.5:14b"),
            max_tokens=1024,
        )
    else:
        raise ValueError(
            f"Unknown ROLE_AGENT_MODEL_PROVIDER: {ROLE_AGENT_MODEL_PROVIDER}. "
            "Use 'bedrock' or 'ollama'."
        )
