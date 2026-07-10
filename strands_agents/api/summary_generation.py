"""
Summary-agent helper for the FastAPI route layer.

The browser asks `/session/{id}/summary` for a completed visit note. This
module keeps the off-GPU Strands call, schema validation, and optional clinical
context prompt outside `server.py` so the route stays focused on HTTP and
Mercure behavior.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from api.agent_observability import agent_metric_fields as _agent_metric_fields
from api.summary_fidelity import (
    FidelityViolation,
    find_fidelity_violations,
    regeneration_feedback,
    summary_with_unverified_flags,
)
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
    transcript_segments: list[dict[str, Any]] | None = None,
    citation_source_index: str | None = None,
) -> dict | None:
    """Generate the note the clinician sees after pressing Summarise.

    Use after recording stops, so the SOAP panel can appear beside the
    transcript while the GPU remains reserved for speech recognition. Drafts
    failing the deterministic fidelity checks get ONE regeneration; sentences
    that still fail ship visibly flagged, never silently stripped (M07).

    Args:
        session_id: Session shown in the UI; empty would make the retry/error logs hard to trace.
        transcript: Role-attributed visit text; blank means the browser should get a retryable failure.
        citation_segments: Corrected transcript rows whose IDs may be cited; `None` or empty keeps the
            legacy uncited summary path.
        transcript_segments: Visit rows (role/text) the fidelity checks verify against; `None` or
            empty skips fidelity checking, so the note ships exactly as generated.
        citation_source_index: Preformatted selected citation rows; null preserves legacy
            formatting, while a truncation marker can separate selected opening/tail runs.

    Returns:
        Parsed summary payload; `None` means the browser should show a generation failure.
    """
    try:
        context_snippets = retrieve_clinical_context(transcript)
        base_prompt = summary_generation_prompt(
            transcript,
            context_snippets,
            citation_segments=citation_segments,
            citation_source_index=citation_source_index,
        )

        violations: list[FidelityViolation] = []
        drafts: list[tuple[SessionSummaryOutput, list[FidelityViolation], dict[str, Any]]] = []
        # One clean draft plus at most one fidelity-guided redo keeps the wait
        # after "Summarise" bounded while still fixing most fabrications.
        for attempt in (0, 1):
            prompt = base_prompt
            # The redo names each rejected sentence so the model cannot miss it.
            if violations:
                prompt = base_prompt + regeneration_feedback(violations)

            validated_summary, metric_fields = _generate_validated_draft(
                session_id, prompt, citation_segments
            )
            # Without a validated object, the browser should show a retryable failure.
            if validated_summary is None:
                return None

            violations = find_fidelity_violations(
                [section.model_dump() for section in validated_summary.sections],
                list(validated_summary.key_points),
                transcript_segments or [],
            )
            drafts.append((validated_summary, violations, metric_fields))
            # A fidelity-clean draft is the note the clinician gets - done.
            if not violations:
                break

            _log_fidelity_violations(session_id, attempt, violations)

        # The redo must never make the note worse: ship whichever draft has
        # fewer unsupported sentences; a tie keeps the redo, which followed the
        # named-sentence feedback (M10 field case: a 1-violation first draft
        # was once replaced by a 3-violation retry).
        selected_attempt = min(
            range(len(drafts)), key=lambda index: (len(drafts[index][1]), -index)
        )
        validated_summary, violations, metric_fields = drafts[selected_attempt]
        if len(drafts) > 1:
            logger.warning(
                "summary.fidelity_draft_selected session_id=%s selected_attempt=%s"
                " attempt_0_count=%s attempt_1_count=%s",
                session_id,
                selected_attempt,
                len(drafts[0][1]),
                len(drafts[1][1]),
                extra={
                    "session_id": session_id,
                    "selected_attempt": selected_attempt,
                    "attempt_0_count": len(drafts[0][1]),
                    "attempt_1_count": len(drafts[1][1]),
                },
            )

        parsed_summary = validated_summary.model_dump()
        # Sentences the redo could not support stay visible but marked, so the
        # clinician sees exactly which claims lack transcript evidence.
        if violations:
            parsed_summary = summary_with_unverified_flags(parsed_summary, violations)
            logger.warning(
                "summary.fidelity_flagged session_id=%s flagged=%s",
                session_id,
                len(violations),
                extra={"session_id": session_id, "flagged": len(violations)},
            )
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


def _log_fidelity_violations(
    session_id: str, attempt: int, violations: list[FidelityViolation]
) -> None:
    """Log one draft's fidelity failures without exposing transcript text.

    Args:
        session_id: Session shown in the UI, for traceable logs.
        attempt: Which draft failed; `0` means the redo is about to run.
        violations: Failures found in this draft; never empty when called.
    """
    rules = sorted({violation.rule for violation in violations})
    # Each entry identifies where and why WITHOUT clinical prose: note
    # sentences, topics, and content-derived hashes must never reach logs.
    violation_fields = [
        {
            "ordinal": ordinal,
            "rule": violation.rule,
            "subtype": violation.subtype,
            "location": violation.location,
            "sentence_ordinal": violation.sentence_ordinal,
            "word_count": violation.word_count,
        }
        for ordinal, violation in enumerate(violations)
    ]
    logger.warning(
        "summary.fidelity_violations session_id=%s attempt=%s count=%s rules=%s",
        session_id,
        attempt,
        len(violations),
        rules,
        extra={
            "session_id": session_id,
            "attempt": attempt,
            "count": len(violations),
            "rules": rules,
            "violations": violation_fields,
        },
    )


def _generate_validated_draft(
    session_id: str,
    prompt: str,
    citation_segments: list[dict[str, Any]] | None,
) -> tuple[SessionSummaryOutput | None, dict[str, Any]]:
    """Run one summary draft through the agent, citation validation, and text cleanup.

    Use once per fidelity attempt: the first call drafts the note, a second
    call (with the rejection feedback appended to the prompt) redoes it.

    Args:
        session_id: Session shown in the UI, for traceable logs.
        prompt: Full generation prompt, possibly carrying fidelity rejection feedback.
        citation_segments: Corrected rows whose IDs may be cited; `None` or empty strips
            any model-made citation IDs so the page never shows dead source chips.

    Returns:
        Validated summary plus agent metrics; a `None` summary means structured output
        was missing and the browser should show a retryable generation failure.
    """
    from agents import create_summary_agent

    agent = create_summary_agent()
    agent_result = agent(prompt, structured_output_model=SessionSummaryOutput)

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
        return None, metric_fields

    # No corrected rows were selected, so any model-made citation IDs are stripped before the browser sees them.
    validated_summary = summary_with_validated_citations(
        structured_summary,
        citation_segments or [],
        session_id=session_id,
    )
    # Provenance lives in the structured citations; the prose the clinician
    # reads must not repeat reference markers as text.
    validated_summary = summary_with_clean_display_text(
        validated_summary, session_id=session_id
    )

    return validated_summary, metric_fields


def summary_generation_prompt(
    transcript: str,
    context_snippets: list[dict[str, str]],
    citation_segments: list[dict[str, Any]] | None = None,
    citation_source_index: str | None = None,
) -> str:
    """Build the prompt used for the clinician's post-visit summary.

    Use when FastAPI has selected either live rows or corrected rows for the
    note; corrected rows add source IDs so the browser can show citations.

    Args:
        transcript: Role-attributed consultation text; empty means the note would have no useful content.
        context_snippets: Retrieved KB snippets; empty means the prompt has no extra documentation reminders.
        citation_segments: Corrected transcript rows available for source citations; `None` or empty means the
            prompt uses the plain transcript and returns no source chips.
        citation_source_index: Optional preformatted source rows selected by the request layer;
            null derives the unchanged source index from `citation_segments`.

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
        source_index = (
            citation_source_index
            if citation_source_index is not None
            else source_index_text(citation_segments)
        )
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


# One bracketed reference marker as the model writes them into prose: a time
# or time range (legacy invalid seconds included), a segment ID with optional
# "to" range or trailing time range, or comma-joined lists of those. Anything
# else inside brackets is clinical text and must stay untouched.
_REFERENCE_TIME = r"\d{1,3}:\d{2,3}(?:-\d{1,3}:\d{2,3})?"
_REFERENCE_ID = r"(?:corrected|seg)-\d{1,6}(?:-\d{1,3})*"
_REFERENCE_UNIT = (
    rf"(?:{_REFERENCE_ID}(?:\s+to\s+{_REFERENCE_ID})?(?:\s+{_REFERENCE_TIME})?"
    rf"|{_REFERENCE_TIME})"
)
_INLINE_REFERENCE = re.compile(
    rf"\s*\[{_REFERENCE_UNIT}(?:\s*,\s*{_REFERENCE_UNIT})*\]"
)
_SPACE_BEFORE_PUNCTUATION = re.compile(r"\s+([.,;:!?])")


def strip_inline_reference_text(content: str) -> str:
    """Remove inline reference markers from prose the clinician reads.

    Use on summary section text and key points before they reach the browser:
    provenance stays available through each section's structured citations, so
    the note reads clean. Bracketed clinical wording ("[severe]") is not a
    reference and passes through unchanged.

    Args:
        content: Generated prose; empty means there is nothing to clean.

    Returns:
        The prose without reference markers or the doubled spacing they leave.
    """
    stripped = _INLINE_REFERENCE.sub("", content)
    # Removing a marker before punctuation would otherwise leave "fever ."
    stripped = _SPACE_BEFORE_PUNCTUATION.sub(r"\1", stripped)
    return re.sub(r"  +", " ", stripped).strip() if stripped != content else content


def summary_with_clean_display_text(
    structured_summary: SessionSummaryOutput,
    session_id: str = "",
) -> SessionSummaryOutput:
    """Return the note with reference-free prose and untouched citations.

    Use after citation validation, before Mercure or HTTP sends the summary to
    the browser panel, so both delivery paths show the same clean note.

    Args:
        structured_summary: Validated summary; empty sections pass through.
        session_id: Session shown in the UI; empty only in unit tests.

    Returns:
        A copy whose section content and key points carry no inline markers.
    """
    stripped_count = 0
    cleaned_sections = []
    # Each section's prose is cleaned; its citations array is the provenance record.
    for section in structured_summary.sections:
        cleaned_content = strip_inline_reference_text(section.content)
        # A changed section means at least one marker left the visible note.
        if cleaned_content != section.content:
            stripped_count += 1
        cleaned_sections.append(section.model_copy(update={"content": cleaned_content}))

    cleaned_key_points = []
    # Key points render as the TL;DR strip, so they get the same treatment.
    for key_point in structured_summary.key_points:
        cleaned_point = strip_inline_reference_text(key_point)
        if cleaned_point != key_point:
            stripped_count += 1
        cleaned_key_points.append(cleaned_point)

    # Counts only - transcript content never reaches the logs.
    if stripped_count:
        logger.info(
            "summary.reference_markers_stripped session_id=%s blocks=%s",
            session_id,
            stripped_count,
            extra={"session_id": session_id, "stripped_blocks": stripped_count},
        )

    return structured_summary.model_copy(
        update={"sections": cleaned_sections, "key_points": cleaned_key_points}
    )


# Only IDs in the synthetic reference shape may be echoed to logs; anything else
# is model-fabricated free text and could repeat transcript content.
_SAFE_LOG_ID = re.compile(rf"^{_REFERENCE_ID}$")
_DROPPED_ID_LOG_LIMIT = 5


def summary_with_validated_citations(
    structured_summary: SessionSummaryOutput,
    citation_segments: list[dict[str, Any]],
    session_id: str = "",
) -> SessionSummaryOutput:
    """Return the note with only citations the clinician can trace.

    Use after the model returns structured output, before Mercure or HTTP sends
    the summary to the browser panel.

    Args:
        structured_summary: Model-validated summary object; empty sections render as no summary content.
        citation_segments: Corrected rows the model was allowed to cite; empty strips all citation chips.
        session_id: Session shown in the UI; empty only in unit tests.

    Returns:
        Summary with invalid, duplicate, or blank citation IDs removed.
    """
    source_rows = _citation_source_rows(citation_segments)
    sections: list[SummarySectionOutput] = []
    blank_count = 0
    duplicate_count = 0
    unresolved_ids: list[str] = []

    # Each section keeps its text even when every citation is dropped.
    for section in structured_summary.sections:
        seen_ids: set[str] = set()
        validated_citations: list[SummaryCitationOutput] = []
        # Each model-provided ID must match one source row before the browser can show it.
        for citation in section.citations:
            segment_id = citation.segment_id.strip()
            # Bad or repeated IDs are omitted so the clinician never sees a false source chip.
            if segment_id == "":
                blank_count += 1
                continue
            if segment_id in seen_ids:
                duplicate_count += 1
                continue
            if segment_id not in source_rows:
                unresolved_ids.append(segment_id)
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

    # Dropped citations mean lost provenance, so the event is worth a warning;
    # only counts and pattern-safe synthetic IDs reach the logs.
    if blank_count or duplicate_count or unresolved_ids:
        loggable_ids = list(
            dict.fromkeys(
                candidate
                for candidate in unresolved_ids
                if _SAFE_LOG_ID.fullmatch(candidate)
            )
        )[:_DROPPED_ID_LOG_LIMIT]
        logger.warning(
            "summary.citations_dropped session_id=%s unresolved=%s duplicates=%s blank=%s ids=%s",
            session_id,
            len(unresolved_ids),
            duplicate_count,
            blank_count,
            loggable_ids,
            extra={
                "session_id": session_id,
                "unresolved_citations": len(unresolved_ids),
                "duplicate_citations": duplicate_count,
                "blank_citations": blank_count,
                "unresolved_ids_sample": loggable_ids,
            },
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
