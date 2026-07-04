"""
Summary-agent helper for the FastAPI route layer.

The browser asks `/session/{id}/summary` for a completed visit note. This
module keeps the off-GPU Strands call, JSON extraction, and optional clinical
context prompt outside `server.py` so the route stays focused on HTTP and
Mercure behavior.
"""

from __future__ import annotations

import json
import logging
import re

from api.agent_observability import agent_metric_fields as _agent_metric_fields
from clinical_hints import retrieve_clinical_context

logger = logging.getLogger(__name__)


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
        agent_result = agent(summary_generation_prompt(transcript, context_snippets))

        metric_fields = _agent_metric_fields(agent_result, "summary")
        response_text = str(agent_result)
        try:
            parsed_summary = json.loads(response_text)
            parsed_summary["_agent_metrics"] = metric_fields
            return parsed_summary
        except json.JSONDecodeError:
            match = re.search(r"\{.*\}", response_text, re.DOTALL)
            # Some providers prepend prose before JSON; extract the object for the UI.
            if match:
                parsed_summary = json.loads(match.group())
                parsed_summary["_agent_metrics"] = metric_fields
                return parsed_summary
            logger.warning(
                "summary.no_json_found",
                extra={
                    "session_id": session_id,
                    **metric_fields,
                },
            )
            return None
    except Exception as exc:
        logger.error(
            "summary.agent_failed",
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
