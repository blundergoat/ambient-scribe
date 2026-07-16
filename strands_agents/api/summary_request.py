"""
Summary request helpers for browser-visible transcript text.

The browser asks for a summary of the transcript rows the user can see. These
helpers normalise that browser snapshot, update session storage, and publish
summary outputs while keeping `server.py` focused on HTTP routing and
status codes.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from api import source_integrity
from api.summary_generation import source_index_text
from pydantic import BaseModel, Field
from storage import StorageBackend

logger = logging.getLogger(__name__)

PublishToMercure = Callable[[str, dict[str, Any], int | None], Awaitable[bool]]
SegmentFormatter = Callable[[list[dict[str, Any]]], str]

SUMMARY_TRANSCRIPT_MAX_CHARS = 32_768
SUMMARY_TRANSCRIPT_ELISION_MARKER = "[... transcript rows omitted ...]"


class BrowserVisibleSegment(BaseModel):
    """
    Transcript segment sent back from the browser-visible transcript view.

    Use when the server needs the same transcript subset the user can see
    before generating a summary.
    Empty text means the row is ignored because it would not help the note.
    An empty `segment_id` means an older page without row identity; those rows
    cannot be merged into a populated stored history and are skipped there.
    """

    speaker_id: str = "UNKNOWN"
    text: str = ""
    start: float = 0.0
    end: float = 0.0
    role: str | None = None
    segment_id: str = ""
    # Row ASR confidence echoed back by the browser; None means the row never
    # carried one and a restore-from-browser keeps it unmeasured.
    confidence: float | None = None


class SummaryRequest(BaseModel):
    """
    Optional summary body containing browser-visible transcript text.

    Use when the user asks for a note from only the transcript currently on
    screen. Empty segments mean the route should use the stored session
    transcript instead.
    """

    segments: list[BrowserVisibleSegment] = Field(default_factory=list)


@dataclass(slots=True)
class TranscriptSelection:
    """Whole transcript rows selected within one formatter-specific budget.

    Opening and tail rows remain separate so every formatter can place the
    same explicit elision marker between the non-contiguous runs without
    inventing a clinical row or citation ID.
    """

    opening_segments: list[dict[str, Any]]
    tail_segments: list[dict[str, Any]]
    formatted_text: str
    original_chars: int
    kept_chars: int
    original_segments: int
    kept_segments: int
    truncated: bool

    @property
    def selected_segments(self) -> list[dict[str, Any]]:
        """Return selected rows in consultation order."""
        return [*self.opening_segments, *self.tail_segments]

    def format_with(self, formatter: SegmentFormatter) -> str:
        """Format the same selected rows for retrieval or generation."""
        return _format_selected_runs(
            self.opening_segments,
            self.tail_segments,
            formatter,
            truncated=self.truncated,
        )


@dataclass(slots=True)
class SummaryContext:
    """
    Transcript context selected for one summary request.

    Use in the FastAPI summary route after the clinician clicks Summarise.
    Corrected sources mean a slower post-visit pass has produced higher-quality
    rows. Browser-visible sources mean the UI sent the rows on screen;
    session-store sources mean the user summarized the full stored transcript.

    Attributes:
        complete_segments: Full rows retained in storage/history.
        selected_segments: Whole rows selected for generation and fidelity checks.
        transcript: Role-attributed selected text used for retrieval and uncited prompts.
        source: Log label explaining whether UI replay rows or stored rows were used.
        citation_segments: Rows whose `segment_id` values may be cited by the
            summary agent; empty keeps the legacy uncited summary flow.
        citation_source_index: Preformatted selected citation rows, including
            an elision marker when rows were omitted; null keeps the uncited flow.
        transcript_truncated: True only when row selection omitted content.
        original_transcript_chars: Formatter-specific characters before selection.
        kept_transcript_chars: Formatter-specific characters sent after selection.
    """

    complete_segments: list[dict[str, Any]]
    selected_segments: list[dict[str, Any]]
    transcript: str
    source: str
    citation_segments: list[dict[str, Any]]
    citation_source_index: str | None
    transcript_truncated: bool
    original_transcript_chars: int
    kept_transcript_chars: int


def select_summary_segments(
    transcript_segments: list[dict[str, Any]],
    formatter: SegmentFormatter,
    max_chars: int = SUMMARY_TRANSCRIPT_MAX_CHARS,
) -> TranscriptSelection:
    """Select complete opening and tail rows within one formatted budget.

    Args:
        transcript_segments: Complete source rows; blank/unformattable rows stay in storage only.
        formatter: Exact plain-transcript or citation-source formatter used by generation.
        max_chars: Maximum formatted input characters; zero keeps no clinical rows.

    Returns:
        Whole-row selection plus explicit truncation metadata.
    """
    formattable_segments = [
        segment for segment in transcript_segments if formatter([segment]) != ""
    ]
    original_text = formatter(formattable_segments)
    original_chars = len(original_text)
    original_segments = len(formattable_segments)

    # Under budget means the model sees the exact pre-M08 row order and formatting.
    if original_chars <= max_chars:
        return TranscriptSelection(
            opening_segments=formattable_segments,
            tail_segments=[],
            formatted_text=original_text,
            original_chars=original_chars,
            kept_chars=original_chars,
            original_segments=original_segments,
            kept_segments=original_segments,
            truncated=False,
        )

    # A non-positive budget intentionally keeps no clinical content.
    if max_chars <= 0:
        return TranscriptSelection(
            opening_segments=[],
            tail_segments=[],
            formatted_text="",
            original_chars=original_chars,
            kept_chars=0,
            original_segments=original_segments,
            kept_segments=0,
            truncated=original_segments > 0,
        )

    opening_budget = max(1, max_chars // 4)
    opening_segments: list[dict[str, Any]] = []
    # Reserve at least one quarter of the budget for contiguous opening rows.
    for segment in formattable_segments:
        candidate = [*opening_segments, segment]
        if len(formatter(candidate)) > opening_budget:
            break
        opening_segments = candidate

    # A single long opening row may exceed the quarter allocation but still
    # remains whole when it can coexist with the marker and some tail budget.
    if not opening_segments and formattable_segments:
        first_segment = formattable_segments[0]
        first_with_marker = _format_selected_runs(
            [first_segment], [], formatter, truncated=True
        )
        if len(first_with_marker) <= max_chars:
            opening_segments = [first_segment]

    tail_segments: list[dict[str, Any]] = []
    tail_candidates = formattable_segments[len(opening_segments) :]
    # Walk backward so the final Assessment/Plan rows are always the first tail
    # content considered; stopping preserves one contiguous tail run.
    for segment in reversed(tail_candidates):
        candidate = [segment, *tail_segments]
        candidate_text = _format_selected_runs(
            opening_segments, candidate, formatter, truncated=True
        )
        if len(candidate_text) > max_chars:
            break
        tail_segments = candidate

    formatted_text = _format_selected_runs(
        opening_segments, tail_segments, formatter, truncated=True
    )
    selected_segments = len(opening_segments) + len(tail_segments)
    return TranscriptSelection(
        opening_segments=opening_segments,
        tail_segments=tail_segments,
        formatted_text=formatted_text,
        original_chars=original_chars,
        kept_chars=len(formatted_text),
        original_segments=original_segments,
        kept_segments=selected_segments,
        truncated=selected_segments < original_segments,
    )


def _format_selected_runs(
    opening_segments: list[dict[str, Any]],
    tail_segments: list[dict[str, Any]],
    formatter: SegmentFormatter,
    *,
    truncated: bool,
) -> str:
    """Format selected runs with a non-clinical marker between omitted rows."""
    if not truncated:
        return formatter(opening_segments)

    parts = [formatter(opening_segments), SUMMARY_TRANSCRIPT_ELISION_MARKER]
    tail_text = formatter(tail_segments)
    if tail_text:
        parts.append(tail_text)
    return "\n".join(part for part in parts if part)


def _summary_context_from_segments(
    session_id: str,
    complete_segments: list[dict[str, Any]],
    source: str,
    *,
    allow_citations: bool,
) -> SummaryContext:
    """Build one source lane's aligned prompt, citation, and fidelity rows."""
    meaningful_segments = [
        segment
        for segment in complete_segments
        if str(segment.get("text", "")).strip() != ""
    ]
    # A partially citable corrected prompt would silently omit unidentified
    # clinical rows, so mixed/legacy rows use the complete uncited formatter.
    use_citations = allow_citations and bool(meaningful_segments) and all(
        source_index_text([segment]) != "" for segment in meaningful_segments
    )
    prompt_formatter = source_index_text if use_citations else transcript_text_from_segments
    selection = select_summary_segments(complete_segments, prompt_formatter)
    selected_segments = selection.selected_segments
    transcript = selection.format_with(transcript_text_from_segments)
    citation_segments = selected_segments if use_citations else []
    citation_source_index = selection.formatted_text if use_citations else None

    if selection.truncated:
        logger.warning(
            "summary.transcript_truncated session_id=%s source=%s original_chars=%s "
            "kept_chars=%s original_segments=%s kept_segments=%s",
            session_id,
            source,
            selection.original_chars,
            selection.kept_chars,
            selection.original_segments,
            selection.kept_segments,
            extra={
                "session_id": session_id,
                "source": source,
                "original_chars": selection.original_chars,
                "kept_chars": selection.kept_chars,
                "original_segments": selection.original_segments,
                "kept_segments": selection.kept_segments,
            },
        )

    return SummaryContext(
        complete_segments=complete_segments,
        selected_segments=selected_segments,
        transcript=transcript,
        source=source,
        citation_segments=citation_segments,
        citation_source_index=citation_source_index,
        transcript_truncated=selection.truncated,
        original_transcript_chars=selection.original_chars,
        kept_transcript_chars=selection.kept_chars,
    )


def _corrected_rows_are_current(session_id: str) -> bool:
    """Whether stored corrected rows belong to the visit the user just finished.

    Example: the clinician pressed Stop (correction ran and stored rows), then
    pressed Start to continue the visit and stopped again. The old artifact no
    longer describes the full visit; letting it win source selection would make
    every summary click show "note unavailable" even when the live transcript
    is an authorized fallback.

    Args:
        session_id: Visit shown in the browser; unknown ids have no watermark.

    Returns:
        True when the current terminal watermark attests the corrected rows,
        or when no watermark exists yet (the route then fails the request
        closed before generation, so nothing stale can reach the clinician).
    """
    watermark = source_integrity.get_terminal_watermark(session_id)
    # No watermark means the visit never finalized in this process; keep the
    # legacy preference and let the summary route refuse the request itself.
    if watermark is None:
        return True
    return watermark.correction_status == "attested_corrected"


def build_summary_context(
    session_id: str,
    summary_request: SummaryRequest | None,
    sessions: StorageBackend,
) -> SummaryContext:
    """Choose the transcript text used by the summary agent.

    Args:
        session_id: Browser session UUID; empty would not map to stored rows.
        summary_request: Optional browser-visible replay rows; null uses storage.
        sessions: Transcript store; empty store means there is nothing to summarize.

    Returns:
        SummaryContext with selected rows, text, and source label for logs.
    """
    corrected_segments = sessions.get_corrected_segments(session_id)
    # Corrected rows are opt-in at the storage layer, but summary generation is
    # the first consumer that should prefer them once the slower pass exists.
    if corrected_segments and _corrected_rows_are_current(session_id):
        return _summary_context_from_segments(
            session_id,
            corrected_segments,
            "corrected_segments",
            allow_citations=True,
        )

    browser_visible_segments = browser_visible_segments_from_summary(summary_request)
    # Browser-provided rows anchor the note to the transcript the user can see,
    # merged so rows the browser missed (finalize flush, dropped events) still
    # reach the note instead of being deleted from server history.
    if browser_visible_segments:
        merge_result = sessions.merge_browser_segments(
            session_id, browser_visible_segments
        )
        # Unknown rows or a restore-from-browser are worth a trace when
        # diagnosing a summary that does not match the stored transcript.
        if merge_result.get("unknown", 0) or merge_result.get("restored"):
            logger.info(
                "summary.segments_merged session_id=%s matched=%s unknown=%s restored=%s",
                session_id,
                merge_result.get("matched", 0),
                merge_result.get("unknown", 0),
                merge_result.get("restored", False),
                extra={"session_id": session_id, **merge_result},
            )
        # Reading the rows back applies server-side row corrections and keeps
        # tail rows the browser never received, so the note covers the whole
        # stored consultation with the labels the clinician trusts.
        merged_rows = sessions.get_segments(session_id)
        return _summary_context_from_segments(
            session_id,
            merged_rows,
            "browser_visible_segments",
            allow_citations=False,
        )

    return _summary_context_from_segments(
        session_id,
        sessions.get_segments(session_id),
        "session_store",
        allow_citations=False,
    )


def browser_visible_segments_from_summary(
    summary_request: SummaryRequest | None,
) -> list[dict[str, Any]]:
    """Return summary request segments that contain visible transcript text.

    Args:
        summary_request: Optional JSON body from the browser; null means use stored text.

    Returns:
        Segment dicts with non-empty text; empty means the route should use storage.
    """
    # Empty request body means live recordings should keep using stored session text.
    if summary_request is None:
        return []

    return normalise_browser_visible_segments(summary_request.segments)


def normalise_browser_visible_segments(
    browser_segments: list[BrowserVisibleSegment],
) -> list[dict[str, Any]]:
    """Convert browser-visible segment models into storage-safe dictionaries.

    Args:
        browser_segments: Browser transcript rows; empty means no text is visible.

    Returns:
        Segment dictionaries; rows with blank text are skipped for summary quality.
    """
    normalised_segments: list[dict[str, Any]] = []
    # Every browser-visible row becomes one role-aware summary line.
    for browser_segment in browser_segments:
        segment_payload = browser_segment.model_dump()
        text = str(segment_payload.get("text", "")).strip()
        # Blank text is not useful to the clinician's note.
        if text == "":
            continue

        segment_payload["text"] = text
        # Unmeasured rows drop the key so stored history stays absent-is-absent.
        if segment_payload.get("confidence") is None:
            segment_payload.pop("confidence", None)
        normalised_segments.append(segment_payload)

    return normalised_segments


def transcript_text_from_segments(
    transcript_segments: list[dict[str, Any]],
    max_chars: int | None = None,
) -> str:
    """Build role-attributed transcript text from browser-visible segments.

    Args:
        transcript_segments: Visible rows selected by the user flow; empty returns no text.
        max_chars: Optional caller-specific cap; null returns every formatted row.

    Returns:
        Plain transcript text used by the summary agent; empty means nothing can be summarized.
    """
    # A zero budget lets tests or callers intentionally request no summary context.
    if max_chars is not None and max_chars <= 0:
        return ""

    transcript_lines: list[str] = []
    # Each visible segment keeps the role label the clinician saw in the browser.
    for segment in transcript_segments:
        speaker = segment.get("role") or segment.get("speaker_id") or "UNKNOWN"
        text = str(segment.get("text", "")).strip()
        # Empty text rows were already filtered, but this keeps the helper safe.
        if text == "":
            continue

        transcript_lines.append(f"[{speaker}] {text}")

    full_transcript = "\n".join(transcript_lines)
    if max_chars is None:
        return full_transcript
    return full_transcript[:max_chars]


async def publish_summary_outputs(
    session_id: str,
    summary: dict[str, Any],
    *,
    mercure_event_ids: dict[str, int],
    publish_to_mercure: PublishToMercure,
    logger: logging.Logger,
) -> None:
    """Publish summary output for the browser.

    Args:
        session_id: Browser session UUID; empty would publish to the wrong topic.
        summary: Summary payload; empty still publishes an empty panel state.
        mercure_event_ids: Per-session counters used for EventSource resume.
        publish_to_mercure: Browser event publisher; false means HTTP still returns data.
        logger: Process logger; null is not accepted because failures should be visible.
    """
    mercure_event_ids.setdefault(session_id, 0)
    mercure_event_ids[session_id] += 1
    delivered = await publish_to_mercure(
        f"scribe/session/{session_id}/summary",
        {
            "type": "summary",
            "session_id": session_id,
            **summary,
        },
        event_id=mercure_event_ids[session_id],
    )
    if not delivered:
        logger.warning(
            "summary.publish_failed session_id=%s",
            session_id,
            extra={"session_id": session_id},
        )
