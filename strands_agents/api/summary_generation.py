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
from api.summary_confidence import (
    CORRECTED_NOTE_REVIEW_THRESHOLD,
    _sentence_needs_wording_review,
    note_review_reasons,
)
from api.summary_fidelity import (
    FidelityViolation,
    _extract_quoted_spans,
    find_fidelity_violations,
    note_coverage_review_reasons,
    regeneration_feedback,
)
from clinical_context import retrieve_clinical_context

from pydantic import BaseModel, Field
from strands.types.exceptions import MaxTokensReachedException

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
        Parsed summary payload. `None` means the browser should show a
        retryable generation failure; `{"status": "failed", "reason": ...}`
        names a specific non-retryable failure (currently only
        `note_output_limit`) for honest browser copy.
    """
    try:
        context_snippets = retrieve_clinical_context(transcript)
        # Units are built BEFORE generation: their ids are the only citation
        # targets the model ever sees, so it cannot cite an arbitrary row.
        source_units = build_source_units(citation_segments or [])
        base_prompt = summary_generation_prompt_v2(
            transcript, context_snippets, source_units
        )

        violations: list[FidelityViolation] = []
        drafts: list[
            tuple[SessionSummaryV2Output, list[FidelityViolation], dict[str, Any]]
        ] = []
        # One clean draft plus at most one fidelity-guided redo keeps the wait
        # after "Summarise" bounded while still fixing most fabrications.
        for attempt in (0, 1):
            prompt = base_prompt
            # The redo names each rejected sentence so the model cannot miss it.
            if violations:
                prompt = base_prompt + regeneration_feedback(violations)

            try:
                validated_summary, metric_fields = _generate_validated_v2_draft(
                    session_id, prompt, source_units
                )
            except MaxTokensReachedException:
                # Example: a long visit's first draft fit the output budget but
                # carried flags, and the feedback-laden redo overflowed it
                # (consult 5.3 lost a 3-flag note this way). The clinician
                # still gets the flagged first draft instead of an error.
                if not drafts:
                    raise
                logger.warning(
                    "summary.output_limit_retry_kept_first_draft session_id=%s",
                    session_id,
                    extra={"session_id": session_id},
                )
                break
            # Without a validated object, the browser should show a retryable failure.
            if validated_summary is None:
                return None

            detector_sections, key_point_texts = _detector_sections_view(
                validated_summary, source_units
            )
            violations = find_fidelity_violations(
                detector_sections,
                key_point_texts,
                transcript_segments or [],
            )
            # Note-level critical-coverage misses (an answered mental-health
            # screen or a spoken emergency-disposition component absent from
            # the whole note) join the same single bounded retry; their
            # synthetic sentences match no note text, so the payload itself
            # never carries them (M05 - the reason lane surfaces survivors).
            violations.extend(
                FidelityViolation(
                    location="note",
                    sentence=coverage_reason["detail"],
                    rule="critical-coverage",
                    reason=coverage_reason["detail"],
                    subtype=coverage_reason["reason"],
                )
                for coverage_reason in note_coverage_review_reasons(
                    detector_sections,
                    key_point_texts,
                    transcript_segments or [],
                )
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

        # Sentences the redo could not support stay visible but marked, so the
        # clinician sees exactly which claims lack transcript evidence.
        if violations:
            logger.warning(
                "summary.fidelity_flagged session_id=%s flagged=%s",
                session_id,
                len(violations),
                extra={"session_id": session_id, "flagged": len(violations)},
            )

        # The M05 reason lanes attach per claim through the detector view; the
        # full selected rows supply the stored confidence values for linkage.
        detector_sections, key_point_texts = _detector_sections_view(
            validated_summary, source_units
        )
        sentence_reasons = [
            lane_reason
            for lane_reason in note_review_reasons(
                {"sections": detector_sections, "key_points": key_point_texts},
                citation_segments or [],
            )
            if lane_reason.get("section") != "note"
        ]
        # Note-level coverage verifies against the same rows the fidelity loop
        # used, never the citation rows: an uncited live-fallback note has no
        # citation rows, and a surviving critical miss (an answered risk screen
        # absent from the whole note) must still reach the clinician as a cue.
        coverage_reasons = note_coverage_review_reasons(
            detector_sections,
            key_point_texts,
            transcript_segments or [],
        )
        review_reason_count = len(coverage_reasons) + len(sentence_reasons)
        # Counts only keep the reason lanes observable without clinical prose.
        if review_reason_count:
            logger.info(
                "summary.review_reasons session_id=%s reasons=%s",
                session_id,
                review_reason_count,
                extra={"session_id": session_id, "reasons": review_reason_count},
            )

        parsed_summary = hydrated_v2_payload(
            validated_summary,
            source_units,
            citation_segments or [],
            violations,
            coverage_reasons,
            sentence_reasons,
        )
        parsed_summary["_agent_metrics"] = metric_fields
        return parsed_summary
    except MaxTokensReachedException as exc:
        # The provider was reachable and generating - the visit's draft (or
        # its feedback-laden retry) simply exceeded the output budget. This
        # is its own honest failure, so the browser never blames an
        # unavailable model for it.
        logger.error(
            "summary.output_limit session_id=%s %s: %s",
            session_id,
            type(exc).__name__,
            str(exc)[:200],
            extra={
                "session_id": session_id,
                "error_type": type(exc).__name__,
            },
        )
        return {"status": "failed", "reason": "note_output_limit"}
    except Exception as exc:
        # Example: the clinician presses Summarise while provider output fails schema validation.
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


def _generate_validated_v2_draft(
    session_id: str,
    prompt: str,
    source_units: list[dict[str, Any]],
) -> tuple[SessionSummaryV2Output | None, dict[str, Any]]:
    """Run one v2 draft through the agent, unit validation, and text cleanup.

    Use once per fidelity attempt, mirroring the v1 helper: the model output
    is validated against the server-built units and its prose is cleaned of
    inline reference markers before any detector reads it.

    Args:
        session_id: Session shown in the UI, for traceable logs.
        prompt: Full generation prompt, possibly carrying rejection feedback.
        source_units: Server-built units whose ids are the only valid citations.

    Returns:
        Validated v2 summary plus agent metrics; a `None` summary means
        structured output was missing and the browser should show a retryable
        generation failure.
    """
    from agents import create_summary_agent

    agent = create_summary_agent()
    agent_result = agent(prompt, structured_output_model=SessionSummaryV2Output)

    metric_fields = _agent_metric_fields(agent_result, "summary")
    structured_summary = getattr(agent_result, "structured_output", None)
    # Without a validated object, the browser should show a retryable failure.
    if not isinstance(structured_summary, SessionSummaryV2Output):
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

    validated_summary = validated_v2_summary(
        structured_summary, source_units, session_id=session_id
    )
    # Provenance lives in the unit citations; the prose the clinician reads
    # must not repeat reference markers as text.
    validated_summary = v2_summary_with_clean_display_text(
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
                'section\'s `citations` array as objects like {"segment_id": "seg-0001"}. '
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
        # Example: an older corrected row stores an empty timestamp, so its chip omits the clock.
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


# --- M06 schema v2: atomic claims citing complete source units ---------------
# The model receives complete, server-defined UNIT ids (never row ids) and
# returns atomic claims; the server validates ids, hydrates units from the
# attested selected rows, assigns artifact-local claim ids, and attaches the
# M05 review reasons per claim. Summaries stay ephemeral; the browser keeps a
# v1 renderer as the adapter for old payloads.

SUMMARY_SCHEMA_VERSION = 2
# A turn longer than this subdivides at row boundaries that end a sentence;
# a turn that cannot split cleanly stays whole (frozen at M06 approval).
SOURCE_UNIT_SUBDIVISION_MAX_CHARS = 700
# Display-only neighbor rows shown beside a unit; never counted as evidence.
SOURCE_UNIT_CONTEXT_ROWS = 2

QUOTE_NOT_MATCHED_REASON = "quote_not_matched"
QUOTE_NOT_MATCHED_WORDING = (
    "Quoted wording could not be matched to the cited transcript - verify manually"
)
_VALID_EVIDENCE_BASES = ("source_unit", "transcript_absence", "none")
_SENTENCE_FINAL_ROW_PATTERN = re.compile(r"[.?!][\"')\]]*\s*$")


class ClaimOutput(BaseModel):
    """
    One atomic claim the model asserts, citing complete source units.

    The model supplies text, an evidence basis, and unit ids only; claim ids
    and hydrated evidence are server-owned. An unknown basis or invalid unit
    id is downgraded/dropped during validation, never trusted.
    """

    text: str = ""
    evidence_basis: str = "none"
    source_unit_ids: list[str] = Field(default_factory=list)


class ClaimSectionOutput(BaseModel):
    """One SOAP section as an ordered list of atomic claims."""

    heading: str = ""
    claims: list[ClaimOutput] = Field(default_factory=list)


class SessionSummaryV2Output(BaseModel):
    """
    Schema v2 for the generated post-consultation summary.

    Use as the Strands `structured_output_model`. Key points are claims, not
    strings; string-only key points fail validation by design (M06).
    """

    title: str = ""
    sections: list[ClaimSectionOutput] = Field(default_factory=list)
    key_points: list[ClaimOutput] = Field(default_factory=list)


def _unit_row_view(segment: dict[str, Any]) -> dict[str, Any] | None:
    """One selected row as unit evidence; None when it cannot be cited."""
    segment_id = str(segment.get("segment_id", "")).strip()
    text = str(segment.get("text", "")).strip()
    # Rows without identity or words cannot help a clinician verify a claim.
    if segment_id == "" or text == "":
        return None
    return {
        "segment_id": segment_id,
        "start": _float_or_none(segment.get("start")),
        "end": _float_or_none(segment.get("end")),
        "text": text,
    }


def _unit_id_for_rows(unit_rows: list[dict[str, Any]], part_number: int = 0) -> str:
    """Artifact-local unit id from the first/last row identities."""

    def _suffix(segment_id: str) -> str:
        digit_match = re.search(r"(\d+)$", segment_id)
        return digit_match.group(1) if digit_match else segment_id

    base = f"unit-{_suffix(unit_rows[0]['segment_id'])}-{_suffix(unit_rows[-1]['segment_id'])}"
    return base if part_number == 0 else f"{base}-p{part_number:02d}"


def build_source_units(
    citation_segments: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Construct complete source units from the attested selected rows.

    Use BEFORE model generation: the units' ids are the only citation targets
    the model ever sees. One unit is one contiguous same-speaker turn; any
    opposite-role row (including a backchannel) ends the turn, because units
    are display and citation surfaces, not quote-matching runs.

    Args:
        citation_segments: M02-attested selected rows; empty means the visit
            has no citable source and generation runs uncited.

    Returns:
        Ordered units: {unit_id, role, start, end, rows}; empty for no input.
    """
    turns: list[tuple[str, list[dict[str, Any]]]] = []
    current_role: str | None = None
    # Consecutive rows from one speaker stitch into one complete turn.
    for segment in citation_segments:
        row_view = _unit_row_view(segment)
        if row_view is None:
            continue
        role = str(segment.get("role") or segment.get("speaker_id") or "UNKNOWN")
        if role != current_role:
            turns.append((role, []))
            current_role = role
        turns[-1][1].append(row_view)

    source_units: list[dict[str, Any]] = []
    for role, turn_rows in turns:
        for part_number, unit_rows in enumerate(_subdivided_turn_rows(turn_rows)):
            multi_part = (
                part_number > 0 or len(list(_subdivided_turn_rows(turn_rows))) > 1
            )
            source_units.append(
                {
                    "unit_id": _unit_id_for_rows(
                        unit_rows, part_number + 1 if multi_part else 0
                    ),
                    "role": role,
                    "start": unit_rows[0]["start"],
                    "end": unit_rows[-1]["end"],
                    "rows": unit_rows,
                }
            )
    return source_units


def _subdivided_turn_rows(
    turn_rows: list[dict[str, Any]],
) -> list[list[dict[str, Any]]]:
    """Split one long turn at sentence-ending row boundaries, else keep whole.

    Deterministic: rows accumulate until the joined text would pass the bound
    AND the accumulated run already ends a sentence; a run that cannot end on
    a sentence boundary stays with the whole turn (per the milestone rule).
    """
    joined_length = len(" ".join(row["text"] for row in turn_rows))
    # Short turns are already one readable evidence unit.
    if joined_length <= SOURCE_UNIT_SUBDIVISION_MAX_CHARS:
        return [turn_rows]

    parts: list[list[dict[str, Any]]] = []
    current_part: list[dict[str, Any]] = []
    current_length = 0
    for row in turn_rows:
        row_length = len(row["text"]) + (1 if current_part else 0)
        exceeds = current_length + row_length > SOURCE_UNIT_SUBDIVISION_MAX_CHARS
        ends_sentence = bool(
            current_part
            and _SENTENCE_FINAL_ROW_PATTERN.search(current_part[-1]["text"])
        )
        # Cut only where the previous row finished a sentence.
        if exceeds and ends_sentence:
            parts.append(current_part)
            current_part = []
            current_length = 0
            row_length = len(row["text"])
        current_part.append(row)
        current_length += row_length
    if current_part:
        parts.append(current_part)

    # A turn that never offered a clean cut stays whole rather than splitting
    # mid-sentence.
    if len(parts) <= 1:
        return [turn_rows]
    return parts


def unit_index_text(source_units: list[dict[str, Any]]) -> str:
    """Format the units as the only citation targets the model may use."""
    unit_lines: list[str] = []
    for source_unit in source_units:
        start = _format_seconds(source_unit["start"])
        end = _format_seconds(source_unit["end"])
        unit_text = " ".join(row["text"] for row in source_unit["rows"])
        unit_lines.append(
            f"[unit:{source_unit['unit_id']} {start}-{end} {source_unit['role']}] {unit_text}"
        )
    return "\n".join(unit_lines)


def summary_generation_prompt_v2(
    transcript: str,
    context_snippets: list[dict[str, str]],
    source_units: list[dict[str, Any]],
) -> str:
    """Build the v2 prompt: claims cite complete unit ids or carry an honest basis.

    Args:
        transcript: Role-attributed visit text; always present for the model.
        context_snippets: Retrieved KB reminders; empty adds nothing.
        source_units: Server-defined units; empty means no citable source, so
            every claim must use basis transcript_absence or none.

    Returns:
        Prompt text for the off-GPU summary model.
    """
    prompt_parts = ["Generate a medical summary for this session transcript:"]
    if context_snippets:
        prompt_parts.append(
            "Use these non-exhaustive clinical context notes only as documentation reminders:"
        )
        for snippet in context_snippets:
            prompt_parts.append(
                f"- {snippet['title']}: {snippet['snippet']} ({snippet['provenance']})"
            )

    if source_units:
        prompt_parts.append(
            "Cite evidence ONLY as source unit IDs from the list below, in each"
            ' claim\'s `source_unit_ids` array (e.g. ["unit-0416-0423"]). Unit IDs'
            " are the only permitted citation form; never invent IDs and never"
            " cite row or segment IDs. Set `evidence_basis` to `source_unit` when"
            " citing, `transcript_absence` for a bounded negative supported by"
            " what the transcript covers, or `none` when no evidence exists."
        )
        prompt_parts.append(unit_index_text(source_units))
    else:
        prompt_parts.append(
            "No citable source units exist for this visit, so no claim may cite"
            " evidence and every claim's `source_unit_ids` stays empty. Set"
            " `evidence_basis` to `none` for every statement about what WAS said,"
            " reported, or observed. Reserve `transcript_absence` strictly for"
            " bounded negatives - statements that something is NOT documented or"
            " NOT mentioned in the transcript (for example an absent examination,"
            " assessment, or plan). Never mark a positive clinical statement as"
            " `transcript_absence`."
        )
        prompt_parts.append(transcript)
    return "\n\n".join(prompt_parts)


def validated_v2_summary(
    structured_summary: SessionSummaryV2Output,
    source_units: list[dict[str, Any]],
    session_id: str = "",
) -> SessionSummaryV2Output:
    """Return the v2 note with only unit citations the clinician can trace.

    Blank, duplicate, and foreign unit ids are dropped (the v1 citation
    precedent); a `source_unit` claim left with no valid ids downgrades to
    basis `none` (review-required), and `derived_metadata` always downgrades
    because 0.4.0 has no trusted encounter metadata. The note is never
    rejected wholesale here - source-level rejection belongs to the M02 gates.
    """
    known_unit_ids = {source_unit["unit_id"] for source_unit in source_units}
    dropped_ids: list[str] = []
    downgraded_bases = 0

    def _validated_claim(claim: ClaimOutput) -> ClaimOutput:
        nonlocal downgraded_bases
        seen: set[str] = set()
        valid_ids: list[str] = []
        for unit_id in claim.source_unit_ids:
            candidate = unit_id.strip()
            # Bad or repeated ids are omitted so no false evidence chip renders.
            if candidate == "" or candidate in seen:
                continue
            if candidate not in known_unit_ids:
                dropped_ids.append(candidate)
                continue
            seen.add(candidate)
            valid_ids.append(candidate)

        basis = claim.evidence_basis
        # No trusted metadata exists; a metadata basis cannot be honest yet.
        if basis not in _VALID_EVIDENCE_BASES:
            basis = "none"
            downgraded_bases += 1
        # A citing claim with no surviving evidence must say so.
        if basis == "source_unit" and not valid_ids:
            basis = "none"
            downgraded_bases += 1
        # Absence/none claims never carry unit citations.
        if basis != "source_unit":
            valid_ids = []
        return ClaimOutput(
            text=claim.text, evidence_basis=basis, source_unit_ids=valid_ids
        )

    validated_sections = [
        ClaimSectionOutput(
            heading=section.heading,
            claims=[_validated_claim(claim) for claim in section.claims],
        )
        for section in structured_summary.sections
    ]
    validated_key_points = [
        _validated_claim(claim) for claim in structured_summary.key_points
    ]

    if dropped_ids or downgraded_bases:
        loggable_ids = [
            candidate
            for candidate in dict.fromkeys(dropped_ids)
            if re.fullmatch(r"unit-[\w-]{1,40}", candidate)
        ][:_DROPPED_ID_LOG_LIMIT]
        logger.warning(
            "summary.v2_citations_dropped session_id=%s dropped=%s downgraded=%s ids=%s",
            session_id,
            len(dropped_ids),
            downgraded_bases,
            loggable_ids,
            extra={
                "session_id": session_id,
                "dropped_unit_ids": len(dropped_ids),
                "downgraded_bases": downgraded_bases,
                "dropped_ids_sample": loggable_ids,
            },
        )

    return SessionSummaryV2Output(
        title=structured_summary.title,
        sections=validated_sections,
        key_points=validated_key_points,
    )


def v2_summary_with_clean_display_text(
    structured_summary: SessionSummaryV2Output,
    session_id: str = "",
) -> SessionSummaryV2Output:
    """Strip inline reference markers from every claim's visible prose."""
    stripped_count = 0

    def _cleaned(claim: ClaimOutput) -> ClaimOutput:
        nonlocal stripped_count
        cleaned_text = strip_inline_reference_text(claim.text)
        if cleaned_text != claim.text:
            stripped_count += 1
        return claim.model_copy(update={"text": cleaned_text})

    cleaned_sections = [
        section.model_copy(
            update={"claims": [_cleaned(claim) for claim in section.claims]}
        )
        for section in structured_summary.sections
    ]
    cleaned_key_points = [_cleaned(claim) for claim in structured_summary.key_points]
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


def _detector_sections_view(
    structured_summary: SessionSummaryV2Output,
    source_units: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[str]]:
    """Present v2 claims in the v1 shape the M05 detectors consume.

    Each section's content joins its claim texts (claims are sentences), and
    its citations enumerate the rows of every unit its claims cite, so the
    confidence linkage sees the same evidence the clinician will.
    """
    units_by_id = {source_unit["unit_id"]: source_unit for source_unit in source_units}
    detector_sections: list[dict[str, Any]] = []
    for section in structured_summary.sections:
        cited_rows: list[dict[str, str]] = []
        seen_row_ids: set[str] = set()
        for claim in section.claims:
            for unit_id in claim.source_unit_ids:
                for row in units_by_id.get(unit_id, {}).get("rows", []):
                    if row["segment_id"] in seen_row_ids:
                        continue
                    seen_row_ids.add(row["segment_id"])
                    cited_rows.append({"segment_id": row["segment_id"]})
        detector_sections.append(
            {
                "heading": section.heading,
                "content": " ".join(claim.text for claim in section.claims),
                "citations": cited_rows,
            }
        )
    key_point_texts = [claim.text for claim in structured_summary.key_points]
    return detector_sections, key_point_texts


def _claim_review_reasons(
    claim_text: str,
    lane_reasons: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Reasons whose flagged sentence belongs to this claim's prose."""
    return [
        {
            "reason": lane_reason["reason"],
            "detail": str(lane_reason.get("detail", "")),
            "segment_ids": list(lane_reason.get("segment_ids", [])),
        }
        for lane_reason in lane_reasons
        if lane_reason.get("sentence")
        and (
            lane_reason["sentence"] == claim_text
            or lane_reason["sentence"] in claim_text
        )
    ]


def _claim_quote_state(
    claim: ClaimOutput,
    source_units: list[dict[str, Any]],
) -> tuple[str, list[dict[str, Any]]]:
    """Deterministic exact-quote state for one claim against its cited units.

    Returns:
        (quote_state, extra review reasons). `no_quote` means the claim is a
        paraphrase: source-linked at most, never quote-verified.
    """
    if not _extract_quoted_spans(claim.text):
        return "no_quote", []

    units_by_id = {source_unit["unit_id"]: source_unit for source_unit in source_units}
    cited_rows = [
        {"role": str(units_by_id[unit_id]["role"]).upper(), "text": row["text"].lower()}
        for unit_id in claim.source_unit_ids
        if unit_id in units_by_id
        for row in units_by_id[unit_id]["rows"]
    ]
    violation = _quote_violation_for_rows(claim.text, cited_rows)
    if violation is None:
        return "verified", []

    quote_state = (
        "wrong_role" if violation[1] == "quote-in-wrong-role" else "not_matched"
    )
    return quote_state, [
        {
            "reason": QUOTE_NOT_MATCHED_REASON,
            "detail": QUOTE_NOT_MATCHED_WORDING,
            "segment_ids": [
                row["segment_id"]
                for unit_id in claim.source_unit_ids
                if unit_id in units_by_id
                for row in units_by_id[unit_id]["rows"]
            ][:4],
        }
    ]


def _quote_violation_for_rows(
    sentence: str, normalized_rows: list[dict[str, str]]
) -> tuple[str, str] | None:
    """Run the F1 exact-quote matcher against one claim's cited rows only."""
    from api.summary_fidelity import _non_verbatim_quote_violation

    return _non_verbatim_quote_violation(sentence, normalized_rows)


def _heading_slug(heading: str, fallback_ordinal: int) -> str:
    """Stable artifact-local id stem for one section heading."""
    slug = re.sub(r"[^a-z0-9]+", "-", heading.lower()).strip("-")
    return slug or f"section-{fallback_ordinal + 1}"


def hydrated_v2_payload(
    structured_summary: SessionSummaryV2Output,
    source_units: list[dict[str, Any]],
    citation_segments: list[dict[str, Any]],
    violations: list[FidelityViolation],
    coverage_reasons: list[dict[str, Any]],
    lane_reasons: list[dict[str, Any]],
) -> dict[str, Any]:
    """Assemble the browser payload: claims, deduplicated units, reasons.

    Args:
        structured_summary: Validated, display-clean v2 note.
        source_units: Server-built units for this generation.
        citation_segments: Attested selected rows (context rows come from here).
        violations: Surviving fidelity-rule findings (visible-lane rules).
        coverage_reasons: Note-level M05 coverage findings.
        lane_reasons: Per-sentence M05 reason-lane findings.

    Returns:
        schema_version-2 payload dict; the route adds attestation fields.
    """
    violation_reasons = [
        {
            "sentence": violation.sentence,
            "reason": violation.rule,
            "detail": violation.reason,
            "segment_ids": [],
        }
        for violation in violations
        if violation.rule != "critical-coverage"
    ]
    sentence_reasons = violation_reasons + list(lane_reasons)

    # Stored confidence rides with the full selected rows, not the unit views;
    # the wording-review threshold needs it (the hc12-class garble markers
    # must survive the v1->v2 payload change).
    confidence_rows_by_id = {
        str(segment.get("segment_id", "")): segment for segment in citation_segments
    }
    units_for_confidence = {
        source_unit["unit_id"]: source_unit for source_unit in source_units
    }

    cited_unit_ids: list[str] = []

    def _payload_claim(claim: ClaimOutput, claim_id: str) -> dict[str, Any]:
        quote_state, quote_reasons = _claim_quote_state(claim, source_units)
        review_reasons = _claim_review_reasons(claim.text, sentence_reasons)
        review_reasons.extend(quote_reasons)
        for unit_id in claim.source_unit_ids:
            if unit_id not in cited_unit_ids:
                cited_unit_ids.append(unit_id)
        # The M03 threshold lane, claim-scoped: uncertain cited wording keeps
        # its visible review cue even without a lexicon-variant reason.
        cited_confidence_rows = [
            confidence_rows_by_id[row["segment_id"]]
            for unit_id in claim.source_unit_ids
            for row in units_for_confidence.get(unit_id, {}).get("rows", [])
            if row["segment_id"] in confidence_rows_by_id
        ]
        return {
            "claim_id": claim_id,
            "text": claim.text,
            "evidence_basis": claim.evidence_basis,
            "source_unit_ids": list(claim.source_unit_ids),
            "quote_state": quote_state,
            "wording_review": _sentence_needs_wording_review(
                claim.text, cited_confidence_rows, CORRECTED_NOTE_REVIEW_THRESHOLD
            ),
            "review_reasons": review_reasons,
        }

    payload_sections = []
    for section_ordinal, section in enumerate(structured_summary.sections):
        slug = _heading_slug(section.heading, section_ordinal)
        payload_sections.append(
            {
                "heading": section.heading,
                "claims": [
                    _payload_claim(claim, f"{slug}-{claim_ordinal + 1:02d}")
                    for claim_ordinal, claim in enumerate(section.claims)
                ],
            }
        )
    payload_key_points = [
        _payload_claim(claim, f"key-point-{claim_ordinal + 1:02d}")
        for claim_ordinal, claim in enumerate(structured_summary.key_points)
    ]

    row_positions = {
        str(segment.get("segment_id", "")): position
        for position, segment in enumerate(citation_segments)
    }
    units_by_id = {source_unit["unit_id"]: source_unit for source_unit in source_units}
    payload_units = []
    # Only cited units ship; each embeds its rows once plus labelled context.
    for unit_id in cited_unit_ids:
        source_unit = units_by_id.get(unit_id)
        if source_unit is None:
            continue
        first_position = row_positions.get(source_unit["rows"][0]["segment_id"], 0)
        last_position = row_positions.get(source_unit["rows"][-1]["segment_id"], 0)
        context_before = [
            row_view
            for segment in citation_segments[
                max(0, first_position - SOURCE_UNIT_CONTEXT_ROWS) : first_position
            ]
            if (row_view := _unit_row_view(segment)) is not None
        ]
        context_after = [
            row_view
            for segment in citation_segments[
                last_position + 1 : last_position + 1 + SOURCE_UNIT_CONTEXT_ROWS
            ]
            if (row_view := _unit_row_view(segment)) is not None
        ]
        payload_units.append(
            {
                "unit_id": source_unit["unit_id"],
                "role": source_unit["role"],
                "start": source_unit["start"],
                "end": source_unit["end"],
                "rows": source_unit["rows"],
                "context_before": context_before,
                "context_after": context_after,
            }
        )

    return {
        "schema_version": SUMMARY_SCHEMA_VERSION,
        "title": structured_summary.title,
        "sections": payload_sections,
        "key_points": payload_key_points,
        "source_units": payload_units,
        "note_review_reasons": [
            {
                "reason": coverage_reason["reason"],
                "detail": str(coverage_reason.get("detail", "")),
                "segment_ids": list(coverage_reason.get("segment_ids", [])),
            }
            for coverage_reason in coverage_reasons
        ],
    }
