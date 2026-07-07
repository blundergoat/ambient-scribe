"""
Strands summary agent for the browser's final consultation summary.

When the user ends a session, this agent reads the role-attributed transcript
and drafts SOAP-style sections for review. It uses Bedrock or CPU-only Ollama,
never the NeMo GPU, and each call gets a fresh Agent so one visit's transcript
cannot remain in another visit's conversation history.
"""

from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

SUMMARY_AGENT_MODEL_PROVIDER = os.environ.get(
    "SUMMARY_AGENT_MODEL_PROVIDER",
    os.environ.get("ROLE_AGENT_MODEL_PROVIDER", "bedrock"),
)
SUMMARY_AGENT_MODEL_ID = os.environ.get(
    "SUMMARY_AGENT_MODEL_ID",
    os.environ.get(
        "ROLE_AGENT_MODEL_ID",
        "au.anthropic.claude-haiku-4-5-20251001-v1:0",
    ),
)
SUMMARY_AGENT_OLLAMA_MODEL = os.environ.get(
    "SUMMARY_AGENT_OLLAMA_MODEL",
    os.environ.get("ROLE_AGENT_OLLAMA_MODEL", "qwen3.5:9b"),
)
SUMMARY_AGENT_MAX_TOKENS = int(os.environ.get("SUMMARY_AGENT_MAX_TOKENS", "4096"))

_SHARED_SUMMARY_RULES = """
Rules:
- Cite transcript timestamps in square brackets as MM:SS-MM:SS, e.g. [02:15-02:30].
- Timestamps are minutes and seconds: the seconds field is always 00-59. Convert row times
  given in seconds (a row at 196 seconds is [03:16], never [02:76] or [196]).
- Square brackets contain timestamps only - never segment IDs; IDs belong solely in each
  section's `citations` array.
- When the prompt includes source IDs, cite only those IDs in each section's `citations` array.
- Keep the summary concise - aim for 200-400 words.
- Use the speaker role names (DOCTOR, PATIENT, etc.), not raw speaker IDs.
- If the transcript is too short or uninformative, say so briefly rather than inventing content.
- Never assert a clinical fact the transcript does not support.
- When the patient expresses uncertainty ("I don't know", "maybe", "not sure"), document the
  point explicitly as unclear or not established - never resolve it to one side, and never
  infer the answer from surrounding phrasing (a patient answering "I don't know really, it
  just happened" to a sudden-vs-gradual question means onset is UNKNOWN, not sudden).
- Clinical characteristics (onset, severity, laterality, timing) appear only as the speaker
  stated them, preserving the speaker's own certainty.
- Record a negative finding only when the patient explicitly denied it or it was examined;
  absence of mention is not a negative finding, and a clinician question or statement the
  patient never answered is not a denial (transcripts often end mid-question - an unanswered
  "your breathing is okay?" establishes nothing about breathing).
- Patient answers to screening questions are reported history, never examination findings -
  nothing is "intact" or "normal on examination" unless an examination was performed.
- Use the structured summary schema only. No prose outside the result.

Output format:
{
    "title": "Brief session title",
    "sections": [
        {
            "heading": "Section Name",
            "content": "Section content with [MM:SS-MM:SS] citations.",
            "citations": [{"segment_id": "seg-0001"}]
        }
    ],
    "key_points": ["Point 1", "Point 2"]
}
"""

MEDICAL_SUMMARY_PROMPT = f"""You are a medical documentation agent.

You receive a complete, role-attributed consultation transcript.
Generate a SOAP note summarising the encounter.

Required sections:
- **Subjective**: Patient's chief complaint, symptoms, history as reported. State each
  clinical characteristic (onset, severity, timing) exactly as the patient answered - if
  the patient was unsure or did not know, write that the patient was unsure. Never restate
  the clinician's question wording as if it were the patient's answer.
- **Objective**: Only clinician-performed examination findings, vitals, or observations.
  Patient-reported symptoms belong in Subjective. If no examination is documented, say so.
- **Assessment**: Only diagnoses or differentials the clinician stated. Never add
  AI-inferred diagnoses, suggested conditions, or unstated rule-outs. If the clinician
  stated none, say no assessment was documented.
- **Plan**: Prescribed treatment, follow-up instructions, referrals

If the doctor did not explicitly state an assessment or plan, say that none was documented;
you may summarise what was discussed without converting it into a diagnosis or plan.
{_SHARED_SUMMARY_RULES}"""


def create_summary_agent():
    """Create a fresh Strands agent for one summary request.

    Returns:
        Agent configured for off-GPU summary generation and isolated history.

    Raises:
        RuntimeError: When the configured Bedrock or Ollama model cannot be created.
    """
    try:
        from strands import Agent

        model = _create_summary_model()

        return Agent(
            model=model,
            tools=[],
            system_prompt=MEDICAL_SUMMARY_PROMPT,
            callback_handler=None,
            name="summary",
            agent_id="ambient-scribe-summary",
            trace_attributes={"scribe.specialty": "medical"},
        )
    except Exception as e:
        raise RuntimeError(f"Failed to create summary agent: {e}") from e


def _create_summary_model():
    """Create the off-GPU model used for summary generation.

    Returns:
        Bedrock or CPU-only Ollama model; never a GPU-backed local model.
    """
    # Bedrock is the default path for clinician-ready final summaries.
    if SUMMARY_AGENT_MODEL_PROVIDER == "bedrock":
        from strands.models.bedrock import BedrockModel

        return BedrockModel(
            model_id=SUMMARY_AGENT_MODEL_ID,
            region_name=os.environ.get("AWS_DEFAULT_REGION") or "ap-southeast-2",
            streaming=True,
            max_tokens=SUMMARY_AGENT_MAX_TOKENS,
        )
    # Ollama remains CPU-only for local development because NeMo owns the GPU.
    elif SUMMARY_AGENT_MODEL_PROVIDER == "ollama":
        from strands.models.ollama import OllamaModel

        return OllamaModel(
            host=os.environ.get("OLLAMA_HOST", "http://localhost:11434"),
            model_id=SUMMARY_AGENT_OLLAMA_MODEL,
            max_tokens=SUMMARY_AGENT_MAX_TOKENS,
        )
    else:
        raise ValueError(
            f"Unknown SUMMARY_AGENT_MODEL_PROVIDER: {SUMMARY_AGENT_MODEL_PROVIDER}. "
            "Use 'bedrock' or 'ollama'."
        )
