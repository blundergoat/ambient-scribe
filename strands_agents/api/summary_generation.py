"""
Summary-agent helper for the FastAPI route layer.

The browser asks `/session/{id}/summary` for a completed visit note. This
module keeps the off-GPU Strands call, schema validation, and optional clinical
context prompt outside `server.py` so the route stays focused on HTTP and
Mercure behavior.
"""

from __future__ import annotations

import logging

from api.agent_observability import agent_metric_fields as _agent_metric_fields
from clinical_hints import retrieve_clinical_context
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class SummarySectionOutput(BaseModel):
    """
    One generated summary section shown in the browser panel.

    The structured-output tool validates this before the route publishes it.
    Empty content is allowed so sparse but valid summaries can still render
    instead of failing the whole post-visit note.
    """

    heading: str = ""
    content: str = ""


class SessionSummaryOutput(BaseModel):
    """
    Schema for the generated post-consultation summary.

    Use as the Strands `structured_output_model` for summary calls. The browser
    renders the title, SOAP-style sections, and key points; missing structured
    output makes generation fail cleanly instead of scraping prose.
    """

    title: str = ""
    sections: list[SummarySectionOutput] = Field(default_factory=list)
    key_points: list[str] = Field(default_factory=list)


def run_summary_generation(
    session_id: str,
    transcript: str,
) -> dict | None:
    """Run the summary agent synchronously for a completed consultation.

    Args:
        session_id: Session shown in the UI; empty would make logs hard to correlate.
        transcript: Role-attributed visit text; blank makes summary generation unhelpful.

    Returns:
        Parsed summary payload; `None` means the browser should show a generation failure.
    """
    try:
        from agents import create_summary_agent

        agent = create_summary_agent()
        context_snippets = retrieve_clinical_context(transcript)
        agent_result = agent(
            summary_generation_prompt(transcript, context_snippets),
            structured_output_model=SessionSummaryOutput,
        )

        metric_fields = _agent_metric_fields(agent_result, "summary")
        structured_summary = getattr(agent_result, "structured_output", None)
        # Without a validated object, the browser should show a retryable failure.
        if not isinstance(structured_summary, SessionSummaryOutput):
            logger.warning(
                "summary.structured_output_missing session_id=%s output_type=%s",
                session_id,
                type(structured_summary).__name__,
                extra={
                    "session_id": session_id,
                    "output_type": type(structured_summary).__name__,
                    **metric_fields,
                },
            )
            return None

        parsed_summary = structured_summary.model_dump()
        parsed_summary["_agent_metrics"] = metric_fields
        return parsed_summary
    except Exception as exc:
        logger.error(
            "summary.agent_failed session_id=%s %s: %s",
            session_id,
            type(exc).__name__,
            str(exc)[:200],
            exc_info=exc,
            extra={
                "session_id": session_id,
                "error_type": type(exc).__name__,
                "error": str(exc)[:200],
            },
        )
        return None


def summary_generation_prompt(
    transcript: str,
    context_snippets: list[dict[str, str]],
) -> str:
    """Build the summary request with optional PoC clinical context.

    Args:
        transcript: Role-attributed consultation text; empty makes the summary uninformative.
        context_snippets: Retrieved KB snippets; empty means no grounding section is added.

    Returns:
        Prompt text sent to the off-GPU summary model for the clinician's note.
    """
    prompt_parts = ["Generate a medical summary for this session transcript:"]
    # Retrieved snippets ground the SOAP draft without replacing clinician judgement.
    if context_snippets:
        prompt_parts.append(
            "Use these non-exhaustive clinical context notes only as documentation reminders:"
        )
        # Each snippet stays short so the model sees provenance without transcript leakage.
        for snippet in context_snippets:
            prompt_parts.append(
                f"- {snippet['title']}: {snippet['snippet']} ({snippet['provenance']})"
            )

    prompt_parts.append(transcript)
    return "\n\n".join(prompt_parts)
