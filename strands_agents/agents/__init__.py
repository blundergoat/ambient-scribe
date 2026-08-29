"""
Agent registry for the ambient scribe.

Public entry point for the two model-backed agents behind a consultation. Both run off the GPU, which NeMo owns outright:

- Role inference (`transcription_agent`) turns raw spk_0/spk_1 rows into the Doctor and Patient labels the clinician sees.
- Summary (`summary_agent`) drafts the SOAP note once the clinician presses Summarise.

Agents own the model reasoning and system prompts; the sibling `tools` package owns the state those decisions are written into.
The NeMo transcription pipeline is deliberately not an agent: it is a separate GPU-bound pipeline reached through
`NemoPipeline` and never goes through the Strands SDK.
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
