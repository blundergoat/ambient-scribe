"""
Summary-agent helper for the FastAPI route layer.

The browser asks `/session/{id}/summary` for a completed visit note. This
module keeps the off-GPU Strands call, schema validation, and optional clinical
context prompt outside `server.py` so the route stays focused on HTTP and
Mercure behavior.
"""

from __future__ import annotations

import logging
from typing import Any

from api.agent_observability import agent_metric_fields as _agent_metric_fields
from clinical_context import retrieve_clinical_context

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class SummaryCitationOutput(BaseModel):
    """
    One source row cited by a generated summary section.

    Use when the browser needs a source chip under a SOAP section, so the
    clinician can trace a claim to transcript evidence. Empty fields mean the
    chip is omitted or rendered with less context, never with invented detail.
    """

    segment_id: str = ""
    start: float | None = None
    end: float | None = None
    role: str | None = None
    text: str = ""


class SummarySectionOutput(BaseModel):
    """
    One generated summary section shown in the browser panel.

    The structured-output tool validates this before the route publishes it.
    Empty content is allowed so sparse but valid summaries can still render
    instead of failing the whole post-visit note. Empty citations mean the
    section stays visible without source chips.
    """

    heading: str = ""
    content: str = ""
    citations: list[SummaryCitationOutput] = Field(default_factory=list)


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
    citation_segments: list[dict[str, Any]] | None = None,
) -> dict | None:
    """Generate the note the clinician sees after pressing Summarise.

    Use after recording stops, so the SOAP panel can appear beside the
    transcript while the GPU remains reserved for speech recognition.

    Args:
        session_id: Session shown in the UI; empty would make the retry/error logs hard to trace.
        transcript: Role-attributed visit text; blank means the browser should get a retryable failure.
        citation_segments: Corrected transcript rows whose IDs may be cited; `None` or empty keeps the
            legacy uncited summary path.

    Returns:
        Parsed summary payload; `None` means the browser should show a generation failure.
    """
    try:
        from agents import create_summary_agent

        agent = create_summary_agent()
        context_snippets = retrieve_clinical_context(transcript)
        agent_result = agent(
            summary_generation_prompt(
                transcript,
                context_snippets,
                citation_segments=citation_segments,
            ),
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

        # No corrected rows were selected, so any model-made citation IDs are stripped before the browser sees them.
        allowed_citation_segments = citation_segments or []
        validated_summary = summary_with_validated_citations(
            structured_summary,
            allowed_citation_segments,
        )
        parsed_summary = validated_summary.model_dump()
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
    citation_segments: list[dict[str, Any]] | None = None,
) -> str:
    """Build the prompt used for the clinician's post-visit summary.

    Use when FastAPI has selected either live rows or corrected rows for the
    note; corrected rows add source IDs so the browser can show citations.

    Args:
        transcript: Role-attributed consultation text; empty means the note would have no useful content.
        context_snippets: Retrieved KB snippets; empty means the prompt has no extra documentation reminders.
        citation_segments: Corrected transcript rows available for source citations; `None` or empty means the
            prompt uses the plain transcript and returns no source chips.

    Returns:
        Prompt text sent to the off-GPU summary model for the clinician's note.
    """
    prompt_parts = ["Generate a medical summary for this session transcript:"]
    # Matched documentation reminders can improve the draft, but the clinician still reviews the note.
    if context_snippets:
        prompt_parts.append(
            "Use these non-exhaustive clinical context notes only as documentation reminders:"
        )
        # One short reminder per match keeps the note grounded without crowding out the transcript.
        for snippet in context_snippets:
            prompt_parts.append(
                f"- {snippet['title']}: {snippet['snippet']} ({snippet['provenance']})"
            )

    # Corrected rows are available, so the model gets stable IDs for source-linked note chips.
    if citation_segments:
        source_index = source_index_text(citation_segments)
        # No corrected row had both text and ID, so the clinician still gets an uncited note.
        if source_index == "":
            prompt_parts.append(transcript)
        else:
            # At least one corrected row is citable, so the model can attach source chips.
            prompt_parts.append(
                "Use only these source IDs in section citations. Put citations in each "
                "section's `citations` array as objects like {\"segment_id\": \"seg-0001\"}. "
                "Do not cite IDs that are not listed here."
            )
            prompt_parts.append(source_index)
    else:
        # No corrected source rows exist yet, so the browser keeps the familiar uncited summary.
        prompt_parts.append(transcript)
    return "\n\n".join(prompt_parts)


def source_index_text(citation_segments: list[dict[str, Any]]) -> str:
    """Format corrected transcript rows as source lines the model may cite.

    Use only for corrected rows selected for the summary, so source IDs match
    the rows the clinician can review after the high-accuracy pass.

    Args:
        citation_segments: Corrected rows selected for summary generation; empty yields an empty source list.

    Returns:
        Plain text source index; rows without text are omitted.
    """
    source_lines: list[str] = []
    # Each corrected row becomes one source the note may cite.
    for segment in citation_segments:
        text = str(segment.get("text", "")).strip()
        # Blank corrected rows do not help the clinician verify a summary claim.
        if text == "":
            continue

        segment_id = str(segment.get("segment_id", "")).strip()
        # Rows without a stable ID cannot become traceable source chips for the clinician.
        if segment_id == "":
            continue

        # Missing role falls back to the raw speaker label so the source still has a visible owner.
        role = segment.get("role") or segment.get("speaker_id") or "UNKNOWN"
        start = _format_seconds(segment.get("start"))
        end = _format_seconds(segment.get("end"))
        source_lines.append(f"[source:{segment_id} {start}-{end} {role}] {text}")

    return "\n".join(source_lines)


def summary_with_validated_citations(
    structured_summary: SessionSummaryOutput,
    citation_segments: list[dict[str, Any]],
) -> SessionSummaryOutput:
    """Return the note with only citations the clinician can trace.

    Use after the model returns structured output, before Mercure or HTTP sends
    the summary to the browser panel.

    Args:
        structured_summary: Model-validated summary object; empty sections render as no summary content.
        citation_segments: Corrected rows the model was allowed to cite; empty strips all citation chips.

    Returns:
        Summary with invalid, duplicate, or blank citation IDs removed.
    """
    source_rows = _citation_source_rows(citation_segments)
    sections: list[SummarySectionOutput] = []

    # Each section keeps its text even when every citation is dropped.
    for section in structured_summary.sections:
        seen_ids: set[str] = set()
        validated_citations: list[SummaryCitationOutput] = []
        # Each model-provided ID must match one source row before the browser can show it.
        for citation in section.citations:
            segment_id = citation.segment_id.strip()
            # Bad or repeated IDs are omitted so the clinician never sees a false source chip.
            if segment_id == "" or segment_id in seen_ids or segment_id not in source_rows:
                continue

            seen_ids.add(segment_id)
            validated_citations.append(source_rows[segment_id])

        sections.append(
            SummarySectionOutput(
                heading=section.heading,
                content=section.content,
                citations=validated_citations,
            )
        )

    return SessionSummaryOutput(
        title=structured_summary.title,
        sections=sections,
        key_points=structured_summary.key_points,
    )


def _citation_source_rows(
    citation_segments: list[dict[str, Any]],
) -> dict[str, SummaryCitationOutput]:
    """Build trusted source-chip payloads from corrected transcript rows.

    Use before validating the model output, so the browser receives timing,
    role, and text from storage rather than from the model.
    """
    source_rows: dict[str, SummaryCitationOutput] = {}
    # Every corrected row with text and an ID can become a browser source chip.
    for segment in citation_segments:
        segment_id = str(segment.get("segment_id", "")).strip()
        text = str(segment.get("text", "")).strip()
        # Rows without text or identity cannot help the clinician verify a claim.
        if segment_id == "" or text == "":
            continue

        source_rows[segment_id] = SummaryCitationOutput(
            segment_id=segment_id,
            start=_float_or_none(segment.get("start")),
            end=_float_or_none(segment.get("end")),
            role=segment.get("role") or segment.get("speaker_id") or "UNKNOWN",
            text=text,
        )

    return source_rows


def _float_or_none(value: Any) -> float | None:
    """Return timing for a source chip, or no timing when storage lacks it.

    Use when a citation still has useful text but no reliable clock position.
    """
    # No timestamp was stored, so the browser can still show the quote without a time.
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _format_seconds(value: Any) -> str:
    """Return a compact timestamp the model can mirror in section content.

    Use in source lines so generated prose and source chips share the same time.
    """
    seconds = _float_or_none(value)
    # Missing timing means the source is still citable by ID, but not by clock time.
    if seconds is None:
        return "??:??"

    total_seconds = max(0, int(seconds))
    minutes = total_seconds // 60
    remainder = total_seconds % 60
    return f"{minutes:02d}:{remainder:02d}"
