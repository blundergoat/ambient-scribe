"""
Strands summary agent — generates structured session summaries.

Triggered when a session ends ("End Session" button). Receives the full
role-attributed medical transcript and produces a SOAP note with Subjective,
Objective, Assessment, and Plan sections.

GPU CONSTRAINT: Same as the role agent — Bedrock or CPU-only Ollama.
"""

from __future__ import annotations

import logging
import os
from functools import lru_cache

logger = logging.getLogger(__name__)

SUMMARY_AGENT_MODEL_PROVIDER = os.environ.get("ROLE_AGENT_MODEL_PROVIDER", "bedrock")
SUMMARY_AGENT_MODEL_ID = os.environ.get(
    "ROLE_AGENT_MODEL_ID",
    "us.anthropic.claude-haiku-4-5-20251001-v1:0",
)

_SHARED_SUMMARY_RULES = """
Rules:
- Cite transcript timestamps in square brackets, e.g. [02:15-02:30].
- Keep the summary concise — aim for 200-400 words.
- Use the speaker role names (DOCTOR, PATIENT, etc.), not raw speaker IDs.
- If the transcript is too short or uninformative, say so briefly rather than inventing content.
- Respond with valid JSON only. No text outside the JSON.

Output format:
{
    "title": "Brief session title",
    "sections": [
        {"heading": "Section Name", "content": "Section content with [MM:SS-MM:SS] citations."}
    ],
    "key_points": ["Point 1", "Point 2"],
    "duration_seconds": 0
}
"""

MEDICAL_SUMMARY_PROMPT = f"""You are a medical documentation agent.

You receive a complete, role-attributed consultation transcript.
Generate a SOAP note summarising the encounter.

Required sections:
- **Subjective**: Patient's chief complaint, symptoms, history as reported
- **Objective**: Any examination findings, vitals, or observations mentioned
- **Assessment**: Doctor's working diagnosis or differential
- **Plan**: Prescribed treatment, follow-up instructions, referrals

If the doctor did not explicitly state an assessment or plan, note what was discussed
and indicate that formal documentation was not captured in the transcript.
{_SHARED_SUMMARY_RULES}"""


@lru_cache(maxsize=1)
def create_summary_agent():
    """Create a Strands Agent for session summary generation.

    Uses the same model provider as the role inference agent
    (Bedrock or CPU-only Ollama — never GPU).

    Returns:
        A Strands Agent configured for summary generation.

    Raises:
        RuntimeError: If the agent cannot be created.
    """
    try:
        from strands import Agent

        model = _create_summary_model()

        return Agent(
            model=model,
            tools=[],
            system_prompt=MEDICAL_SUMMARY_PROMPT,
            name="summary",
            agent_id="ambient-scribe-summary",
            trace_attributes={"scribe.specialty": "medical"},
        )
    except Exception as e:
        raise RuntimeError(f"Failed to create summary agent: {e}") from e


def _create_summary_model():
    """Create the model instance for the summary agent.

    Returns:
        A Strands SDK model instance (BedrockModel or OllamaModel).
    """
    if SUMMARY_AGENT_MODEL_PROVIDER == "bedrock":
        from strands.models.bedrock import BedrockModel

        return BedrockModel(
            model_id=SUMMARY_AGENT_MODEL_ID,
            region_name=os.environ.get("AWS_DEFAULT_REGION", "ap-southeast-2"),
            streaming=True,
            max_tokens=2048,
        )
    elif SUMMARY_AGENT_MODEL_PROVIDER == "ollama":
        from strands.models.ollama import OllamaModel

        return OllamaModel(
            host=os.environ.get("OLLAMA_HOST", "http://localhost:11434"),
            model_id=os.environ.get("ROLE_AGENT_OLLAMA_MODEL", "qwen2.5:14b"),
            max_tokens=2048,
        )
    else:
        raise ValueError(
            f"Unknown ROLE_AGENT_MODEL_PROVIDER: {SUMMARY_AGENT_MODEL_PROVIDER}. "
            "Use 'bedrock' or 'ollama'."
        )
