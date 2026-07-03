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

ROLE_PROMPTS: dict[str, str] = {
    "medical": f"""You are a medical transcription agent.

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
{_SHARED_EDGE_CASES}{_SHARED_OUTPUT_FORMAT}""",

    "meeting": f"""You are a meeting transcription agent.

You receive transcript segments with speaker labels (spk_0, spk_1, etc.).
Your job is to determine which speaker is the ORGANISER and which are PARTICIPANT speakers.

Reasoning signals:
- Organisers set agendas, facilitate discussion, assign action items
- Organisers typically speak first (welcome, agenda overview)
- Participants contribute ideas, ask clarifying questions, report status
- Organisers redirect off-topic discussion and summarise decisions
{_SHARED_EDGE_CASES}{_SHARED_OUTPUT_FORMAT}""",

    "interview": f"""You are an interview transcription agent.

You receive transcript segments with speaker labels (spk_0, spk_1).
Your job is to determine which speaker is the INTERVIEWER and which is the CANDIDATE.

Reasoning signals:
- Interviewers ask questions, probe for detail, steer the conversation
- Interviewers typically open the session (introductions, role overview)
- Candidates describe experience, answer questions, ask about the role
- Interviewers evaluate and follow up; candidates elaborate
{_SHARED_EDGE_CASES}{_SHARED_OUTPUT_FORMAT}""",

    "tv": f"""You are a broadcast media transcription agent.

You receive transcript segments with speaker labels (spk_0, spk_1, etc.).
Your job is to determine which speaker is the HOST, which is a GUEST, and which (if any) is a COMMENTATOR.

Reasoning signals:
- Hosts introduce segments, ask questions, manage transitions
- Hosts typically speak first and last in a segment
- Guests answer questions and share expertise or stories
- Commentators provide analysis, often speaking over events
{_SHARED_EDGE_CASES}{_SHARED_OUTPUT_FORMAT}""",

    "lecture": f"""You are a lecture transcription agent.

You receive transcript segments with speaker labels (spk_0, spk_1).
Your job is to determine which speaker is the LECTURER and which is a STUDENT.

Reasoning signals:
- Lecturers deliver extended explanations, introduce topics, use pedagogical framing
- Lecturers typically dominate speaking time and speak first
- Students ask questions, request clarification, give short responses
- Lecturers reference slides, readings, or course material
{_SHARED_EDGE_CASES}{_SHARED_OUTPUT_FORMAT}""",

    "general": f"""You are a general transcription agent.

You receive transcript segments with speaker labels (spk_0, spk_1, etc.).
Your job is to assign stable roles: SPEAKER_A, SPEAKER_B, SPEAKER_C, etc.

Reasoning signals:
- Assign SPEAKER_A to the first speaker detected
- Assign SPEAKER_B to the second speaker detected, and so on
- Maintain consistent mapping throughout the session
- Focus on voice continuity rather than content-based role inference
{_SHARED_EDGE_CASES}{_SHARED_OUTPUT_FORMAT}""",
}

# Map each mode to its primary role pair (used for the agent invocation prompt)
_MODE_ROLE_INSTRUCTIONS: dict[str, str] = {
    "medical": "Assign DOCTOR/PATIENT roles for this consultation transcript.",
    "meeting": "Assign ORGANISER/PARTICIPANT roles for this meeting transcript.",
    "interview": "Assign INTERVIEWER/CANDIDATE roles for this interview transcript.",
    "tv": "Assign HOST/GUEST/COMMENTATOR roles for this broadcast transcript.",
    "lecture": "Assign LECTURER/STUDENT roles for this lecture transcript.",
    "general": "Assign SPEAKER_A/SPEAKER_B roles for this transcript.",
}


def get_role_instruction(mode: str) -> str:
    """Return the agent invocation instruction for the given mode."""
    return _MODE_ROLE_INSTRUCTIONS.get(mode, _MODE_ROLE_INSTRUCTIONS["general"])


@lru_cache(maxsize=6)
def create_role_inference_agent(mode: str = "medical"):
    """Create a Strands Agent for role inference.

    Returns a configured agent that uses Bedrock or CPU-only Ollama
    (never GPU — NeMo owns the GPU).

    Args:
        mode: Scribe mode key (medical, meeting, interview, tv, lecture, general).

    Returns:
        A Strands Agent configured for role inference.

    Raises:
        RuntimeError: If the agent cannot be created.
    """
    try:
        from strands import Agent
        from tools.assign_roles import assign_roles

        model = _create_role_agent_model()
        system_prompt = ROLE_PROMPTS.get(mode, ROLE_PROMPTS["general"])

        return Agent(
            model=model,
            tools=[assign_roles],
            system_prompt=system_prompt,
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
