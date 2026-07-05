"""
Strands role inference agent for browser-visible DOCTOR/PATIENT labels.

Raw transcript rows reach the UI first; this off-loop agent later reads bounded
speaker evidence and commits role labels through `assign_roles`. NeMo keeps the
GPU, so this agent must use Bedrock or CPU-only Ollama. Tool calls stay compact
so long visits keep receiving role updates instead of raw speaker labels.
"""

from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

# Model configuration for the role inference agent
# NeMo owns the GPU - this agent uses Bedrock or CPU-only Ollama
ROLE_AGENT_MODEL_PROVIDER = os.environ.get("ROLE_AGENT_MODEL_PROVIDER", "bedrock")
ROLE_AGENT_MODEL_ID = os.environ.get(
    "ROLE_AGENT_MODEL_ID",
    "au.anthropic.claude-haiku-4-5-20251001-v1:0",
)
ROLE_AGENT_MAX_TOKENS = int(os.environ.get("ROLE_AGENT_MAX_TOKENS", "2048"))

_SHARED_EDGE_CASES = """
Edge cases:
- If only one speaker is present for an extended period, maintain the existing
  mapping. Do NOT reassign roles based on a monologue.
- If diarization labels flip (a known Sortformer issue), detect the flip by
  comparing recent speech content against established DOCTOR/PATIENT patterns.
  Change an established mapping only when both speakers' roles clearly swap.
- During silence or minimal speech, return the existing mapping unchanged
  with the same confidence level.

Maintain your speaker-to-role mapping across the session. If you become more
confident over time, update the mapping.

You MUST call the assign_roles tool with your decision. Pass:
- session_id: from the input payload
- mapping: JSON string of speaker→role mapping using only DOCTOR or PATIENT
- confidence: your confidence (0.0–1.0)
- reasoning: 2 sentences or fewer; do not restate transcript segments

Call assign_roles on every request, including weak or early evidence. If no
stable mapping exists yet, make the best low-confidence DOCTOR/PATIENT mapping
from the bounded speaker evidence rather than ending the turn without the tool.
Do not answer in prose or JSON outside the tool call.
"""

MEDICAL_ROLE_PROMPT = f"""You are a medical transcription agent.

You receive transcript segments with speaker labels (spk_0, spk_1).
Your job is to determine which speaker is the DOCTOR and which is the PATIENT.

Reasoning signals:
- Doctors ask clinical questions, use medical terminology, give instructions
- Patients describe symptoms, ask about treatment, express concerns
- Doctors typically speak first in a consultation (greeting, opening)
- Medical jargon density is higher for the doctor
{_SHARED_EDGE_CASES}"""

MEDICAL_ROLE_INSTRUCTION = (
    "Assign DOCTOR/PATIENT roles for this consultation transcript."
)


def create_role_inference_agent():
    """Create the Strands agent that relabels transcript speakers.

    Returns:
        Fresh Agent configured for off-GPU role inference and isolated session state.

    Raises:
        RuntimeError: When the configured Bedrock or Ollama model cannot be created.
    """
    try:
        from strands import Agent
        from tools.assign_roles import assign_roles

        model = _create_role_agent_model()

        return Agent(
            model=model,
            tools=[assign_roles],
            system_prompt=MEDICAL_ROLE_PROMPT,
            callback_handler=None,
            name="role-inference",
            agent_id="ambient-scribe-role-inference",
            trace_attributes={"scribe.specialty": "medical"},
        )
    except Exception as e:
        raise RuntimeError(f"Failed to create role inference agent: {e}") from e


def _create_role_agent_model():
    """Create the off-GPU model used for role labels.

    Returns:
        Bedrock or CPU-only Ollama model; never a GPU-backed local model.
    """
    # Bedrock is the default path for clinician-ready summaries and role labels.
    if ROLE_AGENT_MODEL_PROVIDER == "bedrock":
        from strands.models.bedrock import BedrockModel

        return BedrockModel(
            model_id=ROLE_AGENT_MODEL_ID,
            region_name=os.environ.get("AWS_DEFAULT_REGION", "ap-southeast-2"),
            streaming=True,
            max_tokens=ROLE_AGENT_MAX_TOKENS,
        )
    # Ollama remains CPU-only for local development because NeMo owns the GPU.
    elif ROLE_AGENT_MODEL_PROVIDER == "ollama":
        from strands.models.ollama import OllamaModel

        return OllamaModel(
            host=os.environ.get("OLLAMA_HOST", "http://localhost:11434"),
            model_id=os.environ.get("ROLE_AGENT_OLLAMA_MODEL", "qwen3.5:9b"),
            max_tokens=ROLE_AGENT_MAX_TOKENS,
        )
    else:
        raise ValueError(
            f"Unknown ROLE_AGENT_MODEL_PROVIDER: {ROLE_AGENT_MODEL_PROVIDER}. "
            "Use 'bedrock' or 'ollama'."
        )
