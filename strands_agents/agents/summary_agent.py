"""
Strands summary agent — generates structured session summaries.

Triggered when a session ends ("End Session" button). Receives the full
role-attributed transcript and produces a mode-appropriate summary:

  - Medical: SOAP note (Subjective, Objective, Assessment, Plan)
  - Meeting: Action items, decisions, attendees, next steps
  - Interview: Key topics, candidate strengths/concerns, follow-ups
  - TV/Media: Key moments, speaker highlights, topics covered
  - Lecture: Key concepts, questions raised, learning objectives
  - General: Key points, speaker contributions, topics covered

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

SUMMARY_PROMPTS: dict[str, str] = {
    "medical": f"""You are a medical documentation agent.

You receive a complete, role-attributed consultation transcript.
Generate a SOAP note summarising the encounter.

Required sections:
- **Subjective**: Patient's chief complaint, symptoms, history as reported
- **Objective**: Any examination findings, vitals, or observations mentioned
- **Assessment**: Doctor's working diagnosis or differential
- **Plan**: Prescribed treatment, follow-up instructions, referrals

If the doctor did not explicitly state an assessment or plan, note what was discussed
and indicate that formal documentation was not captured in the transcript.
{_SHARED_SUMMARY_RULES}""",

    "meeting": f"""You are a meeting documentation agent.

You receive a complete, role-attributed meeting transcript.
Generate a structured meeting summary.

Required sections:
- **Attendees**: List speakers and their roles
- **Agenda Items**: Topics discussed, in order
- **Decisions**: What was decided, by whom
- **Action Items**: Who does what, by when (if mentioned)
- **Next Steps**: Follow-up meetings, deadlines

If no clear decisions or action items were stated, note the discussion topics instead.
{_SHARED_SUMMARY_RULES}""",

    "interview": f"""You are an interview documentation agent.

You receive a complete, role-attributed interview transcript.
Generate a structured interview summary.

Required sections:
- **Position/Context**: Role or topic discussed
- **Key Topics**: Main areas of discussion
- **Candidate Strengths**: Notable skills or experience demonstrated
- **Areas of Concern**: Gaps, unclear answers, or red flags
- **Follow-Up Items**: Questions deferred, next steps mentioned
{_SHARED_SUMMARY_RULES}""",

    "tv": f"""You are a broadcast media documentation agent.

You receive a complete, role-attributed broadcast transcript.
Generate a structured segment summary.

Required sections:
- **Topic**: Main subject of the segment
- **Key Moments**: Notable quotes, revelations, or exchanges
- **Speaker Highlights**: Main contributions from each speaker
- **Context**: Background information referenced
{_SHARED_SUMMARY_RULES}""",

    "lecture": f"""You are an educational documentation agent.

You receive a complete, role-attributed lecture transcript.
Generate a structured lecture summary.

Required sections:
- **Topic**: Main subject of the lecture
- **Key Concepts**: Core ideas and definitions introduced
- **Examples**: Illustrative examples or case studies mentioned
- **Questions Raised**: Student questions and lecturer responses
- **Learning Objectives**: What students should take away
{_SHARED_SUMMARY_RULES}""",

    "general": f"""You are a transcription documentation agent.

You receive a complete, role-attributed conversation transcript.
Generate a structured summary.

Required sections:
- **Overview**: Brief description of the conversation
- **Key Points**: Main topics and ideas discussed
- **Speaker Contributions**: Notable input from each speaker
- **Outcomes**: Any conclusions, agreements, or next steps
{_SHARED_SUMMARY_RULES}""",
}


@lru_cache(maxsize=6)
def create_summary_agent(mode: str = "medical"):
    """Create a Strands Agent for session summary generation.

    Uses the same model provider as the role inference agent
    (Bedrock or CPU-only Ollama — never GPU).

    Args:
        mode: Scribe mode key (medical, meeting, interview, tv, lecture, general).

    Returns:
        A Strands Agent configured for summary generation.

    Raises:
        RuntimeError: If the agent cannot be created.
    """
    try:
        from strands import Agent

        model = _create_summary_model()
        system_prompt = SUMMARY_PROMPTS.get(mode, SUMMARY_PROMPTS["general"])

        return Agent(
            model=model,
            tools=[],
            system_prompt=system_prompt,
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
